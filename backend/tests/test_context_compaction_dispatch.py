import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from open_webui.utils.context_compaction_dispatch import run_with_context_retry


def request(mode='v2'):
    return SimpleNamespace(state=SimpleNamespace(context_compaction={'mode': mode}))


@pytest.mark.asyncio
async def test_http_context_limit_retries_once_with_force_without_tools():
    req = request()
    forced = []

    async def send():
        forced.append(req.state.context_compaction.get('force', False))
        if len(forced) == 1:
            return JSONResponse({'error': {'code': 'context_length_exceeded'}}, status_code=400)
        return {'choices': [{'message': {'content': 'ok'}}]}

    result = await run_with_context_retry(req, send)
    assert result['choices'][0]['message']['content'] == 'ok'
    assert forced == [False, True]
    assert not req.state.context_compaction.get('force')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mode,error',
    [('off', 'context_length_exceeded'), ('legacy', 'context_length_exceeded'), ('v2', 'rate_limit_exceeded')],
)
async def test_only_v2_explicit_context_errors_retry(mode, error):
    send = AsyncMock(return_value=JSONResponse({'error': {'code': error}}, status_code=400))
    await run_with_context_retry(request(mode), send)
    assert send.await_count == 1


@pytest.mark.asyncio
async def test_retry_is_bounded_even_if_second_send_overflows():
    send = AsyncMock(return_value=JSONResponse({'error': {'code': 'context_length_exceeded'}}, status_code=400))
    await run_with_context_retry(request(), send)
    assert send.await_count == 2


@pytest.mark.asyncio
async def test_streaming_context_error_before_content_retries_and_closes_responses():
    cleanup = AsyncMock()

    async def failed():
        yield 'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        yield 'data: {"error":{"code":"context_length_'
        yield 'exceeded"}}\n\n'

    async def success():
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'

    send = AsyncMock(
        side_effect=[StreamingResponse(failed(), background=BackgroundTask(cleanup)), StreamingResponse(success())]
    )
    response = await run_with_context_retry(request(), send)
    output = ''.join([str(x) async for x in response.body_iterator])
    assert 'ok' in output and 'context_length' not in output
    assert send.await_count == 2
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_retry_after_content_or_tool_delta_is_delivered():
    for delta in ('{"content":"already visible"}', '{"tool_calls":[{"id":"call_1"}]}'):

        async def stream():
            yield f'data: {{"choices":[{{"delta":{delta}}}]}}\n\n'
            yield 'data: {"error":{"code":"context_length_exceeded"}}\n\n'

        send = AsyncMock(return_value=StreamingResponse(stream()))
        response = await run_with_context_retry(request(), send)
        assert len([x async for x in response.body_iterator]) == 2
        assert send.await_count == 1
