"""Native wire budgets and signed multi-round stream persistence, without app startup."""

import copy
import importlib
import json
import time
from uuid import uuid4

import pytest

from test_web_tool_citations import load_functions
from test_streaming_error_recovery import runtime as stream_runtime


@pytest.fixture
def v2():
    return importlib.import_module('open_webui.utils.context_compaction_v2')


def payload(kind='anthropic'):
    from open_webui.utils.native_providers import make_binding

    base = 'https://gateway.example.test/v1'
    return {
        'model': 'synthetic-model',
        'messages': [{'role': 'user', 'content': 'hello'}],
        '_native_transport': {
            'api_type': kind,
            'base_url': base,
            'binding': make_binding(kind, base, 'synthetic-model'),
        },
    }


@pytest.mark.parametrize('kind', ['anthropic', 'gemini'])
def test_budget_measures_serialized_native_wire(v2, kind):
    from open_webui.utils.native_providers import build_request

    data = payload(kind)
    marker = data['_native_transport']
    _, wire = build_request(kind, marker['base_url'], data, marker['binding'])
    assert v2.estimate_payload_tokens(data) == 3 + v2.estimate_text(wire)
    assert '_native_transport' not in wire
    assert data['messages'][0]['content'] == 'hello'
    if kind == 'anthropic':
        assert wire['max_tokens'] == 4096


@pytest.mark.parametrize('kind', ['anthropic', 'gemini'])
def test_native_images_use_fixed_allowance_without_tokenizing_base64(v2, kind):
    data = payload(kind)
    data['messages'][0]['content'] = [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'A' * 100000}}
    ]
    original = v2.estimate_text

    def checked(value):
        assert 'A' * 1000 not in json.dumps(value)
        return original(value)

    from unittest.mock import patch

    with patch.object(v2, 'estimate_text', side_effect=checked):
        cost = v2.estimate_payload_tokens(data)
    assert 4096 < cost < 5000


def test_native_fingerprint_includes_binding_and_native_parameters(v2):
    data = payload()
    before = v2.request_fingerprint(data)
    data['thinking'] = {'type': 'adaptive'}
    assert v2.request_fingerprint(data) != before
    before = v2.request_fingerprint(data)
    data['_native_transport']['binding']['url_hash'] = 'changed-connection'
    assert v2.request_fingerprint(data) != before


def test_legacy_fingerprint_unchanged_by_native_only_parameters(v2):
    data = payload()
    del data['_native_transport']
    before = v2.request_fingerprint(data)
    data['thinking'] = {'type': 'adaptive'}
    assert v2.request_fingerprint(data) == before


def test_streamed_native_envelopes_stay_at_each_round_and_roundtrip():
    ns = {'time': time, 'copy': copy, 'output_id': lambda prefix: prefix + uuid4().hex}
    load_functions(ns, 'utils/middleware.py', {'append_native_streamed_reasoning'})
    load_functions(
        ns, 'utils/misc.py', {'convert_output_to_messages', 'get_paired_tool_call_ids', 'reconcile_tool_pairs'}
    )
    from open_webui.utils.native_providers import normalize_response, build_request

    data = payload()
    marker = data['_native_transport']
    output = []
    details = []
    for index in range(2):
        output.append({'type': 'message', 'content': [{'type': 'output_text', 'text': f'round {index}'}]})
        native = normalize_response(
            'anthropic',
            {
                'content': [
                    {'type': 'thinking', 'thinking': '', 'signature': f'signed-{index}'},
                    {'type': 'text', 'text': f'round {index}'},
                    {'type': 'tool_use', 'id': f'call-{index}', 'name': 'example', 'input': {}},
                ]
            },
            data['model'],
            marker['binding'],
        )['choices'][0]['message']
        detail = native['reasoning_details'][0]
        details.append(copy.deepcopy(detail))
        output.append(
            {
                'type': 'function_call',
                'call_id': f'call-{index}',
                'status': 'completed',
                'name': 'example',
                'arguments': '{}',
            }
        )
        item = ns['append_native_streamed_reasoning'](output, [detail])
        assert output[-1] is item
        assert item['status'] == 'completed'
        assert item['content'] == []
        output.append(
            {
                'type': 'function_call_output',
                'call_id': f'call-{index}',
                'output': [{'type': 'input_text', 'text': 'result'}],
            }
        )
    wire = ns['convert_output_to_messages'](output, raw=True)
    assistants = [message for message in wire if message['role'] == 'assistant']
    assert [message['reasoning_details'] for message in assistants] == [[details[0]], [details[1]]]
    assert [message['content'] for message in assistants] == ['round 0', 'round 1']
    assert len({item['id'] for item in output if item['type'] == 'reasoning'}) == 2
    data['messages'].extend(wire)
    _, replay = build_request('anthropic', marker['base_url'], data, marker['binding'])
    replayed = [message for message in replay['messages'] if message['role'] == 'assistant']
    assert [message['content'][0]['signature'] for message in replayed] == ['signed-0', 'signed-1']


def test_native_signed_replay_counts_opaque_signatures(v2):
    from open_webui.utils.native_providers import normalize_response

    data = payload()
    binding = data['_native_transport']['binding']

    def signed(signature):
        return normalize_response(
            'anthropic',
            {
                'content': [
                    {'type': 'thinking', 'thinking': '', 'signature': signature},
                    {'type': 'text', 'text': ' exact text '},
                ]
            },
            data['model'],
            binding,
        )['choices'][0]['message']

    data['messages'].append(signed('small'))
    small = v2.estimate_payload_tokens(data)
    old_fingerprint = v2.request_fingerprint(data)
    data['messages'][-1] = signed('opaque-long-signature-' * 500)
    assert v2.estimate_payload_tokens(data) > small + 1000
    assert v2.request_fingerprint(data) != old_fingerprint


@pytest.mark.asyncio
async def test_native_initial_stream_failure_emits_error_without_dispatch():
    import ast
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from test_web_tool_citations import BACKEND

    tree = ast.parse((BACKEND / 'utils/middleware.py').read_text(encoding='utf-8'))
    boundary = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        and any(
            isinstance(child, ast.Assign)
            and isinstance(child.value, ast.Await)
            and isinstance(child.value.value, ast.Call)
            and isinstance(child.value.value.func, ast.Name)
            and child.value.value.func.id == 'stream_body_handler'
            for child in node.body
        )
    )
    fn = ast.AsyncFunctionDef(
        name='run',
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[
            boundary,
            ast.Expr(
                value=ast.Await(value=ast.Call(func=ast.Name(id='execute_tools', ctx=ast.Load()), args=[], keywords=[]))
            ),
        ],
        decorator_list=[],
    )
    ns = {
        'response': SimpleNamespace(background=None),
        'form_data': {},
        'continuing': False,
        'stream_body_handler': AsyncMock(side_effect=ValueError('Native stream ended before signature')),
        'emit_message_error': AsyncMock(),
        'get_message_error_content': str,
        'execute_tools': AsyncMock(),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<stream-boundary>', 'exec'), ns)
    await ns['run']()
    ns['emit_message_error'].assert_awaited_once_with('Native stream ended before signature')
    ns['execute_tools'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['blocked', 'thinking', 'hidden', 'legacy-empty', 'error'])
async def test_nonstream_native_terminal_choices_finalize_save_usage_and_details(case):
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from open_webui.utils.native_providers import normalize_response

    data = payload('gemini' if case == 'blocked' else 'anthropic')
    marker = data['_native_transport']
    if case == 'blocked':
        raw = {'promptFeedback': {'blockReason': 'SAFETY'}, 'usageMetadata': {'promptTokenCount': 12}}
    else:
        block = (
            {'type': 'thinking', 'thinking': 'thought', 'signature': 'signed'}
            if case == 'thinking'
            else {'type': 'redacted_thinking', 'data': 'opaque'}
        )
        raw = {'content': [block], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 12, 'output_tokens': 3}}
    normalized = normalize_response(marker['api_type'], raw, data['model'], marker['binding'])
    if case == 'legacy-empty':
        normalized = {'choices': [{'message': {'content': ''}}]}
    elif case == 'error':
        normalized = {'error': {'detail': 'synthetic provider failure'}}
    emitter = AsyncMock()
    chats = SimpleNamespace(
        upsert_message_to_chat_by_id_and_message_id=AsyncMock(), get_chat_title_by_id=AsyncMock(return_value='')
    )
    ctx = {
        'request': object(),
        'user': object(),
        'metadata': {'chat_id': 'saved', 'message_id': 'reply'},
        'events': [],
        'event_emitter': emitter,
    }
    ns = {
        'get_response_data': lambda value: (value, value),
        'is_saved_chat_id': lambda value: True,
        'Chats': chats,
        'get_reasoning_details': lambda value: value.get('reasoning_details'),
        'output_id': lambda prefix: prefix + uuid4().hex,
        'normalize_usage': lambda value: value,
        'publish_chat_finished_event': AsyncMock(),
        'outlet_filter_handler': AsyncMock(),
        'background_tasks_handler': AsyncMock(),
        'build_response_object': lambda response, data: data,
        'merge_events_into_response': lambda data, events: data,
        'publish_chat_failed_event': AsyncMock(),
        'log': logging.getLogger(__name__),
    }
    ns['copy'] = copy
    load_functions(
        ns, 'utils/middleware.py', {'non_streaming_chat_response_handler', 'append_native_streamed_reasoning'}
    )
    result = await ns['non_streaming_chat_response_handler'](normalized, ctx)
    assert result == normalized
    ns['publish_chat_failed_event'].assert_not_awaited()
    if case == 'legacy-empty':
        chats.upsert_message_to_chat_by_id_and_message_id.assert_not_awaited()
        assert 'assistant_message' not in ctx
    elif case == 'error':
        assert chats.upsert_message_to_chat_by_id_and_message_id.call_args.args[2] == {
            'done': True,
            'error': {'content': 'synthetic provider failure'},
        }
        ns['publish_chat_finished_event'].assert_not_awaited()
    else:
        saved = chats.upsert_message_to_chat_by_id_and_message_id.call_args.args[2]
        assert saved['done'] is True
        assert saved['usage']['prompt_tokens'] == 12
        assert any(call.args[0]['data'].get('done') for call in emitter.call_args_list)
        ns['publish_chat_finished_event'].assert_awaited_once()
        if case != 'blocked':
            assert saved['output'][-1]['reasoning_details'] == normalized['choices'][0]['message']['reasoning_details']


@pytest.mark.asyncio
async def test_native_envelope_runs_through_complete_stream_handler(stream_runtime):
    from open_webui.utils.native_providers import normalize_response
    from test_streaming_error_recovery import chat_chunk

    ns = stream_runtime.ns
    load_functions(ns, 'utils/middleware.py', {'append_native_streamed_reasoning'})
    data = payload()
    detail = normalize_response(
        'anthropic',
        {
            'content': [
                {'type': 'thinking', 'thinking': '', 'signature': 'opaque-signed'},
                {'type': 'text', 'text': ' exact answer '},
            ]
        },
        data['model'],
        data['_native_transport']['binding'],
    )['choices'][0]['message']['reasoning_details']
    await stream_runtime.run(
        [
            chat_chunk(' exact answer '),
            {'choices': [{'delta': {'reasoning_details': detail}, 'finish_reason': 'stop'}]},
        ]
    )
    assert not any(event['data'].get('error') for event in stream_runtime.events)
    assert stream_runtime.saved['done'] is True
    output = stream_runtime.saved['output']
    assert output[-1]['reasoning_details'] == detail
    assert ns['get_output_text'](output) == ' exact answer '
    ns['execute_tool_call'].assert_not_awaited()
    ns['publish_chat_finished_event'].assert_awaited_once()
    previous = copy.deepcopy(stream_runtime.saved)
    second = normalize_response(
        'anthropic',
        {
            'content': [
                {'type': 'thinking', 'thinking': '', 'signature': 'second-signature'},
                {'type': 'text', 'text': ' continuation '},
            ]
        },
        data['model'],
        data['_native_transport']['binding'],
    )['choices'][0]['message']['reasoning_details']
    await stream_runtime.run(
        [
            chat_chunk(' continuation '),
            {'choices': [{'delta': {'reasoning_details': second}, 'finish_reason': 'stop'}]},
        ],
        previous=previous,
    )
    messages = ns['convert_output_to_messages'](stream_runtime.saved['output'], raw=True)
    assert [message['content'] for message in messages] == [' exact answer ', ' continuation ']
    from open_webui.utils.native_providers import build_request

    data['messages'].extend(messages)
    marker = data['_native_transport']
    _, wire = build_request('anthropic', marker['base_url'], data, marker['binding'])
    assert [
        part['signature']
        for message in wire['messages']
        for part in message['content']
        if part.get('type') == 'thinking'
    ] == ['opaque-signed', 'second-signature']


@pytest.mark.parametrize('thinking', [False, True])
def test_signed_continue_boundaries_roundtrip_without_merging(thinking):
    from open_webui.utils.native_providers import normalize_response, build_request

    ns = {'copy': copy, 'output_id': lambda prefix: prefix + uuid4().hex}
    load_functions(ns, 'utils/middleware.py', {'append_native_streamed_reasoning'})
    load_functions(
        ns, 'utils/misc.py', {'convert_output_to_messages', 'get_paired_tool_call_ids', 'reconcile_tool_pairs'}
    )
    data = payload()
    marker = data['_native_transport']
    output = []
    for index, text in enumerate([' first ', ' continued ']):
        blocks = [{'type': 'thinking', 'thinking': 'display thought', 'signature': f'sig-{index}'}] if thinking else []
        blocks.append({'type': 'text', 'text': text})
        native = normalize_response('anthropic', {'content': blocks}, data['model'], marker['binding'])['choices'][0][
            'message'
        ]
        output.append({'type': 'message', 'content': [{'type': 'output_text', 'text': text}]})
        ns['append_native_streamed_reasoning'](output, native['reasoning_details'])
    messages = ns['convert_output_to_messages'](output, raw=True)
    assert [m['content'] for m in messages] == [' first ', ' continued ']
    data['messages'].extend(messages)
    _, wire = build_request('anthropic', marker['base_url'], data, marker['binding'])
    assert [
        part['text']
        for m in wire['messages']
        if m['role'] == 'assistant'
        for part in m['content']
        if part.get('type') == 'text'
    ] == [' first ', ' continued ']


@pytest.mark.asyncio
async def test_nonstream_continue_keeps_signed_response_boundaries():
    import logging
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from open_webui.utils.native_providers import normalize_response, build_request

    data = payload()
    marker = data['_native_transport']
    ctx = {
        'request': object(),
        'user': object(),
        'metadata': {'chat_id': 'saved', 'message_id': 'reply'},
        'events': [],
        'event_emitter': AsyncMock(),
    }
    ns = {
        'copy': copy,
        'get_response_data': lambda value: (value, value),
        'is_saved_chat_id': lambda value: True,
        'Chats': SimpleNamespace(
            upsert_message_to_chat_by_id_and_message_id=AsyncMock(), get_chat_title_by_id=AsyncMock(return_value='')
        ),
        'get_reasoning_details': lambda value: value.get('reasoning_details'),
        'output_id': lambda prefix: prefix + uuid4().hex,
        'normalize_usage': lambda value: value,
        'publish_chat_finished_event': AsyncMock(),
        'outlet_filter_handler': AsyncMock(),
        'background_tasks_handler': AsyncMock(),
        'build_response_object': lambda response, data: data,
        'merge_events_into_response': lambda data, events: data,
        'publish_chat_failed_event': AsyncMock(),
        'log': logging.getLogger(__name__),
    }
    load_functions(
        ns, 'utils/middleware.py', {'non_streaming_chat_response_handler', 'append_native_streamed_reasoning'}
    )
    load_functions(
        ns,
        'utils/misc.py',
        {'convert_output_to_messages', 'get_paired_tool_call_ids', 'reconcile_tool_pairs', 'get_output_text'},
    )
    for index, text in enumerate([' first ', ' continued ']):
        normalized = normalize_response(
            'anthropic',
            {
                'content': [
                    {'type': 'thinking', 'thinking': 'display', 'signature': f'signature-{index}'},
                    {'type': 'text', 'text': text},
                ],
                'stop_reason': 'end_turn',
            },
            data['model'],
            marker['binding'],
        )
        if index:
            ctx['metadata']['assistant_message_id'] = 'reply'
        await ns['non_streaming_chat_response_handler'](normalized, ctx)
        ns['publish_chat_failed_event'].assert_not_awaited()
    output = ctx['assistant_message']['output']
    assert len([item for item in output if item.get('reasoning_details')]) == 2
    # Display thoughts are retained once per round; the empty envelope contains no duplicate text.
    assert len([item for item in output if item['type'] == 'reasoning' and item.get('content')]) == 2
    messages = ns['convert_output_to_messages'](output, raw=True)
    assert [message['content'] for message in messages] == [' first ', ' continued ']
    data['messages'].extend(messages)
    _, wire = build_request('anthropic', marker['base_url'], data, marker['binding'])
    parts = [part for message in wire['messages'] if message['role'] == 'assistant' for part in message['content']]
    assert [part['signature'] for part in parts if part['type'] == 'thinking'] == ['signature-0', 'signature-1']
