"""Preview revocation covers the complete request and socket establishment."""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest

from test_v0133_preview_routes import (
    PORT,
    TERMINAL,
    UPSTREAM_PREFIX,
    BrowserSocket,
    api,
    asgi_scope,
    issue,
    preview_api,
    preview_database,
    proxy_resource,
    request,
)


@pytest.mark.asyncio
async def test_revocation_cancels_upstream_request_waiting_for_headers(preview_api, monkeypatch):
    opened = (await issue(preview_api)).json()['url']
    original = preview_api.preview.get_terminal_client
    upstream_started, upstream_cancelled = asyncio.Event(), asyncio.Event()

    class Session(original):
        def request(self, method, url, **options):
            if UPSTREAM_PREFIX not in url:
                return super().request(method, url, **options)

            async def pending():
                upstream_started.set()
                try:
                    await asyncio.Future()
                finally:
                    upstream_cancelled.set()

            return pending()

    monkeypatch.setattr(preview_api.preview, 'get_terminal_client', Session)
    task = asyncio.create_task(request(preview_api, 'GET', opened))
    try:
        await asyncio.wait_for(upstream_started.wait(), 2)
        preview_api.login_tokens.remove('alice-login-1')
        done, _ = await asyncio.wait({task}, timeout=5)
        assert task in done, 'Revocation left the upstream response waiting for headers'
        assert task.result().status_code == 403
        assert upstream_cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_port_visibility_wait_cannot_authorize_a_revoked_login(preview_api, monkeypatch):
    opened = (await issue(preview_api)).json()['url']
    original = preview_api.preview.get_terminal_client
    checking_port, finish = asyncio.Event(), asyncio.Event()

    class Session(original):
        @asynccontextmanager
        async def check_port(self, method, url, options):
            checking_port.set()
            await finish.wait()
            async with super().request(method, url, **options) as response:
                yield response

        def request(self, method, url, **options):
            if url.endswith('/ports'):
                return self.check_port(method, url, options)
            return super().request(method, url, **options)

    monkeypatch.setattr(preview_api.preview, 'get_terminal_client', Session)
    calls = len(preview_api.calls)
    task = asyncio.create_task(request(preview_api, 'GET', opened))
    try:
        await asyncio.wait_for(checking_port.wait(), 2)
        preview_api.login_tokens.remove('alice-login-1')
        finish.set()
        assert (await asyncio.wait_for(task, 5)).status_code == 403
        assert len(preview_api.calls) == calls
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('redirect', [False, True])
async def test_revoked_binary_or_redirect_response_never_sends_upstream_headers(preview_api, monkeypatch, redirect):
    opened = (await issue(preview_api)).json()['url']
    original = preview_api.preview.get_terminal_client
    resource = (
        proxy_resource(b'', 'application/octet-stream', status=302, Location=UPSTREAM_PREFIX + '/runtime.js')
        if redirect
        else proxy_resource(b'binary content', 'application/octet-stream')
    )

    class Session(original):
        def request(self, method, url, **options):
            if UPSTREAM_PREFIX not in url:
                return super().request(method, url, **options)

            async def reply():
                preview_api.login_tokens.remove('alice-login-1')
                return resource

            return reply()

    monkeypatch.setattr(preview_api.preview, 'get_terminal_client', Session)
    denied = await request(preview_api, 'GET', opened)
    assert denied.status_code == 403
    assert 'location' not in denied.headers and b'binary content' not in denied.content
    assert resource.released


async def open_socket(preview_api):
    url = (await issue(preview_api)).json()['url'].split('/frame/')[0] + '/ws'
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    scope = asgi_scope(url, 'websocket', [(b'origin', b'null')])
    await incoming.put({'type': 'websocket.connect'})
    task = asyncio.create_task(preview_api.app(scope, incoming.get, outgoing.put))
    assert (await asyncio.wait_for(outgoing.get(), 2))['type'] == 'websocket.accept'
    return task, incoming, outgoing


@pytest.mark.asyncio
@pytest.mark.parametrize('send_target', [False, True])
async def test_websocket_revoked_while_waiting_for_opening_never_connects(preview_api, send_target):
    preview_api.socket = BrowserSocket()
    task, incoming, outgoing = await open_socket(preview_api)
    try:
        preview_api.login_tokens.remove('alice-login-1')
        if send_target:
            await incoming.put({'type': 'websocket.receive', 'text': json.dumps({'url': 'ws://localhost:3100/hmr'})})
        closed = await asyncio.wait_for(outgoing.get(), 5)
        assert closed == {'type': 'websocket.close', 'code': 1008, 'reason': ''}
        assert not preview_api.socket_calls and preview_api.socket.sent.empty()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_handshake', [False, True])
async def test_websocket_never_forwards_target_after_revocation_during_handshake(
    preview_api, monkeypatch, finish_handshake
):
    original = preview_api.preview.get_terminal_client
    handshake, finish, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    preview_api.socket = BrowserSocket()

    class Session(original):
        @asynccontextmanager
        async def ws_connect(self, url, **options):
            handshake.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            async with super().ws_connect(url, **options) as socket:
                yield socket

    monkeypatch.setattr(preview_api.preview, 'get_terminal_client', Session)
    task, incoming, outgoing = await open_socket(preview_api)
    try:
        await incoming.put({'type': 'websocket.receive', 'text': json.dumps({'url': 'ws://localhost:3100/hmr'})})
        await asyncio.wait_for(handshake.wait(), 2)
        preview_api.login_tokens.remove('alice-login-1')
        if finish_handshake:
            finish.set()
        assert (await asyncio.wait_for(outgoing.get(), 5))['code'] == 1008
        assert preview_api.socket.sent.empty()
        assert preview_api.socket.closed if finish_handshake else cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_port_preview_can_reuse_visible_browser_without_allocating_a_hidden_session(preview_api):
    data = {**PORT, 'session_id': 'browser-1'}
    opened = await issue(preview_api, data)
    assert opened.status_code == 200
    url = opened.json()['url']
    assert url.endswith('/app')
    renewed = await issue(preview_api, {**data, 'previous_url': url})
    assert renewed.status_code == 200 and renewed.json()['url'] == url
    assert [(method, target) for method, target, *_ in preview_api.calls] == [
        ('GET', TERMINAL + '/browser/sessions/browser-1'),
        ('GET', TERMINAL + '/browser/sessions/browser-1'),
    ]
    count = len(preview_api.calls)
    wrong = await issue(preview_api, {**data, 'session_id': 'another', 'previous_url': url})
    assert wrong.status_code == 403 and len(preview_api.calls) == count
