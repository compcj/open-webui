"""New provider rounds must not be mistaken for saved tool-call replay."""

import ast
import copy
from uuid import uuid4

import pytest

from test_streaming_tool_call_dedup import call, completed, runtime
from test_web_tool_citations import BACKEND, load_functions


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments', ['{}', '{"changed":true}', '{"record":1}{"record":2}'])
@pytest.mark.parametrize('history', ['output', 'prior_output'])
async def test_fresh_round_executes_reused_provider_id_without_mutating_history(runtime, arguments, history):
    ns = runtime.ns
    frozen = completed('call_0')
    ns[history] = copy.deepcopy(frozen)
    ns['new_tool_round'] = True
    ns['round_tool_call_ids'] = {}
    await ns['ingest_delta']([{'index': 0, **call('call_0', arguments)}])
    new_id = ns['response_tool_calls'][0]['id']
    assert new_id != 'call_0'
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    expected_count = 2 if arguments == '{"record":1}{"record":2}' else 1
    assert ns['execute_tool_call'].await_count == expected_count
    assert ns[history][: len(frozen)] == frozen
    assert all(entry.args[-1]['id'] != 'call_0' for entry in ns['execute_tool_call'].call_args_list)


@pytest.mark.asyncio
async def test_fresh_round_repeated_id_and_fragmented_arguments_share_one_remapping(runtime):
    ns = runtime.ns
    ns['prior_output'] = completed('call_0')
    ns['new_tool_round'] = True
    ns['round_tool_call_ids'] = {}
    await ns['ingest_delta']([{'index': 0, **call('call_0', '{"record":')}])
    new_id = ns['response_tool_calls'][0]['id']
    assert new_id != 'call_0'
    await ns['ingest_delta']([{'index': 0, 'id': 'call_0', 'function': {'arguments': '2}'}}])
    await ns['ingest_delta']([{'index': 1, **call('call_0', '{"record":3}')}])
    assert [item['id'] for item in ns['response_tool_calls']] == [new_id, new_id]
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()
    executed = ns['execute_tool_call'].call_args.args[-1]
    assert executed['id'] == new_id
    assert executed['function']['arguments'] == '{"record":2}'


@pytest.mark.asyncio
async def test_three_fresh_rounds_pair_unique_results_and_saved_resume_does_not_repeat(runtime):
    ns = runtime.ns
    ns['output_id'] = lambda prefix: prefix + '-' + uuid4().hex
    frozen = completed('call_0')
    ns['prior_output'] = copy.deepcopy(frozen)
    ids = ['call_0']
    for _ in range(3):
        ns['new_tool_round'] = True
        ns['round_tool_call_ids'] = {}
        ns['response_tool_calls'] = []
        await ns['ingest_delta']([{'index': 0, **call('call_0')}])
        ids.append(ns['response_tool_calls'][0]['id'])
        assert len(ids) == len(set(ids))
        await ns['queue_stream_calls']()
        await ns['execute_queued_calls']()
        ns['output'].append(completed(ids[-1])[1])
        ns['prior_output'].extend(ns['output'])
        ns['output'] = []
    assert ns['execute_tool_call'].await_count == 3
    assert ns['prior_output'][: len(frozen)] == frozen
    load_functions(
        ns, 'utils/misc.py', {'get_paired_tool_call_ids', 'reconcile_tool_pairs', 'convert_output_to_messages'}
    )
    wire = ns['convert_output_to_messages'](ns['prior_output'], raw=True)
    assert [tc['id'] for message in wire for tc in message.get('tool_calls', [])] == ids
    assert [message['tool_call_id'] for message in wire if message['role'] == 'tool'] == ids

    saved = copy.deepcopy(ns['prior_output'])
    ns['new_tool_round'] = False
    ns['round_tool_call_ids'] = {}
    ns['response_tool_calls'] = []
    await ns['ingest_delta']([{'index': 0, **call(ids[-1])}])
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    assert ns['execute_tool_call'].await_count == 3
    assert ns['prior_output'] == saved


@pytest.mark.asyncio
@pytest.mark.parametrize('resume', ['tools', 'model', None])
async def test_first_provider_response_after_approval_or_continue_is_a_new_round(runtime, resume):
    ns = runtime.ns
    frozen = completed('call_0')
    ns['prior_output'] = copy.deepcopy(frozen)
    ns['continuing'] = True
    ns['metadata'] = {'assistant_message_id': 'reply', 'tool_approval_resume': resume}
    ns['response'] = object()

    async def provider_stream(response, form_data, *, new_tool_round=False):
        ns['new_tool_round'] = new_tool_round
        await ns['ingest_delta']([{'index': 0, **call('call_0')}])

    ns['stream_body_handler'] = provider_stream
    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    initial_dispatch = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'stream_body_handler'
        and isinstance(node.value.args[0], ast.Name)
        and node.value.args[0].id == 'response'
    )
    wrapper = ast.parse('async def dispatch_initial_stream():\n    pass')
    wrapper.body[0].body = [ast.Expr(value=copy.deepcopy(initial_dispatch))]
    exec(compile(ast.fix_missing_locations(wrapper), 'initial_provider_stream_dispatch', 'exec'), ns)
    await ns['dispatch_initial_stream']()
    await ns['queue_stream_calls']()
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()
    assert ns['execute_tool_call'].call_args.args[-1]['id'] != 'call_0'
    assert ns['prior_output'] == frozen
