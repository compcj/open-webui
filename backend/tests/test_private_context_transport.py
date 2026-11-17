"""Automatic context must not impersonate a provider's thinking/tool response."""

import copy
import itertools
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_native_connections_integration import CapturedSend, sender
from test_web_tool_citations import load_functions
from open_webui.utils.responses_reasoning import remap_reasoning_effort_for_responses


@pytest.fixture
def private_context(request):
    ids = itertools.count()
    ns = {'output_id': lambda prefix: f'{prefix}-{next(ids)}'}
    load_functions(ns, 'utils/middleware.py', {'hidden_tool_result', 'get_reasoning_format'})
    load_functions(
        ns,
        'utils/misc.py',
        {'convert_output_to_messages', 'reconcile_tool_pairs', 'get_paired_tool_call_ids'},
    )
    output = [
        *ns['hidden_tool_result']('recall_memories', '  Synthetic memory\nexact bytes  '),
        *ns['hidden_tool_result']('view_skill', 'Synthetic skill instructions', arguments={'id': 'skill-1'}),
    ]
    return ns['convert_output_to_messages'](
        output, raw=True, reasoning_format=ns['get_reasoning_format'](getattr(request, 'param', {}))
    )


@pytest.fixture(autouse=True)
def isolated_system_merge(monkeypatch):
    # Execute the real normalization helper without loading env.py or deployment secrets.
    module = ModuleType('open_webui.utils.misc')
    load_functions(module.__dict__, 'utils/misc.py', {'merge_system_messages'})
    monkeypatch.setitem(sys.modules, module.__name__, module)


def form_with_context(context):
    return {
        'model': 'connection.native-test',
        'messages': [
            {'role': 'system', 'content': 'Stable model instructions'},
            {'role': 'user', 'content': 'First question'},
            *copy.deepcopy(context),
        ],
        'tools': [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {'type': 'object'}}}],
    }


async def capture(sender, form):
    request, ns, captured = sender
    with pytest.raises(CapturedSend):
        await ns['generate_chat_completion'](request, form, SimpleNamespace(role='admin'))
    return json.loads(captured['send']['data'])


@pytest.mark.asyncio
@pytest.mark.parametrize('replay', [True, False])
async def test_first_request_projects_automatic_context_before_budget_without_fake_reasoning(
    sender, private_context, replay
):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://api.deepseek.com', 'synthetic', {}))
    ns['Models'].get_model_by_id.return_value.meta.reasoning_content_replay = replay
    request.state.private_tool_context = copy.deepcopy(private_context)
    form = form_with_context(private_context)
    original = copy.deepcopy(form)

    wire = await capture(sender, form)

    assert not any(
        message.get('tool_calls') or message['role'] in {'assistant', 'tool'} for message in wire['messages']
    )
    assert 'reasoning_content' not in json.dumps(wire)
    assert [message['role'] for message in wire['messages']] == ['system', 'user']
    assert wire['messages'][1] == original['messages'][1]
    text = wire['messages'][0]['content']
    assert text.startswith(original['messages'][0]['content'] + '\n')
    for value in [
        '  Synthetic memory\nexact bytes  ',
        'Synthetic skill instructions',
        'recall_memories',
        'view_skill',
        'skill-1',
    ]:
        assert text.count(value) == 1
    assert captured['budget']['messages'] == wire['messages']
    assert wire['tools'] == original['tools']
    assert form == original
    assert request.state.private_tool_context == private_context


@pytest.mark.asyncio
async def test_real_tool_reasoning_and_frozen_prefix_survive_followup_and_approval(sender, private_context):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    request.state.private_tool_context = copy.deepcopy(private_context)
    first = form_with_context(private_context)
    first_wire = await capture(sender, first)
    actual = [
        {
            'role': 'assistant',
            'content': 'Checking',
            'reasoning_content': '  actual upstream reasoning\n',
            'tool_calls': [
                {'id': 'actual-call', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}
            ],
        },
        {'role': 'tool', 'tool_call_id': 'actual-call', 'content': 'Actual tool result'},
    ]
    followup = {**first, 'messages': [*first['messages'], *copy.deepcopy(actual)]}
    original = copy.deepcopy(followup)
    for _ in range(2):
        # Approval restoration uses the same frozen request-local snapshot.
        request.state.private_tool_context = copy.deepcopy(private_context)
        wire = await capture(sender, followup)
        assert wire['messages'] == [*first_wire['messages'], *actual]
        assert [call['id'] for message in wire['messages'] for call in message.get('tool_calls', [])] == ['actual-call']
        assert captured['budget']['messages'] == wire['messages']
    assert followup == original


@pytest.mark.asyncio
async def test_unregistered_calls_with_private_source_names_are_not_reclassified(sender, private_context):
    request, ns, _ = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    request.state.private_tool_context = []
    form = form_with_context(private_context)
    # Neither source names nor client metadata confer server provenance.
    form['metadata'] = {'private_tool_context': copy.deepcopy(private_context)}
    original_messages = copy.deepcopy(form['messages'])
    wire = await capture(sender, form)
    assert wire['messages'] == original_messages


@pytest.mark.asyncio
async def test_projection_preserves_filter_edits_and_cached_context_blocks(sender, private_context):
    request, ns, _ = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    request.state.private_tool_context = copy.deepcopy(private_context)
    form = form_with_context(private_context)
    block = {'type': 'text', 'text': 'Filtered context', 'cache_control': {'type': 'ephemeral'}}
    next(message for message in form['messages'] if message['role'] == 'tool')['content'] = [copy.deepcopy(block)]
    original = copy.deepcopy(form)
    wire = await capture(sender, form)
    contexts = [message for message in wire['messages'] if message['role'] == 'system']
    assert contexts
    assert block in contexts[0]['content']
    assert 'Synthetic memory' not in json.dumps(wire)
    assert form == original


@pytest.mark.asyncio
async def test_mixed_call_batch_keeps_real_call_and_result_adjacent(sender, private_context):
    request, ns, _ = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://gateway.example.test/v1', 'synthetic', {}))
    request.state.private_tool_context = copy.deepcopy(private_context)
    actual_call = {'id': 'actual', 'type': 'function', 'function': {'name': 'lookup', 'arguments': '{}'}}
    actual_message = {
        'role': 'assistant',
        'content': 'Actual provider text',
        'reasoning_content': 'Actual provider reasoning',
        'tool_calls': [actual_call],
    }
    actual_result = {'role': 'tool', 'tool_call_id': 'actual', 'content': 'Actual result'}
    form = form_with_context([])
    form['messages'].extend(
        [
            {**copy.deepcopy(actual_message), 'tool_calls': [private_context[0]['tool_calls'][0], actual_call]},
            copy.deepcopy(private_context[1]),
            actual_result,
        ]
    )
    original = copy.deepcopy(form)
    wire = await capture(sender, form)
    index = next(i for i, message in enumerate(wire['messages']) if message.get('tool_calls'))
    assert wire['messages'][index : index + 2] == [actual_message, actual_result]
    assert wire['messages'][0]['role'] == 'system'
    assert 'Synthetic memory' in wire['messages'][0]['content']
    assert form == original


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', ['responses', 'anthropic', 'gemini'])
async def test_other_protocols_keep_existing_private_context_representation(sender, private_context, protocol):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(
        return_value=('https://gateway.example.test/v1', 'synthetic', {'api_type': protocol})
    )
    ns['remap_reasoning_effort_for_responses'] = remap_reasoning_effort_for_responses
    load_functions(ns, 'routers/openai.py', {'convert_to_responses_payload'})
    request.state.private_tool_context = copy.deepcopy(private_context)
    form = form_with_context(private_context)
    original = copy.deepcopy(form)
    await capture(sender, form)
    assert captured['budget']['messages'] == original['messages']
    assert form == original


@pytest.mark.asyncio
@pytest.mark.parametrize('model_id,role', [('gpt-5', 'developer'), ('o1-mini', 'user')])
async def test_projected_context_obeys_existing_reasoning_model_role_conversion(
    sender, private_context, model_id, role
):
    request, ns, captured = sender
    ns['get_openai_connection'] = AsyncMock(return_value=('https://api.openai.com/v1', 'synthetic', {}))
    ns['is_openai_new_model'] = lambda model: True
    load_functions(ns, 'routers/openai.py', {'openai_reasoning_model_handler'})
    request.app.state.OPENAI_MODELS[model_id] = {'urlIdx': 0}
    request.state.private_tool_context = copy.deepcopy(private_context)
    form = {**form_with_context(private_context), 'model': model_id}
    wire = await capture(sender, form)
    assert [message['role'] for message in wire['messages']] == [role, 'user']
    assert 'Synthetic memory' in wire['messages'][0]['content']
    assert captured['budget']['messages'] == wire['messages']


@pytest.mark.asyncio
@pytest.mark.parametrize('private_context', [{'provider': 'llama.cpp'}], indirect=True)
async def test_llama_context_does_not_leave_empty_assistant_prefill(sender, private_context):
    request, ns, _ = sender
    ns['get_openai_connection'] = AsyncMock(
        return_value=('https://gateway.example.test/v1', 'synthetic', {'provider': 'llama.cpp'})
    )
    assert private_context[0]['reasoning_content'] == ''
    request.state.private_tool_context = copy.deepcopy(private_context)
    form = form_with_context(private_context)
    form['metadata'] = {'assistant_message_id': 'reply'}
    wire = await capture(sender, form)
    assert [message['role'] for message in wire['messages']] == ['system', 'user']
    assert 'continue_final_message' not in wire
    assert 'add_generation_prompt' not in wire
