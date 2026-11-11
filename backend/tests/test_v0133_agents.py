"""A local carrier closing is never proof that a remote agent stopped."""

import ast
import asyncio
import contextlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from fastapi import HTTPException


def transport():
    source = Path(__file__).parents[1] / 'open_webui/utils/agents/transport.py'
    nodes = [node for node in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(node, ast.ClassDef)]
    ns = {
        'aiohttp': aiohttp,
        'HTTPException': HTTPException,
        'asyncio': asyncio,
        'contextlib': contextlib,
        'json': json,
        'time': time,
        'AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL': False,
        'get_terminal_server_url': lambda connection: connection['url'],
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), ns)
    value = ns['TerminalTransport']({'url': 'http://synthetic-terminal'}, {}, {}, 'actor')
    value.session_id = 'remote-session'
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', [TimeoutError(), ConnectionError(), ValueError('404'), {'status': False}, {}])
async def test_remote_close_failure_is_not_completion(outcome):
    value = transport()
    value.request = AsyncMock(side_effect=outcome if isinstance(outcome, Exception) else None, return_value=outcome)
    with pytest.raises(Exception):
        await value.close()
    assert not value.completion_confirmed


@pytest.mark.asyncio
async def test_successful_delete_is_executor_confirmation_and_repeated_close_is_idempotent():
    value = transport()
    value.request = AsyncMock(return_value={'status': True})
    assert await value.close() is True
    assert value.completion_confirmed
    assert await value.close() is True
    value.request.assert_awaited_once_with('DELETE', '/agents/sessions/remote-session')


@pytest.mark.asyncio
async def test_start_with_lost_response_keeps_unknown_execution_state():
    value = transport()
    value.session_id = None
    value.request = AsyncMock(side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        await value.start('codex', {'request_id': 'reply-1'})
    with pytest.raises(Exception):
        await value.close()
    assert not value.completion_confirmed


def agent_socket(monkeypatch, value, raw, on_ack=lambda: None, *, sequence=1):
    sent = []
    acknowledged = asyncio.Event()

    async def receive():
        yield SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=json.dumps({'type': 'output', 'sequence': sequence, 'data': raw}),
        )
        await asyncio.Future()

    async def send(encoded):
        frame = json.loads(encoded)
        sent.append(frame)
        if frame['type'] == 'ack':
            on_ack()
            acknowledged.set()

    @contextlib.asynccontextmanager
    async def ws_connect(*args, **kwargs):
        class Socket:
            send_str = staticmethod(send)

            def __aiter__(self):
                return receive()

        yield Socket()

    @contextlib.asynccontextmanager
    async def terminal_client(*args, **kwargs):
        yield SimpleNamespace(ws_connect=ws_connect)

    monkeypatch.setitem(value._pump.__globals__, 'get_terminal_client', terminal_client)
    return sent, acknowledged


@pytest.mark.asyncio
@pytest.mark.parametrize('secondary_queue', [False, True])
async def test_acknowledged_output_survives_worker_loss_before_provider_consumption(monkeypatch, secondary_queue):
    value = transport()
    raw = '  {"method": "item/agentMessage/delta", "params": {"delta": "raw \u2603"}}  \r\n'
    persisted_at_ack = []
    persisted = []
    _, acknowledged = agent_socket(monkeypatch, value, raw, lambda: persisted_at_ack.extend(persisted))

    async def journal(sequence, data):
        persisted.append((sequence, data))
        return True

    value.output_journal = journal
    reader = None
    if secondary_queue:
        source = Path(__file__).parents[1] / 'open_webui/utils/agents/codex.py'
        node = next(
            node for node in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(node, ast.ClassDef)
        )
        ns = {'asyncio': asyncio}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), ns)
        client = ns['CodexAppServer'](value)
        available = asyncio.Event()
        enqueue = client.events.put

        async def track_secondary_queue(message):
            await enqueue(message)
            available.set()

        client.events.put = track_secondary_queue
        reader = asyncio.create_task(client._read())
    value.task = asyncio.create_task(value._pump())
    try:
        await asyncio.wait_for(acknowledged.wait(), 1)
        if secondary_queue:
            await asyncio.wait_for(available.wait(), 1)
            assert client.events.qsize() == 1
        else:
            assert value.messages.qsize() == 1
    finally:
        value.task.cancel()
        if reader:
            reader.cancel()
        await asyncio.gather(*(task for task in (value.task, reader) if task), return_exceptions=True)
    assert persisted_at_ack == [(1, raw)]


@pytest.mark.asyncio
async def test_redirect_response_cannot_confirm_remote_delete(monkeypatch):
    value = transport()
    calls = []

    @contextlib.asynccontextmanager
    async def request(method, url, **kwargs):
        calls.append((method, kwargs['allow_redirects']))
        yield SimpleNamespace(status=307, json=AsyncMock(return_value={'status': True}))

    @contextlib.asynccontextmanager
    async def terminal_client(*args, **kwargs):
        yield SimpleNamespace(request=request)

    monkeypatch.setitem(value.request.__globals__, 'get_terminal_client', terminal_client)
    with pytest.raises(Exception):
        await value.close()
    assert calls == [('DELETE', False)]
    assert value.completion_confirmed is False


@pytest.mark.asyncio
async def test_force_stop_drain_uses_durable_cursor_and_sends_no_agent_input(monkeypatch):
    value = transport()
    raw = ' {"delta":"retained after worker loss"} \n'
    sent, _ = agent_socket(monkeypatch, value, raw, sequence=4)
    journal = AsyncMock(return_value='stored')

    assert await value.drain_available_output(journal, 3) is True

    assert sent == [{'type': 'auth', 'token': '', 'cursor': 3}, {'type': 'ack', 'sequence': 4}]
    journal.assert_awaited_once_with(4, raw)
    assert value.cursor == 4
    assert value.messages.empty()


@pytest.mark.asyncio
async def test_force_stop_drain_defers_if_original_peer_is_connected(monkeypatch):
    value = transport()

    class BusySocket:
        close_code = 4409
        send_str = AsyncMock()

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    @contextlib.asynccontextmanager
    async def ws_connect(*args, **kwargs):
        yield BusySocket()

    @contextlib.asynccontextmanager
    async def terminal_client(*args, **kwargs):
        yield SimpleNamespace(ws_connect=ws_connect)

    monkeypatch.setitem(value._pump.__globals__, 'get_terminal_client', terminal_client)
    journal = AsyncMock()
    assert await value.drain_available_output(journal, 0) is False
    journal.assert_not_awaited()
