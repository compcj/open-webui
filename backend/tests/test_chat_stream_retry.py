"""Exercise early transport recovery through the real router, without app/database imports."""

import asyncio
import copy
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from fastapi import HTTPException

from test_native_connections_integration import sender
from test_streaming_error_recovery import chat_chunk, runtime, tool_call_chunk
from test_temporary_context_adapters import context
from test_web_tool_citations import load_functions


def event(data):
    return f'data: {json.dumps(data)}\n\n'.encode()


ROLE = event({'id': 'discarded-attempt', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}}]})
ANSWER = event(chat_chunk('Recovered answer', 'stop'))
DONE = b'data: [DONE]\n\n'


class Response:
    def __init__(self, chunks, *, status=200, content_type='text/event-stream'):
        self.status = status
        self.headers = {'Content-Type': content_type}
        self.closed = False
        self.chunks = chunks
        self.content = self

    async def iter_chunks(self):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk, False

    def close(self):
        self.closed = True


@pytest.fixture
def router(sender):
    request, ns, captured = sender
    transport = {'CHAT_STREAM_RESPONSE_CHUNK_MAX_BUFFER_SIZE': None, 'log': logging.getLogger(__name__)}
    load_functions(transport, 'utils/misc.py', {'stream_chunks_handler'})
    load_functions(transport, 'utils/session_pool.py', {'stream_wrapper', 'cleanup_response'})
    ns.update(
        stream_wrapper=transport['stream_wrapper'],
        cleanup_response=transport['cleanup_response'],
        _clean_proxy_headers=lambda headers: headers,
        ERROR_MESSAGES=SimpleNamespace(SERVER_CONNECTION_ERROR='Connection failed'),
    )
    requests = []
    responses = []
    user = SimpleNamespace(id='test-user', role='admin', name='Synthetic user', email='test@example.test')

    async def invoke(
        attempts,
        *,
        protocol='chat_completions',
        pipeline=False,
        stream=True,
        url='https://example.test/v1',
        session=None,
        form_data=None,
    ):
        responses.extend(attempts)
        request.app.state.OPENAI_MODELS['connection.native-test']['pipeline'] = pipeline
        ns['get_openai_connection'] = AsyncMock(
            return_value=(url, 'synthetic-key', {'api_type': protocol, 'prefix_id': 'connection'})
        )

        async def send(**kwargs):
            if requests:
                assert responses[len(requests) - 1].closed, 'Close the failed response before retrying'
            requests.append(copy.deepcopy(kwargs))
            if session is not None:
                responses.append(await session.request(**kwargs))
            return responses[len(requests) - 1]

        ns['get_session'] = AsyncMock(return_value=SimpleNamespace(request=send))
        return await ns['generate_chat_completion'](
            request,
            {
                'messages': [{'role': 'user', 'content': 'Test'}],
                **(form_data or {}),
                'model': 'connection.native-test',
                'stream': stream,
            },
            user,
        )

    return SimpleNamespace(invoke=invoke, requests=requests, responses=responses, ns=ns, request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize('prelude', [[], [b': keep-alive\n\n'], [ROLE], [ROLE, b': keep-alive\n\n']])
async def test_early_payload_failure_resends_identical_request_once(router, prelude):
    first = Response([*prelude, aiohttp.ClientPayloadError('truncated before output')])
    second = Response([ANSWER, DONE])
    response = await router.invoke([first, second])
    result = b''.join([chunk async for chunk in response.body_iterator])
    assert ANSWER in result and result.endswith(DONE)
    assert b'discarded-attempt' not in result
    assert len(router.requests) == 2
    assert router.requests[0] == router.requests[1]
    assert first.closed and second.closed
    router.ns['check_model_access'].assert_awaited_once()


@pytest.mark.asyncio
async def test_second_payload_failure_is_reported_without_third_attempt(router):
    first = Response([aiohttp.ClientPayloadError('first failure')])
    second = Response([ROLE, aiohttp.ClientPayloadError('second failure')])
    response = await router.invoke([first, second])
    with pytest.raises(aiohttp.ClientPayloadError, match='second failure'):
        _ = [chunk async for chunk in response.body_iterator]
    assert len(router.requests) == 2
    assert first.closed and second.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'chunk',
    [
        event(chat_chunk('Partial answer')),
        event({'choices': [{'delta': {'reasoning_content': 'Thinking'}}]}),
        event({'choices': [{'delta': {'reasoning': 'Thinking'}}]}),
        event({'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '{'}}]}}]}),
        event({'choices': [{'delta': {'function_call': {'arguments': '{'}}}]}),
        event({'choices': [], 'usage': {'prompt_tokens': 4}}),
        event({'error': {'message': 'Provider error'}}),
        event({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}),
        event({'event': {'type': 'status', 'data': {'description': 'Effect'}}}),
        event({'choices': [{'delta': {'unknown': ''}}]}),
        b'data: malformed\n\n',
        DONE,
        b'data: {"choices":\n',
        ROLE * 800,
    ],
    ids=[
        'content',
        'reasoning_content',
        'reasoning',
        'tool_calls',
        'function_call',
        'usage',
        'error',
        'finish',
        'custom_event',
        'unknown_delta',
        'malformed',
        'done',
        'partial_json',
        'buffer_limit',
    ],
)
async def test_meaningful_or_unknown_events_disable_retry(router, chunk):
    first = Response([chunk, aiohttp.ClientPayloadError('interrupted')])
    response = await router.invoke([first])
    received = []
    with pytest.raises(aiohttp.ClientPayloadError, match='interrupted'):
        async for item in response.body_iterator:
            received.append(item)
    assert b''.join(received) == chunk
    assert len(router.requests) == 1
    assert first.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [asyncio.CancelledError(), asyncio.TimeoutError(), ConnectionResetError()])
async def test_cancellation_and_other_failures_do_not_retry(router, failure):
    response = await router.invoke([Response([failure])])
    with pytest.raises(type(failure)):
        _ = [chunk async for chunk in response.body_iterator]
    assert len(router.requests) == 1
    assert router.responses[0].closed


@pytest.mark.asyncio
async def test_pipeline_streams_do_not_retry(router):
    response = await router.invoke([Response([aiohttp.ClientPayloadError('pipeline failure')])], pipeline=True)
    with pytest.raises(aiohttp.ClientPayloadError, match='pipeline failure'):
        _ = [chunk async for chunk in response.body_iterator]
    assert len(router.requests) == 1


@pytest.mark.asyncio
async def test_unsolicited_stream_for_nonstreaming_request_does_not_retry(router):
    response = await router.invoke([Response([aiohttp.ClientPayloadError('unexpected stream')])], stream=False)
    with pytest.raises(aiohttp.ClientPayloadError, match='unexpected stream'):
        _ = [chunk async for chunk in response.body_iterator]
    assert len(router.requests) == 1


@pytest.mark.asyncio
async def test_responses_protocol_does_not_retry(router):
    from open_webui.utils.responses_reasoning import remap_reasoning_effort_for_responses

    router.ns['remap_reasoning_effort_for_responses'] = remap_reasoning_effort_for_responses
    load_functions(router.ns, 'routers/openai.py', {'convert_to_responses_payload'})
    response = await router.invoke([Response([aiohttp.ClientPayloadError('responses failure')])], protocol='responses')
    with pytest.raises(aiohttp.ClientPayloadError, match='responses failure'):
        _ = [chunk async for chunk in response.body_iterator]
    assert len(router.requests) == 1


@pytest.mark.asyncio
async def test_heartbeats_are_forwarded_while_empty_role_is_held(router):
    response = await router.invoke([Response([ROLE, b': keep-alive\n\n', ANSWER, DONE])])
    iterator = response.body_iterator
    assert await anext(iterator) == b': keep-alive\n'
    assert await anext(iterator) == b'\n'
    assert b''.join([chunk async for chunk in iterator]) == ROLE + ANSWER + DONE
    assert len(router.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('prelude', [[], [ROLE]])
async def test_single_line_heartbeat_does_not_wait_for_an_empty_line(router, prelude):
    async def chunks():
        for chunk in prelude:
            yield chunk, False
        yield b': keep-alive\n', False
        await asyncio.Event().wait()

    upstream = Response([])
    upstream.iter_chunks = chunks
    response = await router.invoke([upstream])
    try:
        assert await asyncio.wait_for(anext(response.body_iterator), timeout=1) == b': keep-alive\n'
    finally:
        await response.body_iterator.aclose()
    assert upstream.closed
    assert len(router.requests) == 1


@pytest.mark.asyncio
async def test_cancelling_retry_delay_does_not_start_another_request(router, monkeypatch):
    from open_webui.utils import chat_stream_retry

    waiting = asyncio.Event()

    async def pause(delay):
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(chat_stream_retry, 'asyncio', SimpleNamespace(sleep=pause))
    response = await router.invoke([Response([aiohttp.ClientPayloadError('before cancellation')])])

    async def consume():
        return [chunk async for chunk in response.body_iterator]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(waiting.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(router.requests) == 1
    assert router.responses[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize('closed', [False, True])
async def test_retry_rechecks_temporary_scope(router, context, closed):
    from open_webui.utils.context_store_errors import ContextGone

    router.request.state.context_store = context.store.bundle()
    response = await router.invoke(
        [Response([aiohttp.ClientPayloadError('scope interrupted')]), Response([ANSWER, DONE])]
    )
    if closed:
        await context.repo.close(context.doc['id'], 'owner', context.doc['epoch'])
        with pytest.raises(ContextGone):
            _ = [chunk async for chunk in response.body_iterator]
        assert len(router.requests) == 1
    else:
        assert b''.join([chunk async for chunk in response.body_iterator]) == ANSWER + DONE
        assert len(router.requests) == 2
    assert router.responses[0].closed


@pytest.mark.asyncio
@pytest.mark.parametrize('status,content_type', [(503, 'text/event-stream'), (200, 'application/json')])
async def test_retry_rejects_error_status_or_nonstream_and_closes_response(router, status, content_type):
    failed = Response([aiohttp.ClientPayloadError('first failure')])
    unexpected = Response([], status=status, content_type=content_type)
    response = await router.invoke([failed, unexpected])
    with pytest.raises(HTTPException) as caught:
        _ = [chunk async for chunk in response.body_iterator]
    assert caught.value.status_code == (status if status >= 400 else 502)
    assert len(router.requests) == 2
    assert failed.closed and unexpected.closed


@pytest.mark.asyncio
async def test_tool_followup_recovers_without_reexecuting_resolved_tool(router, runtime):
    async def followup(request, form_data, user, **kwargs):
        return await router.invoke(
            [Response([ROLE, aiohttp.ClientPayloadError('followup truncated')]), Response([ANSWER, DONE])],
            form_data=form_data,
        )

    def setup(ctx, ns, response_for):
        ns['generate_chat_completion'] = AsyncMock(side_effect=followup)

    await runtime.run([tool_call_chunk()], setup=setup)
    assert runtime.saved.get('error') is None
    assert runtime.ns['get_output_text'](runtime.saved['output']) == 'Recovered answer'
    assert [item['call_id'] for item in runtime.saved['output'] if item['type'] == 'function_call_output'] == ['call-1']
    runtime.ns['execute_tool_call'].assert_awaited_once()
    runtime.ns['generate_chat_completion'].assert_awaited_once()
    assert len(router.requests) == 2
    assert router.requests[0] == router.requests[1]
    sent = json.loads(router.requests[1]['data'])
    assert any(message['role'] == 'tool' and message['tool_call_id'] == 'call-1' for message in sent['messages'])


@pytest.mark.asyncio
async def test_clean_eof_does_not_retry(router):
    response = await router.invoke([Response([ROLE])])
    assert b''.join([chunk async for chunk in response.body_iterator]) == ROLE
    assert len(router.requests) == 1


@pytest.mark.asyncio
async def test_real_chunked_http_truncation_before_content_recovers(router):
    """The first real HTTP response omits its terminating zero-length chunk."""
    disconnect = asyncio.Event()
    received_bodies = []
    finished = [asyncio.Event(), asyncio.Event()]

    async def serve(reader, writer):
        index = len(received_bodies)
        try:
            head = await reader.readuntil(b'\r\n\r\n')
            headers = dict(line.split(b':', 1) for line in head.split(b'\r\n')[1:] if b':' in line)
            length = next(value for key, value in headers.items() if key.lower() == b'content-length')
            received_bodies.append(await reader.readexactly(int(length)))
            body = ROLE + b': keep-alive\n\n' if index == 0 else ANSWER + DONE
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n'
                + f'{len(body):x}\r\n'.encode()
                + body
                + b'\r\n'
                + (b'' if index == 0 else b'0\r\n\r\n')
            )
            await writer.drain()
            if index == 0:
                await disconnect.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            finished[index].set()

    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            response = await router.invoke(
                [],
                url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}',
                session=session,
            )
            received = []
            async for chunk in response.body_iterator:
                received.append(chunk)
                if chunk == b': keep-alive\n':
                    disconnect.set()
    finally:
        disconnect.set()
        server.close()
        await server.wait_closed()
        for done in finished[: len(received_bodies)]:
            await asyncio.wait_for(done.wait(), timeout=5)

    assert b''.join(received) == b': keep-alive\n\n' + ANSWER + DONE
    assert len(received_bodies) == 2 and received_bodies[0] == received_bodies[1]
    assert all(response.closed for response in router.responses)
