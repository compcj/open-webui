"""Run provider streams through the real middleware without importing the app/database."""

import asyncio
import copy
import hashlib
import json
import logging
import re
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import aiohttp
import pytest
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.images.context import build_tool_result_parts
from test_web_tool_citations import load_functions


ERROR = {'message': 'Upstream request failed', 'code': 'upstream_error'}


def chat_chunk(content='', finish_reason=None):
    return {'choices': [{'delta': {'content': content}, 'finish_reason': finish_reason}]}


def response_output(text='Recovered answer'):
    return [
        {
            'type': 'message',
            'id': 'msg-recovered',
            'role': 'assistant',
            'status': 'completed',
            'content': [{'type': 'output_text', 'text': text}],
        }
    ]


def tool_call_chunk():
    return {
        'choices': [
            {
                'delta': {
                    'tool_calls': [{'index': 0, 'id': 'call-1', 'function': {'name': 'test_tool', 'arguments': '{}'}}]
                },
                'finish_reason': 'tool_calls',
            }
        ]
    }


@pytest.fixture
def runtime():
    events = []
    saved = {}

    async def emit(event):
        events.append(copy.deepcopy(event))

    async def save(chat_id, message_id, data, **kwargs):
        saved.update(copy.deepcopy(data))

    ns = {
        'asyncio': asyncio,
        'copy': copy,
        'hashlib': hashlib,
        'time': time,
        'uuid4': uuid4,
        'JSONCodec': json,
        'json': json,
        'HTTPException': HTTPException,
        'StreamingResponse': StreamingResponse,
        'is_saved_chat_id': is_saved_chat_id,
        'UserModel': type('UserModel', (), {}),
        'FilterContext': lambda: None,
        'log': logging.getLogger(__name__),
        'ENABLE_FUNCTIONS': False,
        'ENABLE_RESPONSES_API_STATEFUL': False,
        'ENABLE_CHAT_RESPONSE_STREAM_INPLACE_APPEND': False,
        'ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION': False,
        'CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE': 1,
        'CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS': 1,
        'RESPONSE_COMPLETION_RESPONSE_FIELDS': ('error', 'id', 'output', 'usage'),
        'get_system_oauth_token': AsyncMock(return_value=None),
        'get_tool_citation_source_ids': lambda *args: {},
        'save_response_stream': AsyncMock(),
        'clear_response_stream': AsyncMock(),
        'publish_chat_finished_event': AsyncMock(),
        'outlet_filter_handler': AsyncMock(),
        'background_tasks_handler': AsyncMock(),
        'execute_tool_call': AsyncMock(return_value=({}, 'Tool result', {}, 'builtin', False)),
        'stage_ask_user_tool_calls': lambda *args: (False, None),
        'build_terminal_file_tool_result': lambda *args: None,
        'process_tool_result': AsyncMock(return_value=('Tool result', [], [])),
        'terminal_event_handler': AsyncMock(),
        'attach_tool_citations': AsyncMock(),
        'build_tool_result_parts': build_tool_result_parts,
        'get_reasoning_format': lambda model: None,
        'convert_url_images_to_base64': AsyncMock(side_effect=lambda data, **kwargs: data),
        'normalize_messages_for_model': lambda data: data,
        'merge_usage': lambda current, incoming: {**(current or {}), **incoming},
        'Chats': SimpleNamespace(
            get_message_by_id_and_message_id=AsyncMock(return_value=None),
            get_chat_title_by_id=AsyncMock(return_value='Synthetic chat'),
            upsert_message_to_chat_by_id_and_message_id=save,
        ),
    }
    load_functions(ns, 'utils/response.py', {'normalize_usage'})
    load_functions(ns, 'utils/tool_search.py', {'resolve_tool_call'})
    load_functions(
        ns,
        'utils/misc.py',
        {
            'get_output_text',
            'get_reasoning_details',
            'convert_output_to_messages',
            'reconcile_tool_pairs',
            'get_paired_tool_call_ids',
            'get_last_user_message_item',
        },
    )
    load_functions(
        ns,
        'utils/middleware.py',
        {
            'streaming_chat_response_handler',
            'output_id',
            'append_to_text_field',
            'get_response_completion_event_data',
            'handle_responses_streaming_event',
            '_deduplicate_tool_calls',
            '_split_tool_calls',
            'tool_result_content',
            '_is_tool_result_error',
        },
    )

    def response_for(chunks):
        async def body():
            for chunk in chunks:
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk if isinstance(chunk, str) else f'data: {json.dumps(chunk)}\n\n'

        return StreamingResponse(body())

    async def run(chunks, *, temporary=False, follow_up=None, previous=None, setup=None):
        ns['generate_chat_completion'] = (
            AsyncMock(side_effect=follow_up)
            if isinstance(follow_up, Exception)
            else AsyncMock(return_value=response_for(follow_up or []))
        )
        if previous:
            saved.update(copy.deepcopy(previous))
            ns['Chats'].get_message_by_id_and_message_id.return_value = copy.deepcopy(previous)
        ctx = {
            'request': SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(redis=None))),
            'form_data': {'model': 'synthetic-provider', 'messages': [{'role': 'user', 'content': 'Test'}]},
            'user': SimpleNamespace(id='user', role='admin'),
            'model': {'id': 'synthetic-provider'},
            'metadata': {
                'chat_id': 'local:test' if temporary else 'saved-chat',
                'message_id': 'reply',
                'params': {'reasoning_tags': False},
            },
            'events': [],
            'event_emitter': emit,
            'event_caller': None,
        }
        if previous:
            ctx['metadata']['assistant_message_id'] = 'reply'
            ctx['metadata']['tool_approval_resume'] = 'model'
            ctx['assistant_message'] = copy.deepcopy(previous)
        if setup is not None:
            setup(ctx, ns, response_for)
        await ns['streaming_chat_response_handler'](response_for(chunks), ctx)
        return ctx

    return SimpleNamespace(run=run, ns=ns, events=events, saved=saved)


@pytest.mark.asyncio
@pytest.mark.parametrize('done', ['data: [DONE]\n\n', 'data:[DONE]\r\n'])
async def test_explicit_done_does_not_read_a_late_transport_failure(runtime, done):
    await runtime.run([chat_chunk('Complete answer', 'stop'), done, aiohttp.ClientPayloadError('synthetic late reset')])
    assert runtime.saved.get('error') is None
    assert runtime.saved['done'] is True
    assert runtime.ns['get_output_text'](runtime.saved['output']) == 'Complete answer'


@pytest.mark.asyncio
@pytest.mark.parametrize('after_tool', [False, True])
@pytest.mark.parametrize('temporary', [False, True])
@pytest.mark.parametrize('failure_type', [aiohttp.ClientPayloadError, ConnectionResetError])
async def test_transport_failure_preserves_incomplete_output_and_buffered_deltas(
    runtime, after_tool, temporary, failure_type
):
    failure = failure_type('synthetic truncated response')

    def setup(ctx, ns, response_for):
        ctx['metadata']['params']['stream_delta_chunk_size'] = 100

    interrupted = [chat_chunk('Partial answer'), failure]
    await runtime.run(
        [tool_call_chunk()] if after_tool else interrupted,
        follow_up=interrupted if after_tool else None,
        temporary=temporary,
        setup=setup,
    )
    errors = [event for event in runtime.events if event['type'] == 'chat:message:error']
    assert len(errors) == 1
    assert errors[0]['data']['error']['content'] == str(failure)
    deltas = [event['data'].get('delta') for event in runtime.events if event['type'] == 'response:completion']
    assert 'Partial answer' in deltas
    snapshots = [event['data']['output'] for event in runtime.events if 'output' in event['data']]
    partial = snapshots[-1]
    assert runtime.ns['get_output_text'](partial) == 'Partial answer'
    assert next(item for item in partial if item['type'] == 'message')['status'] == 'incomplete'
    if not temporary:
        assert runtime.saved['output'] == partial
        assert runtime.saved['done'] is True
        assert runtime.saved['error']['content'] == str(failure)
    if after_tool:
        assert [item['call_id'] for item in partial if item['type'] == 'function_call_output'] == ['call-1']
    assert runtime.ns['execute_tool_call'].await_count == int(after_tool)
    assert runtime.ns['generate_chat_completion'].await_count == int(after_tool)
    runtime.ns['clear_response_stream'].assert_awaited()


@pytest.mark.asyncio
async def test_tool_followup_done_closes_active_iterator_without_waiting_for_eof(runtime):
    closed = []
    consumed_tail = []

    def setup(ctx, ns, response_for):
        async def body():
            try:
                yield f'data: {json.dumps(chat_chunk("Answer", "stop"))}\n\n'
                yield 'data: [DONE]\n\n'
                consumed_tail.append(True)
                raise aiohttp.ClientPayloadError('synthetic late reset')
            finally:
                closed.append(True)

        ns['generate_chat_completion'] = AsyncMock(return_value=StreamingResponse(body()))

    await runtime.run([tool_call_chunk()], setup=setup)
    assert consumed_tail == []
    assert closed == [True]
    assert runtime.saved.get('error') is None
    runtime.ns['execute_tool_call'].assert_awaited_once()
    assert runtime.ns['get_output_text'](runtime.saved['output']) == 'Answer'


@pytest.mark.asyncio
async def test_finish_reason_still_reads_usage_before_done(runtime):
    await runtime.run(
        [
            chat_chunk('Literal data: [DONE] in answer', 'stop'),
            {'choices': [], 'usage': {'prompt_tokens': 12, 'completion_tokens': 4, 'total_tokens': 16}},
            'data: [DONE]\n\n',
            ConnectionResetError('late reset'),
        ]
    )
    assert runtime.saved.get('error') is None
    assert runtime.saved['usage']['total_tokens'] == 16
    assert runtime.ns['get_output_text'](runtime.saved['output']) == 'Literal data: [DONE] in answer'


@pytest.mark.asyncio
async def test_truncated_tool_arguments_never_execute(runtime):
    chunk = tool_call_chunk()
    chunk['choices'][0]['delta']['tool_calls'][0]['function']['arguments'] = '{"unfinished":'
    chunk['choices'][0]['finish_reason'] = None
    await runtime.run([chunk, aiohttp.ClientPayloadError('truncated arguments')])
    runtime.ns['execute_tool_call'].assert_not_awaited()
    runtime.ns['generate_chat_completion'].assert_not_awaited()
    assert runtime.saved['error'] == {'content': 'truncated arguments'}
    assert runtime.saved['output'][0]['status'] == 'incomplete'


@pytest.mark.asyncio
async def test_interrupted_approval_resume_preserves_prefix_without_tool_reexecution(runtime):
    await runtime.run(
        [chat_chunk('Partial continuation'), ConnectionResetError('reset during resume')],
        previous={'output': response_output('Earlier answer')},
    )
    assert runtime.ns['get_output_text'](runtime.saved['output']) == 'Earlier answerPartial continuation'
    assert runtime.saved['output'][0]['status'] == 'incomplete'
    assert runtime.saved['done'] is True
    runtime.ns['execute_tool_call'].assert_not_awaited()
    runtime.ns['generate_chat_completion'].assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_tool_followup_preserves_cancellation(runtime):
    with pytest.raises(asyncio.CancelledError):
        await runtime.run([tool_call_chunk()], follow_up=[chat_chunk('Partial'), asyncio.CancelledError()])
    assert runtime.saved.get('error') is None
    assert runtime.saved['done'] is True
    assert any(event['type'] == 'chat:tasks:cancel' for event in runtime.events)
    runtime.ns['execute_tool_call'].assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('interrupted', [False, True])
async def test_legacy_code_executes_only_after_an_uninterrupted_followup(runtime, interrupted):
    execute_code = AsyncMock(return_value={'stdout': 'synthetic result', 'stderr': '', 'result': ''})

    def setup(ctx, ns, response_for):
        ctx['metadata']['params']['function_calling'] = 'legacy'
        ctx['metadata']['features'] = {'code_interpreter': True}
        ctx['event_caller'] = execute_code
        ns['Config'] = SimpleNamespace(
            get=AsyncMock(
                side_effect=lambda key: {'code_interpreter.enable': True, 'code_interpreter.engine': 'pyodide'}[key]
            )
        )
        ns['sanitize_code'] = lambda code: code
        ns['CODE_INTERPRETER_BLOCKED_MODULES'] = []
        ns['DEFAULT_CODE_INTERPRETER_TAGS'] = [('<code_interpreter>', '</code_interpreter>')]
        ns['re'] = re
        load_functions(ns, 'utils/middleware.py', {'_start_tag_pattern'})
        code = '<code_interpreter type="code">print("synthetic code")'
        ns['generate_chat_completion'] = AsyncMock(
            side_effect=[
                response_for(
                    [
                        chat_chunk(code if interrupted else code + '</code_interpreter>'),
                        ConnectionResetError('reset before code is complete') if interrupted else 'data: [DONE]\n\n',
                    ]
                ),
                response_for([chat_chunk('Code finished', 'stop'), 'data: [DONE]\n\n']),
            ]
        )

    await runtime.run([tool_call_chunk()], setup=setup)
    if interrupted:
        execute_code.assert_not_awaited()
        runtime.ns['generate_chat_completion'].assert_awaited_once()
        assert runtime.saved['output'][-1]['status'] == 'incomplete'
        assert runtime.saved['error']['content'] == 'reset before code is complete'
    else:
        execute_code.assert_awaited_once()
        assert runtime.ns['generate_chat_completion'].await_count == 2
        assert runtime.saved.get('error') is None
        assert runtime.ns['get_output_text'](runtime.saved['output']).endswith('Code finished')


@pytest.mark.asyncio
@pytest.mark.parametrize('completed', [False, True])
async def test_real_chunked_http_interruption_after_tool_result(runtime, completed):
    """Use aiohttp's real parser, with no terminating zero-length HTTP chunk."""
    release_connection = asyncio.Event()
    server_finished = asyncio.Event()

    async def serve(reader, writer):
        try:
            await reader.readuntil(b'\r\n\r\n')
            body = f'data: {json.dumps(chat_chunk("Wire answer"))}\n\n'.encode()
            if completed:
                body += b'data: [DONE]\n\n'
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n'
                + f'{len(body):x}\r\n'.encode()
                + body
                + b'\r\n'
            )
            await writer.drain()
            await release_connection.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            server_finished.set()

    transport = {'CHAT_STREAM_RESPONSE_CHUNK_MAX_BUFFER_SIZE': None, 'log': logging.getLogger(__name__)}
    load_functions(transport, 'utils/misc.py', {'stream_chunks_handler'})
    load_functions(transport, 'utils/session_pool.py', {'stream_wrapper', 'cleanup_response'})
    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    upstream_responses = []
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:

            async def send(*args, **kwargs):
                response = await session.get(f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}')
                upstream_responses.append(response)

                async def body():
                    source = transport['stream_wrapper'](response)
                    try:
                        async for line in source:
                            try:
                                yield line
                            finally:
                                if not completed or line.strip() == b'data: [DONE]':
                                    release_connection.set()
                    finally:
                        await source.aclose()

                return StreamingResponse(body())

            def setup(ctx, ns, response_for):
                ns['generate_chat_completion'] = AsyncMock(side_effect=send)

            await runtime.run([tool_call_chunk()], setup=setup)
    finally:
        release_connection.set()
        server.close()
        await server.wait_closed()
        if upstream_responses:
            await asyncio.wait_for(server_finished.wait(), timeout=5)

    assert len(upstream_responses) == 1
    assert upstream_responses[0].closed
    runtime.ns['execute_tool_call'].assert_awaited_once()
    assert runtime.ns['get_output_text'](runtime.saved['output']) == 'Wire answer'
    assert runtime.saved['done'] is True
    if completed:
        assert runtime.saved.get('error') is None
    else:
        assert 'Response payload is not completed' in runtime.saved['error']['content']
        assert runtime.saved['output'][-1]['status'] == 'incomplete'


@pytest.mark.asyncio
@pytest.mark.parametrize('error_frame', [{'error': ERROR}, json.dumps({'error': ERROR})])
@pytest.mark.parametrize('temporary', [False, True])
async def test_recovered_chat_stream_never_publishes_or_saves_a_terminal_error(runtime, error_frame, temporary):
    await runtime.run(
        [error_frame, chat_chunk('Recovered answer'), chat_chunk(finish_reason='stop')], temporary=temporary
    )
    assert not any(event['data'].get('error') for event in runtime.events)
    assert not runtime.saved.get('error')
    final = runtime.events[-1]['data']
    assert final['done'] is True
    assert runtime.ns['get_output_text'](final['output']) == 'Recovered answer'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error_frame',
    [
        {'type': 'error', 'message': 'Upstream request failed', 'code': 'upstream_error'},
        {'type': 'error', 'error': ERROR},
        {'type': 'response.failed', 'response': {'error': ERROR}},
    ],
)
async def test_recovered_responses_stream_does_not_leave_a_message_error(runtime, error_frame):
    await runtime.run(
        [error_frame, {'type': 'response.completed', 'response': {'status': 'completed', 'output': response_output()}}]
    )
    # Shared socket projection also reads raw response events, not just chat:completion.
    assert not any(
        event['data'].get('error') or event['data'].get('response', {}).get('error') for event in runtime.events
    )
    assert not runtime.saved.get('error')
    assert runtime.ns['get_output_text'](runtime.saved['output']) == 'Recovered answer'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'tail',
    [
        [],
        [chat_chunk('partial after error')],
        ['data: [DONE]\n\n'],
        [{'choices': [{'delta': {'role': 'assistant'}}]}],
        [{'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 1}}],
        [chat_chunk(finish_reason='error')],
        [{'type': 'response.incomplete', 'response': {'status': 'incomplete'}}],
    ],
)
@pytest.mark.parametrize(
    'error_frame',
    [
        {'error': ERROR},
        json.dumps({'error': ERROR}),
        {'type': 'response.failed', 'response': {'error': ERROR}},
    ],
)
async def test_unrecovered_stream_error_is_kept_even_with_partial_content_or_done(runtime, tail, error_frame):
    await runtime.run([chat_chunk('partial before error'), error_frame, *tail])
    assert runtime.saved['error'] == {'content': ERROR}
    assert any(event['data'].get('error') for event in runtime.events)
    assert runtime.saved['done'] is True
    assert 'partial before error' in runtime.ns['get_output_text'](runtime.saved['output'])


@pytest.mark.asyncio
async def test_error_after_successful_provider_finish_is_not_cleared_by_outer_completion(runtime):
    await runtime.run([chat_chunk('Partial answer', 'stop'), {'error': ERROR}, 'data: [DONE]\n\n'])
    assert runtime.saved['error'] == {'content': ERROR}
    assert runtime.events[-1]['data']['done'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('temporary', [False, True])
@pytest.mark.parametrize(
    'follow_up',
    [
        [chat_chunk('Recovered answer'), chat_chunk(finish_reason='stop')],
        [{'type': 'response.completed', 'response': {'status': 'completed', 'output': response_output()}}],
    ],
)
async def test_successful_tool_followup_clears_reported_error_without_repeating_tool(runtime, follow_up, temporary):
    await runtime.run([tool_call_chunk(), {'error': ERROR}], follow_up=follow_up, temporary=temporary)
    runtime.ns['execute_tool_call'].assert_awaited_once()
    runtime.ns['generate_chat_completion'].assert_awaited_once()
    assert runtime.saved.get('error') is None
    final = runtime.events[-1]['data']
    assert runtime.ns['get_output_text'](final['output']) == 'Recovered answer'
    assert [item['call_id'] for item in final['output'] if item['type'] == 'function_call_output'] == ['call-1']
    if not temporary:
        assert runtime.saved['output'] == final['output']
    error_updates = [event['data'] for event in runtime.events if 'error' in event['data']]
    assert error_updates[-1] == {'error': None, 'done': False}


@pytest.mark.asyncio
async def test_tool_followup_exception_is_not_cleared_by_final_output(runtime):
    await runtime.run([tool_call_chunk(), {'error': ERROR}], follow_up=HTTPException(502, 'Continuation failed'))
    assert runtime.saved['error'] == {'content': 'Continuation failed'}
    assert runtime.events[-1]['data']['done'] is True
    runtime.ns['execute_tool_call'].assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('temporary', [False, True])
async def test_successful_approval_resume_clears_error_saved_by_previous_stream(runtime, temporary):
    await runtime.run(
        [chat_chunk('Recovered answer'), chat_chunk(finish_reason='stop')],
        previous={'output': response_output('Earlier partial answer'), 'error': {'content': ERROR}},
        temporary=temporary,
    )
    if not temporary:
        assert runtime.saved.get('error') is None
    error_updates = [event['data'] for event in runtime.events if 'error' in event['data']]
    assert error_updates == [{'error': None, 'done': False}]
    assert 'Earlier partial answer' in runtime.ns['get_output_text'](runtime.events[-1]['data']['output'])
    runtime.ns['execute_tool_call'].assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_round_usage_counts_only_latest_response_and_new_tool_text(runtime):
    from test_context_compaction_legacy import load
    from open_webui.utils.response import merge_usage

    snapshots = []
    threshold = load(
        {'_exceeds_token_threshold', '_usage_token_count'},
        _estimate_messages_tokens=lambda rows: sum(len(row.get('content') or '') for row in rows),
        _estimate_tokens=len,
    )['_exceeds_token_threshold']

    async def capture(request, user, messages, *args):
        snapshots.append(copy.deepcopy(messages))
        return messages, None, False

    def setup(ctx, ns, response_for):
        ctx['request'].state.context_compaction = {'mode': 'legacy'}
        ctx['request'].app.state.MODELS = {}
        ns['CHAT_RESPONSE_MAX_TOOL_CALL_ITERATIONS'] = 2
        ns['merge_usage'] = merge_usage
        ns['compact_messages_for_request'] = capture
        load_functions(ns, 'utils/middleware.py', {'process_messages_with_output'})
        ns['process_tool_result'] = AsyncMock(side_effect=[('A' * 80, [], []), ('B' * 90, [], [])])
        second = tool_call_chunk()
        second['choices'][0]['delta']['tool_calls'][0]['id'] = 'call-2'
        ns['generate_chat_completion'] = AsyncMock(
            side_effect=[
                response_for([second, {'usage': {'prompt_tokens': 200, 'completion_tokens': 20}, 'choices': []}]),
                response_for([chat_chunk('done', 'stop')]),
            ]
        )

    await runtime.run(
        [tool_call_chunk(), {'usage': {'prompt_tokens': 100, 'completion_tokens': 10}, 'choices': []}],
        setup=setup,
    )
    assert len(snapshots) == 2
    assert [row['content'] for row in snapshots[1] if row.get('role') == 'tool'] == ['A' * 80, 'B' * 90]
    first_usage = next(row['usage'] for row in reversed(snapshots[0]) if row.get('usage'))
    second_index = next(i for i in range(len(snapshots[1]) - 1, -1, -1) if snapshots[1][i].get('usage'))
    assert first_usage['total_tokens'] == 110
    assert snapshots[1][second_index]['usage']['total_tokens'] == 220
    assert [row['content'] for row in snapshots[1][second_index + 1 :] if row.get('role') == 'tool'] == ['B' * 90]
    assert runtime.saved['usage']['total_tokens'] == 330
    # Latest 220 plus only the new 90-character result: neither billing 330 nor old tool text.
    assert threshold(snapshots[1], '', None, 309)
    assert not threshold(snapshots[1], '', None, 310)
