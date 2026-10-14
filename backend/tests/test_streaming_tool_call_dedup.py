"""Exercise actual streaming extraction and pre-execution batches without app imports."""

import ast
import asyncio
import copy
import json
import hashlib
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from test_web_tool_citations import BACKEND, load_functions


def call(call_id, arguments='{}'):
    return {'id': call_id, 'function': {'name': 'write_record', 'arguments': arguments}}


def completed(call_id):
    return [
        {'type': 'function_call', 'call_id': call_id, 'name': 'write_record', 'status': 'completed'},
        {
            'type': 'function_call_output',
            'call_id': call_id,
            'status': 'completed',
            'output': [{'type': 'input_text', 'text': 'Frozen result'}],
            'citation_context': 'Frozen citation',
        },
    ]


@pytest.fixture
def runtime():
    ns = {
        'asyncio': asyncio,
        'JSONCodec': json,
        'json': json,
        'copy': copy,
        'hashlib': hashlib,
        'uuid4': uuid4,
        'ENABLE_CHAT_RESPONSE_STREAM_INPLACE_APPEND': False,
        'output_id': lambda prefix: prefix + '-new',
        'output': [],
        'prior_output': [],
        'response_tool_calls': [],
        'new_tool_round': False,
        'round_tool_call_ids': {},
        'tool_calls': [],
        'emit_response_completion_event': AsyncMock(),
        'save_to_chat': False,
        'metadata': {},
        'form_data': {},
        'event_caller': None,
        'execute_tool_call': AsyncMock(return_value=({}, {}, {}, '', False)),
        'stage_ask_user_tool_calls': Mock(return_value=(False, None)),
        'emit_output': AsyncMock(),
    }
    tree = load_functions(
        ns, 'utils/middleware.py', {'_split_tool_calls', '_deduplicate_tool_calls', 'append_to_text_field'}
    )

    def calls(node, name):
        return any(
            isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == name
            for child in ast.walk(node)
        )

    queue_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and (
            (
                isinstance(node.test, ast.Name)
                and node.test.id == 'response_tool_calls'
                and calls(node, '_split_tool_calls')
                and calls(node, 'emit_response_completion_event')
            )
            or (ast.unparse(node.test) == 'not response_tool_calls and output' and calls(node, '_split_tool_calls'))
        )
    ]
    assert len(queue_nodes) == 2
    wrapper = ast.parse('async def queue_stream_calls():\n    global response_tool_calls\n')
    wrapper.body[0].body.extend(copy.deepcopy(queue_nodes))
    exec(compile(ast.fix_missing_locations(wrapper), 'streaming_tool_call_queue', 'exec'), ns)
    loop = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.While) and calls(node, 'stage_ask_user_tool_calls') and calls(node, 'execute_tool_call')
    )
    end = next(
        index
        for index, node in enumerate(loop.body)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == 'update'
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == 'tool_results'
    )
    extraction = copy.deepcopy(loop)
    extraction.test = ast.Name(id='tool_calls', ctx=ast.Load())
    extraction.body = extraction.body[: end + 1]
    wrapper = ast.parse('async def execute_queued_calls():\n    tool_call_iterations = 0\n')
    wrapper.body[0].body.append(extraction)
    exec(compile(ast.fix_missing_locations(wrapper), 'streaming_tool_call_execution', 'exec'), ns)
    delta_block = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == 'delta_tool_calls'
        and calls(node, 'save_current_response_stream')
    )
    wrapper = ast.parse('async def ingest_delta(delta_tool_calls):\n    pass')
    wrapper.body[0].body = [copy.deepcopy(delta_block)]
    exec(compile(ast.fix_missing_locations(wrapper), 'live_chat_tool_delta_ingestion', 'exec'), ns)
    ns['full_output'] = lambda: ns['prior_output'] + ns['output']
    ns['save_current_response_stream'] = AsyncMock()
    return SimpleNamespace(ns=ns)


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['chat', 'responses'])
async def test_same_batch_duplicate_id_executes_once(runtime, api):
    ns = runtime.ns
    if api == 'chat':
        ns['response_tool_calls'] = [call('A'), call('A', '{"different":true}')]
    else:
        ns['output'] = [
            {'type': 'function_call', 'call_id': 'A', 'name': 'write_record', 'arguments': '{}'},
            {'type': 'function_call', 'call_id': 'A', 'name': 'write_record', 'arguments': '{"different":true}'},
        ]
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()
    assert ns['execute_tool_call'].call_args.args[-1]['function']['arguments'] == '{}'


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['chat', 'responses'])
@pytest.mark.parametrize('history', ['current-round', 'approval-resume'])
async def test_handled_id_is_filtered_before_approval_or_side_effects(runtime, api, history):
    ns = runtime.ns
    frozen = completed('A')
    ns['prior_output' if history == 'approval-resume' else 'output'] = copy.deepcopy(frozen)
    if api == 'chat':
        ns['response_tool_calls'] = [call('A')]
    else:
        ns['output'].append({'type': 'function_call', 'call_id': 'A', 'name': 'write_record', 'arguments': '{}'})
    previous = copy.deepcopy(ns['prior_output'])
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_not_awaited()
    ns['stage_ask_user_tool_calls'].assert_not_called()
    assert ns['prior_output'] == previous
    assert ns['output'][: len(frozen)] == frozen if history == 'current-round' else ns['prior_output'] == frozen


@pytest.mark.asyncio
async def test_later_queued_round_rechecks_newly_completed_ids(runtime):
    ns = runtime.ns
    ns['tool_calls'] = [[call('A')]]
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()
    ns['output'].extend(completed('A'))
    ns['stage_ask_user_tool_calls'].reset_mock()
    ns['tool_calls'] = [[call('A')]]
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()
    ns['stage_ask_user_tool_calls'].assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['chat', 'responses'])
@pytest.mark.parametrize('history', ['current-round', 'approval-resume'])
async def test_concatenated_arguments_have_stable_distinct_ids_and_never_repeat_effects(runtime, api, history):
    ns = runtime.ns
    arguments = '{"record":1}{"record":2}'
    if api == 'chat':
        ns['output'] = [
            {
                'type': 'function_call',
                'call_id': 'provider-A',
                'name': 'write_record',
                'arguments': arguments,
                'status': 'in_progress',
            }
        ]
        ns['response_tool_calls'] = [call('provider-A', arguments), call('provider-A', arguments)]
    else:
        ns['output'] = [
            {
                'type': 'function_call',
                'call_id': 'provider-A',
                'name': 'write_record',
                'arguments': arguments,
                'status': 'in_progress',
            }
        ]
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    executed = [item.args[-1] for item in ns['execute_tool_call'].call_args_list]
    assert len(executed) == 2 and len({item['id'] for item in executed}) == 2
    assert [item['function']['arguments'] for item in executed] == ['{"record":1}', '{"record":2}']
    for item in ns['output']:
        if item['type'] == 'function_call':
            item['status'] = 'completed'
    ns['output'].extend(completed(item['id'])[1] for item in executed)
    load_functions(
        ns, 'utils/misc.py', {'get_paired_tool_call_ids', 'reconcile_tool_pairs', 'convert_output_to_messages'}
    )
    wire = ns['convert_output_to_messages'](ns['output'], raw=True)
    assert {entry['id'] for message in wire for entry in message.get('tool_calls', [])} == {
        item['id'] for item in executed
    }
    assert {message['tool_call_id'] for message in wire if message['role'] == 'tool'} == {
        item['id'] for item in executed
    }
    frozen = copy.deepcopy(ns['output'])
    if history == 'approval-resume':
        ns['prior_output'], ns['output'] = ns['output'], []
    ns['stage_ask_user_tool_calls'].reset_mock()
    if api == 'chat':
        ns['response_tool_calls'] = [call('provider-A', '{"record":99}{"record":100}{"record":101}')]
    else:
        ns['response_tool_calls'] = []
        ns['output'].append(
            {
                'type': 'function_call',
                'call_id': 'provider-A',
                'name': 'write_record',
                'arguments': '{"record":99}{"record":100}{"record":101}',
                'status': 'in_progress',
            }
        )
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    assert ns['execute_tool_call'].await_count == 2
    ns['stage_ask_user_tool_calls'].assert_not_called()
    if history == 'approval-resume':
        assert ns['prior_output'] == frozen
    else:
        assert ns['output'][: len(frozen)] == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize('history', ['current-round', 'approval-resume'])
async def test_replayed_chat_delta_keeps_completed_execution_ids(runtime, history):
    ns = runtime.ns
    frozen = completed('provider-A')
    ns['prior_output' if history == 'approval-resume' else 'output'] = copy.deepcopy(frozen)
    await ns['ingest_delta']([{'index': 0, **call('provider-A', '{"changed":true}')}])
    assert ns['response_tool_calls'][0]['id'] == 'provider-A'
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_not_awaited()
    ns['emit_response_completion_event'].assert_not_awaited()
    assert ns['prior_output' if history == 'approval-resume' else 'output'] == frozen


@pytest.mark.asyncio
async def test_actual_chat_delta_ingestion_keeps_missing_id_fallback_and_new_calls(runtime):
    ns = runtime.ns
    await ns['ingest_delta']([{'index': 0, 'function': {'name': 'write_record', 'arguments': '{}'}}])
    assert ns['response_tool_calls'][0]['id'] == 'fc-new'
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_split_completion_blocks_provider_replay_but_resumes_pending_child(runtime):
    ns = runtime.ns
    original = call('provider-A', '{"record":1}{"record":2}')
    children = ns['_split_tool_calls']([copy.deepcopy(original)])
    ns['prior_output'] = [
        {
            'type': 'function_call',
            'call_id': 'provider-A',
            'name': 'write_record',
            'arguments': original['function']['arguments'],
            'status': 'completed',
        },
        completed(children[0]['id'])[1],
        {
            'type': 'function_call',
            'call_id': children[1]['id'],
            'name': 'write_record',
            'arguments': children[1]['function']['arguments'],
            'status': 'pending',
        },
    ]
    frozen = copy.deepcopy(ns['prior_output'])
    ns['response_tool_calls'] = [call('provider-A', '{"record":99}{"record":100}{"record":101}')]
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_not_awaited()
    ns['stage_ask_user_tool_calls'].assert_not_called()
    ns['tool_calls'] = [children]
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()
    assert ns['execute_tool_call'].call_args.args[-1] == children[1]
    assert ns['prior_output'] == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize('partial', [False, True])
async def test_live_concatenated_provider_replay_preserves_saved_arguments(runtime, partial):
    ns = runtime.ns
    original = call('provider-A', '{"record":1}{"record":2}')
    children = ns['_split_tool_calls']([copy.deepcopy(original)])
    ns['prior_output'] = [
        {
            'type': 'function_call',
            'call_id': 'provider-A',
            'name': 'write_record',
            'arguments': original['function']['arguments'],
            'status': 'completed',
        },
        *[completed(child['id'])[1] for child in children[: 1 if partial else 2]],
    ]
    frozen = copy.deepcopy(ns['prior_output'])
    await ns['ingest_delta']([{'index': 0, **call('provider-A', '{"record":99}{"record":100}{"record":101}')}])
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_not_awaited()
    ns['stage_ask_user_tool_calls'].assert_not_called()
    ns['emit_response_completion_event'].assert_not_awaited()
    assert ns['prior_output'] == frozen and ns['output'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('inplace_append', [False, True])
async def test_live_fragmented_new_provider_call_keeps_two_intended_split_effects(runtime, inplace_append):
    ns = runtime.ns
    ns['ENABLE_CHAT_RESPONSE_STREAM_INPLACE_APPEND'] = inplace_append
    await ns['ingest_delta']([{'index': 0, **call('provider-A', '{"record":')}])
    await ns['ingest_delta']([{'index': 0, 'function': {'arguments': '1}{"record":2}'}}])
    assert ns['response_tool_calls'][0]['function']['arguments'] == '{"record":1}{"record":2}'
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    executed = [entry.args[-1] for entry in ns['execute_tool_call'].call_args_list]
    assert [entry['function']['arguments'] for entry in executed] == ['{"record":1}', '{"record":2}']
    assert len({entry['id'] for entry in executed}) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['chat', 'responses'])
@pytest.mark.parametrize('mode', ['default', 'gemini'])
async def test_tool_round_freezes_its_actual_model_validation_before_execution(runtime, api, mode):
    ns = runtime.ns
    ns['metadata'] = {'_tool_schema_validation': {'initial': 'default', 'later': mode}}
    ns['form_data'] = {'model': 'later'}
    ns['output'] = [
        {
            'type': 'function_call',
            'call_id': 'A',
            'name': 'write_record',
            'arguments': '{"record":1}{"record":2}',
            # A provider-supplied marker must not opt into a different profile.
            'tool_schema_compatibility': 'gemini',
        }
    ]
    if api == 'chat':
        ns['response_tool_calls'] = [{**call('A', '{"record":1}{"record":2}'), '_gemini_schema_validation': True}]
    await ns['queue_stream_calls']()
    assert (ns['output'][0].get('tool_schema_compatibility') == 'gemini') == (mode == 'gemini')

    # Execution closes over the initial request even when a continuation filter
    # selected another model. Every split child must retain the later contract.
    ns['form_data'] = {'model': 'initial'}
    await ns['execute_queued_calls']()
    executed = [entry.args[-1] for entry in ns['execute_tool_call'].call_args_list]
    assert len(executed) == 2
    assert all((entry.get('_gemini_schema_validation') is True) == (mode == 'gemini') for entry in executed)
    children = [entry for entry in ns['output'] if entry.get('call_id') in {call['id'] for call in executed}]
    assert len(children) == 2
    assert all((entry.get('tool_schema_compatibility') == 'gemini') == (mode == 'gemini') for entry in children)
