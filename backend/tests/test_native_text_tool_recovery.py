"""Text-call recovery must preserve native signed response projections."""

import copy

import pytest

from open_webui.utils.native_providers import build_request, make_binding, normalize_response
from test_streaming_error_recovery import chat_chunk, runtime as stream_runtime
from test_web_tool_citations import load_functions


BASE_URL = 'https://native.example.test/v1'
MODEL = 'synthetic-provider'
TOOL_TEXT = '<tool_call>{"name":"test_tool","arguments":{}}</tool_call>'


def native_text_response(protocol, protection=None):
    binding = make_binding(protocol, BASE_URL, MODEL)
    if protocol == 'anthropic':
        blocks = []
        if protection == 'thinking':
            blocks.append({'type': 'thinking', 'thinking': 'Synthetic thought', 'signature': 'synthetic-signature'})
        elif protection == 'redacted_thinking':
            blocks.append({'type': 'redacted_thinking', 'data': 'synthetic-opaque-data'})
        blocks.append({'type': 'text', 'text': TOOL_TEXT})
        response = {'content': blocks, 'stop_reason': 'end_turn'}
    else:
        blocks = [{'text': TOOL_TEXT, **({'thoughtSignature': 'synthetic-signature'} if protection else {})}]
        response = {
            'responseId': 'synthetic-response',
            'candidates': [{'content': {'parts': blocks}, 'finishReason': 'STOP'}],
        }
    message = normalize_response(protocol, response, MODEL, binding)['choices'][0]['message']
    return binding, blocks, message['reasoning_details']


def allow_test_tool(ctx, namespace, response_for):
    ctx['form_data']['tools'] = [
        {
            'type': 'function',
            'function': {'name': 'test_tool', 'parameters': {'type': 'object', 'properties': {}}},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'protocol,protection',
    [('anthropic', 'thinking'), ('anthropic', 'redacted_thinking'), ('gemini', 'thoughtSignature')],
)
async def test_signed_native_tool_text_stays_unexecuted_and_replays_exactly(stream_runtime, protocol, protection):
    binding, blocks, details = native_text_response(protocol, protection)
    frozen_details = copy.deepcopy(details)
    load_functions(stream_runtime.ns, 'utils/middleware.py', {'append_native_streamed_reasoning'})
    context = await stream_runtime.run(
        [
            chat_chunk(TOOL_TEXT),
            {'choices': [{'delta': {'reasoning_details': details}, 'finish_reason': 'stop'}]},
            'data: [DONE]\n\n',
        ],
        setup=allow_test_tool,
    )

    stream_runtime.ns['execute_tool_call'].assert_not_awaited()
    stream_runtime.ns['generate_chat_completion'].assert_not_awaited()
    assert stream_runtime.saved['done'] is True
    assert stream_runtime.saved.get('error') is None
    output = stream_runtime.saved['output']
    assert stream_runtime.ns['get_output_text'](output) == TOOL_TEXT
    assert not any(item.get('type') in {'function_call', 'function_call_output'} for item in output)
    assert next(item['reasoning_details'] for item in output if item.get('reasoning_details')) == frozen_details
    assert details == frozen_details

    replay = stream_runtime.ns['convert_output_to_messages'](output, raw=True)
    payload = {
        'model': MODEL,
        'messages': [*context['form_data']['messages'], *replay, {'role': 'user', 'content': 'Continue'}],
    }
    _, wire = build_request(protocol, BASE_URL, payload, binding)
    messages_key, native_role, parts_key = (
        ('messages', 'assistant', 'content') if protocol == 'anthropic' else ('contents', 'model', 'parts')
    )
    assert next(message[parts_key] for message in wire[messages_key] if message['role'] == native_role) == blocks


@pytest.mark.asyncio
@pytest.mark.parametrize('protocol', [None, 'anthropic', 'gemini'])
async def test_unprotected_tool_text_still_executes_and_continues(stream_runtime, protocol):
    chunks = [chat_chunk(TOOL_TEXT)]
    if protocol:
        binding, _, details = native_text_response(protocol)
        load_functions(stream_runtime.ns, 'utils/middleware.py', {'append_native_streamed_reasoning'})
        chunks.append({'choices': [{'delta': {'reasoning_details': details}, 'finish_reason': 'stop'}]})
    else:
        chunks.append(chat_chunk(finish_reason='stop'))
    chunks.append('data: [DONE]\n\n')
    await stream_runtime.run(
        chunks,
        follow_up=[chat_chunk('Finished', 'stop'), 'data: [DONE]\n\n'],
        setup=allow_test_tool,
    )

    stream_runtime.ns['execute_tool_call'].assert_awaited_once()
    stream_runtime.ns['generate_chat_completion'].assert_awaited_once()
    assert stream_runtime.saved['done'] is True
    assert stream_runtime.saved.get('error') is None
    assert stream_runtime.ns['get_output_text'](stream_runtime.saved['output']) == 'Finished'
    payload = stream_runtime.ns['generate_chat_completion'].await_args.args[1]
    calls = [call for message in payload['messages'] for call in message.get('tool_calls', [])]
    results = [message for message in payload['messages'] if message['role'] == 'tool']
    assert len(calls) == len(results) == 1
    assert calls[0]['function']['name'] == 'test_tool'
    assert calls[0]['id'] == results[0]['tool_call_id']
    if protocol:
        build_request(protocol, BASE_URL, payload, binding)


@pytest.mark.asyncio
@pytest.mark.parametrize('blocked_by', ['tool_choice', 'interruption', 'length'])
async def test_text_recovery_keeps_existing_execution_gates(stream_runtime, blocked_by):
    def setup(ctx, namespace, response_for):
        allow_test_tool(ctx, namespace, response_for)
        if blocked_by == 'tool_choice':
            ctx['form_data']['tool_choice'] = 'none'

    chunks = [chat_chunk(TOOL_TEXT)]
    if blocked_by == 'interruption':
        chunks.append(ConnectionResetError('Synthetic interrupted response'))
    else:
        chunks.extend([chat_chunk(finish_reason='length' if blocked_by == 'length' else 'stop'), 'data: [DONE]\n\n'])
    await stream_runtime.run(chunks, setup=setup)

    stream_runtime.ns['execute_tool_call'].assert_not_awaited()
    stream_runtime.ns['generate_chat_completion'].assert_not_awaited()
    output = stream_runtime.saved['output']
    assert stream_runtime.ns['get_output_text'](output) == TOOL_TEXT
    assert not any(item.get('type') in {'function_call', 'function_call_output'} for item in output)
