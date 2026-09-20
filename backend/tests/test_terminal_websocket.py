"""Exercise the real proxy against Open Terminal's HTTP/WebSocket ownership contract."""

import asyncio
import sys
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp import ClientSession, WSMsgType, web

from test_terminal_file_routes import api  # noqa: F401


@pytest_asyncio.fixture
async def terminal_ws(api, monkeypatch):
    # Keep auth/storage isolated, but use real HTTP and WebSocket transports.
    monkeypatch.setattr(api.router.aiohttp, 'ClientSession', ClientSession)
    monkeypatch.setattr(
        api.router,
        'EVENTS',
        SimpleNamespace(TERMINAL_SESSION_OPENED='opened', TERMINAL_SESSION_CLOSED='closed'),
    )

    async def verified_token(token, redis=None):
        if token in {'owner', 'other'}:
            return SimpleNamespace(id='user-1' if token == 'owner' else 'user-2', role='user')
        return None

    monkeypatch.setattr(
        sys.modules['open_webui.utils.auth'], 'get_verified_user_by_token', verified_token, raising=False
    )
    state = SimpleNamespace(sessions={}, requests=[], rejected=[], frames=[])

    async def create(request):
        if request.headers.get('Authorization') != 'Bearer terminal-secret':
            raise web.HTTPUnauthorized()
        session_id = f'shell-{len(state.sessions)}'
        state.sessions[session_id] = (
            request.headers.get('X-User-Id', ''),
            request.headers.get('X-Session-Id', ''),
        )
        state.requests.append(request)
        return web.json_response({'id': session_id, 'created_at': '2026-01-01T00:00:00Z', 'pid': 123})

    async def attach(request):
        state.requests.append(request)
        socket = web.WebSocketResponse(receive_timeout=5)
        await socket.prepare(request)
        auth = await socket.receive_json()
        if auth.get('type') != 'auth' or auth.get('token') != 'terminal-secret':
            await socket.close(code=4001, message=b'Invalid API key')
            return socket
        # Open Terminal binds sessions to these headers when POST creates them.
        # The user_id query parameter is an orchestrator routing hint, not ownership.
        owner = (
            request.headers.get('X-User-Id', ''),
            request.headers.get('X-Session-Id', auth.get('chat_id', '')),
        )
        if state.sessions.get(request.match_info['session_id']) != owner:
            state.rejected.append(owner)
            await socket.close(code=4004, message=b'Session not found')
            return socket
        await socket.send_bytes(b'$ ')
        async for message in socket:
            state.frames.append(message)
            if message.type == WSMsgType.BINARY:
                await socket.send_bytes(message.data)
            elif message.type == WSMsgType.TEXT:
                await socket.send_str(message.data)
        return socket

    upstream = web.Application()
    for prefix in ('', '/p/policy-1'):
        upstream.router.add_post(f'{prefix}/api/terminals', create)
        upstream.router.add_get(f'{prefix}/api/terminals/{{session_id}}', attach)
    runner = web.AppRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    port = runner.addresses[0][1]
    api.connection['url'] = f'http://127.0.0.1:{port}'
    api.connection.pop('server_type')
    api.connection.pop('config')
    try:
        yield SimpleNamespace(api=api, state=state)
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('chat_id', ['', 'chat-1'])
@pytest.mark.parametrize('orchestrator', [False, True])
async def test_created_terminal_can_attach_and_forward_frames(terminal_ws, chat_id, orchestrator):
    api, state = terminal_ws.api, terminal_ws.state
    if orchestrator:
        api.connection.update(
            server_type='orchestrator',
            policy_id='policy-1',
            config={'contexts': {'chat': {'context_id': 'chat_id' if chat_id else 'default'}}},
        )

    def exercise():
        headers = {'Authorization': 'owner', 'X-User-Id': 'spoofed-user'}
        if chat_id:
            headers['X-Session-Id'] = chat_id
        response = api.client.post('/api/v1/terminals/terminal-1/api/terminals', headers=headers)
        assert response.status_code == 200
        session_id = response.json()['id']
        with api.client.websocket_connect(
            f'/api/v1/terminals/terminal-1/api/terminals/{session_id}',
            headers={'X-User-Id': 'spoofed-user', 'X-Session-Id': 'spoofed-chat'},
        ) as socket:
            socket.send_json({'type': 'auth', 'token': 'owner', 'chat_id': chat_id})
            prompt = socket.receive()
            assert prompt.get('bytes') == b'$ ', (prompt, state.rejected)
            socket.send_bytes(b'printf hello\r')
            assert socket.receive_bytes() == b'printf hello\r'
            resize = {'type': 'resize', 'cols': 120, 'rows': 40}
            socket.send_json(resize)
            assert socket.receive_json() == resize
            socket.send_json({'type': 'ping'})
            assert socket.receive_json() == {'type': 'ping'}

    await asyncio.to_thread(exercise)
    assert not state.rejected
    assert len(state.requests) == 2
    created, attached = state.requests
    assert created.headers['X-User-Id'] == attached.headers['X-User-Id'] == 'user-1'
    assert created.headers.get('X-Session-Id', '') == attached.headers.get('X-Session-Id', '') == chat_id
    expected_context = f'chat:{chat_id}' if orchestrator and chat_id else None
    assert created.headers.get('X-Terminal-Context-Id') == expected_context
    assert attached.headers.get('X-Terminal-Context-Id') == expected_context
    assert attached.query == {'user_id': 'user-1'}


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['user', 'chat'])
async def test_websocket_cannot_attach_to_another_owner(terminal_ws, change):
    api, state = terminal_ws.api, terminal_ws.state

    def exercise():
        response = api.client.post(
            '/api/v1/terminals/terminal-1/api/terminals',
            headers={'Authorization': 'owner', 'X-Session-Id': 'chat-1'},
        )
        assert response.status_code == 200
        session_id = response.json()['id']
        with api.client.websocket_connect(
            f'/api/v1/terminals/terminal-1/api/terminals/{session_id}',
            headers={'X-User-Id': 'user-1', 'X-Session-Id': 'chat-1'},
        ) as socket:
            socket.send_json(
                {
                    'type': 'auth',
                    'token': 'other' if change == 'user' else 'owner',
                    'chat_id': 'chat-2' if change == 'chat' else 'chat-1',
                }
            )
            assert socket.receive()['type'] == 'websocket.close'

    await asyncio.to_thread(exercise)
    assert len(state.rejected) == 1
