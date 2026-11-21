"""Authenticated ticket issuance and a preview-only ASGI gateway."""

import asyncio
import copy
import hashlib
import inspect
import json
import os
import re
import secrets
from contextlib import AsyncExitStack, suppress
from types import SimpleNamespace
from urllib.parse import quote, unquote, urlsplit

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL
from open_webui.models.config import Config
from open_webui.utils.auth import decode_token, get_verified_user, get_verified_user_by_token
from open_webui.utils.terminal_preview import (
    PORT_CREATE_RETENTION,
    PREVIEW_PATH,
    ticket_store,
    unavailable,
    validate_preview_base_url,
)
from open_webui.utils.terminal_preview_response import (
    TEXT_RESOURCE_TYPES,
    rewrite_preview_body,
    rewrite_preview_location,
)
from open_webui.utils.tether import get_terminal_client

router = APIRouter()


class PreviewTicketForm(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    port: int | None = Field(default=None, ge=1, le=65535, strict=True)
    path: str = Field(default='/', max_length=16384)
    chat_id: str | None = Field(default=None, max_length=256)
    model_id: str | None = Field(default=None, max_length=1024)
    instance_user_id: str | None = Field(default=None, max_length=256)
    temporary_context: dict[str, str] | None = None
    previous_url: str | None = Field(default=None, max_length=32768)


def login_token(request):
    token = request.headers.get('authorization', '').removeprefix('Bearer ').strip()
    token = token or request.cookies.get('token')
    if not token or token.startswith('sk-'):
        raise HTTPException(403, 'Preview requires an authenticated browser session')
    claims = decode_token(token)
    if not claims or claims.get('typ', 'session') != 'session':
        raise HTTPException(403, 'Preview requires an authenticated browser session')
    identity = claims.get('sid') or claims.get('jti')
    if not identity:
        raise HTTPException(403, 'Refresh your login session before opening a preview')
    return token, claims, identity


async def terminal_access(request, user, record):
    from open_webui.routers.terminals import _http_terminal_context

    # Reconstitute only the server-held authentication context. The preview page
    # cannot supply user identity, terminal credentials or temporary provenance.
    headers = {'authorization': 'Bearer ' + record['auth_token']}
    temporary = record.get('temporary_context')
    if temporary:
        headers.update({'x-temporary-context-id': temporary['id'], 'x-temporary-context-epoch': temporary['epoch']})
    if record.get('oauth_session_id'):
        headers['cookie'] = 'oauth_session_id=' + quote(record['oauth_session_id'], safe='')
    scoped = Request(
        {
            **request.scope,
            'type': 'http',
            'method': 'GET',
            'headers': [(key.encode(), value.encode()) for key, value in headers.items()],
            'state': {},
        }
    )
    scoped.state.token = SimpleNamespace(credentials=record['auth_token'])
    metadata = {key: record.get(key) for key in ('chat_id', 'model_id', 'instance_user_id')}
    metadata['temporary_context'] = copy.deepcopy(temporary)
    return await _http_terminal_context(record['server_id'], scoped, user, metadata, require_context=True)


def relay_options(record):
    from open_webui.routers.terminals import relay_service

    server_id = record.get('server_id') or ''
    return {'service': relay_service(server_id)} if server_id.startswith('tether:') else {}


async def remote_json(
    base, user, headers, cookies, method, path, body=None, *, allow_missing=False, service='open_terminal'
):
    try:
        async with get_terminal_client(
            base, user.id, service=service, timeout=aiohttp.ClientTimeout(total=30), trust_env=True
        ) as client:
            async with client.request(
                method,
                base + path,
                headers=headers,
                cookies=cookies,
                json=body,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
                allow_redirects=False,
            ) as response:
                if response.status >= 500:
                    raise unavailable()
                if allow_missing and response.status == 404:
                    error = await response.json()
                    if isinstance(error, dict) and error.get('detail') in {
                        'Browser session not found',
                        'Browser session expired',
                    }:
                        return None
                if method == 'POST' and response.status in (400, 401, 403, 404, 409, 422, 429):
                    raise HTTPException(response.status, 'Terminal refused to open a browser session')
                if response.status < 200 or response.status >= 300:
                    raise HTTPException(502 if method == 'POST' else 403, 'Browser session is unavailable')
                result = await response.json()
    except (aiohttp.ClientError, TimeoutError, ConnectionError):
        raise unavailable() from None
    except (ValueError, TypeError):
        raise HTTPException(502, 'Invalid browser response') from None
    if not isinstance(result, dict):
        raise HTTPException(502, 'Invalid browser response')
    return result


async def require_visible_port(base, user, headers, cookies, port, **options):
    ports = (await remote_json(base, user, headers, cookies, 'GET', '/ports', **options)).get('ports')
    if not isinstance(ports, list):
        raise HTTPException(502, 'Terminal returned an invalid port list')
    if not any(isinstance(item, dict) and type(item.get('port')) is int and item['port'] == port for item in ports):
        raise HTTPException(403, 'The terminal port is not available to this user')


async def port_browser_session(store, record, terminal_base, user, headers, cookies, path):
    """Reuse one preview-owned session per effective terminal and login scope."""
    scope = [
        record.get(field)
        for field in (
            'user_id',
            'login_session',
            'server_id',
            'chat_id',
            'model_id',
            'instance_user_id',
            'temporary_context',
            'terminal_context',
            'port',
        )
    ]
    key = hashlib.sha256(json.dumps([terminal_base, scope], sort_keys=True).encode()).hexdigest()
    await store.check_connection()
    for _ in range(3):
        now = int(store.clock())
        saved, expires_at = await Config.get_preview_port(key)
        if saved:
            if session_id := saved.get('session_id'):
                session = await remote_json(
                    terminal_base,
                    user,
                    headers,
                    cookies,
                    'GET',
                    '/browser/sessions/' + quote(session_id, safe=''),
                    allow_missing=True,
                    **relay_options(record),
                )
                if session is not None:
                    if session.get('id') != session_id:
                        raise HTTPException(502, 'Browser response changed the requested session')
                    return session
            elif expires_at is None or expires_at > now:
                raise unavailable()
            # Only definitive absence or expiry of an unpublished create claim
            # permits replacing the session. The claim token fences concurrent workers.
            await Config.settle_preview_port(key, saved['claim'])
            continue

        claim = secrets.token_urlsafe(24)
        if not await Config.claim_preview_port(key, claim, now + PORT_CREATE_RETENTION):
            continue
        try:
            session = await remote_json(
                terminal_base,
                user,
                headers,
                cookies,
                'POST',
                '/browser/sessions',
                {'url': f'http://localhost:{record["port"]}' + path},
                **relay_options(record),
            )
        except HTTPException as error:
            if error.status_code in (400, 401, 403, 404, 409, 422, 429):
                await Config.settle_preview_port(key, claim)
            raise
        session_id = session.get('id')
        if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
            raise HTTPException(502, 'Invalid browser response')
        # Save the returned identity even if access is revoked during the POST.
        # The index carries no credentials or browser capability.
        if not await Config.settle_preview_port(key, claim, session_id):
            raise unavailable()
        return session
    raise unavailable()


async def configured_base(*main_urls):
    try:
        return validate_preview_base_url(
            await Config.get('terminal.preview.base_url', ''),
            os.getenv('WEBUI_URL', ''),
            *main_urls,
        )
    except ValueError:
        return ''


@router.post('/{server_id}/preview-tickets')
async def create_preview_ticket(
    server_id: str, form_data: PreviewTicketForm, request: Request, user=Depends(get_verified_user)
):
    base = await configured_base(str(request.base_url), await Config.get('webui.url', ''))
    if not base:
        raise HTTPException(403, 'Independent terminal preview is not configured')
    if form_data.kind not in {'browser', 'port'}:
        raise HTTPException(400, 'Preview target must be a browser session or terminal port')
    if form_data.kind == 'browser' and (not form_data.session_id or form_data.port is not None):
        raise HTTPException(400, 'A browser session is required')
    if form_data.kind == 'port' and form_data.port is None:
        raise HTTPException(400, 'A terminal port is required')
    if not form_data.path.startswith('/') or form_data.path.startswith('//') or '\\' in form_data.path:
        raise HTTPException(400, 'Preview path must be relative to its target')
    token, claims, login_session = login_token(request)
    if claims.get('id') != user.id:
        raise HTTPException(403, 'Preview session mismatch')
    context = form_data.temporary_context
    if context is not None and (set(context) != {'id', 'epoch'} or form_data.chat_id != 'temporary:' + context['id']):
        raise HTTPException(400, 'Invalid temporary context')
    parent = urlsplit(str(request.base_url))
    parent_origin = f'{parent.scheme}://{parent.netloc}'
    record = {
        'user_id': user.id,
        'login_session': login_session,
        'auth_token': token,
        'server_id': server_id,
        'kind': form_data.kind,
        'session_id': form_data.session_id,
        'port': form_data.port,
        'chat_id': form_data.chat_id,
        'model_id': form_data.model_id,
        'instance_user_id': form_data.instance_user_id,
        'temporary_context': context,
        'base_url': base,
        'parent_origin': parent_origin,
        'oauth_session_id': request.cookies.get('oauth_session_id'),
    }
    store = ticket_store(request)
    previous_token = None
    previous = None
    if form_data.previous_url:
        previous_url = urlsplit(form_data.previous_url)
        match = PREVIEW_PATH.match(previous_url.path)
        if not match or f'{previous_url.scheme}://{previous_url.netloc}' != base:
            raise HTTPException(403, 'Invalid preview renewal URL')
        previous_token = match[1]
        previous = await store.get(previous_token)
        for field in (
            'user_id',
            'login_session',
            'server_id',
            'kind',
            'port',
            'chat_id',
            'model_id',
            'instance_user_id',
            'temporary_context',
            'base_url',
            'parent_origin',
        ):
            if previous.get(field) != record.get(field):
                raise HTTPException(403, 'A preview ticket cannot change identity or target')
        if form_data.kind == 'port':
            if record['session_id'] and record['session_id'] != previous['session_id']:
                raise HTTPException(403, 'A preview ticket cannot change identity or target')
            record['session_id'] = previous['session_id']
    terminal_base, headers, cookies, terminal_context = await terminal_access(request, user, record)
    record['terminal_context'] = terminal_context
    if previous and (
        previous.get('terminal_base') != terminal_base or previous.get('terminal_context') != terminal_context
    ):
        raise HTTPException(403, 'Terminal connection changed; open a new preview')
    if form_data.kind == 'port':
        await require_visible_port(terminal_base, user, headers, cookies, form_data.port, **relay_options(record))
    if record['session_id']:
        session = await remote_json(
            terminal_base,
            user,
            headers,
            cookies,
            'GET',
            '/browser/sessions/' + quote(record['session_id'], safe=''),
            **relay_options(record),
        )
    else:
        try:
            session = await port_browser_session(store, record, terminal_base, user, headers, cookies, form_data.path)
        except SQLAlchemyError:
            raise unavailable() from None
    if not session.get('id') or not session.get('token') or session.get('mode') != 'proxy':
        raise HTTPException(409, 'Use Proxy mode for isolated preview or Chrome for full browser capabilities')
    if record['session_id'] and session['id'] != record['session_id']:
        raise HTTPException(502, 'Browser response changed the requested session')
    record.update(session_id=session['id'], upstream_token=session['token'], terminal_base=terminal_base)
    if form_data.kind == 'port':
        # Renewal extends authorization; it must not navigate an active tab.
        record['entry_path'] = (previous or {}).get('entry_path') or (
            '/frame/http/' + quote(f'localhost:{form_data.port}', safe='') + form_data.path
        )
    else:
        target = urlsplit(str(session.get('url') or ''))
        record['entry_path'] = ''
        if target.scheme in {'http', 'https'} and target.hostname and not target.username and not target.password:
            record['entry_path'] = (
                '/frame/' + target.scheme + '/' + quote(target.netloc, safe='') + (target.path or '/')
            )
            if target.query:
                record['entry_path'] += '?' + target.query
            if target.fragment:
                record['entry_path'] += '#' + target.fragment
    # Recheck after network I/O: closing a scope, disabling Tether, revocation or
    # changing the selected server while the browser responds cannot mint access.
    current_user = await get_verified_user_by_token(token, getattr(request.app.state, 'redis', None))
    if not current_user or current_user.id != user.id:
        raise HTTPException(403, 'Preview session was revoked')
    current_base, current_headers, current_cookies, current_context = await terminal_access(
        request, current_user, record
    )
    if (current_base, current_context) != (terminal_base, terminal_context) or await configured_base() != base:
        raise HTTPException(409, 'Preview configuration changed; retry')
    if form_data.kind == 'port':
        await require_visible_port(
            current_base, current_user, current_headers, current_cookies, form_data.port, **relay_options(record)
        )
    capability, saved = await store.issue(record, previous_token=previous_token)
    url = base + '/p/' + capability
    if form_data.kind == 'port':
        url += record['entry_path']
    return {'url': url, 'expires_at': saved['expires_at']}


async def preview_connection(request, record):
    base = await configured_base()
    if not base or base != record['base_url']:
        raise HTTPException(403, 'Preview was disabled or reconfigured')
    user = await get_verified_user_by_token(record['auth_token'], getattr(request.app.state, 'redis', None))
    if not user or user.id != record['user_id']:
        raise HTTPException(403, 'Preview session was revoked')
    terminal_base, headers, cookies, terminal_context = await terminal_access(request, user, record)
    if (terminal_base, terminal_context) != (record['terminal_base'], record.get('terminal_context')):
        raise HTTPException(403, 'Terminal connection changed')
    return user, terminal_base, headers, cookies


async def authorize_preview(request):
    store, token = ticket_store(request), request.scope['preview_token']
    record = await store.get(token)
    access = await preview_connection(request, record)
    if record['kind'] == 'port':
        user, terminal_base, headers, cookies = access
        await require_visible_port(terminal_base, user, headers, cookies, record['port'], **relay_options(record))
        # The terminal ownership query can block on the network. Do not start
        # target traffic with the login, ACL or ticket state from before it.
        record = await store.get(token)
        access = await preview_connection(request, record)
    return (record, *access)


def target_port(target):
    try:
        port = target.port
    except ValueError:
        raise HTTPException(400, 'Invalid target port') from None
    if port == 0:
        raise HTTPException(400, 'Invalid target port')
    return port if port is not None else (443 if target.scheme in {'https', 'wss'} else 80)


def target_path(record, path):
    decoded = path
    for _ in range(8):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    if (
        unquote(decoded) != decoded
        or '\\' in decoded
        or any(ord(c) < 32 for c in decoded)
        or '..' in decoded.split('/')
    ):
        raise HTTPException(400, 'Invalid preview resource path')
    if path == 'runtime.js':
        return path
    if not path.startswith('frame/'):
        raise HTTPException(404, 'Preview resource not found')
    segments = path.split('/', 3)
    if len(segments) != 4 or segments[1] not in {'http', 'https'}:
        raise HTTPException(400, 'Invalid preview target')
    target = urlsplit(segments[1] + '://' + unquote(segments[2]))
    if not target.hostname or target.username or target.password or target.path:
        raise HTTPException(400, 'Invalid preview target')
    port = target_port(target)
    if record['kind'] == 'port' and (
        target.hostname != 'localhost' or port != record['port'] or target.scheme != 'http'
    ):
        raise HTTPException(403, 'Preview ticket belongs to a different terminal port')
    return path


def response_headers(record, content_type):
    origin = record['base_url']
    websocket_origin = origin.replace('https://', 'wss://').replace('http://', 'ws://')
    return {
        'Content-Type': content_type,
        'Cache-Control': 'no-store',
        'Referrer-Policy': 'no-referrer',
        'X-Content-Type-Options': 'nosniff',
        'Access-Control-Allow-Origin': 'null',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,HEAD,OPTIONS',
        'Access-Control-Allow-Headers': 'content-type,range,last-event-id',
        'Cross-Origin-Resource-Policy': 'cross-origin',
        'Content-Security-Policy': (
            'sandbox allow-scripts allow-forms allow-downloads; '
            "default-src 'self' data: blob: 'unsafe-inline' 'unsafe-eval'; "
            f"connect-src 'self' {websocket_origin}; frame-ancestors {record['parent_origin']}; "
            f"base-uri 'none'; form-action {origin}"
        ),
    }


def harden_browser_runtime(body, record, capability):
    text = body.decode('utf-8')
    marker = "if(event.source !== parent || event.data?.type !== 'terminal-browser-command') return;"
    if 'const config = window.__terminalBrowser;' not in text or marker not in text:
        raise HTTPException(502, 'Terminal browser runtime is incompatible with isolated preview')
    settings = json.dumps({'ticket': capability, 'parent_origin': record['parent_origin']}).replace('<', '\\u003c')
    text = text.replace('parent.postMessage(', '_previewPost(')
    text = text.replace(
        'const config = window.__terminalBrowser;',
        'const config = window.__terminalBrowser;\n'
        f'  const preview = {settings};\n'
        '  const _previewPost = message => parent.postMessage({...message, preview_ticket: preview.ticket}, preview.parent_origin);',
    )
    text = text.replace(
        marker,
        "if(event.source !== parent || event.origin !== preview.parent_origin || event.data?.preview_ticket !== preview.ticket || event.data?.type !== 'terminal-browser-command') return;",
    )
    return text.encode()


async def watch_preview(request):
    while True:
        await asyncio.sleep(2)
        await authorize_preview(request)


async def await_preview_operation(watcher, operation, *args, **kwargs):
    async def run():
        if watcher.done():
            watcher.result()
        return await operation(*args, **kwargs)

    pending = asyncio.create_task(run())
    try:
        done, _ = await asyncio.wait((pending, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            watcher.result()
        return pending.result()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)


async def release(response, client):
    if response is not None:
        result = response.release()
        if inspect.isawaitable(result):
            await result
    await client.close()


async def preview_http(scope, receive, send):
    request = Request(scope, receive)
    client = upstream = watcher = None
    started = False

    async def send_response(message):
        nonlocal started
        if message['type'] == 'http.response.start':
            if watcher is not None:
                await await_preview_operation(watcher, authorize_preview, request)
            started = True
        await send(message)

    try:
        record, user, base, headers, cookies = await authorize_preview(request)
        if not scope['preview_path']:
            entry_headers = response_headers(record, 'text/html; charset=utf-8')
            if record.get('entry_path'):
                entry_headers['Location'] = '/p/' + scope['preview_token'] + record['entry_path']
                await Response(status_code=302, headers=entry_headers)(scope, receive, send_response)
            else:
                await Response(
                    '<!doctype html><title>Terminal preview</title><p>Choose a URL in the browser panel.</p>',
                    headers=entry_headers,
                )(scope, receive, send_response)
            return
        path = target_path(record, scope['preview_path'])
        if request.method == 'OPTIONS':
            await Response(status_code=204, headers=response_headers(record, 'text/plain'))(
                scope, receive, send_response
            )
            return
        for name in ('accept', 'content-type', 'range', 'last-event-id', 'content-length'):
            if value := request.headers.get(name):
                headers[name] = value
        if request.headers.get('origin'):
            headers['Origin'] = 'null'  # Open Terminal substitutes the target site's origin.
        upstream_prefix = (
            '/browser/view/' + quote(record['session_id'], safe='') + '/' + quote(record['upstream_token'], safe='')
        )
        preview_prefix = '/p/' + scope['preview_token']
        url = base + upstream_prefix + '/' + path
        client = get_terminal_client(
            base,
            user.id,
            timeout=aiohttp.ClientTimeout(total=None, connect=15),
            trust_env=True,
            **relay_options(record),
        )
        watcher = asyncio.create_task(watch_preview(request))
        upstream = await await_preview_operation(
            watcher,
            client.request,
            request.method,
            url,
            headers=headers,
            cookies=cookies,
            params=request.query_params,
            data=request.stream()
            if request.headers.get('content-length') or request.headers.get('transfer-encoding')
            else None,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            allow_redirects=False,
        )
        safe_headers = response_headers(record, upstream.headers.get('content-type', 'application/octet-stream'))
        if location := upstream.headers.get('location'):
            safe_headers['Location'] = rewrite_preview_location(
                url, location, urlsplit(base).path + upstream_prefix, preview_prefix
            )

        async def chunks():
            iterator = upstream.content.iter_any().__aiter__()
            while True:
                try:
                    yield await await_preview_operation(watcher, anext, iterator)
                except StopAsyncIteration:
                    break

        content_type = upstream.headers.get('content-type', '').lower()
        mime = content_type.partition(';')[0].strip()
        text_resource = mime in TEXT_RESOURCE_TYPES or mime.startswith('text/') or mime.endswith(('+json', '+xml'))
        if path == 'runtime.js' or (text_resource and mime != 'text/event-stream'):
            body = bytearray()
            async for chunk in chunks():
                body.extend(chunk)
                if len(body) > 16 * 1024 * 1024:
                    raise HTTPException(413, 'Preview resource exceeds 16 MiB')
            body = (
                harden_browser_runtime(bytes(body), record, scope['preview_token'])
                if path == 'runtime.js'
                else rewrite_preview_body(
                    bytes(body), content_type, base, urlsplit(base).path + upstream_prefix, preview_prefix
                )
            )
            await Response(body, status_code=upstream.status, headers=safe_headers)(scope, receive, send_response)
            return

        async def stream():
            pending = b''
            async for chunk in chunks():
                if mime != 'text/event-stream':
                    yield chunk
                    continue
                # An SSE event may span transport chunks. Rewrite only complete
                # events so a capability cannot leak across a chunk boundary.
                pending += chunk
                if len(pending) > 16 * 1024 * 1024:
                    raise HTTPException(413, 'Preview event exceeds 16 MiB')
                while match := re.search(rb'\r\n\r\n|\n\n|\r\r', pending):
                    event, pending = pending[: match.end()], pending[match.end() :]
                    yield rewrite_preview_body(
                        event, content_type, base, urlsplit(base).path + upstream_prefix, preview_prefix
                    )
            if pending:
                yield rewrite_preview_body(
                    pending, content_type, base, urlsplit(base).path + upstream_prefix, preview_prefix
                )

        await StreamingResponse(stream(), status_code=upstream.status, headers=safe_headers)(
            scope, receive, send_response
        )
    except HTTPException as error:
        if started:
            # The stream closes on revoked access; never send a second status line.
            with suppress(Exception):
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            return
        await JSONResponse(
            {'detail': error.detail},
            status_code=error.status_code,
            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer', **(error.headers or {})},
        )(scope, receive, send)
    except (aiohttp.ClientError, TimeoutError, ConnectionError):
        if started:
            with suppress(Exception):
                await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
            return
        await JSONResponse(
            {'detail': 'Terminal preview is unavailable'},
            status_code=503,
            headers={'Cache-Control': 'no-store', 'Retry-After': '2'},
        )(scope, receive, send)
    finally:
        if watcher:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        if client:
            await release(upstream, client)


async def preview_websocket(scope, receive, send):
    ws = WebSocket(scope, receive, send)
    watcher = None
    try:
        if scope['preview_path'] != 'ws':
            raise HTTPException(404, 'Preview socket not found')
        record, _, _, _, _ = await authorize_preview(ws)
        if ws.headers.get('origin') != 'null':
            raise HTTPException(403, 'Preview socket requires its sandboxed page')
        watcher = asyncio.create_task(watch_preview(ws))
        await await_preview_operation(watcher, ws.accept)
        # This is the upstream browser protocol, not a main-site Socket.IO channel.
        async with asyncio.timeout(10):
            opening = await await_preview_operation(watcher, ws.receive_json)
        if not isinstance(opening, dict):
            raise HTTPException(400, 'Invalid browser socket target')
        target = urlsplit(str(opening.get('url', '')))
        if target.scheme not in {'ws', 'wss'} or target.username or target.password or not target.hostname:
            raise HTTPException(400, 'Invalid browser socket target')
        port = target_port(target)
        if record['kind'] == 'port' and (
            target.hostname != 'localhost' or port != record['port'] or target.scheme != 'ws'
        ):
            raise HTTPException(403, 'Socket target is outside the authorized terminal port')
        record, user, base, headers, cookies = await await_preview_operation(watcher, authorize_preview, ws)
        url = (
            base
            + '/browser/view/'
            + quote(record['session_id'], safe='')
            + '/'
            + quote(record['upstream_token'], safe='')
            + '/ws'
        )
        async with AsyncExitStack() as stack:
            client = await stack.enter_async_context(
                get_terminal_client(base, user.id, trust_env=True, cookies=cookies, **relay_options(record))
            )
            upstream = await await_preview_operation(
                watcher,
                lambda: stack.enter_async_context(
                    client.ws_connect(
                        url, headers=headers, ssl=AIOHTTP_CLIENT_SESSION_SSL, max_msg_size=16 * 1024 * 1024
                    )
                ),
            )
            await await_preview_operation(watcher, authorize_preview, ws)
            await await_preview_operation(watcher, upstream.send_str, json.dumps(opening))
            from open_webui.routers.terminals import relay_from_browser, relay_to_browser

            tasks = (
                asyncio.create_task(relay_from_browser(ws, upstream)),
                asyncio.create_task(relay_to_browser(ws, upstream)),
            )
            try:
                done, _ = await asyncio.wait((*tasks, watcher), return_when=asyncio.FIRST_COMPLETED)
                if watcher in done:
                    watcher.result()
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except HTTPException as error:
        with suppress(Exception):
            await ws.close(code=1013 if error.status_code == 503 else 1008)
    except Exception:
        with suppress(Exception):
            await ws.close(code=1013)
    finally:
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
        with suppress(Exception):
            await ws.close()
