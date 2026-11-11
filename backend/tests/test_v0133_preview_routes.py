"""Exercise independent terminal preview at the FastAPI and ASGI boundaries."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from multidict import CIMultiDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from open_webui.models import config as config_model
from open_webui.utils.terminal_preview import PreviewHostMiddleware, TicketStore
from test_terminal_file_routes import Upstream, api, load
from test_web_tool_citations import load_functions


AIOHTTP_CLIENT_SESSION = aiohttp.ClientSession
MAIN = 'https://main.test'
PREVIEW = 'https://preview.test'
TERMINAL = 'https://terminal.test'
TICKET_ROUTE = '/api/v1/terminals/terminal-1/preview-tickets'
UPSTREAM_TOKEN = 'synthetic-upstream-capability'
UPSTREAM_PREFIX = '/browser/view/browser-1/' + UPSTREAM_TOKEN
ALICE = {'Authorization': 'Bearer alice-login-1'}
PORT = {'kind': 'port', 'port': 3100, 'path': '/app', 'chat_id': 'chat-1'}


def proxy_resource(content=b'preview content', mime='text/html', status=200, **headers):
    response = Upstream(data=content, mime=mime, status=status)
    response.headers = CIMultiDict({**response.headers, **headers})
    return response


class PendingResponse:
    def __init__(self, response):
        self.response = response

    def __await__(self):
        async def receive():
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

        return receive().__await__()

    async def __aenter__(self):
        return await self

    async def __aexit__(self, *_):
        self.response.release()


class BrowserSocket:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.sent = asyncio.Queue()
        self.closed = False

    async def send_str(self, value):
        await self.sent.put(value)

    async def send_bytes(self, value):
        await self.sent.put(value)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.messages.get()


@pytest.fixture
def event_loop_policy():
    if sys.platform == 'win32':
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture
async def preview_database(tmp_path, monkeypatch, request):
    schema = None
    if getattr(request, 'param', 'sqlite') == 'postgresql':
        url = os.getenv('TEST_POSTGRES_URL')
        if not url:
            pytest.skip('Set TEST_POSTGRES_URL to exercise preview state on PostgreSQL')
        schema = 'v0133_preview_' + uuid4().hex
        engine = create_async_engine(url, execution_options={'schema_translate_map': {None: schema}})
    else:
        engine = create_async_engine(f'sqlite+aiosqlite:///{(tmp_path / "preview.db").as_posix()}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def database():
        async with sessions() as session:
            yield session

    monkeypatch.setattr(config_model, 'get_async_db', database)
    try:
        async with engine.begin() as connection:
            if schema:
                await connection.execute(text(f'CREATE SCHEMA {schema}'))
            await connection.run_sync(config_model.Config.__table__.create)
        yield config_model.Config
    finally:
        if schema:
            async with engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS {schema} CASCADE'))
        await engine.dispose()


@pytest.fixture
def preview_api(api, monkeypatch, preview_database):
    settings = {'terminal.preview.base_url': PREVIEW, 'webui.url': MAIN}
    env = sys.modules['open_webui.env']
    for key, value in {
        'REDIS_URL': '',
        'REDIS_SENTINEL_HOSTS': '',
        'REDIS_KEY_PREFIX': 'test',
        'UVICORN_WORKERS': 1,
    }.items():
        monkeypatch.setattr(env, key, value, raising=False)
    config = sys.modules['open_webui.models.config'].Config
    config.get.side_effect = lambda key, default=None: (
        [api.connection] if key == 'terminal_server.connections' else settings.get(key, default)
    )
    for method in ('get_preview_port', 'claim_preview_port', 'settle_preview_port'):
        monkeypatch.setattr(config, method, getattr(preview_database, method), raising=False)

    async def get_settings():
        return dict(settings)

    clock = [1000.0]
    claims = {
        'alice-login-1': {'id': 'alice', 'sid': 'login-1'},
        'alice-login-2': {'id': 'alice', 'sid': 'login-2'},
        'alice-refreshed': {'id': 'alice', 'sid': 'login-1'},
        'bob-login': {'id': 'bob', 'sid': 'bob-login'},
        'alice-old-token': {'id': 'alice'},
    }
    login_tokens = set(claims)
    users = {actor: SimpleNamespace(id=actor, role='user') for actor in ('alice', 'bob')}

    async def get_verified_user_by_token(token, redis=None):
        return users[claims[token]['id']] if token in login_tokens else None

    async def get_verified_user(request: Request):
        token = request.headers.get('authorization', '').removeprefix('Bearer ') or request.cookies.get('token')
        if not token:
            raise HTTPException(401, 'Not authenticated')
        user = await get_verified_user_by_token(token)
        if not user:
            raise HTTPException(401, 'Not authenticated')
        return user

    auth = sys.modules['open_webui.utils.auth']
    monkeypatch.setattr(auth, 'decode_token', claims.get, raising=False)
    monkeypatch.setattr(auth, 'get_verified_user', get_verified_user)
    monkeypatch.setattr(auth, 'get_verified_user_by_token', get_verified_user_by_token)
    monkeypatch.setitem(sys.modules, 'open_webui.routers.terminals', api.router)
    preview = load('open_webui.routers.terminal_preview', 'routers/terminal_preview.py', monkeypatch)
    browser = load('open_webui.utils.browser', 'utils/browser.py', monkeypatch)
    monkeypatch.setattr(api.router, 'browser_ticket', browser.browser_ticket)
    monkeypatch.setattr(api.router, 'browser_access', browser.browser_access)
    api.chats.get_accessible_chat_by_id.side_effect = lambda chat_id, user, **kwargs: (
        object() if chat_id == 'chat-1' and user.id == 'alice' else None
    )
    closed_contexts = set()
    from open_webui.utils.context_store_errors import ContextGone

    async def read_context(context_id, actor_id, epoch, *, allow_closed=False):
        if (context_id, actor_id, epoch) != ('a' * 32, 'alice', 'epoch-1') or context_id in closed_contexts:
            raise ContextGone()
        return {}

    temporary = ModuleType('open_webui.utils.temporary_contexts')
    temporary.temporary_repository = lambda request: SimpleNamespace(read=read_context)
    monkeypatch.setitem(sys.modules, temporary.__name__, temporary)
    state = SimpleNamespace(
        settings=settings,
        calls=[],
        login_tokens=login_tokens,
        clock=clock,
        closed_contexts=closed_contexts,
        response=proxy_resource(),
        remote_session={
            'id': 'browser-1',
            'token': UPSTREAM_TOKEN,
            'mode': 'proxy',
            'url': 'http://localhost:3100/app',
        },
        socket=None,
        socket_calls=[],
        on_browser_session=None,
        session_error=None,
        create_started=asyncio.Event(),
        create_finish=None,
        session_lookup_detail=None,
        visible_ports=[{'port': port} for port in (80, 3100, 3101)],
        port_calls=[],
        preview=preview,
        api=api,
    )

    class Session:
        def __init__(self, base, user_id, **options):
            self.base = base
            self.user_id = user_id
            self.options = options

        def request(self, method, url, **options):
            if method == 'GET' and url.endswith('/ports'):
                state.port_calls.append((url, self.user_id, options))
                response = proxy_resource(b'', 'application/json')

                async def ports_json():
                    return {'ports': state.visible_ports}

                response.json = ports_json
                return PendingResponse(response)
            state.calls.append((method, url, self.user_id, options))
            if '/browser/sessions' in url:
                if state.session_error:
                    return PendingResponse(state.session_error)
                if method == 'GET' and state.session_lookup_detail:
                    response = proxy_resource(b'', 'application/json', status=404)

                    async def error_json():
                        return {'detail': state.session_lookup_detail}

                    response.json = error_json
                    return PendingResponse(response)
                response = proxy_resource(json.dumps(state.remote_session).encode(), 'application/json')

                async def get_json():
                    if method == 'POST' and state.create_finish is not None:
                        state.create_started.set()
                        await state.create_finish.wait()
                    if state.on_browser_session:
                        state.on_browser_session()
                    return dict(state.remote_session)

                response.json = get_json
                return PendingResponse(response)
            return PendingResponse(state.response)

        @asynccontextmanager
        async def ws_connect(self, url, **options):
            state.socket_calls.append((url, self.user_id, options))
            assert state.socket is not None
            try:
                yield state.socket
            finally:
                state.socket.closed = True

        async def close(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            await self.close()

    monkeypatch.setattr(preview, 'get_terminal_client', Session)
    monkeypatch.setattr(api.router, 'get_terminal_client', Session)
    app = FastAPI()
    app.dependency_overrides[api.router.get_verified_user] = get_verified_user
    app.state.terminal_preview_tickets = TicketStore(clock=lambda: clock[0])
    app.include_router(preview.router, prefix='/api/v1/terminals')
    app.include_router(api.router.router, prefix='/api/v1/terminals')
    app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'])

    @app.get('/api/config')
    async def main_config():
        return {'main': True}

    async def preview_app(scope, receive, send):
        await (preview.preview_websocket if scope['type'] == 'websocket' else preview.preview_http)(
            scope, receive, send
        )

    app.add_middleware(PreviewHostMiddleware, settings=get_settings, preview_app=preview_app)
    state.app = app
    return state


async def request(api, method, url, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=api.app), base_url=MAIN) as client:
        return await client.request(method, url, **kwargs)


async def issue(api, data=PORT, headers=ALICE):
    return await request(api, 'POST', TICKET_ROUTE, json=data, headers=headers)


def assert_no_secrets(response):
    text = response.text + str(response.headers)
    for secret in (UPSTREAM_TOKEN, 'terminal-secret', 'alice-login-1', 'probe-secret'):
        assert secret not in text


@pytest.mark.asyncio
async def test_ticket_issue_and_renewal_use_actor_login_and_existing_target(preview_api):
    opened = await issue(preview_api)
    assert opened.status_code == 200
    original = opened.json()
    assert set(original) == {'url', 'expires_at'} and original['expires_at'] == 1300
    assert original['url'].startswith(PREVIEW + '/p/') and original['url'].endswith('/app')
    assert_no_secrets(opened)
    method, url, actor, upstream = preview_api.calls[0]
    assert (method, url, actor) == ('POST', TERMINAL + '/browser/sessions', 'alice')
    assert upstream['json'] == {'url': 'http://localhost:3100/app'}
    assert upstream['allow_redirects'] is False
    assert upstream['headers']['X-Terminal-Context-Id'] == 'chat:chat-1'

    preview_api.clock[0] += 45
    renewed = await issue(
        preview_api, {**PORT, 'previous_url': original['url']}, {'Authorization': 'Bearer alice-refreshed'}
    )
    assert renewed.json() == {**original, 'expires_at': 1345}
    assert preview_api.calls[-1][:2] == ('GET', TERMINAL + '/browser/sessions/browser-1')

    count = len(preview_api.calls)
    for data, auth in (
        (PORT, {'Authorization': 'Bearer bob-login'}),
        (PORT, {'Authorization': 'Bearer alice-login-2'}),
        (PORT, {'Authorization': 'Bearer alice-old-token'}),
        ({**PORT, 'port': 3101}, ALICE),
        ({**PORT, 'chat_id': 'other-chat'}, ALICE),
    ):
        denied = await issue(preview_api, {**data, 'previous_url': original['url']}, auth)
        assert denied.status_code == 403
        assert_no_secrets(denied)
    assert len(preview_api.calls) == count


@pytest.mark.asyncio
async def test_port_renewal_cannot_change_url_path(preview_api):
    opened = (await issue(preview_api)).json()['url']
    renewed = await issue(preview_api, {**PORT, 'previous_url': opened, 'path': '/other'})
    assert renewed.status_code == 403 or renewed.json()['url'] == opened


@pytest.mark.asyncio
async def test_reopening_port_after_ticket_expiry_reuses_its_browser_session(preview_api):
    original = await issue(preview_api)
    assert original.status_code == 200
    for _ in range(17):
        preview_api.clock[0] += 301
        reopened = await issue(preview_api, {**PORT, 'path': '/another?tab=1'})
        assert reopened.status_code == 200
        assert reopened.json()['url'].endswith('/another?tab=1')
    assert len([call for call in preview_api.calls if call[0] == 'POST']) == 1
    assert all(call[1].endswith('/browser/sessions/browser-1') for call in preview_api.calls[1:])


@pytest.mark.asyncio
async def test_lost_port_create_response_does_not_retry_the_remote_effect(preview_api):
    preview_api.session_error = aiohttp.ClientConnectionError('response was lost')
    assert (await issue(preview_api)).status_code == 503
    preview_api.session_error = None
    preview_api.app.state.terminal_preview_tickets = TicketStore(clock=lambda: preview_api.clock[0])
    assert (await issue(preview_api)).status_code == 503
    assert [call[0] for call in preview_api.calls] == ['POST']


@pytest.mark.asyncio
async def test_port_session_is_kept_when_connection_changes_during_create(preview_api):
    preview_api.on_browser_session = lambda: preview_api.api.connection.update(url='https://new-terminal.test')
    assert (await issue(preview_api)).status_code == 409
    assert not preview_api.app.state.terminal_preview_tickets.memory
    preview_api.on_browser_session = None
    preview_api.api.connection['url'] = TERMINAL
    assert (await issue(preview_api)).status_code == 200
    assert [call[0] for call in preview_api.calls] == ['POST', 'GET']


@pytest.mark.asyncio
async def test_port_reopens_only_after_terminal_confirms_the_old_session_is_missing(preview_api):
    assert (await issue(preview_api)).status_code == 200
    preview_api.session_lookup_detail = 'Unrecognized proxy path'
    assert (await issue(preview_api)).status_code == 403
    assert [call[0] for call in preview_api.calls] == ['POST', 'GET']
    preview_api.session_lookup_detail = 'Browser session not found'
    preview_api.remote_session['id'] = 'browser-2'
    assert (await issue(preview_api)).status_code == 200
    assert [call[0] for call in preview_api.calls] == ['POST', 'GET', 'GET', 'POST']


@pytest.mark.asyncio
async def test_port_preview_requires_the_live_actor_scoped_terminal_port(preview_api):
    preview_api.visible_ports = [{'port': 3101}]
    assert (await issue(preview_api)).status_code == 403
    assert not preview_api.calls
    assert preview_api.port_calls[0][:2] == (TERMINAL + '/ports', 'alice')
    assert preview_api.port_calls[0][2]['headers']['X-Terminal-Context-Id'] == 'chat:chat-1'
    preview_api.visible_ports = [{'port': 3100}]
    opened = (await issue(preview_api)).json()['url']
    assert (await request(preview_api, 'GET', opened)).status_code == 200
    preview_api.visible_ports.clear()
    count = len(preview_api.calls)
    assert (await request(preview_api, 'GET', opened)).status_code == 403
    assert len(preview_api.calls) == count


@pytest.mark.asyncio
@pytest.mark.parametrize('preview_database', ['sqlite', 'postgresql'], indirect=True)
async def test_environment_preview_hostname_is_reserved_across_reconfigure_and_config_reset(
    preview_database, monkeypatch
):
    from open_webui.utils.terminal_preview import preview_settings

    monkeypatch.setenv('WEBUI_URL', MAIN)
    monkeypatch.setattr(preview_database, 'PERSISTENT_ENABLED', False)
    monkeypatch.setattr(preview_database, 'DEFAULTS', {'terminal.preview.base_url': PREVIEW, 'webui.url': MAIN})
    assert (await preview_settings())['isolated_hosts'] == {'preview.test'}
    monkeypatch.setattr(preview_database, 'DEFAULTS', {'terminal.preview.base_url': '', 'webui.url': MAIN})
    assert (await preview_settings())['isolated_hosts'] == {'preview.test'}
    await preview_database.clear()
    assert (await preview_settings())['isolated_hosts'] == {'preview.test'}
    assert not await preview_database.get_namespace('server')
    assert not any(key.startswith('server.terminal-preview.') for key in await preview_database.get_all())

    monkeypatch.setattr(preview_database, 'PERSISTENT_ENABLED', True)
    with pytest.raises(HTTPException, match='reserved preview hostname'):
        await preview_database.upsert({'webui.url': PREVIEW})
    with pytest.raises(HTTPException, match='different hostname'):
        await preview_database.upsert({'terminal.preview.base_url': MAIN + ':443'})
    await asyncio.gather(
        preview_database.upsert({'terminal.preview.base_url': 'https://another-preview.test'}),
        preview_database.upsert({'terminal.preview.base_url': 'https://third-preview.test'}),
    )
    await preview_database.upsert({'terminal.preview.base_url': ''})
    await preview_database.upsert({'server.terminal-preview.hosts': []})
    assert (await preview_settings())['isolated_hosts'] == {
        'preview.test',
        'another-preview.test',
        'third-preview.test',
    }

    claims = await asyncio.gather(
        preview_database.claim_preview_port('port-key', 'claim-1', 2000),
        preview_database.claim_preview_port('port-key', 'claim-2', 2000),
    )
    assert claims.count(True) == 1
    saved, deadline = await preview_database.get_preview_port('port-key')
    assert deadline == 2000
    assert not await preview_database.settle_preview_port('port-key', 'wrong-claim')
    assert await preview_database.settle_preview_port('port-key', saved['claim'], 'browser-1')
    assert await preview_database.get_preview_port('port-key') == ({**saved, 'session_id': 'browser-1'}, None)
    await preview_database.clear()
    assert await preview_database.get_preview_port('port-key') == ({**saved, 'session_id': 'browser-1'}, None)
    assert not await preview_database.get_namespace('server')
    assert not any(key.startswith('server.terminal-preview.') for key in await preview_database.get_all())


@pytest.mark.asyncio
async def test_browser_ticket_opens_its_entry_frame(preview_api):
    opened = await issue(preview_api, {'kind': 'browser', 'session_id': 'browser-1', 'chat_id': 'chat-1'})
    assert opened.status_code == 200
    entry_url = opened.json()['url']
    entry = await request(preview_api, 'GET', entry_url)
    assert entry.status_code == 302
    assert entry.headers['location'] == urlsplit(entry_url).path + '/frame/http/localhost%3A3100/app'
    assert_no_secrets(entry)
    page = await request(preview_api, 'GET', entry_url, follow_redirects=True)
    assert page.status_code == 200
    assert 'sandbox allow-scripts allow-forms' in page.headers['content-security-policy']


@pytest.mark.asyncio
async def test_scope_ownership_terminal_access_and_login_are_revalidated(preview_api):
    temporary = {'id': 'a' * 32, 'epoch': 'epoch-1'}
    data = {**PORT, 'chat_id': 'temporary:' + temporary['id'], 'temporary_context': temporary}
    assert (await issue(preview_api, data, {'Authorization': 'Bearer bob-login'})).status_code in {403, 404, 410}
    assert (await issue(preview_api, {**data, 'temporary_context': {**temporary, 'epoch': 'old'}})).status_code in {
        403,
        404,
        410,
    }
    opened = await issue(preview_api, data)
    assert opened.status_code == 200
    preview_api.closed_contexts.add(temporary['id'])
    assert (await request(preview_api, 'GET', opened.json()['url'])).status_code in {403, 404, 410}

    url = (await issue(preview_api)).json()['url']
    preview_api.api.access.return_value = False
    assert (await request(preview_api, 'GET', url)).status_code == 403
    preview_api.api.access.return_value = True
    preview_api.api.chats.get_accessible_chat_by_id.return_value = None
    old_side_effect = preview_api.api.chats.get_accessible_chat_by_id.side_effect
    preview_api.api.chats.get_accessible_chat_by_id.side_effect = None
    assert (await request(preview_api, 'GET', url)).status_code == 404
    preview_api.api.chats.get_accessible_chat_by_id.side_effect = old_side_effect
    preview_api.api.connection['url'] = 'https://new-terminal.test'
    assert (await request(preview_api, 'GET', url)).status_code == 403
    preview_api.api.connection['url'] = TERMINAL
    preview_api.login_tokens.remove('alice-login-1')
    assert (await request(preview_api, 'GET', url)).status_code == 403


@pytest.mark.asyncio
async def test_preview_host_cors_credential_scrubbing_and_safe_errors(preview_api, caplog):
    assert (await request(preview_api, 'GET', MAIN + '/api/config')).json() == {'main': True}
    for path in ('/api/config', '/api/v1/auths', '/ws/socket.io/', TICKET_ROUTE):
        assert (await request(preview_api, 'GET', PREVIEW + path, headers=ALICE)).status_code == 404
    assert (
        await request(
            preview_api,
            'OPTIONS',
            MAIN + '/api/config',
            headers={
                'Origin': 'null',
                'Access-Control-Request-Method': 'GET',
            },
        )
    ).status_code == 403

    opened = (await issue(preview_api)).json()['url']
    count = len(preview_api.calls)
    options = await request(
        preview_api,
        'OPTIONS',
        opened,
        headers={
            'Origin': 'null',
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type',
        },
    )
    assert options.status_code == 204 and len(preview_api.calls) == count
    assert options.headers['access-control-allow-origin'] == 'null'
    assert 'access-control-allow-credentials' not in options.headers
    response = await request(
        preview_api,
        'GET',
        opened,
        headers={
            'Authorization': 'Bearer probe-secret',
            'Cookie': 'session=probe-secret',
            'Referer': 'https://main.test/probe-secret',
            'Origin': 'null',
        },
    )
    assert response.status_code == 200
    assert_no_secrets(response)
    assert not {'set-cookie', 'refresh', 'access-control-allow-credentials'} & set(response.headers)
    assert 'allow-same-origin' not in response.headers['content-security-policy']
    assert response.headers['cache-control'] == 'no-store'
    _, _, _, call = preview_api.calls[-1]
    assert call['headers']['Authorization'] == 'Bearer terminal-secret'
    assert call['headers']['Origin'] == 'null' and call['cookies'] == {}
    assert 'referer' not in {name.lower() for name in call['headers']}

    preview_api.response = aiohttp.ClientConnectionError('network error at ' + UPSTREAM_PREFIX + '?secret=probe-secret')
    failure = await request(preview_api, 'GET', opened)
    assert failure.status_code == 503
    assert_no_secrets(failure)
    assert 'probe-secret' not in caplog.text and UPSTREAM_TOKEN not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mime,template',
    [
        ('text/html', '<script src="{prefix}/runtime.js"></script>'),
        ('text/css', 'a {{background-image: url({prefix}/frame/http/localhost%3A3100/bg.png)}}'),
        ('application/javascript', 'const path = "{prefix}/runtime.js";'),
        ('application/json', '{{"resource": "{prefix}/runtime.js"}}'),
        ('text/plain', 'error at {prefix}/runtime.js'),
    ],
)
async def test_upstream_text_never_exposes_terminal_capability(preview_api, mime, template):
    opened = (await issue(preview_api)).json()['url']
    preview_api.response = proxy_resource(template.format(prefix=UPSTREAM_PREFIX).encode(), mime)
    response = await request(preview_api, 'GET', opened)
    assert response.status_code in {200, 502}
    assert_no_secrets(response)
    if response.status_code == 200:
        assert urlsplit(opened).path.split('/frame/', 1)[0] in response.text


@pytest.mark.asyncio
async def test_redirects_are_rewritten_only_inside_the_authorized_proxy(preview_api):
    opened = (await issue(preview_api)).json()['url']
    preview_api.response = proxy_resource(
        b'', status=302, Location=TERMINAL + UPSTREAM_PREFIX + '/frame/http/localhost%3A3100/next'
    )
    response = await request(preview_api, 'GET', opened)
    assert response.status_code == 302
    assert response.headers['location'] == urlsplit(opened).path.removesuffix('/app') + '/next'
    assert_no_secrets(response)
    preview_api.response = proxy_resource(
        b'', status=302, Location='https://evil.test' + UPSTREAM_PREFIX + '/runtime.js'
    )
    response = await request(preview_api, 'GET', opened)
    assert response.status_code == 502
    assert_no_secrets(response)


@pytest.mark.asyncio
async def test_main_origin_web_pages_and_ports_never_reach_the_terminal(preview_api):
    for path in (
        '/proxy/3100/',
        '/browser/view/browser-1/ticket/runtime.js',
        '/browser/view/browser-1/ticket/frame/http/site/',
    ):
        response = await request(preview_api, 'GET', '/api/v1/terminals/terminal-1' + path, headers=ALICE)
        assert response.status_code == 403
    assert not preview_api.calls


@pytest.mark.asyncio
async def test_chrome_control_ticket_survives_token_refresh_within_the_same_login(preview_api):
    preview_api.remote_session['mode'] = 'chrome'
    route = '/api/v1/terminals/terminal-1/chats/chat-1/browser/sessions/browser-1'
    opened = await request(preview_api, 'GET', route, headers=ALICE)
    assert opened.status_code == 200
    first = opened.json()['token']
    assert len(first) == 43
    assert_no_secrets(opened)
    renewed = await request(preview_api, 'GET', route, headers={'Authorization': 'Bearer alice-refreshed'})
    assert renewed.json()['token'] == first
    other_login = await request(preview_api, 'GET', route, headers={'Authorization': 'Bearer alice-login-2'})
    assert other_login.json()['token'] != first


def asgi_scope(url, kind, headers=()):
    target = urlsplit(url)
    return {
        'type': kind,
        'asgi': {'version': '3.0', 'spec_version': '2.4'},
        'http_version': '1.1',
        'method': 'GET',
        'scheme': 'wss' if kind == 'websocket' else 'https',
        'server': (target.hostname, 443),
        'client': ('127.0.0.1', 10000),
        'path': target.path,
        'raw_path': target.path.encode(),
        'query_string': target.query.encode(),
        'root_path': '',
        'headers': [(b'host', target.netloc.encode()), *headers],
        'subprotocols': [],
    }


@pytest.mark.asyncio
async def test_revoked_http_stream_closes_and_never_emits_a_second_status(preview_api):
    opened = (await issue(preview_api)).json()['url']
    preview_api.response = proxy_resource(b'', 'text/event-stream')

    async def chunks():
        yield b'data: ready\n\n'
        await asyncio.Future()

    preview_api.response.content.iter_any = chunks
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    scope = asgi_scope(opened + '?secret=probe-secret', 'http')
    task = asyncio.create_task(preview_api.app(scope, incoming.get, outgoing.put))
    try:
        start = await asyncio.wait_for(outgoing.get(), 2)
        first = await asyncio.wait_for(outgoing.get(), 2)
        assert start['status'] == 200 and first['body'] == b'data: ready\n\n'
        preview_api.login_tokens.remove('alice-login-1')
        await asyncio.wait_for(task, 5)
        assert await outgoing.get() == {'type': 'http.response.body', 'body': b'', 'more_body': False}
        assert outgoing.empty() and preview_api.response.released
        assert 'probe-secret' not in scope['path'] and scope['query_string'] == b''
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('port,suffix', [(3100, ':3100'), (80, '')])
async def test_hmr_websocket_is_bound_to_the_port_and_closes_after_revocation(preview_api, port, suffix):
    opened = (await issue(preview_api, {**PORT, 'port': port})).json()['url'].split('/frame/')[0]
    preview_api.socket = BrowserSocket()
    for target in ('ws://localhost:3101/hmr', f'ws://localhost{suffix}/hmr'):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        scope = asgi_scope(opened + '/ws', 'websocket', [(b'origin', b'null'), (b'cookie', b'bad=probe-secret')])
        await incoming.put({'type': 'websocket.connect'})
        await incoming.put({'type': 'websocket.receive', 'text': json.dumps({'url': target})})
        task = asyncio.create_task(preview_api.app(scope, incoming.get, outgoing.put))
        try:
            assert (await asyncio.wait_for(outgoing.get(), 2))['type'] == 'websocket.accept'
            if target.endswith('3101/hmr'):
                assert (await asyncio.wait_for(outgoing.get(), 2))['code'] == 1008
                assert not preview_api.socket_calls
            else:
                assert json.loads(await asyncio.wait_for(preview_api.socket.sent.get(), 2)) == {'url': target}
                await preview_api.socket.messages.put(aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, 'hot update', None))
                assert (await asyncio.wait_for(outgoing.get(), 2))['text'] == 'hot update'
                preview_api.api.access.return_value = False
                closed = await asyncio.wait_for(outgoing.get(), 5)
                assert closed['type'] == 'websocket.close' and closed['code'] == 1008
                assert preview_api.socket.closed
            await asyncio.wait_for(task, 2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_ticket_checks_login_again_after_terminal_io(preview_api):
    preview_api.on_browser_session = lambda: preview_api.login_tokens.remove('alice-login-1')
    response = await issue(preview_api)
    assert response.status_code == 403
    assert_no_secrets(response)
    assert not preview_api.app.state.terminal_preview_tickets.memory


@pytest.mark.asyncio
async def test_browser_session_response_must_preserve_requested_id(preview_api):
    preview_api.remote_session['id'] = 'unexpected'
    response = await issue(preview_api, {'kind': 'browser', 'session_id': 'browser-1', 'chat_id': 'chat-1'})
    assert response.status_code == 502
    assert_no_secrets(response)
    assert not preview_api.app.state.terminal_preview_tickets.memory


@pytest.mark.asyncio
async def test_revocation_ends_a_stalled_html_response_before_exposing_bytes(preview_api):
    opened = (await issue(preview_api)).json()['url']
    preview_api.response = proxy_resource(b'', 'text/html')
    started_reading = asyncio.Event()

    async def chunks():
        started_reading.set()
        yield b'<div>private text'
        await asyncio.Future()

    preview_api.response.content.iter_any = chunks
    task = asyncio.create_task(request(preview_api, 'GET', opened))
    try:
        await asyncio.wait_for(started_reading.wait(), 2)
        preview_api.login_tokens.remove('alice-login-1')
        completed, _ = await asyncio.wait({task}, timeout=5)
        assert completed, 'The buffered HTML read stayed open after revocation'
        denied = task.result()
        assert denied.status_code == 403
        assert 'private text' not in denied.text
        assert preview_api.response.released
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_direct_websocket_redirect_is_never_followed(preview_api, monkeypatch):
    from aiohttp import web

    handshake, unexpected = asyncio.Event(), asyncio.Event()

    async def redirect(request):
        handshake.set()
        assert request.headers['Authorization'] == 'Bearer terminal-secret'
        raise web.HTTPFound('/other-target')

    async def other(request):
        unexpected.set()
        return web.Response(text='unexpected')

    async def ports(request):
        assert request.headers['Authorization'] == 'Bearer terminal-secret'
        return web.json_response({'ports': [{'port': 3100}]})

    upstream = web.Application()
    upstream.router.add_get(UPSTREAM_PREFIX + '/ws', redirect)
    upstream.router.add_get('/other-target', other)
    upstream.router.add_get('/ports', ports)
    runner = web.AppRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    try:
        preview_api.api.connection['url'] = f'http://127.0.0.1:{runner.addresses[0][1]}'
        capability = (await issue(preview_api)).json()['url'].split('/frame/')[0]
        monkeypatch.setattr(aiohttp, 'ClientSession', AIOHTTP_CLIENT_SESSION)
        transport = {'aiohttp': aiohttp, 'HTTPException': HTTPException}
        load_functions(transport, 'utils/tether.py', {'get_terminal_client'})
        monkeypatch.setattr(preview_api.preview, 'get_terminal_client', transport['get_terminal_client'])
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        scope = asgi_scope(capability + '/ws', 'websocket', [(b'origin', b'null')])
        await incoming.put({'type': 'websocket.connect'})
        await incoming.put({'type': 'websocket.receive', 'text': json.dumps({'url': 'ws://localhost:3100/hmr'})})
        await asyncio.wait_for(preview_api.app(scope, incoming.get, outgoing.put), 5)
        assert (await outgoing.get())['type'] == 'websocket.accept'
        assert (await outgoing.get())['type'] == 'websocket.close'
        assert handshake.is_set()
        assert not unexpected.is_set()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_real_redis_preview_capability_survives_worker_switch_and_fails_closed(preview_api):
    from redis.asyncio import Redis

    redis_url = os.getenv('TERMINAL_PREVIEW_TEST_REDIS_URL')
    if not redis_url:
        pytest.skip('Set TERMINAL_PREVIEW_TEST_REDIS_URL to run the isolated real Redis integration')
    redis = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
    offline = Redis.from_url('redis://127.0.0.1:0/0', socket_connect_timeout=1, socket_timeout=1)
    prefix = 'v0133-preview-test-' + uuid4().hex
    store = TicketStore(redis=redis, redis_required=True, prefix=prefix)
    key = None
    try:
        preview_api.app.state.terminal_preview_tickets = store
        response = await issue(preview_api)
        assert response.status_code == 200
        opened = response.json()['url']
        token = urlsplit(opened).path.split('/')[2]
        key = store.key(token)
        assert 295 <= await redis.ttl(key) <= 300

        preview_api.app.state.terminal_preview_tickets = TicketStore(redis=redis, redis_required=True, prefix=prefix)
        assert (await request(preview_api, 'GET', opened)).status_code == 200
        preview_api.login_tokens.remove('alice-login-1')
        assert (await request(preview_api, 'GET', opened)).status_code == 403

        preview_api.app.state.terminal_preview_tickets = TicketStore(redis=offline, redis_required=True, prefix=prefix)
        unavailable = await request(preview_api, 'GET', opened)
        assert unavailable.status_code == 503 and unavailable.headers['retry-after']
        assert_no_secrets(unavailable)
    finally:
        if key:
            await redis.delete(key)
        await redis.aclose()
        await offline.aclose()


@pytest.mark.asyncio
async def test_real_redis_workers_share_one_durable_port_create(preview_api):
    from redis.asyncio import Redis

    redis_url = os.getenv('TERMINAL_PREVIEW_TEST_REDIS_URL')
    if not redis_url:
        pytest.skip('Set TERMINAL_PREVIEW_TEST_REDIS_URL to run the isolated real Redis integration')
    redis = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
    prefix = 'v0133-preview-workers-' + uuid4().hex
    preview_api.app.state.terminal_preview_tickets = TicketStore(redis, redis_required=True, prefix=prefix)
    preview_api.create_finish = asyncio.Event()
    first = asyncio.create_task(issue(preview_api))
    try:
        await asyncio.wait_for(preview_api.create_started.wait(), 5)
        preview_api.app.state.terminal_preview_tickets = TicketStore(redis, redis_required=True, prefix=prefix)
        overlapping = await issue(preview_api)
        assert overlapping.status_code == 503 and overlapping.headers['retry-after']
        assert [call[0] for call in preview_api.calls] == ['POST']
        preview_api.create_finish.set()
        opened = await asyncio.wait_for(first, 5)
        assert opened.status_code == 200
        reopened = await issue(preview_api, {**PORT, 'path': '/again'})
        assert reopened.status_code == 200 and reopened.json()['url'].endswith('/again')
        assert [call[0] for call in preview_api.calls] == ['POST', 'GET']
        assert (await request(preview_api, 'GET', opened.json()['url'])).status_code == 200
    finally:
        preview_api.create_finish.set()
        await asyncio.gather(first, return_exceptions=True)
        keys = [key async for key in redis.scan_iter(match=prefix + ':*')]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


@pytest.mark.asyncio
async def test_sse_rewrites_split_terminal_capability_before_emitting_event(preview_api):
    opened = (await issue(preview_api)).json()['url']
    preview_api.response = proxy_resource(b'', 'text/event-stream')

    async def chunks():
        body = f'data: {{"url":"{UPSTREAM_PREFIX}/runtime.js"}}\n\n'.encode()
        cutoff = body.index(UPSTREAM_TOKEN.encode()) + 8
        yield body[:cutoff]
        yield body[cutoff:]

    preview_api.response.content.iter_any = chunks
    response = await request(preview_api, 'GET', opened)
    assert response.status_code == 200
    assert_no_secrets(response)
    prefix = urlsplit(opened).path.split('/frame/')[0]
    assert json.loads(response.text.removeprefix('data: ').strip()) == {'url': prefix + '/runtime.js'}


@pytest.mark.asyncio
async def test_renewal_refuses_reconfigured_terminal_before_contacting_new_base(preview_api):
    opened = (await issue(preview_api)).json()['url']
    preview_api.api.connection['url'] = 'https://new-terminal.test'
    count = len(preview_api.calls)
    response = await issue(preview_api, {**PORT, 'previous_url': opened})
    assert response.status_code in {403, 409}
    assert len(preview_api.calls) == count
    assert_no_secrets(response)


@pytest.mark.asyncio
async def test_ticket_issue_returns_sanitized_retryable_error_if_terminal_unreachable(preview_api, caplog):
    preview_api.session_error = aiohttp.ClientConnectionError(
        'transport failed at ' + UPSTREAM_PREFIX + '?x=probe-secret'
    )
    async with AsyncClient(
        transport=ASGITransport(app=preview_api.app, raise_app_exceptions=False), base_url=MAIN
    ) as client:
        response = await client.post(TICKET_ROUTE, json=PORT, headers=ALICE)
    assert response.status_code == 503
    assert_no_secrets(response)
    assert 'probe-secret' not in caplog.text and UPSTREAM_TOKEN not in caplog.text
    assert not preview_api.app.state.terminal_preview_tickets.memory
