"""Preview capabilities and the isolated Host boundary, without application imports."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit, unquote
import json

import pytest
from fastapi import HTTPException

from open_webui.utils.terminal_preview import PreviewHostMiddleware, TicketStore, validate_preview_base_url
from test_auth_revocation import load_functions


@pytest.mark.parametrize(
    'base',
    [
        'https://webui.test:9443',
        'http://preview.test',
        'https://a:b@preview.test',
        'https://preview.test/path',
        'https://preview.test/?secret=1',
    ],
)
def test_invalid_or_same_hostname_configuration_is_rejected(base):
    with pytest.raises(ValueError):
        validate_preview_base_url(base, 'https://webui.test')


def test_https_and_explicit_separate_local_development_hosts():
    assert validate_preview_base_url('', 'https://webui.test') == ''
    assert validate_preview_base_url('https://preview.test/', 'https://webui.test') == 'https://preview.test'
    assert (
        validate_preview_base_url('http://preview.localhost:8080', 'http://app.localhost:8080')
        == 'http://preview.localhost:8080'
    )
    with pytest.raises(ValueError):
        validate_preview_base_url('http://localhost:9090', 'http://localhost:8080')


def record():
    return {
        'user_id': 'alice',
        'login_session': 'login-1',
        'server_id': 'terminal',
        'kind': 'browser',
        'session_id': 'browser-1',
        'chat_id': 'chat-1',
        'temporary_context': None,
        'base_url': 'https://preview.test',
        'auth_token': 'synthetic-secret',
        'upstream_token': 'remote-secret',
    }


@pytest.mark.asyncio
async def test_ticket_is_opaque_and_expiry_is_authoritative():
    clock = [1000.0]
    store = TicketStore(clock=lambda: clock[0])
    token, saved = await store.issue(record())
    assert len(token) >= 40 and 'secret' not in token and saved['expires_at'] == 1300
    assert (await store.get(token))['chat_id'] == 'chat-1'
    clock[0] = 1300
    with pytest.raises(HTTPException) as caught:
        await store.get(token)
    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_renewal_preserves_url_but_cannot_change_actor_session_target_or_context():
    store = TicketStore()
    token, _ = await store.issue(record())
    renewed, _ = await store.issue(record(), previous_token=token)
    assert renewed == token
    for field, value in [
        ('user_id', 'bob'),
        ('login_session', 'login-2'),
        ('server_id', 'other'),
        ('session_id', 'browser-2'),
        ('chat_id', 'other-chat'),
        ('temporary_context', {'id': 'new', 'epoch': 'new'}),
    ]:
        with pytest.raises(HTTPException) as caught:
            await store.issue({**record(), field: value}, previous_token=token)
        assert caught.value.status_code == 403
    assert (await store.get(token))['user_id'] == 'alice'


@pytest.mark.asyncio
async def test_configured_redis_failure_and_multiworker_memory_fail_closed():
    for store in [
        TicketStore(redis_required=True),
        TicketStore(workers=2),
        TicketStore(redis=SimpleNamespace(get=AsyncMock(side_effect=ConnectionError('unavailable')))),
    ]:
        with pytest.raises(HTTPException) as caught:
            await store.get('a' * 43)
        assert caught.value.status_code == 503
        assert caught.value.headers['Retry-After']


async def call_host(host, path, *, origin=None, cookie=None, websocket=False, config=None):
    main, preview = AsyncMock(), AsyncMock()

    async def settings():
        return config or {'terminal.preview.base_url': 'https://preview.test', 'webui.url': 'https://main.test'}

    app = PreviewHostMiddleware(main, settings=settings, preview_app=preview)
    headers = [(b'host', host.encode())]
    if origin:
        headers.append((b'origin', origin.encode()))
    if cookie:
        headers.append((b'cookie', cookie.encode()))
    scope = {
        'type': 'websocket' if websocket else 'http',
        'method': 'GET',
        'headers': headers,
        'path': path,
        'raw_path': path.encode(),
        'query_string': b'',
        'scheme': 'https',
        'server': (host, 443),
    }
    send = AsyncMock()
    await app(scope, AsyncMock(), send)
    return main, preview, send, scope


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/v1/auths/', '/api/config', '/ws/socket.io', '/static/app.js', '/'])
async def test_preview_host_never_reaches_main_app(path):
    main, preview, send, _ = await call_host('preview.test', path, cookie='token=synthetic')
    main.assert_not_awaited()
    preview.assert_not_awaited()
    assert send.await_args_list[0].args[0]['status'] == 404


@pytest.mark.asyncio
async def test_capability_route_strips_browser_credentials_and_redacts_access_log_scope():
    main, preview, _, scope = await call_host('preview.test', '/p/' + 'a' * 43 + '/runtime.js', cookie='token=secret')
    main.assert_not_awaited()
    preview.assert_awaited_once()
    forwarded = preview.await_args.args[0]
    assert all(k.lower() not in {b'cookie', b'authorization'} for k, _ in forwarded['headers'])
    assert 'a' * 43 not in scope['path'] and scope['query_string'] == b''


@pytest.mark.asyncio
@pytest.mark.parametrize('origin', ['https://preview.test', 'null'])
async def test_preview_origin_cannot_make_credentialed_main_requests(origin):
    main, _, send, _ = await call_host('main.test', '/api/config', origin=origin, cookie='token=secret')
    main.assert_not_awaited()
    assert send.await_args_list[0].args[0]['status'] == 403


@pytest.mark.asyncio
async def test_main_origin_still_routes_normally():
    main, preview, _, _ = await call_host('main.test', '/api/config', origin='https://main.test', cookie='token=secret')
    main.assert_awaited_once()
    preview.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('base', ['', 'https://new-preview.test'])
async def test_retired_preview_hostname_and_origin_stay_isolated(base):
    settings = {
        'terminal.preview.base_url': base,
        'webui.url': 'https://main.test',
        'isolated_hosts': {'preview.test'},
    }
    main, preview, send, _ = await call_host('preview.test', '/api/config', config=settings)
    main.assert_not_awaited()
    preview.assert_not_awaited()
    assert send.await_args_list[0].args[0]['status'] == 404

    main, preview, send, _ = await call_host(
        'main.test', '/api/config', origin='https://preview.test:443', config=settings
    )
    main.assert_not_awaited()
    preview.assert_not_awaited()
    assert send.await_args_list[0].args[0]['status'] == 403


def preview_functions():
    ns = {'HTTPException': HTTPException, 'urlsplit': urlsplit, 'unquote': unquote, 'json': json}
    return load_functions(
        'routers/terminal_preview.py', {'target_path', 'target_port', 'response_headers', 'harden_browser_runtime'}, ns
    )


@pytest.mark.parametrize(
    'path',
    [
        'frame/http/localhost%3A9999/app',
        'frame/https/localhost%3A3000/app',
        'frame/http/elsewhere.test/app',
        'frame/http/localhost%3A3000/../../api',
        'frame/http/localhost%3A3000/%252e%252e/api',
        'stream',
        'vnc',
    ],
)
def test_port_capability_cannot_select_another_service_or_escape_proxy(path):
    with pytest.raises(HTTPException):
        preview_functions()['target_path']({'kind': 'port', 'port': 3000}, path)


def test_preview_response_keeps_sandbox_even_outside_iframe():
    headers = preview_functions()['response_headers']({**record(), 'parent_origin': 'https://main.test'}, 'text/html')
    assert 'allow-same-origin' not in headers['Content-Security-Policy']
    assert 'allow-top-navigation' not in headers['Content-Security-Policy']
    assert 'sandbox allow-scripts allow-forms' in headers['Content-Security-Policy']
    assert headers['Referrer-Policy'] == 'no-referrer' and headers['Cache-Control'] == 'no-store'
    assert not any(key.lower() in {'set-cookie', 'access-control-allow-credentials'} for key in headers)


def test_runtime_bridge_binds_both_directions_and_rejects_unknown_protocol():
    harden = preview_functions()['harden_browser_runtime']
    runtime = b"const config = window.__terminalBrowser; parent.postMessage({type:'terminal-browser-state'}, '*'); if(event.source !== parent || event.data?.type !== 'terminal-browser-command') return;"
    result = harden(runtime, {**record(), 'parent_origin': 'https://main.test'}, 'a' * 43).decode()
    assert 'event.origin !== preview.parent_origin' in result
    assert 'event.data?.preview_ticket !== preview.ticket' in result
    assert 'preview_ticket: preview.ticket' in result
    with pytest.raises(HTTPException):
        harden(b'unknown browser runtime', record(), 'a' * 43)
