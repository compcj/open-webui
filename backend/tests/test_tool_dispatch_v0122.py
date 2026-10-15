"""Real dispatch/replay functions with synthetic provider data, without app imports."""

import ast
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from test_web_tool_citations import BACKEND, load_functions
from test_streaming_tool_call_dedup import runtime as stream_runtime
from test_image_tool_context import runtime as output_runtime


def dispatch_namespace():
    ns = {'JSONCodec': json}
    tree = ast.parse((BACKEND / 'utils/tool_search.py').read_text(encoding='utf-8'))
    nodes = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == 'resolve_tool_call')
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id in {'CALL_TOOL_NAME', 'SEARCH_TOOL_NAME'}
                for target in node.targets
            )
        )
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'tool_dispatch', 'exec'), ns)
    return ns


def wrapped(call_id='dispatch-1', params=None, name='write_record'):
    return {
        'id': call_id,
        'function': {
            'name': 'call_tool',
            'arguments': json.dumps({'tool_name': name, 'params': json.dumps(params or {})}, ensure_ascii=False),
        },
    }


def registry():
    return {
        'call_tool': {'type': 'builtin', 'tool_id': 'builtin:call_tool'},
        'write_record': {
            'type': 'external',
            'tool_id': 'records',
            'spec': {
                'parameters': {
                    'type': 'object',
                    'properties': {'record': {'type': 'integer'}},
                    'required': ['record'],
                    'additionalProperties': False,
                }
            },
        },
    }


def test_dispatch_keeps_verbatim_provider_arguments_and_re_resolving_is_idempotent():
    ns = dispatch_namespace()
    call = wrapped(params={'record': 1})
    original = copy.deepcopy(call['function'])
    assert ns['resolve_tool_call'](call, registry()) is None
    assert call['function'] == {'name': 'write_record', 'arguments': '{"record": 1}'}
    assert call['provider_function'] == original
    saved = copy.deepcopy(call)
    assert ns['resolve_tool_call'](call, registry()) is None
    assert call == saved


@pytest.mark.parametrize('kind,identity', [('external', 'builtin:call_tool'), ('builtin', 'external:call_tool')])
def test_external_homonym_cannot_unwrap_or_impersonate_builtin(kind, identity):
    ns = dispatch_namespace()
    tools = registry()
    tools['call_tool'] = {'type': kind, 'tool_id': identity}
    call = wrapped(params={'record': 1})
    before = copy.deepcopy(call)
    assert ns['resolve_tool_call'](call, tools) is None
    assert call == before


@pytest.mark.parametrize(
    'arguments',
    [
        'null',
        '[]',
        '{',
        '{}',
        '{"tool_name":"write_record","params":{}}',
        '{"tool_name":"write_record","params":"[]"}',
        '{"tool_name":"call_tool","params":"{}"}',
        '{"tool_name":"search_tools","params":"{}"}',
        '{"tool_name":"unknown","params":"{}"}',
        '{"tool_name":"write_record","params":"{}","extra":true}',
    ],
)
def test_invalid_dispatch_is_a_failed_result_without_target_or_execution(arguments):
    ns = dispatch_namespace()
    call = {'function': {'name': 'call_tool', 'arguments': arguments}}
    before = copy.deepcopy(call)
    assert ns['resolve_tool_call'](call, registry()).startswith('Error:')
    assert call == before


@pytest.mark.asyncio
@pytest.mark.parametrize('direct', [False, True])
async def test_dispatched_gemini_target_rejects_invalid_original_schema_before_approval(direct):
    from test_gemini_schema_compiler import execution_namespace

    ns = execution_namespace()
    ns.update(dispatch_namespace())
    tools = registry()
    tools['write_record'].update(direct=direct, callable=AsyncMock())
    callback = AsyncMock(return_value=True)
    metadata = {'tools': tools, 'params': {'tool_approval_mode': 'ask'}, '_tool_schema_compatibility': 'gemini'}
    call = wrapped(params={'record': '1'})
    _, result, *_ = await ns['execute_tool_call']({'model': 'strict'}, metadata, callback, call)
    assert result.startswith('Error:')
    callback.assert_not_awaited()
    tools['write_record']['callable'].assert_not_awaited()
    assert call['provider_function']['name'] == 'call_tool'


@pytest.mark.asyncio
async def test_stream_dispatch_approval_uses_target_and_replay_keeps_wrapper(stream_runtime):
    ns = stream_runtime.ns
    ns.update(dispatch_namespace())
    tools = registry()
    ns['metadata'] = {'tools': tools}
    call = wrapped(params={'record': 2})
    call['_gemini_schema_validation'] = True
    original = copy.deepcopy(call['function'])
    ns['tool_calls'] = [[call]]
    await ns['execute_queued_calls']()
    execution = ns['execute_tool_call'].call_args.args[-1]
    assert execution['function']['name'] == 'write_record'
    saved = next(item for item in ns['output'] if item['type'] == 'function_call')
    assert saved['name'] == 'write_record'
    assert saved['provider_function'] == original
    assert saved['tool_schema_compatibility'] == 'gemini'
    assert ns['stage_ask_user_tool_calls'].call_args.args[0][0]['function']['name'] == 'write_record'

    saved['status'] = 'completed'
    ns['output'].append(
        {
            'type': 'function_call_output',
            'call_id': call['id'],
            'output': [{'type': 'input_text', 'text': 'raw'}],
            'citation_context': 'frozen citation',
            'status': 'completed',
        }
    )
    load_functions(
        ns,
        'utils/misc.py',
        {
            'get_provider_function',
            'convert_output_to_messages',
            'reconcile_tool_pairs',
            'get_paired_tool_call_ids',
        },
    )
    before = copy.deepcopy(ns['output'])
    wire = ns['convert_output_to_messages'](ns['output'], raw=True)
    assert wire[0]['tool_calls'][0]['function'] == original
    assert 'frozen citation' in wire[1]['content']
    assert ns['output'] == before
    ns['tool_calls'] = [[wrapped(params={'record': 2})]]
    await ns['execute_queued_calls']()
    ns['execute_tool_call'].assert_awaited_once()


def test_responses_replay_restores_wrapper_and_strips_local_fields_without_mutation():
    ns = dispatch_namespace()
    load_functions(ns, 'utils/misc.py', {'get_provider_function'})
    load_functions(ns, 'routers/openai.py', {'_normalize_stored_item'})
    ns['RESPONSES_ALLOWED_FIELDS'] = {
        'function_call': {'type', 'call_id', 'name', 'arguments'},
        'function_call_output': {'type', 'call_id', 'output'},
    }
    original = wrapped(params={'record': 3})['function']
    item = {
        'type': 'function_call',
        'call_id': 'd3',
        'name': 'write_record',
        'arguments': '{"record":3}',
        'provider_function': original,
        'tool_schema_compatibility': 'gemini',
    }
    before = copy.deepcopy(item)
    wire = ns['_normalize_stored_item'](item)
    assert wire == {'type': 'function_call', 'call_id': 'd3', **original}
    assert item == before


@pytest.mark.parametrize(
    'marker',
    [
        {'type': 'open_webui:context_compaction'},
        {'type': 'open_webui:context_compaction', 'call_id': 'a', 'summary': None},
        {'type': 'open_webui:context_compaction', 'call_id': 'a', 'summary': '  '},
    ],
)
def test_incomplete_legacy_marker_replays_raw_without_crashing_or_losing_history(marker):
    from test_context_compaction_legacy import load

    messages = [
        {'role': 'user', 'content': 'old raw'},
        {
            'role': 'assistant',
            'output': [
                {'type': 'function_call', 'call_id': 'a', 'name': 'write_record', 'arguments': '{}'},
                marker,
            ],
        },
    ]
    before = copy.deepcopy(messages)
    ns = load({'_apply_latest_summary_checkpoint'})
    assert ns['_apply_latest_summary_checkpoint'](messages) == (messages, None)
    assert messages == before


@pytest.mark.asyncio
async def test_invalid_dispatch_result_reaches_stateful_provider_without_execution(output_runtime):
    ns = output_runtime.ns
    output = [
        {'type': 'function_call', 'call_id': 'invalid', 'name': 'call_tool', 'arguments': '{}', 'status': 'failed'},
        {
            'type': 'function_call_output',
            'id': 'invalid-result',
            'call_id': 'invalid',
            'status': 'failed',
            'output': [{'type': 'input_text', 'text': 'Error: call_tool requires tool_name and params.'}],
        },
    ]
    ns.update(
        output=output,
        results=[],
        new_tool_output_ids=['invalid-result'],
        form_data={'messages': [{'role': 'system', 'content': 'fixed rules'}]},
        metadata={},
        model_id='strict',
        model={},
        ENABLE_RESPONSES_API_STATEFUL=True,
        last_response_id='provider-response',
        get_reasoning_format=lambda model: None,
        request=SimpleNamespace(state=SimpleNamespace()),
        user=SimpleNamespace(id='owner'),
        filter_functions=[],
        generate_chat_completion=AsyncMock(),
        convert_url_images_to_base64=AsyncMock(side_effect=lambda data, **kwargs: data),
    )
    await ns['dispatch_followup']()
    payload = ns['generate_chat_completion'].await_args.args[1]
    assert payload['previous_response_id'] == 'provider-response'
    results = [item for item in payload['messages'] if item['role'] == 'tool']
    assert len(results) == 1 and results[0]['tool_call_id'] == 'invalid'
    assert results[0]['content'].startswith('Error:')


def test_legacy_tool_marker_reloads_exact_retained_group_without_mutating_saved_output():
    from test_context_compaction_legacy import load

    messages = [
        {'role': 'user', 'content': 'keep the latest task'},
        {
            'role': 'assistant',
            'output': [
                {'type': 'function_call', 'call_id': 'old'},
                {'type': 'function_call_output', 'call_id': 'old', 'output': 'old raw'},
                {'type': 'reasoning', 'summary': [{'text': 'retain reasoning'}]},
                {
                    'type': 'function_call',
                    'call_id': 'keep',
                    'provider_function': {'name': 'call_tool', 'arguments': '{}'},
                },
                {'type': 'function_call_output', 'call_id': 'keep', 'output': 'kept raw', 'citation_context': 'frozen'},
                {'type': 'open_webui:context_compaction', 'call_id': 'keep', 'summary': 'prior facts'},
            ],
        },
    ]
    before = copy.deepcopy(messages)
    retained, summary = load({'_apply_latest_summary_checkpoint'})['_apply_latest_summary_checkpoint'](messages)
    assert summary == 'prior facts' and retained[0] == messages[0]
    assert [item['type'] for item in retained[1]['output']] == ['reasoning', 'function_call', 'function_call_output']
    assert retained[1]['output'][-1]['citation_context'] == 'frozen'
    assert messages == before
