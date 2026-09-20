"""Real terminal router requests with isolated auth/storage and an upstream transport double."""

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import unquote, urlsplit

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

BACKEND = Path(__file__).parents[1] / 'open_webui'
SECRET = 'synthetic-terminal-link-secret'


def load(name, path, monkeypatch):
    spec = importlib.util.spec_from_file_location(name, BACKEND / path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class Content:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    async def read(self, size=-1):
        end = len(self.data) if size < 0 else self.offset + size
        chunk = self.data[self.offset : end]
        self.offset += len(chunk)
        return chunk

    async def iter_chunked(self, size):
        while chunk := await self.read(size):
            yield chunk

    def iter_any(self):
        return self.iter_chunked(65536)


class Upstream:
    def __init__(self, data=b'<html><script>bad()</script></html>', mime='text/html', status=200):
        self.status = status
        self.content = Content(data)
        self.headers = {
            'content-type': mime,
            'Set-Cookie': 'token=bad',
            'Refresh': '0;url=https://evil.test',
            'Content-Security-Policy': 'sandbox allow-scripts allow-same-origin',
        }
        self.released = False
        self.buffered = False

    async def read(self):
        self.buffered = True
        return await self.content.read()

    def release(self):
        self.released = True


@pytest.fixture
def api(monkeypatch):
    connection = {
        'id': 'terminal-1',
        'url': 'https://terminal.test',
        'key': 'terminal-secret',
        'server_type': 'orchestrator',
        'config': {'contexts': {'chat': {'context_id': 'chat_id'}}},
    }
    config = SimpleNamespace(get=AsyncMock(return_value=[connection]))
    chats = SimpleNamespace(get_chat_by_id_for_user=AsyncMock(return_value=object()))
    access = AsyncMock(return_value=True)

    async def verified(request: Request):
        identity = request.headers.get('authorization') or request.cookies.get('token')
        if not identity:
            raise HTTPException(401, 'Not authenticated')
        return SimpleNamespace(id='user-2' if identity == 'other' else 'user-1', role='user')

    for name, values in {
        'open_webui.config': {
            'TERMINAL_PROXY_HEADERS': {'Content-Type': 'text/html', 'content-security-policy': 'unsafe'}
        },
        'open_webui.env': {'AIOHTTP_CLIENT_SESSION_SSL': True, 'WEBUI_SECRET_KEY': SECRET},
        'open_webui.events': {'EVENTS': SimpleNamespace(), 'publish_event': AsyncMock()},
        'open_webui.models.config': {'Config': config},
        'open_webui.models.chats': {'Chats': chats},
        'open_webui.models.groups': {'Groups': SimpleNamespace(get_groups_by_member_id=AsyncMock(return_value=[]))},
        'open_webui.utils.access_control': {'has_connection_access': access},
        'open_webui.utils.auth': {'get_verified_user': verified},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.syspath_prepend(str(BACKEND.parent))
    for name in ('open_webui', 'open_webui.utils'):
        package = ModuleType(name)
        package.__path__ = [str(BACKEND if name == 'open_webui' else BACKEND / 'utils')]
        monkeypatch.setitem(sys.modules, name, package)
    codec = ModuleType('open_webui.utils.json_codec')
    codec.JSONCodec = json
    monkeypatch.setitem(sys.modules, codec.__name__, codec)
    headers_module = ModuleType('open_webui.utils.headers')
    headers_module.Any = object
    functions = [
        node
        for node in ast.parse((BACKEND / 'utils/headers.py').read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name in {'bearer_auth_header', 'normalize_bearer_token'}
    ]
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<header helpers>', 'exec'), headers_module.__dict__)
    monkeypatch.setitem(sys.modules, headers_module.__name__, headers_module)
    load('open_webui.utils.chat_id', 'utils/chat_id.py', monkeypatch)
    load('open_webui.utils.terminals', 'utils/terminals.py', monkeypatch)
    policy = load('open_webui.utils.terminal_files', 'utils/terminal_files.py', monkeypatch)
    router = load('terminal_router_test', 'routers/terminals.py', monkeypatch)
    state = SimpleNamespace(response=Upstream(), calls=[], closed=0)

    class Session:
        async def request(self, **kwargs):
            state.calls.append(kwargs)
            return state.response

        async def close(self):
            state.closed += 1

    monkeypatch.setattr(router.aiohttp, 'ClientSession', lambda **kwargs: Session())
    app = FastAPI()
    app.include_router(router.router, prefix='/api/v1/terminals')
    client = TestClient(app)

    def urls(path='/work/报告.svg', **kwargs):
        return policy.add_file_delivery_links(
            {'path': path, 'exists': True},
            server_id='terminal-1',
            owner_id='user-1',
            metadata=kwargs.get('metadata', {'chat_id': 'chat-1'}),
            context_id=kwargs.get('context_id', 'chat:chat-1'),
            secret=SECRET,
        )

    return SimpleNamespace(
        client=client,
        state=state,
        urls=urls,
        connection=connection,
        access=access,
        chats=chats,
        router=router,
        policy=policy,
        app=app,
    )


def test_download_is_authenticated_bound_and_streamed(api):
    url = api.urls()['download_url']
    assert api.client.get(url).status_code == 401
    assert api.client.get(url, headers={'Authorization': 'other'}).status_code == 403
    assert not api.state.calls
    response = api.client.get(url, headers={'Authorization': 'owner', 'X-Session-Id': 'attacker-chat'})
    assert response.status_code == 200
    assert response.content.startswith(b'<html>')
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert 'set-cookie' not in response.headers and 'refresh' not in response.headers
    assert 'allow-scripts' not in response.headers['content-security-policy']
    call = api.state.calls[0]
    assert call['headers']['X-User-Id'] == 'user-1'
    assert call['headers']['X-Session-Id'] == 'chat-1'
    assert call['headers']['X-Terminal-Context-Id'] == 'chat:chat-1'
    assert call['allow_redirects'] is False
    assert urlsplit(str(call['url'])).path == '/files/view'
    assert not api.state.response.buffered
    assert api.state.response.released and api.state.closed == 1


@pytest.mark.parametrize('change', ['terminal', 'signature', 'disabled', 'access', 'chat', 'context'])
def test_revoked_or_tampered_links_are_denied_before_upstream(api, change):
    url = api.urls()['download_url']
    if change == 'terminal':
        url = url.replace('/terminal-1/', '/terminal-2/')
    elif change == 'signature':
        url += 'x'
    elif change == 'disabled':
        api.connection['enabled'] = False
    elif change == 'access':
        api.access.return_value = False
    elif change == 'chat':
        api.chats.get_chat_by_id_for_user.return_value = None
    else:
        api.connection['config']['contexts']['chat'] = {'context_id': 'default'}
    assert api.client.get(url, headers={'Authorization': 'owner'}).status_code in (403, 404)
    assert not api.state.calls


def test_image_svg_keeps_security_headers_for_img_and_direct_navigation(api):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect width="20" height="20"/></svg>'
    api.state.response = Upstream(svg, 'text/html')
    api.client.cookies.set('token', 'owner')
    response = api.client.get(api.urls()['image_url'])
    assert response.status_code == 200 and response.content == svg
    assert response.headers['content-type'] == 'image/svg+xml'
    assert "script-src 'none'" in response.headers['content-security-policy']
    assert api.state.response.released and api.state.closed == 1


def test_spoofed_image_is_rejected(api):
    api.state.response.headers['content-type'] = 'image/png'
    response = api.client.get(api.urls('/a.png')['image_url'], headers={'Authorization': 'owner'})
    assert response.status_code == 415
    assert api.state.closed == 1


@pytest.mark.parametrize(
    'path,mime',
    [
        ('files/view?path=/a.html', 'text/html'),
        ('files/serve/a.js', 'application/javascript'),
        ('anything', 'application/xhtml+xml'),
    ],
)
def test_legacy_proxy_cannot_serve_active_content(api, path, mime):
    api.state.response.headers['content-type'] = mime
    response = api.client.get(
        '/api/v1/terminals/terminal-1/' + path, headers={'Authorization': 'owner', 'X-Session-Id': 'chat-1'}
    )
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_port_proxy_denied_even_encoded(api):
    for path in ['proxy/8000/', '%2570roxy/8000/']:
        response = api.client.get('/api/v1/terminals/terminal-1/' + path, headers={'Authorization': 'owner'})
        assert response.status_code == 403
    assert not api.state.calls


@pytest.mark.parametrize(
    'path',
    [
        'pro%09xy/8000/',
        'fi%0Dles/view',
        'fi%250Ales/view',
        'files/view%23ignored',
        'api/config%3fignored',
        '%00proxy/8000',
    ],
)
def test_proxy_rejects_url_normalization_bypasses(api, path):
    assert api.router._sanitize_proxy_path(unquote(path)) is None
    response = api.client.get('/api/v1/terminals/terminal-1/' + path, headers={'Authorization': 'owner'})
    # Starlette may reject a decoded newline before the route is matched.
    assert response.status_code in (400, 404)
    assert not api.state.calls


@pytest.mark.parametrize('status,expected', [(302, 502), (404, 404), (403, 403), (500, 502)])
def test_upstream_errors_do_not_forward_html_or_secrets(api, status, expected):
    api.state.response.status = status
    api.state.response.headers['Location'] = 'https://external.test/?secret=private'
    response = api.client.get(api.urls()['download_url'], headers={'Authorization': 'owner'})
    assert response.status_code == expected
    assert response.headers['content-type'].startswith('application/json')
    assert 'private' not in response.text and 'location' not in response.headers
    assert api.state.response.released and api.state.closed == 1


def test_proxy_display_enriches_real_file_metadata(api):
    api.state.response = Upstream(b'{"path":"/work/plot.svg","exists":true}', 'application/json')
    response = api.client.get(
        '/api/v1/terminals/terminal-1/files/display?path=plot.svg',
        headers={'Authorization': 'owner', 'X-Session-Id': 'chat-1'},
    )
    assert response.status_code == 200
    data = response.json()
    assert data['owner_id'] == 'user-1'
    assert '/files/image?ref=' in data['image_url']


def test_temporary_context_follows_terminal_policy(api):
    url = api.urls(metadata={'chat_id': 'temporary:session'}, context_id=None)['download_url']
    assert api.client.get(url, headers={'Authorization': 'owner'}).status_code == 409
    api.connection['config']['contexts']['chat'] = {'context_id': 'default'}
    assert api.client.get(url, headers={'Authorization': 'owner'}).status_code == 200


def test_control_api_without_chat_stays_available(api):
    api.state.response = Upstream(b'{"features":{"files":true}}', 'application/json')
    response = api.client.get('/api/v1/terminals/terminal-1/api/config', headers={'Authorization': 'owner'})
    assert response.status_code == 200
    assert response.json()['features']['files'] is True
    assert 'content-disposition' not in response.headers


def test_file_api_requires_saved_context_when_configured(api):
    response = api.client.get(
        '/api/v1/terminals/terminal-1/files/display?path=/a.svg', headers={'Authorization': 'owner'}
    )
    assert response.status_code == 409 and not api.state.calls


@pytest.mark.parametrize('path', ['files/view/', 'files/serve', 'files/archive/'])
def test_raw_json_file_is_always_an_attachment(api, path):
    api.state.response = Upstream(b'{"secret":"synthetic"}', 'application/json')
    response = api.client.get(
        '/api/v1/terminals/terminal-1/' + path, headers={'Authorization': 'owner', 'X-Session-Id': 'chat-1'}
    )
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/octet-stream'
    assert response.headers['content-disposition'].startswith('attachment;')


@pytest.mark.parametrize('size', [0, 5 * 1024 * 1024])
def test_empty_and_large_downloads_preserve_bytes_and_release_connection(api, size):
    data = b'x' * size
    api.state.response = Upstream(data, 'application/octet-stream')
    response = api.client.get(api.urls('/work/file.bin')['download_url'], headers={'Authorization': 'owner'})
    assert response.status_code == 200 and response.content == data
    assert not api.state.response.buffered and api.state.response.released
    assert api.state.closed == 1


def test_sse_is_preserved_with_inert_headers(api):
    api.state.response = Upstream(b'data: {"value":1}\n\n', 'text/event-stream')
    response = api.client.get('/api/v1/terminals/terminal-1/api/events', headers={'Authorization': 'owner'})
    assert response.status_code == 200 and response.text == 'data: {"value":1}\n\n'
    assert response.headers['content-type'] == 'text/event-stream'
    assert 'content-disposition' not in response.headers


@pytest.mark.asyncio
async def test_closing_stream_early_releases_connection(api):
    api.state.response = Upstream(b'x' * (3 * 65536), 'application/octet-stream')
    url = urlsplit(api.urls('/a.bin')['download_url'])
    request = Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': url.path,
            'query_string': url.query.encode(),
            'headers': [],
            'app': api.app,
        },
        receive=AsyncMock(return_value={'type': 'http.request', 'body': b''}),
    )
    response = await api.router._proxy_http(
        'terminal-1', 'files/view', request, SimpleNamespace(id='user-1'), reference=request.query_params['ref']
    )
    stream = response.body_iterator
    assert len(await anext(stream)) == 65536
    await stream.aclose()
    await response.background()
    assert api.state.response.content.offset == 65536
    assert api.state.response.released and api.state.closed == 1


@pytest.mark.parametrize('error,status', [(TimeoutError(), 504), (ConnectionError(), 502)])
def test_offline_terminal_returns_safe_error_and_closes_session(api, monkeypatch, error, status):
    session = SimpleNamespace(request=AsyncMock(side_effect=error), close=AsyncMock())
    monkeypatch.setattr(api.router.aiohttp, 'ClientSession', lambda **kwargs: session)
    response = api.client.get(api.urls()['download_url'], headers={'Authorization': 'owner'})
    assert response.status_code == status
    assert response.json()['detail'].startswith('Terminal')
    session.close.assert_awaited_once()


def test_image_size_limit_does_not_limit_download(api, monkeypatch):
    monkeypatch.setattr(api.router, 'MAX_IMAGE_BYTES', 16)
    data = b'x' * 32
    api.state.response = Upstream(data, 'image/svg+xml')
    response = api.client.get(api.urls()['image_url'], headers={'Authorization': 'owner'})
    assert response.status_code == 413 and api.state.response.released
    api.state.response = Upstream(data, 'image/svg+xml')
    response = api.client.get(api.urls()['download_url'], headers={'Authorization': 'owner'})
    assert response.status_code == 200 and response.content == data


@pytest.mark.parametrize('site', ['cross-site', 'same-site'])
def test_cross_origin_embedding_and_cors_reads_are_denied(api, site):
    # CORP alone does not cover CORS-enabled img/fetch requests when the app's
    # global CORS middleware allows credentials. Fetch Metadata closes that gap.
    for mode in ('cors', 'no-cors'):
        response = api.client.get(
            api.urls()['image_url'],
            headers={
                'Authorization': 'owner',
                'Sec-Fetch-Site': site,
                'Sec-Fetch-Mode': mode,
            },
        )
        assert response.status_code == 403
    assert not api.state.calls
    response = api.client.get(
        api.urls()['download_url'],
        headers={
            'Authorization': 'owner',
            'Sec-Fetch-Site': site,
            'Sec-Fetch-Mode': 'navigate',
        },
    )
    assert response.status_code == 200


def test_cross_origin_file_read_denied_without_fetch_metadata(api):
    api.app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True)
    api.client.cookies.set('token', 'owner')
    response = api.client.get(api.urls()['download_url'], headers={'Origin': 'http://other.example.test'})
    assert response.status_code == 403 and not api.state.calls
    response = api.client.get(api.urls()['download_url'], headers={'Origin': 'http://testserver'})
    assert response.status_code == 200


def test_signed_automation_uses_original_context(api):
    api.connection['config']['contexts']['automation'] = {'context_id': 'automation_id'}
    url = api.urls(metadata={'chat_id': 'chat-1', 'automation_id': 'job-1'}, context_id='automation:job-1')[
        'download_url'
    ]
    response = api.client.get(url, headers={'Authorization': 'owner', 'X-Session-Id': 'other-chat'})
    assert response.status_code == 200
    assert api.state.calls[0]['headers']['X-Terminal-Context-Id'] == 'automation:job-1'
    api.connection['config']['contexts']['automation'] = False
    assert api.client.get(url, headers={'Authorization': 'owner'}).status_code == 403


def test_session_terminal_auth_can_use_cookie_authenticated_image(api):
    api.connection['auth_type'] = 'session'
    api.client.cookies.set('token', 'synthetic-owner-token')
    response = api.client.get(api.urls()['download_url'])
    assert response.status_code == 200
    assert api.state.calls[0]['headers']['Authorization'] == 'Bearer synthetic-owner-token'
    assert 'synthetic-owner-token' not in api.urls()['download_url']


def test_oauth_terminal_auth_is_resolved_on_each_access(api):
    api.connection['auth_type'] = 'system_oauth'
    api.client.cookies.set('token', 'owner')
    api.client.cookies.set('oauth_session_id', 'synthetic-session')
    resolver = AsyncMock(return_value={'access_token': 'synthetic-upstream-token'})
    api.app.state.oauth_manager = SimpleNamespace(get_oauth_token=resolver)
    response = api.client.get(api.urls()['download_url'])
    assert response.status_code == 200
    resolver.assert_awaited_once_with('user-1', 'synthetic-session')
    assert api.state.calls[0]['headers']['Authorization'] == 'Bearer synthetic-upstream-token'


@pytest.mark.asyncio
async def test_upstream_stream_failure_releases_connection(api):
    async def broken_stream(size):
        yield b'first bytes'
        raise api.router.aiohttp.ClientPayloadError('synthetic interruption')

    api.state.response.content.iter_chunked = broken_stream
    url = urlsplit(api.urls('/a.bin')['download_url'])
    request = Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': url.path,
            'query_string': url.query.encode(),
            'headers': [],
            'app': api.app,
        },
        receive=AsyncMock(return_value={'type': 'http.request', 'body': b''}),
    )
    response = await api.router._proxy_http(
        'terminal-1', 'files/view', request, SimpleNamespace(id='user-1'), reference=request.query_params['ref']
    )
    assert await anext(response.body_iterator) == b'first bytes'
    with pytest.raises(api.router.aiohttp.ClientPayloadError):
        await anext(response.body_iterator)
    assert api.state.response.released and api.state.closed == 1
