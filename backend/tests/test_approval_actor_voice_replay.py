"""Actor-local approval snapshots and canonical stored voice replay, without app imports."""

import ast
import copy
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_web_tool_citations import BACKEND, load_functions
from open_webui.utils import context_compaction_v2 as v2


def request_metadata(params, messages):
    tree = ast.parse((BACKEND / 'main.py').read_text(encoding='utf-8'))
    handler = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'chat_completion'
    )
    assignment = next(
        node
        for node in ast.walk(handler)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'metadata' for target in node.targets)
        and isinstance(node.value, ast.Dict)
        and any(isinstance(key, ast.Constant) and key.value == 'user_id' for key in node.value.keys)
    )
    namespace = dict(
        copy=copy,
        form_data={'params': copy.deepcopy(params), 'messages': copy.deepcopy(messages)},
        request=SimpleNamespace(headers={}, state=SimpleNamespace()),
        user=SimpleNamespace(id='actor'),
        request_params=params,
        approval_resume=None,
        chat_id='shared',
        user_message={'id': 'u'},
        automation_id=None,
        tool_servers=[],
        chat_variables={},
        model={},
        model_item={},
        model_info_params={},
        model_controls={},
        stream_delta_chunk_size=1,
        reasoning_tags=[],
        compact_token_threshold=0,
        tool_approval_mode='ask',
    )
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), 'main metadata', 'exec'), namespace)
    return namespace['metadata']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'system,messages',
    [
        ('actor turn override', []),
        ('', []),
        (None, [{'role': 'system', 'content': 'actor message instructions'}]),
    ],
)
async def test_participant_approval_preserves_request_params_and_system_reset(system, messages):
    params = {'temperature': 0.2, 'max_tokens': 123, 'custom_params': {'seed': 5}}
    if system is not None:
        params['system'] = system
    metadata = request_metadata(params, messages)
    # A later request mutation must not change the saved actor snapshot.
    params['custom_params']['seed'] = 9
    assistant = {'id': 'a', 'parentId': 'u', 'user_id': 'actor', 'model': 'model'}
    question = {'id': 'u', 'parentId': None, 'role': 'user', 'content': 'shared question'}

    async def save(chat_id, message_id, update, **kwargs):
        assistant.update(copy.deepcopy(update))

    namespace = {
        'copy': copy,
        'Chats': SimpleNamespace(
            upsert_message_to_chat_by_id_and_message_id=AsyncMock(side_effect=save),
            get_message_by_id_and_message_id=AsyncMock(
                side_effect=lambda cid, mid: assistant if mid == 'a' else question
            ),
        ),
        'Users': SimpleNamespace(
            get_user_by_id=AsyncMock(
                return_value=SimpleNamespace(settings=SimpleNamespace(ui={'system': 'changed actor default'}))
            )
        ),
        'Config': SimpleNamespace(get=AsyncMock(return_value={'system': 'global default'})),
    }
    load_functions(namespace, 'utils/middleware.py', {'pause_for_tool_approval'})
    load_functions(namespace, 'utils/tool_approval.py', {'build_tool_approval_resume_payload'})
    await namespace['pause_for_tool_approval'](
        'shared', 'a', [{'type': 'function_call', 'call_id': 'call', 'name': 'tool'}], {}, metadata
    )
    chat = SimpleNamespace(
        user_id='owner',
        chat={'params': {'system': 'OWNER PRIVATE', 'temperature': 0.9}},
        variables={'private': 'OWNER SECRET'},
    )
    resumed = await namespace['build_tool_approval_resume_payload']('shared', 'a', chat)
    assert resumed['params']['temperature'] == 0.2
    assert resumed['params']['max_tokens'] == 123
    assert resumed['params']['custom_params'] == {'seed': 5}
    assert resumed['chat_variables'] == {}
    assert 'OWNER' not in str(resumed)
    if system is not None:
        assert resumed['params']['system'] == system
        assert resumed['messages'] == ([{'role': 'system', 'content': system}] if system else [])
    else:
        assert resumed['messages'] == messages
    namespace['Users'].get_user_by_id.assert_not_awaited()


@pytest.fixture
def canonical_replay(monkeypatch):
    namespace = {'JSONCodec': json}
    load_functions(
        namespace, 'utils/misc.py', {'convert_output_to_messages', 'reconcile_tool_pairs', 'get_paired_tool_call_ids'}
    )
    load_functions(
        namespace, 'utils/middleware.py', {'process_messages_with_output', 'get_reasoning_format', 'add_file_context'}
    )
    middleware, misc, payload = (ModuleType('open_webui.utils.' + name) for name in ('middleware', 'misc', 'payload'))
    for name in ('process_messages_with_output', 'get_reasoning_format'):
        setattr(middleware, name, namespace[name])
    misc.convert_output_to_messages = namespace['convert_output_to_messages']
    payload.convert_messages_openai_to_ollama = lambda messages: messages
    for name, module in [('middleware', middleware), ('misc', misc), ('payload', payload)]:
        monkeypatch.setitem(sys.modules, 'open_webui.utils.' + name, module)
    return namespace['process_messages_with_output']


@pytest.mark.asyncio
@pytest.mark.parametrize('voice_only', [True, False])
async def test_stored_voice_payload_matches_live_tokens_and_checkpoint_keys(canonical_replay, voice_only):
    assistant = {
        'id': 'a',
        'parentId': 'u0',
        'role': 'assistant',
        'model': 'voice' if voice_only else 'model',
        'content': 'spoken words' if voice_only else 'answer',
        'meta': {'voice': {'model': 'voice', 'speech': [{'transcript': 'spoken history ' * 100}]}},
    }
    if not voice_only:
        assistant['output'] = [
            {
                'type': 'message',
                'id': 'out',
                'status': 'completed',
                'role': 'assistant',
                'content': [{'type': 'output_text', 'text': 'answer'}],
            }
        ]
    raw = [
        {'id': 'u0', 'role': 'user', 'content': 'old question'},
        assistant,
        {'id': 'u1', 'parentId': 'a', 'role': 'user', 'content': 'next question'},
    ]
    snapshot = {
        'messages_map': {message['id']: message for message in raw},
        'current_id': 'u1',
        'chat': {},
        'shared_history': True,
    }
    before = copy.deepcopy(snapshot)
    live = canonical_replay(copy.deepcopy(raw), preserve_compaction_ids=True)
    live = [{**v2.wire_message(message), '_context_message_id': message['_context_message_id']} for message in live]
    payload, _ = await v2._history_payload(snapshot, 'model', {'model': {}})
    assert payload['messages'] == live
    assert v2.estimate_payload_tokens(payload) == v2.estimate_payload_tokens({'model': 'model', 'messages': live})
    checkpoint = {
        'summary': 'earlier voice history',
        'covered_message_keys': [
            {'key': v2.message_key(message), 'source_id': message['_context_message_id']}
            for message in payload['messages'][:-1]
        ],
    }
    replay, applied = v2.apply_checkpoint(live, checkpoint)
    assert applied
    assert replay == [v2.history_message('earlier voice history'), live[-1]]
    assert snapshot == before
