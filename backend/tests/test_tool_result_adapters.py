"""Runtime provenance tests without application imports or production database state."""

import asyncio
import copy
import json
import logging
import sys
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from test_web_tool_citations import BACKEND, load_functions


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.syspath_prepend(str(BACKEND.parent))
    from types import ModuleType

    selections = SimpleNamespace(register_pending=AsyncMock(return_value=True))
    module = ModuleType('open_webui.models.chat_tool_result_selections')
    module.get_tool_selection_store = lambda: selections
    monkeypatch.setitem(sys.modules, module.__name__, module)
    ns = {'JSONCodec': json, 'log': logging.getLogger(__name__), 'selection_store': selections}
    load_functions(
        ns,
        'utils/misc.py',
        {
            'convert_output_to_messages',
            'reconcile_tool_pairs',
            'get_paired_tool_call_ids',
            'strip_empty_content_blocks',
            'merge_system_messages',
            'get_system_message',
            'get_last_user_message_item',
            'get_content_from_message',
        },
    )
    load_functions(
        ns,
        'utils/middleware.py',
        {
            'process_messages_with_output',
            'normalize_messages_for_model',
            'attach_tool_citations',
            'get_tool_citation_source_ids',
            'get_source_context',
            'get_citation_source_from_tool_result',
            'get_reasoning_format',
            'sanitize_tool_pairs',
            'convert_url_images_to_base64',
        },
    )
    return ns


def native_output():
    return [
        {'type': 'function_call', 'id': 'fc-a', 'call_id': 'a', 'name': 'search_web', 'status': 'completed'},
        {'type': 'function_call', 'id': 'fc-b', 'call_id': 'b', 'name': 'read_tool_result', 'status': 'completed'},
        {
            'type': 'function_call_output',
            'id': 'result-a',
            'call_id': 'a',
            'output': [
                {'type': 'input_text', 'text': '中文&<raw>\n'},
                {'type': 'input_text', 'text': '{"n": 1}'},
                {'type': 'input_image', 'image_url': 'data:image/png;base64,c3ludGhldGlj'},
            ],
        },
        {
            'type': 'function_call_output',
            'id': 'result-b',
            'call_id': 'b',
            'output': [
                {'type': 'input_text', 'text': '{"text":"exact page","next_cursor":"opaque"}'},
            ],
        },
    ]


def test_native_replay_tracks_exact_text_and_stable_batch_without_mutating_raw(runtime):
    output = native_output()
    before = copy.deepcopy(output)
    messages = runtime['process_messages_with_output'](
        [{'id': 'assistant-1', 'role': 'assistant', 'output': output}], preserve_compaction_ids=True
    )
    tool_messages = [m for m in messages if m['role'] == 'tool']
    refs = [m.get('_tool_result_refs', []) for m in tool_messages]
    assert all(refs), 'native raw replay must carry result provenance'
    assert refs[0][0]['text'] == '中文&<raw>\n{"n": 1}'
    assert refs[0][0]['message_id'] == 'assistant-1'
    assert refs[0][0]['output_id'] == 'result-a'
    assert refs[0][0]['batch_id'] == refs[1][0]['batch_id']
    assert refs[1][0]['kind'] == 'read'
    assert output == before
    replay = runtime['process_messages_with_output'](
        [{'id': 'assistant-1', 'role': 'assistant', 'output': output}], preserve_compaction_ids=True
    )
    assert messages == replay


def test_code_replay_uses_same_exact_extraction_as_raw_source(runtime):
    output = [
        {
            'type': 'open_webui:code_interpreter',
            'id': 'code-1',
            'code': 'print("x")',
            'output': {'stdout': 'unchanged stdout\n', 'result': 'fallback'},
            'status': 'completed',
        }
    ]
    messages = runtime['process_messages_with_output'](
        [{'id': 'assistant-1', 'role': 'assistant', 'output': output}], preserve_compaction_ids=True
    )
    refs = messages[0].get('_tool_result_refs', [])
    assert refs, 'executed code output must be budgeted on replay'
    assert refs[0]['kind'] == 'code'
    from open_webui.utils.tool_result_sources import source_text

    assert source_text({'output': output}, 'code-1') == refs[0]['text'] == 'unchanged stdout\n'
    assert refs[0]['text'] in messages[0]['content']


def test_injected_sidecar_replays_once_and_preserves_original_metadata(runtime):
    sidecar = {
        'id': 'injected-1',
        'call_id': 'i-1',
        'batch_id': 'batch-i',
        'name': 'web',
        'kind': 'injected',
        'text': 'raw & <tag> 中文',
    }
    raw = {
        'id': 'assistant-1',
        'role': 'assistant',
        'content': 'Final answer',
        'meta': {'tool_results': [sidecar], 'existing': True},
    }
    before = copy.deepcopy(raw)
    messages = runtime['process_messages_with_output']([raw], preserve_compaction_ids=True)
    refs = [r for m in messages for r in m.get('_tool_result_refs', [])]
    assert len(refs) == 1, 'persisted legacy tool sidecars must replay with provenance'
    assert refs[0]['message_id'] == 'assistant-1'
    assert refs[0]['text'] == sidecar['text']
    assert sum(m['content'].count(sidecar['text']) for m in messages) == 1
    assert all('meta' not in m and 'tool_results' not in m for m in messages)
    assert raw == before


def test_batch_annotation_survives_partial_approvals(runtime):
    from open_webui.utils.tool_result_sources import annotate_output_batch

    output = native_output()[:2]
    annotate_output_batch(output, ['a', 'b'])
    original_batch = output[0]['tool_result']['batch_id']
    assert original_batch == output[1]['tool_result']['batch_id']
    annotate_output_batch(output, ['b'])
    assert output[1]['tool_result']['batch_id'] == original_batch


def test_raw_source_handles_code_native_and_normalized_sidecars(runtime):
    from open_webui.utils.tool_result_sources import source_text

    assert source_text({'output': native_output()}, 'result-a') == '中文&<raw>\n{"n": 1}'
    assert (
        source_text(
            {
                'output': [
                    {
                        'id': 'c',
                        'type': 'open_webui:code_interpreter',
                        'output': {'stdout': '', 'result': 'exact result'},
                    }
                ]
            },
            'c',
        )
        == 'exact result'
    )
    assert source_text({'meta': {'tool_results': [{'id': 's', 'text': 'original'}]}}, 's') == 'original'
    assert source_text({'output': native_output()}, 'unknown') is None


@pytest.mark.asyncio
async def test_approved_execution_persists_provenance_and_resume_never_reexecutes(runtime):
    calls = native_output()[:2]
    for call in calls:
        call.update(status='queued', approved=True)
    record = {'id': 'assistant-1', 'role': 'assistant', 'output': calls}
    snapshots = []

    async def persist(chat_id, message_id, updates, **kwargs):
        record.update(copy.deepcopy(updates))
        snapshots.append(copy.deepcopy(record))

    async def execute(*args):
        tc = args[-1]
        return {'tool_call_id': tc['id'], 'content': 'raw ' + tc['id']}

    ns = runtime
    ns.update(
        Chats=SimpleNamespace(
            get_message_by_id_and_message_id=AsyncMock(side_effect=lambda *args: record),
            upsert_message_to_chat_by_id_and_message_id=persist,
        ),
        is_saved_chat_id=lambda value: value == 'chat-1',
        get_event_emitter_and_caller=AsyncMock(return_value=(None, None)),
        execute_tool_call_for_output=AsyncMock(side_effect=execute),
        build_tool_result_parts=lambda result: ([{'type': 'input_text', 'text': result['content']}], []),
        _is_tool_result_error=lambda text: False,
        output_id=lambda prefix: prefix + '-' + str(ns['execute_tool_call_for_output'].await_count),
        load_messages_from_db=AsyncMock(return_value=[]),
        ENABLE_PLUGINS=False,
    )

    @asynccontextmanager
    async def edit_output(chat_id, message_id):
        yield record
        snapshots.append(copy.deepcopy(record))

    ns.update(
        copy=copy,
        ENABLE_FUNCTIONS=False,
        strip_reasoning_details=lambda output: output,
        convert_url_images_to_base64=AsyncMock(),
        HTTPException=__import__('fastapi').HTTPException,
    )
    ns['Chats'].edit_message_output = edit_output
    ns['ERROR_MESSAGES'] = SimpleNamespace(NOT_FOUND='not found')
    ns['tool_continuation'] = lambda: {'type': 'message', 'id': 'continuation', 'content': []}
    load_functions(ns, 'utils/tool_approval.py', {'pending_tool_calls', 'complete_tool_call'})
    load_functions(ns, 'utils/middleware.py', {'resume_tool_calls'})
    metadata = {'chat_id': 'chat-1', 'assistant_message_id': 'assistant-1', 'message_id': 'assistant-1'}
    request = SimpleNamespace(
        state=SimpleNamespace(
            context_compaction={
                'mode': 'v2',
                'chat_id': 'chat-1',
                'model_id': 'model',
                'user_message_id': 'user-1',
                'settings': SimpleNamespace(tool_budget_enabled=True),
            }
        )
    )
    await ns['resume_tool_calls'](
        request,
        {'messages': []},
        None,
        {'id': 'model', 'info': {'meta': {'capabilities': {'citations': False}}}},
        metadata,
        record,
    )
    results = [i for i in snapshots[-1]['output'] if i['type'] == 'function_call_output']
    assert all(item.get('tool_result') for item in results), 'approval must save original result identity'
    assert results[0]['tool_result']['batch_id'] == results[1]['tool_result']['batch_id']
    assert results[1]['tool_result']['kind'] == 'read'
    admission = ns['selection_store'].register_pending.await_args
    assert admission.args[:2] == ('chat-1', 'model')
    assert ns['selection_store'].register_pending.await_count == 2
    admitted = [ref for call in ns['selection_store'].register_pending.await_args_list for ref in call.args[2]]
    assert len(admitted) == 2 and len({ref['batch_id'] for ref in admitted}) == 1
    await ns['resume_tool_calls'](
        request,
        {'messages': []},
        None,
        {'id': 'model', 'info': {'meta': {'capabilities': {'citations': False}}}},
        metadata,
        record,
    )
    assert ns['selection_store'].register_pending.await_count == 2
    assert ns['execute_tool_call_for_output'].await_count == 2


@pytest.mark.asyncio
async def test_nonnative_sidecars_are_saved_before_send_and_injected_exactly_once(runtime):
    ns = runtime
    persisted = []

    async def persist(chat, message, updates, **kwargs):
        persisted.append(copy.deepcopy(updates))

    ns['Chats'] = SimpleNamespace(
        get_message_by_id_and_message_id=AsyncMock(return_value={'meta': {'old': 1}}),
        upsert_message_to_chat_by_id_and_message_id=persist,
    )
    load_functions(ns, 'utils/middleware.py', {'persist_injected_tool_results'})
    request = SimpleNamespace(
        state=SimpleNamespace(
            context_compaction={
                'mode': 'v2',
                'chat_id': 'chat-1',
                'model_id': 'model',
                'user_message_id': 'user-1',
                'settings': SimpleNamespace(tool_budget_enabled=True),
            }
        )
    )
    metadata = {'chat_id': 'chat-1', 'message_id': 'assistant-1'}
    source = {
        'source': {'name': 'web'},
        'document': ['original & <raw>'],
        'metadata': [{}],
        'tool_result': True,
        '_tool_result_ref': {'call_id': 'call-1', 'batch_id': 'batch-1', 'name': 'web', 'kind': 'injected'},
    }
    sidecars = await ns['persist_injected_tool_results'](request, metadata, [source])
    assert persisted and persisted[0]['meta']['old'] == 1
    assert persisted[0]['tool_results'][0]['text'] == 'original & <raw>'
    assert persisted[0]['meta']['tool_results'] == sidecars
    assert source['_tool_result_ref']['message_id'] == 'assistant-1'
    ns['selection_store'].register_pending.assert_awaited_once()
    assert ns['selection_store'].register_pending.await_args.args[2][0]['text'] == sidecars[0]['text']


@pytest.mark.asyncio
async def test_native_v2_citations_do_not_reintroduce_raw_snippets(runtime):
    from html import escape
    import re

    ns = runtime
    ns.update(
        escape=escape,
        re=re,
        RAG_SYSTEM_CONTEXT=False,
        filter_source_metadata=lambda meta: {},
        Config=SimpleNamespace(get=AsyncMock(return_value='{{CONTEXT}}')),
        rag_template=AsyncMock(side_effect=lambda template, context, user: context),
    )
    load_functions(
        ns,
        'utils/misc.py',
        {
            'add_or_update_system_message',
            'add_or_update_user_message',
            'get_content_from_message',
            'get_system_message',
            'get_last_user_message_item',
            'update_message_content',
        },
    )
    load_functions(
        ns, 'utils/middleware.py', {'get_source_context', 'get_tool_source_context', 'apply_source_context_to_messages'}
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            context_compaction={
                'mode': 'v2',
                'chat_id': 'chat-1',
                'model_id': 'model',
                'user_message_id': 'user-1',
                'settings': SimpleNamespace(tool_budget_enabled=True),
            }
        )
    )
    load_functions(
        ns,
        'utils/middleware.py',
        {'get_citation_source_from_tool_result', 'attach_tool_citations', '_is_tool_result_error'},
    )
    snippet = 'secret long evidence ' * 1000
    result = {'content': json.dumps([{'title': 'Page', 'link': 'https://example.test/1', 'snippet': snippet}])}
    raw = result['content']
    await ns['attach_tool_citations'](result, 'search_web', {}, {}, {}, None, request=request)
    assert 'secret long evidence' not in result['citation_context'], (
        'budgeted evidence must have only one provider copy'
    )
    assert 'search_snippet' in result['citation_context']
    assert snippet in result['citation_sources'][0]['document'][0]
    assert result['content'] == raw


@pytest.mark.asyncio
async def test_reader_registered_despite_category_toggles_only_for_trusted_native_v2(runtime):
    from test_chat_tool_features import ChatToolFeaturesTests

    context = ChatToolFeaturesTests()
    context.setUp()
    try:
        context.namespace['is_saved_chat_id'] = lambda value: value == 'chat-1'
        context.metadata = {'chat_id': 'chat-1', 'params': {'function_calling': 'native'}}
        context.model = {
            'info': {
                'meta': {
                    'builtinTools': {
                        'time': False,
                        'user_input': False,
                        'knowledge': False,
                        'chats': False,
                        'tasks': False,
                        'tool_results': False,
                    }
                }
            }
        }
        state = {
            'mode': 'v2',
            'chat_id': 'chat-1',
            'user_message_id': 'user-1',
            'settings': SimpleNamespace(tool_budget_enabled=True),
        }
        request = SimpleNamespace(state=SimpleNamespace(context_compaction=state))

        async def selected():
            return await context.namespace['get_builtin_tools'](
                request,
                {'__user__': context.user, '__metadata__': context.metadata},
                {'notes': False, 'knowledge': False},
                context.model,
            )

        tools = await selected()
        assert 'read_tool_result' in tools, 'raw reread is mandatory with budgeted native tools'
        state['settings'] = SimpleNamespace(tool_budget_enabled=False)
        assert 'read_tool_result' not in await selected()
        state['settings'] = SimpleNamespace(tool_budget_enabled=True)
        context.metadata['params']['function_calling'] = 'legacy'
        assert 'read_tool_result' not in await selected()
        context.metadata['params']['function_calling'] = 'native'
        del request.state.context_compaction
        context.metadata['tool_budget_enabled'] = True
        assert 'read_tool_result' not in await selected()
    finally:
        context.doCleanups()


@pytest.mark.asyncio
async def test_reader_binds_server_chat_branch_and_user_and_returns_exact_json(runtime, monkeypatch):
    from types import ModuleType

    service = ModuleType('open_webui.utils.tool_result_store')
    service.read_tool_result_page = AsyncMock(
        return_value={'result_id': 'r', 'text': 'exact <raw> & text', 'next_cursor': 'opaque', 'incomplete': True}
    )
    monkeypatch.setitem(sys.modules, 'open_webui.utils.tool_result_store', service)
    ns = runtime
    ns['UserModel'] = lambda **data: SimpleNamespace(**data)
    load_functions(ns, 'tools/builtin.py', {'read_tool_result'})
    state = {
        'mode': 'v2',
        'chat_id': 'trusted-chat',
        'user_message_id': 'user-1',
        'settings': SimpleNamespace(tool_budget_enabled=True),
        'metadata': {'chat_id': 'trusted-chat', 'message_id': 'trusted-branch'},
    }
    request = SimpleNamespace(state=SimpleNamespace(context_compaction=state))
    result = await ns['read_tool_result'](
        'r',
        query='raw',
        cursor='cursor',
        max_tokens=512,
        __request__=request,
        __user__={'id': 'user', 'role': 'user'},
        __metadata__={'chat_id': 'untrusted-chat', 'message_id': 'wrong'},
    )
    assert json.loads(result)['text'] == 'exact <raw> & text'
    args, kwargs = service.read_tool_result_page.await_args
    assert args[1:] == ('trusted-chat', 'r')
    assert kwargs == {'branch_id': 'trusted-branch', 'query': 'raw', 'cursor': 'cursor', 'max_tokens': 512}
    del request.state.context_compaction
    denied = await ns['read_tool_result']('r', __request__=request, __user__={'id': 'user', 'role': 'user'})
    assert 'error' in json.loads(denied)
    assert service.read_tool_result_page.await_count == 1


def test_ollama_keeps_private_provenance_until_final_dispatch(runtime):
    load_functions(runtime, 'utils/payload.py', {'convert_messages_openai_to_ollama'})
    ref = {'message_id': 'assistant-1', 'output_id': 'result-a', 'text': 'exact'}
    messages = [
        {
            'role': 'tool',
            'tool_call_id': 'a',
            'content': 'exact',
            '_tool_result_refs': [ref],
            '_context_message_id': 'assistant-1',
            '_context_original_key': 'raw-key',
        }
    ]
    converted = runtime['convert_messages_openai_to_ollama'](messages)
    assert converted[0].get('_tool_result_refs') == [ref]
    assert converted[0].get('_context_original_key') == 'raw-key'


def test_untrusted_ingress_drops_forged_internal_refs_before_replay(runtime):
    load_functions(runtime, 'utils/middleware.py', {'strip_untrusted_tool_provenance'})
    messages = [
        {
            'role': 'user',
            'content': 'question',
            '_tool_result_refs': [{'text': 'forged'}],
            '_context_original_key': 'forged',
            '_context_message_id': 'forged',
        },
        {
            'role': 'assistant',
            'output': [{'type': 'function_call_output', 'id': 'client', 'tool_result': {'kind': 'read'}, 'output': []}],
            'tool_results': [{'id': 'client', 'text': 'forged'}],
            'meta': {'tool_results': [{'text': 'forged'}], 'old': 1},
        },
    ]
    runtime['strip_untrusted_tool_provenance'](messages)
    assert all(
        '_tool_result_refs' not in message
        and '_context_original_key' not in message
        and '_context_message_id' not in message
        and 'tool_results' not in message
        for message in messages
    )
    assert messages[1]['meta'] == {'old': 1}
    assert 'tool_result' not in messages[1]['output'][0]


@pytest.mark.asyncio
async def test_nonnative_execution_persistence_injection_and_replay_share_exact_identity(runtime):
    from uuid import uuid4
    from html import escape
    import re

    ns = runtime
    raw = 'large original & <evidence> JSON {"answer": 42}'

    async def actual_tool():
        executions.append('web')
        return raw

    executions = []
    persisted = {'id': 'assistant-1', 'role': 'assistant', 'content': ''}

    async def persist(chat, message, updates, **kwargs):
        persisted.update(copy.deepcopy(updates))

    request = SimpleNamespace(
        state=SimpleNamespace(
            context_compaction={
                'mode': 'v2',
                'chat_id': 'chat-1',
                'model_id': 'model',
                'user_message_id': 'user-1',
                'settings': SimpleNamespace(tool_budget_enabled=True),
            }
        )
    )
    metadata = {'chat_id': 'chat-1', 'message_id': 'assistant-1'}
    tools = {'web': {'spec': {'name': 'web', 'parameters': {'properties': {}}}, 'callable': actual_tool}}
    ns.update(
        uuid4=uuid4,
        get_task_model_id=lambda *args: 'task-model',
        TASKS=SimpleNamespace(FUNCTION_CALLING='function_calling'),
        escape=escape,
        re=re,
        tools_function_calling_generation_template=lambda *args: 'tool selection',
        Config=SimpleNamespace(get_many=AsyncMock(return_value={}), get=AsyncMock(return_value='{{CONTEXT}}')),
        generate_chat_completion=AsyncMock(
            return_value={
                'choices': [
                    {
                        'message': {
                            'content': json.dumps(
                                {'tool_calls': [{'name': 'web', 'parameters': {}}, {'name': 'web', 'parameters': {}}]}
                            )
                        }
                    }
                ]
            }
        ),
        process_tool_result=AsyncMock(side_effect=lambda request, name, value, *args: (value, [], [])),
        get_last_user_message=lambda messages: 'question',
        get_content_from_message=lambda message: message.get('content', ''),
        RAG_SYSTEM_CONTEXT=False,
        rag_template=AsyncMock(side_effect=lambda template, context, user: context),
        filter_source_metadata=lambda meta: {},
        Chats=SimpleNamespace(
            get_message_by_id_and_message_id=AsyncMock(side_effect=lambda *args: persisted),
            upsert_message_to_chat_by_id_and_message_id=persist,
        ),
    )
    load_functions(
        ns, 'utils/misc.py', {'add_or_update_system_message', 'add_or_update_user_message', 'update_message_content'}
    )
    load_functions(
        ns,
        'utils/middleware.py',
        {
            'chat_completion_tools_handler',
            'persist_injected_tool_results',
            'get_source_context',
            'get_tool_source_context',
            'get_tool_citation_instructions',
            'add_tool_citation_instructions',
            'apply_source_context_to_messages',
        },
    )
    body = {'model': 'model', 'messages': [{'role': 'user', 'content': 'question'}]}
    body, flags = await ns['chat_completion_tools_handler'](
        request, body, {'__metadata__': metadata, '__event_call__': None, '__event_emitter__': None}, None, {}, tools
    )
    sources = flags['sources']
    assert len(sources) == len(executions) == 2
    await ns['persist_injected_tool_results'](request, metadata, sources)
    first = await ns['apply_source_context_to_messages'](request, body['messages'], sources, 'question')
    refs = [ref for message in first for ref in message.get('_tool_result_refs', [])]
    assert len(refs) == 2 and refs[0]['batch_id'] == refs[1]['batch_id']
    assert refs[0]['output_id'] != refs[1]['output_id']
    assert all(ref['text'] == raw for ref in refs)
    assert sum(message['content'].count(raw) for message in first) == 2
    second = await ns['apply_source_context_to_messages'](request, first, sources, 'question')
    assert sum(message['content'].count(raw) for message in second) == 2, 'citation refresh must not add raw copies'
    replay = ns['process_messages_with_output']([persisted], preserve_compaction_ids=True)
    assert [ref for message in replay for ref in message.get('_tool_result_refs', [])] == refs
    assert len(executions) == 2


def test_legacy_missing_output_ids_remain_resolvable(runtime):
    from open_webui.utils.tool_result_sources import source_text

    raw = [
        {'type': 'function_call', 'call_id': 'a', 'name': 'web', 'status': 'completed'},
        {'type': 'function_call_output', 'call_id': 'a', 'output': [{'type': 'input_text', 'text': 'exact'}]},
        {'type': 'open_webui:code_interpreter', 'output': {'stdout': 'exact code'}},
    ]
    messages = runtime['process_messages_with_output'](
        [{'id': 'assistant', 'role': 'assistant', 'output': raw}], preserve_compaction_ids=True
    )
    refs = [ref for message in messages for ref in message.get('_tool_result_refs', [])]
    assert len(refs) == 2
    for ref in refs:
        assert source_text({'output': raw}, ref['output_id']) == ref['text']


@pytest.mark.parametrize('stateful', [False, True])
@pytest.mark.asyncio
async def test_native_continuation_fills_all_result_ids_before_provider_send(runtime, stateful):
    import ast

    ns = runtime
    load_functions(
        ns, 'utils/misc.py', {'get_system_message', 'get_content_from_message', 'get_last_user_message_item'}
    )
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    followup = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.If)
            and isinstance(child.test, ast.BoolOp)
            and any(
                isinstance(value, ast.Name) and value.id == 'ENABLE_RESPONSES_API_STATEFUL'
                for value in child.test.values
            )
            for child in node.body
        )
    )
    dispatch_index = next(
        index
        for index, node in enumerate(followup.body)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Await)
        and isinstance(node.value.value, ast.Call)
        and isinstance(node.value.value.func, ast.Name)
        and node.value.value.func.id == 'generate_chat_completion'
    )
    wrapper = ast.parse('async def dispatch_followup():\n    pass\n')
    wrapper.body[0].body = (
        ast.parse("messages = list(form_data['messages'])").body + followup.body[: dispatch_index + 1]
    )
    exec(compile(ast.fix_missing_locations(wrapper), 'native_provenance_followup', 'exec'), ns)
    output = native_output()
    capture = AsyncMock()
    ns.update(
        output=output,
        full_output=lambda: output,
        response_usage={},
        new_tool_output_ids=[item['id'] for item in output if item.get('type') == 'function_call_output'],
        form_data={
            'previous_response_id': 'stale-previous',
            'messages': [
                {'role': 'system', 'content': 'rules'},
                *({'role': 'user', 'content': str(i)} for i in range(5)),
            ],
        },
        model_id='model',
        model={'info': {'meta': {}}},
        metadata={'chat_id': 'chat-1', 'message_id': 'assistant-1'},
        ENABLE_RESPONSES_API_STATEFUL=stateful,
        last_response_id='previous',
        get_reasoning_format=lambda model: None,
        filter_functions=[],
        generate_chat_completion=capture,
        user=SimpleNamespace(id='owner'),
        request=SimpleNamespace(
            state=SimpleNamespace(
                context_compaction={
                    'mode': 'v2',
                    'chat_id': 'chat-1',
                    'model_id': 'model',
                    'user_message_id': 'user-1',
                    'settings': SimpleNamespace(tool_budget_enabled=True),
                }
            )
        ),
        Chats=SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=AsyncMock()),
        convert_url_images_to_base64=AsyncMock(side_effect=lambda payload, **kwargs: payload),
    )
    await ns['dispatch_followup']()
    messages = capture.await_args.args[1]['messages']
    refs = [ref for message in messages for ref in message.get('_tool_result_refs', [])]
    assert refs and all(ref.get('message_id') == 'assistant-1' for ref in refs)
    assert sum(message.get('role') == 'user' for message in messages) >= 5
    assert 'previous_response_id' not in capture.await_args.args[1]
    assert ns['Chats'].upsert_message_to_chat_by_id_and_message_id.await_args.args[2]['output'] == output
    admission = ns['selection_store'].register_pending.await_args
    assert admission.args[:2] == ('chat-1', 'model')
    assert {ref['output_id'] for ref in admission.args[2]} == {'result-a', 'result-b'}


@pytest.mark.asyncio
async def test_auxiliary_reader_path_works_when_builtin_capability_is_disabled(runtime):
    import ast
    from test_chat_tool_features import ChatToolFeaturesTests

    context = ChatToolFeaturesTests()
    context.setUp()
    try:
        context.namespace['is_saved_chat_id'] = lambda value: value == 'chat-1'
        metadata = {'chat_id': 'chat-1', 'params': {'function_calling': 'native'}}
        request = SimpleNamespace(
            state=SimpleNamespace(
                context_compaction={
                    'mode': 'v2',
                    'chat_id': 'chat-1',
                    'model_id': 'model',
                    'user_message_id': 'user-1',
                    'settings': SimpleNamespace(tool_budget_enabled=True),
                }
            )
        )
        model = {'info': {'meta': {'capabilities': {'builtin_tools': False}}}}
        selected = await context.namespace['get_builtin_tools'](
            request, {'__user__': context.user, '__metadata__': metadata}, {}, model, auxiliary_only=True
        )
        assert set(selected) == {'read_tool_result'}
        context.config.get_many.assert_not_awaited()
        tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
        block = min(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.If)
                and any(
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == 'get_builtin_tools'
                    for child in ast.walk(node)
                )
            ),
            key=lambda node: len(list(ast.walk(node))),
        )
        wrapper = ast.parse('async def auxiliary_resolution():\n    pass\n')
        wrapper.body[0].body = [block]
        ns = {
            **context.namespace,
            'request': request,
            'metadata': metadata,
            'model': model,
            'features': {},
            'is_note_chat': False,
            'use_builtin_tools': False,
            'use_tool_result_reader': True,
            'extra_params': {'__user__': context.user, '__metadata__': metadata},
            'event_emitter': None,
            'view_skill_ids': [],
            'resolved_skill_ids': set(),
            'tools_dict': {'read_tool_result': {'type': 'external', 'spec': {}}},
        }
        exec(compile(ast.fix_missing_locations(wrapper), 'auxiliary_resolution', 'exec'), ns)
        await ns['auxiliary_resolution']()
        assert ns['tools_dict']['read_tool_result']['type'] == 'builtin'
    finally:
        context.doCleanups()


def test_code_span_excludes_identical_source_code_and_other_assistant_text(runtime):
    output = [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'hello before code'}]},
        {'type': 'open_webui:code_interpreter', 'id': 'code', 'code': "print('hello')", 'output': {'stdout': 'hello'}},
    ]
    messages = runtime['convert_output_to_messages'](output, include_tool_result_refs=True)
    message = messages[0]
    ref = message['_tool_result_refs'][0]
    assert ref.get('start') is not None, 'exact result spans protect code and surrounding instructions'
    assert message['content'][ref['start'] : ref['end']] == 'hello'
    projected = message['content'][: ref['start']] + '[summary]' + message['content'][ref['end'] :]
    assert "print('hello')" in projected and 'hello before code' in projected
    assert '<code_interpreter_output>\n[summary]\n</code_interpreter_output>' in projected


def test_identical_injected_results_have_disjoint_exact_spans(runtime):
    from open_webui.utils.tool_result_sources import injected_message

    sidecars = [
        {
            'id': f'result-{i}',
            'call_id': f'call-{i}',
            'batch_id': 'batch',
            'name': 'web',
            'kind': 'injected',
            'text': 'same text',
        }
        for i in range(2)
    ]
    message = injected_message(sidecars, 'assistant')
    refs = message['_tool_result_refs']
    assert all('start' in ref and 'end' in ref for ref in refs)
    assert refs[0]['end'] < refs[1]['start']
    for ref in refs:
        assert message['content'][ref['start'] : ref['end']] == 'same text'
    projected = message['content']
    for ref in reversed(refs):
        projected = projected[: ref['start']] + ref['output_id'] + projected[ref['end'] :]
    assert projected.count('result-0') == 2 and projected.count('result-1') == 2
    assert 'same text' not in projected


def test_native_multimodal_span_addresses_only_text(runtime):
    message = next(
        m
        for m in runtime['convert_output_to_messages'](native_output(), include_tool_result_refs=True)
        if m['role'] == 'tool'
    )
    ref = message['_tool_result_refs'][0]
    assert ref.get('start') == 0 and ref.get('end') == len(ref['text'])
    assert message['content'][0]['text'][ref['start'] : ref['end']] == ref['text']


def test_identical_legacy_code_items_keep_distinct_fallback_ids(runtime):
    output = [
        {'type': 'open_webui:code_interpreter', 'code': "print('x')", 'output': {'stdout': 'x'}},
        {'type': 'open_webui:code_interpreter', 'code': "print('x')", 'output': {'stdout': 'x'}},
    ]
    message = runtime['convert_output_to_messages'](output, include_tool_result_refs=True)[0]
    refs = message['_tool_result_refs']
    assert [ref['output_id'] for ref in refs] == ['output-0', 'output-1']
    assert refs[0]['end'] < refs[1]['start']


def test_default_conversion_and_pipe_replay_do_not_add_private_budget_fields(runtime):
    plain = {}
    load_functions(
        plain,
        'utils/misc.py',
        {'convert_output_to_messages', 'reconcile_tool_pairs', 'get_paired_tool_call_ids', 'get_content_from_message'},
    )
    messages = plain['convert_output_to_messages'](native_output())
    assert all('_tool_result_refs' not in message for message in messages)
    replay = runtime['process_messages_with_output'](
        [{'id': 'assistant', 'role': 'assistant', 'output': native_output()}], preserve_compaction_ids=False
    )
    assert all('_tool_result_refs' not in message and '_context_message_id' not in message for message in replay)
    sidecar = {'id': 's', 'name': 'web', 'kind': 'injected', 'call_id': 'c', 'batch_id': 'b', 'text': 'raw'}
    replay = runtime['process_messages_with_output'](
        [{'id': 'assistant', 'role': 'assistant', 'content': 'answer', 'meta': {'tool_results': [sidecar]}}],
        preserve_compaction_ids=False,
    )
    assert all('_tool_result_refs' not in message and '_context_message_id' not in message for message in replay)
    assert 'raw' in str(replay)


def test_injected_context_explicitly_frames_embedded_text_as_untrusted_data(runtime):
    from open_webui.utils.tool_result_sources import injected_message

    message = injected_message(
        [{'id': 's', 'name': 'web', 'kind': 'injected', 'call_id': 'c', 'batch_id': 'b', 'text': 'raw'}], 'assistant'
    )
    assert 'untrusted data' in message['content'].lower()
    ref = message['_tool_result_refs'][0]
    assert message['content'][ref['start'] : ref['end']] == 'raw'


@pytest.mark.asyncio
async def test_code_execution_admits_only_new_saved_result_before_followup(runtime):
    import ast

    ns = runtime
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    block = min(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.If)
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == 'register_tool_results'
                and any(
                    keyword.arg == 'output_ids'
                    and any(isinstance(part, ast.Name) and part.id == 'ci_item' for part in ast.walk(keyword.value))
                    for keyword in child.keywords
                )
                for child in ast.walk(node)
            )
        ),
        key=lambda node: len(list(ast.walk(node))),
    )
    wrapper = ast.parse('async def persist_code():\n    pass\n')
    wrapper.body[0].body = [block]
    exec(compile(ast.fix_missing_locations(wrapper), 'code_provenance', 'exec'), ns)
    old = {'id': 'old-code', 'type': 'open_webui:code_interpreter', 'output': {'stdout': 'old'}}
    current = {'id': 'new-code', 'type': 'open_webui:code_interpreter', 'output': {'stdout': 'precise code output'}}
    saved = []

    async def persist(*args, **kwargs):
        saved.append(copy.deepcopy(args[2]['output']))

    async def admit(chat_id, model_id, descriptors, **kwargs):
        assert saved == [[old, current]], 'raw code must be persisted before server admission'
        assert [r['output_id'] for r in descriptors] == ['new-code']
        assert descriptors[0]['text'] == 'precise code output'
        assert descriptors[0]['kind'] == 'code'
        return True

    ns['selection_store'].register_pending.side_effect = admit
    ns.update(
        request=SimpleNamespace(
            state=SimpleNamespace(
                context_compaction={
                    'mode': 'v2',
                    'chat_id': 'chat-1',
                    'user_message_id': 'u',
                    'model_id': 'model',
                    'settings': SimpleNamespace(tool_budget_enabled=True),
                }
            )
        ),
        metadata={'chat_id': 'chat-1', 'message_id': 'a'},
        model_id='model',
        form_data={'messages': []},
        new_form_data={'messages': []},
        ci_item=current,
        full_output=lambda: [old, current],
        Chats=SimpleNamespace(upsert_message_to_chat_by_id_and_message_id=persist),
    )
    await ns['persist_code']()
    ns['selection_store'].register_pending.assert_awaited_once()
