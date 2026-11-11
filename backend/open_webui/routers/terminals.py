"""Reverse proxy for admin-configured terminal servers.

Routes:
  GET  /                         — list terminals the user has access to
  *    /{server_id}/{path:path}  — proxy request to terminal server
"""

import asyncio
import json
import inspect
import logging
import re
from contextlib import suppress
from html import escape
import posixpath
from urllib.parse import quote, unquote, urljoin, urlsplit

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from open_webui.config import TERMINAL_PROXY_HEADERS
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, ENABLE_TOOL_SERVERS, WEBUI_SECRET_KEY
from open_webui.events import EVENTS, publish_event
from open_webui.models.config import Config
from open_webui.models.chats import Chats
from open_webui.models.groups import Groups
from open_webui.utils.access_control import has_connection_access
from open_webui.utils.auth import get_verified_user, get_verified_user_by_token
from open_webui.utils.chat_id import is_saved_chat_id
from open_webui.utils.browser import browser_access, browser_ticket
from open_webui.utils.tether import RelayInterrupted, get_terminal_connections, get_terminal_client
from open_webui.utils.headers import bearer_auth_header, normalize_bearer_token
from open_webui.utils.json_codec import JSONCodec
from open_webui.utils.terminal_files import (
    IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    add_file_delivery_links,
    detect_image_type,
    file_response_headers,
    verify_file_reference,
)
from open_webui.utils.terminals import (
    TERMINAL_CONTEXT_HEADER,
    get_terminal_server_url,
    is_terminal_orchestrator,
    terminal_chat_uploads,
    terminal_context_available,
    terminal_context_config,
    terminal_context_id,
    terminal_contexts,
)
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from yarl import URL

log = logging.getLogger(__name__)

router = APIRouter()

# Response headers from a terminal are untrusted on the WebUI origin. All
# security/MIME headers are set here, including after custom proxy headers.
SAFE_PROXY_HEADERS = {'content-language', 'retry-after', 'x-accel-buffering'}
STREAMING_CONTENT_TYPES = ('application/octet-stream', 'image/', 'application/pdf', 'text/event-stream')
ADMIN_API_PATHS = ('api/v1/policies', 'api/v1/status', 'api/v1/terminals')


def _port_browser_html(body: bytes, prefix: str, port: int) -> bytes:
    """Keep port links inside the proxy and report navigation from sandboxed frames."""
    config = json.dumps({'prefix': prefix, 'origin': f'http://localhost:{port}'}).replace('<', '\\u003c')
    script = r"""
(() => {
  const config = __CONFIG__;
  const remote = (value = location.href) => {
    const url = new URL(value, location.href);
    return url.pathname.startsWith(config.prefix)
      ? config.origin + '/' + url.pathname.slice(config.prefix.length) + url.search + url.hash
      : url.href;
  };
  const destination = value => new URL(value, remote());
  const proxy = value => {
    const url = destination(value);
    return url.origin === config.origin ? config.prefix + url.pathname.slice(1) + url.search + url.hash : url.href;
  };
  const report = () => parent.postMessage({type: 'terminal-browser-state', url: remote(), title: document.title}, '*');
  for (const method of ['pushState', 'replaceState']) {
    const original = history[method].bind(history);
    history[method] = (state, title, value) => {
      try { const result = original(state, title, value == null ? value : proxy(value)); queueMicrotask(report); return result; }
      catch (error) {
        if (error.name !== 'SecurityError' || value == null) throw error;
        parent.postMessage({type: 'terminal-browser-navigate', url: destination(value).href, replace: method === 'replaceState'}, '*');
      }
    };
  }
  addEventListener('popstate', report);
  addEventListener('hashchange', report);
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.hasAttribute('download')) return;
    const url = destination(remote(link.href));
    if (!/^https?:$/.test(url.protocol)) return;
    event.preventDefault();
    parent.postMessage({type: 'terminal-browser-navigate', url: url.href}, '*');
  }, true);
  document.addEventListener('submit', event => {
    const form = event.target;
    if (form instanceof HTMLFormElement) form.action = proxy(form.getAttribute('action') || remote());
  }, true);
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => nativeFetch(input instanceof Request ? new Request(proxy(input.url), input) : proxy(input), init);
  const nativeOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) { return nativeOpen.call(this, method, proxy(url), ...rest); };
  addEventListener('DOMContentLoaded', () => {
    new MutationObserver(report).observe(document.querySelector('title') || document.head || document.documentElement, {childList:true,subtree:true,characterData:true});
    report();
  });
  report();
})();
""".replace('__CONFIG__', config)
    html = body.decode('utf-8', errors='replace')
    # Root-relative assets otherwise resolve against WebUI instead of the port.
    html = re.sub(
        r"""(\b(?:href|src|action)\s*=\s*["'])/(?!/)""",
        lambda match: match[1] + escape(prefix, quote=True),
        html,
        flags=re.I,
    )
    bridge = '<script>' + script + '</script>'
    head = re.search(r'<head\b[^>]*>', html, re.I)
    if head:
        html = html[: head.end()] + bridge + html[head.end() :]
    else:
        html = bridge + html
    return html.encode('utf-8')


def _sanitize_proxy_path(path: str) -> str | None:
    """Sanitize a proxy path to prevent directory traversal / SSRF.

    Returns the cleaned path, or None if the path is invalid.
    Trailing slashes are preserved — many upstream frameworks treat
    ``/path`` and ``/path/`` differently.
    """
    # Decode until stable: a single unquote pass leaves %252e%252e as %2e%2e,
    # which the upstream then re-decodes into '..', bypassing the check below.
    decoded = path
    for _ in range(8):
        once = unquote(decoded)
        if once == decoded:
            break
        decoded = once
    # Fail closed: still encoded after the cap means the upstream would decode further into traversal.
    if unquote(decoded) != decoded:
        return None
    # posixpath splits on '/' only, so 'a/..\..\b' survives normpath as one component.
    # Upstreams that treat '\' as a separator would resolve it, so reject outright.
    # URL parsers strip tabs/newlines and reinterpret ?/#. Validate before
    # route policy checks so the path we authorize is exactly the path sent.
    if any(char in '\\?#' or ord(char) < 32 or ord(char) == 127 for char in decoded):
        return None
    had_trailing_slash = decoded.endswith('/')
    normalized = posixpath.normpath(decoded)
    # Remove any leading slashes that would reset the base
    cleaned = normalized.lstrip('/')
    # Reject if normpath resolved to parent traversal or current-dir only
    if cleaned.startswith('..') or cleaned == '.':
        return None
    # Restore trailing slash if the original path had one
    if had_trailing_slash and cleaned and not cleaned.endswith('/'):
        cleaned += '/'
    return cleaned


@router.api_route(
    '/{server_id}/browser/view/{sid}/{ticket}/{path:path}',
    methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'],
)
async def browser_http(request: Request, server_id: str, sid: str, ticket: str, path: str):
    raise HTTPException(403, 'Terminal pages require the independent preview hostname')


async def relay_from_browser(ws, upstream):
    while True:
        msg = await ws.receive()
        if msg['type'] == 'websocket.disconnect':
            return
        if msg.get('bytes') is not None:
            await upstream.send_bytes(msg['bytes'])
        elif msg.get('text') is not None:
            await upstream.send_str(msg['text'])


async def relay_to_browser(ws, upstream):
    async for msg in upstream:
        if msg.type == aiohttp.WSMsgType.TEXT:
            await ws.send_text(msg.data)
        elif msg.type == aiohttp.WSMsgType.BINARY:
            await ws.send_bytes(msg.data)
        else:
            return


async def watch_access(server_id, sid, ticket, request):
    while True:
        await asyncio.sleep(10)
        current_user, _ = await browser_access(server_id, sid, ticket, request)
        latest = await get_terminal_connections(current_user)
        current = next((c for c in latest if c.get('id') == server_id), None)
        if (
            not current
            or not current.get('enabled', True)
            or not ENABLE_TOOL_SERVERS
            or not await has_connection_access(current_user, current)
            or not terminal_context_available(current, 'chat')
        ):
            return


@router.websocket('/{server_id}/browser/view/{sid}/{ticket}/{kind}')
async def browser_ws(ws: WebSocket, server_id: str, sid: str, ticket: str, kind: str):
    try:
        user, data = await browser_access(server_id, sid, ticket, ws)
        connections = await get_terminal_connections(user)
        connection = next((c for c in connections if c.get('id') == server_id), None)
        if (
            kind not in ('stream', 'vnc')
            or not ENABLE_TOOL_SERVERS
            or not connection
            or not connection.get('enabled', True)
            or not await has_connection_access(user, connection)
            or not terminal_context_available(connection, 'chat')
        ):
            raise HTTPException(403, 'Browser access denied')
        chat = data.get('chat', '')
        context = data['context_id']
        if terminal_context_config(connection, 'chat').get('context_id') == 'chat_id' and not context:
            raise HTTPException(403, 'Saved chat required')
    except HTTPException as error:
        await ws.close(code=1013 if error.status_code == 503 else 4001)
        return
    await ws.accept()
    base = get_terminal_server_url(connection).replace('https://', 'wss://').replace('http://', 'ws://')
    url = f'{base}/browser/view/{sid}/{quote(data["capability"], safe="")}/{kind}'
    headers = {'X-User-Id': user.id}
    if chat:
        headers['X-Session-Id'] = chat
    if context:
        headers[TERMINAL_CONTEXT_HEADER] = context
    if data['relay'].get('authorization'):
        headers['Authorization'] = data['relay']['authorization']
    tasks = []
    try:
        async with get_terminal_client(
            base, user.id, cookies=data['relay'].get('cookies', {}), trust_env=True
        ) as client:
            async with client.ws_connect(
                url,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
                max_msg_size=16 * 1024 * 1024,
            ) as upstream:
                tasks = [
                    asyncio.create_task(relay_from_browser(ws, upstream)),
                    asyncio.create_task(relay_to_browser(ws, upstream)),
                    asyncio.create_task(watch_access(server_id, sid, ticket, ws)),
                ]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except aiohttp.WSServerHandshakeError as exc:
        await ws.close(code=4004 if exc.status in (401, 403, 404) else 1011)
    except Exception as exc:
        log.debug('Browser relay disconnected: %s', type(exc).__name__)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with suppress(Exception):
            await ws.close(code=1011)


@router.get('/')
async def list_terminal_servers(request: Request, user=Depends(get_verified_user)):
    """Return terminal servers the authenticated user has access to."""
    if not ENABLE_TOOL_SERVERS:
        return []

    connections = await get_terminal_connections(user)
    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, include_inherited=True)}

    return [
        {
            'id': connection.get('id', ''),
            'url': '' if connection.get('owner_id') else connection.get('url', ''),
            'name': connection.get('name', ''),
            'contexts': terminal_contexts(connection),
            'config': {'chat_uploads': terminal_chat_uploads(connection)},
        }
        for connection in connections
        if connection.get('enabled', True) and await has_connection_access(user, connection, user_group_ids)
    ]


PROXY_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS']


def _terminal_error(status: int, detail: str):
    headers = file_response_headers('error')
    headers['Content-Type'] = 'application/json'
    headers.pop('Content-Disposition')
    return JSONResponse({'detail': detail}, status_code=status, headers=headers)


def _cross_origin_file_request(request: Request) -> bool:
    origin = request.headers.get('origin')
    if origin:
        # Fetch Metadata may be absent on ordinary HTTP deployments. CORS
        # requests still carry Origin, independently of the app's CORS policy.
        try:
            supplied = urlsplit(origin)
            expected = urlsplit(str(request.url))
            if (
                supplied.username
                or supplied.password
                or supplied.path not in {'', '/'}
                or supplied.query
                or supplied.fragment
            ):
                return True

            def authority(url):
                return url.scheme, url.hostname, url.port or {'http': 80, 'https': 443}.get(url.scheme)

            if authority(supplied) != authority(expected):
                return True
        except ValueError:
            return True
    return (
        request.headers.get('sec-fetch-site') in {'same-site', 'cross-site'}
        and request.headers.get('sec-fetch-mode') != 'navigate'
    )


async def _temporary_terminal_scope(request, user, metadata, *, signed_delivery=False):
    """Authenticate browsing against the live epoch; delivered artifacts keep their signed origin."""
    from open_webui.utils.context_store_errors import ContextStoreError
    from open_webui.utils.temporary_contexts import temporary_repository

    chat_id = metadata.get('chat_id') or ''
    if not chat_id.startswith('temporary:'):
        return
    identity = metadata.get('temporary_context') if signed_delivery else None
    if identity is None:
        context_id = request.headers.get('X-Temporary-Context-Id') or request.query_params.get('temporary_context_id')
        epoch = request.headers.get('X-Temporary-Context-Epoch') or request.query_params.get('temporary_context_epoch')
        if context_id or epoch:
            identity = {'id': context_id, 'epoch': epoch}
    if identity is None:
        if signed_delivery:
            return  # Legacy delivered artifacts already passed signature/owner checks.
        session_id = chat_id.removeprefix('temporary:')
        if len(session_id) == 32 and all(c in '0123456789abcdef' for c in session_id):
            raise HTTPException(404, 'Temporary context identity is required')
        from open_webui.socket.main import get_event_call_session_error

        error = await get_event_call_session_error(
            session_id,
            {
                'user_id': user.id,
                'chat_id': chat_id,
                'message_id': 'terminal-access',
            },
        )
        if error:
            unavailable = 'unavailable' in error.lower() or 'retry' in error.lower()
            raise HTTPException(503 if unavailable else 403, error)
        return
    if (
        not isinstance(identity, dict)
        or not isinstance(identity.get('id'), str)
        or not isinstance(identity.get('epoch'), str)
        or metadata.get('chat_id') != f'temporary:{identity["id"]}'
    ):
        raise HTTPException(404, 'Temporary context not found')
    if not signed_delivery:
        try:
            await temporary_repository(request).read(
                identity['id'], user.id, identity['epoch'], allow_closed=metadata.get('_temporary_cleanup') is True
            )
        except ContextStoreError as error:
            raise HTTPException(error.status_code, error.detail()) from None
    metadata['temporary_context'] = {key: identity[key] for key in ('id', 'epoch')}


async def _http_terminal_context(server_id, request, user, metadata, *, require_context=False):
    if not ENABLE_TOOL_SERVERS:
        raise HTTPException(403, 'Tool servers are disabled')
    connections = await get_terminal_connections(user)
    connection = next((c for c in connections if c.get('id') == server_id), None)
    if connection is None:
        raise HTTPException(404, 'Terminal server not found')
    if not connection.get('enabled', True):
        raise HTTPException(403, 'Terminal server disabled')
    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id, include_inherited=True)}
    if not await has_connection_access(user, connection, user_group_ids):
        raise HTTPException(403, 'Access denied')
    base_url = get_terminal_server_url(connection)
    if not base_url:
        raise HTTPException(503, 'Terminal server URL not configured')
    session_id = metadata.get('chat_id')
    await _temporary_terminal_scope(request, user, metadata, signed_delivery=metadata.get('_signed_delivery') is True)
    if is_saved_chat_id(session_id) and not await Chats.get_accessible_chat_by_id(session_id, user):
        raise HTTPException(404, 'Chat not found')
    context = 'automation' if metadata.get('automation_id') else 'chat'
    if not terminal_context_available(connection, context):
        raise HTTPException(403, 'Terminal server is not available in this context')
    context_id = terminal_context_id(connection, metadata, context)
    context_config = terminal_context_config(connection, context)
    if (
        (require_context or session_id or metadata.get('automation_id'))
        and context_config.get('context_id') in {'chat_id', 'automation_id'}
        and not context_id
    ):
        raise HTTPException(409, 'A saved context is required for this terminal')
    headers = {'X-User-Id': user.id}
    if session_id:
        headers['X-Session-Id'] = session_id
    if context_id:
        headers[TERMINAL_CONTEXT_HEADER] = context_id
    cookies = getattr(request, 'cookies', {}) if connection.get('forward_cookies', False) else {}
    auth_type = connection.get('auth_type', 'bearer')
    if auth_type == 'bearer':
        headers.update(bearer_auth_header(connection.get('key', '')))
    elif auth_type == 'session':
        token = getattr(request.state, 'token', None)
        credentials = token.credentials if token else request.cookies.get('token')
        if not credentials:
            credentials = request.headers.get('authorization', '').removeprefix('Bearer ')
        headers.update(bearer_auth_header(credentials))
    elif auth_type == 'system_oauth':
        try:
            if request.cookies.get('oauth_session_id'):
                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                    user.id,
                    request.cookies['oauth_session_id'],
                )
                if oauth_token:
                    headers.update(bearer_auth_header(oauth_token.get('access_token', '')))
        except Exception:
            raise HTTPException(503, 'Terminal authentication unavailable') from None
    return base_url, headers, cookies, context_id


@router.get('/{server_id}/files/download')
async def download_terminal_file(server_id: str, ref: str, request: Request, user=Depends(get_verified_user)):
    return await _proxy_http(server_id, 'files/view', request, user, reference=ref)


@router.get('/{server_id}/files/image')
async def display_terminal_image(server_id: str, ref: str, request: Request, user=Depends(get_verified_user)):
    return await _proxy_http(server_id, 'files/view', request, user, reference=ref, image_only=True)


@router.api_route('/{server_id}/{path:path}', methods=PROXY_METHODS)
@router.api_route('/{server_id}/chats/{chat_id}/{path:path}', methods=PROXY_METHODS)
async def proxy_terminal(server_id: str, path: str, request: Request, user=Depends(get_verified_user)):
    return await _proxy_http(server_id, path, request, user)


async def _proxy_http(server_id, path, request, user, *, reference=None, image_only=False):
    safe_path = _sanitize_proxy_path(path)
    if safe_path is None:
        return _terminal_error(400, 'Invalid path')
    if any(safe_path.rstrip('/') == prefix or safe_path.startswith(prefix + '/') for prefix in ADMIN_API_PATHS):
        return _terminal_error(403, 'Path not allowed')
    if safe_path.rstrip('/') in {'proxy', 'browser/view'} or safe_path.startswith(('proxy/', 'browser/view/')):
        return _terminal_error(403, 'Terminal web previews are disabled on this origin')
    # Do not let other methods or encoded aliases fall through to an upstream
    # endpoint with the same name as a protected delivery endpoint.
    if safe_path.rstrip('/') in {'files/download', 'files/image'}:
        return _terminal_error(405, 'Method not allowed')
    metadata = {'chat_id': request.path_params.get('chat_id') or request.headers.get('x-session-id')}
    if request.method == 'DELETE':
        segments = safe_path.strip('/').split('/')
        metadata['_temporary_cleanup'] = (len(segments) == 3 and segments[:2] == ['api', 'terminals']) or (
            len(segments) == 2 and segments[0] == 'notebooks'
        )
    payload = None
    if reference is not None:
        try:
            payload = verify_file_reference(reference, WEBUI_SECRET_KEY)
        except ValueError:
            return _terminal_error(403, 'Invalid file reference')
        if payload['owner_id'] != user.id or payload['server_id'] != server_id:
            return _terminal_error(403, 'Access denied')
        metadata = {**payload, '_signed_delivery': True}
    try:
        base_url, headers, cookies, context_id = await _http_terminal_context(
            server_id,
            request,
            user,
            metadata,
            require_context=payload is not None or safe_path.startswith('files/'),
        )
    except HTTPException as error:
        return _terminal_error(error.status_code, error.detail)
    if payload is not None and context_id != payload.get('context_id'):
        return _terminal_error(403, 'Terminal context has changed')

    query = (
        [('path', payload['path'])]
        if payload
        else [
            (key, value)
            for key, value in request.query_params.multi_items()
            if key not in {'temporary_context_id', 'temporary_context_epoch'}
        ]
    )
    filename = payload['path'] if payload else request.query_params.get('path', safe_path)
    raw_file = (
        payload is not None
        or safe_path.rstrip('/') in {'files/view', 'files/archive', 'files/serve'}
        or safe_path.startswith('files/serve/')
    )
    # CORP protects no-cors embeds. Also reject cross-origin CORS subresources
    # even when the application's global CORS policy permits credentials.
    # Top-level links remain usable for authenticated downloads/navigation.
    if raw_file and _cross_origin_file_request(request):
        return _terminal_error(403, 'Cross-origin file embedding is not allowed')

    content_type = request.headers.get('content-type')
    if content_type:
        headers['Content-Type'] = content_type
    session = get_terminal_client(
        base_url,
        user.id,
        timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=60),
        trust_env=True,
    )
    upstream = None
    streaming = False
    closed = False

    async def cleanup():
        nonlocal closed
        if not closed:
            closed = True
            if upstream is not None:
                released = upstream.release()
                if inspect.isawaitable(released):
                    await released
            await session.close()

    try:
        body = await request.body()
        upstream = await session.request(
            method=request.method,
            url=f'{base_url}/{safe_path}',
            params=query,
            headers=headers,
            cookies=cookies,
            data=body or None,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            allow_redirects=False,
        )
        if 300 <= upstream.status < 400:
            return _terminal_error(502, 'Terminal redirect refused')
        if upstream.status >= 400:
            status = upstream.status if upstream.status < 500 else 502
            return _terminal_error(status, 'Terminal file not found' if status == 404 else 'Terminal request failed')

        mime = upstream.headers.get('content-type', '').split(';', 1)[0].strip().lower()
        response_headers = {}
        for source in (upstream.headers, TERMINAL_PROXY_HEADERS or {}):
            response_headers.update(
                {key.lower(): value for key, value in source.items() if key.lower() in SAFE_PROXY_HEADERS}
            )
        response_headers.update(file_response_headers(filename))

        if not raw_file and (mime == 'application/json' or mime.endswith('+json')):
            data = await upstream.read()
            if safe_path == 'files/display':
                result = add_file_delivery_links(
                    JSONCodec.loads(data),
                    server_id=server_id,
                    owner_id=user.id,
                    metadata=metadata,
                    secret=WEBUI_SECRET_KEY,
                    context_id=context_id,
                )
                data = JSONCodec.dumps(result).encode('utf-8')
            if safe_path.startswith('browser/sessions') and request.method in {'GET', 'POST', 'PATCH'}:
                browser_session = JSONCodec.loads(data)
                if isinstance(browser_session, dict) and browser_session.get('id') and browser_session.get('token'):
                    browser_session['token'] = await browser_ticket(
                        server_id, user.id, metadata, browser_session, request
                    )
                    data = JSONCodec.dumps(browser_session).encode('utf-8')
            response_headers['Content-Type'] = 'application/json'
            response_headers.pop('Content-Disposition')
            return Response(data, status_code=upstream.status, headers=response_headers)

        prefix = b''
        if image_only or (
            payload is None
            and (mime.startswith('image/') or posixpath.splitext(filename)[1].lower() in IMAGE_EXTENSIONS)
        ):
            chunks = []
            size = 0
            while size <= MAX_IMAGE_BYTES:
                chunk = await upstream.content.read(min(65536, MAX_IMAGE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            prefix = b''.join(chunks)
            if image_only and size > MAX_IMAGE_BYTES:
                return _terminal_error(413, 'Image is too large to preview; download the file instead')
            image_type = await asyncio.to_thread(detect_image_type, prefix) if size <= MAX_IMAGE_BYTES else None
            if image_only and image_type is None:
                return _terminal_error(415, 'Unsupported or invalid image')
            if image_type:
                response_headers.update(file_response_headers(filename, image_type))
        elif not raw_file and mime == 'text/event-stream':
            response_headers['Content-Type'] = 'text/event-stream'
            response_headers.pop('Content-Disposition')

        async def stream():
            try:
                if prefix:
                    yield prefix
                async for chunk in upstream.content.iter_chunked(65536):
                    yield chunk
            finally:
                await cleanup()

        response = StreamingResponse(
            stream(),
            status_code=upstream.status,
            headers=response_headers,
            background=BackgroundTask(cleanup),
        )
        streaming = True
        return response
    except ClientDisconnect:
        return _terminal_error(499, 'Client disconnected')
    except RelayInterrupted as error:
        return JSONResponse(error.result(), status_code=502, headers={'Cache-Control': 'no-store'})
    except TimeoutError:
        return _terminal_error(504, 'Terminal request timed out')
    except aiohttp.ClientError:
        return _terminal_error(502, 'Terminal unavailable')
    except HTTPException as error:
        # Preserve retryable authorization/storage errors, without upstream details.
        return JSONResponse(
            {
                'detail': 'Terminal authorization unavailable'
                if error.status_code == 503
                else 'Terminal authorization failed'
            },
            status_code=error.status_code,
            headers={'Cache-Control': 'no-store', **({'Retry-After': '2'} if error.status_code == 503 else {})},
        )
    except Exception as error:
        # Do not expose upstream URLs, response bodies, paths or credentials.
        log.error('Terminal proxy failed (%s)', type(error).__name__)
        return _terminal_error(502, 'Terminal request failed')
    finally:
        if not streaming:
            await cleanup()


# WebSocket proxy for interactive terminal sessions


async def _resolve_authenticated_connection(ws: WebSocket, server_id: str):
    """Authenticate a WebSocket via first-message auth and resolve the terminal server.

    The client must send ``{"type": "auth", "token": "<jwt>"}`` as its first
    message after connecting.

    Returns ``(user, connection, chat_id, token)`` on success, or ``None`` after
    closing *ws* with an appropriate error code.
    """
    # First-message authentication
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        payload = JSONCodec.loads(raw)
        if payload.get('type') != 'auth':
            await ws.close(code=4001, reason='Expected auth message')
            return None
        token = payload.get('token', '')
    except (TimeoutError, JSONCodec.JSONDecodeError):
        await ws.close(code=4001, reason='Auth timeout or invalid payload')
        return None
    except WebSocketDisconnect:
        return None
    except Exception:
        await ws.close(code=4001, reason='Invalid token')
        return None

    result = await _resolve_terminal_access(ws, server_id, token)
    if result is None:
        return None
    user, connection = result
    ws.state.auth = payload
    chat_id = payload.get('chat_id', '')
    chat_id = chat_id if isinstance(chat_id, str) else ''
    if is_saved_chat_id(chat_id) and not await Chats.get_accessible_chat_by_id(chat_id, user):
        await ws.close(code=4003, reason='Chat access denied')
        return None
    metadata = {'chat_id': chat_id}
    try:
        await _temporary_terminal_scope(ws, user, metadata)
    except HTTPException as error:
        await ws.close(code=1013 if error.status_code == 503 else 4003, reason='Temporary context access denied')
        return None
    ws.state.temporary_context = metadata.get('temporary_context')
    return user, connection, chat_id, token


async def _resolve_terminal_access(ws: WebSocket, server_id: str, token: str):
    """Resolve current access for both the handshake and an open terminal session."""
    try:
        user = await get_verified_user_by_token(token, getattr(ws.app.state, 'redis', None))
        if user is None:
            await ws.close(code=4001, reason='Invalid token')
            return None
    except HTTPException as error:
        if error.status_code == 503:
            await ws.close(code=1013, reason='Authentication temporarily unavailable; retry later')
        else:
            await ws.close(code=4001, reason='Invalid token')
        return None
    except Exception:
        await ws.close(code=4001, reason='Invalid token')
        return None

    # Resolve terminal server
    if not ENABLE_TOOL_SERVERS:
        await ws.close(code=4003, reason='Tool servers are disabled')
        return None
    connections = await get_terminal_connections(user)
    connection = next((c for c in connections if c.get('id') == server_id), None)

    if connection is None:
        if server_id.startswith('tether:'):
            await ws.close(code=1013, reason='Tether is offline. Reconnecting.')
        else:
            await ws.close(code=4004, reason='Terminal server not found')
        return None

    if not connection.get('enabled', True):
        await ws.close(code=4003, reason='Terminal server disabled')
        return None

    if not await has_connection_access(user, connection):
        await ws.close(code=4003, reason='Access denied')
        return None

    if not terminal_context_available(connection, 'chat'):
        await ws.close(code=4003, reason='Terminal server is not available in chats')
        return None
    return user, connection


@router.websocket('/{server_id}/events')
async def ws_events(ws: WebSocket, server_id: str):
    await _proxy_terminal_websocket(ws, server_id)


@router.websocket('/{server_id}/desktop/stream')
async def desktop_ws(ws: WebSocket, server_id: str):
    await _proxy_terminal_websocket(ws, server_id, stream_path='desktop/stream')


@router.websocket('/{server_id}/desktop/audio')
async def desktop_audio_ws(ws: WebSocket, server_id: str):
    await _proxy_terminal_websocket(ws, server_id, stream_path='desktop/audio')


@router.websocket('/{server_id}/execute')
async def ws_processes(ws: WebSocket, server_id: str):
    await _proxy_terminal_websocket(ws, server_id, stream_path='execute')


@router.websocket('/{server_id}/execute/{process_id}/status')
async def ws_process_output(ws: WebSocket, server_id: str, process_id: str):
    from urllib.parse import quote

    await _proxy_terminal_websocket(ws, server_id, stream_path=f'execute/{quote(process_id, safe="")}/status')


@router.websocket('/{server_id}/api/terminals/{session_id}')
async def ws_terminal(ws: WebSocket, server_id: str, session_id: str):
    await _proxy_terminal_websocket(ws, server_id, session_id)


async def _proxy_terminal_websocket(
    ws: WebSocket,
    server_id: str,
    session_id: str | None = None,
    stream_path: str = 'events',
):
    """Proxy terminal sessions and filesystem notifications to a terminal server.

    Uses first-message auth: the client sends ``{"type": "auth", "token": "<jwt>"}``
    as its first message. The proxy validates the JWT, then connects to the
    upstream terminal server using the configured terminal auth mode.
    """
    await ws.accept()

    result = await _resolve_authenticated_connection(ws, server_id)
    if result is None:
        return
    user, connection, chat_id, token = result

    base_url = get_terminal_server_url(connection)
    if not base_url:
        await ws.close(code=4003, reason='Terminal server URL not configured')
        return

    # Build upstream WebSocket URL (no token in URL)
    ws_base = base_url.replace('https://', 'wss://').replace('http://', 'ws://')

    upstream_params = {}
    # For orchestrator-backed servers, pass user_id
    upstream_params['user_id'] = user.id
    if stream_path.startswith('execute/'):
        for key in ('offset', 'tail'):
            if key in ws.query_params:
                upstream_params[key] = ws.query_params[key]
    context_id = terminal_context_id(
        connection, {'chat_id': chat_id, 'temporary_context': getattr(ws.state, 'temporary_context', None)}, 'chat'
    )
    # Match the ownership headers used to create the session via HTTP. Open
    # Terminal checks both when attaching, independently of first-message auth.
    upstream_headers = {'X-User-Id': user.id}
    if chat_id:
        upstream_headers['X-Session-Id'] = chat_id
    if terminal_context_config(connection, 'chat').get('context_id') == 'chat_id' and not context_id:
        await ws.close(code=4003, reason='A saved chat is required for this terminal')
        return
    if context_id:
        upstream_headers[TERMINAL_CONTEXT_HEADER] = context_id

    import urllib.parse

    # Encode session_id as an opaque path segment so it cannot smuggle '?'/'#'/'&' (at any
    # decode depth) and inject an attacker-chosen user_id ahead of the one appended below.
    upstream_path = (
        f'api/terminals/{urllib.parse.quote(session_id, safe="")}' if session_id is not None else stream_path
    )
    upstream_url = f'{ws_base}/{upstream_path}'
    if upstream_params:
        upstream_url += f'?{urllib.parse.urlencode(upstream_params)}'

    request = Request({**ws.scope, 'type': 'http', 'method': 'GET'})
    opened = False
    session = get_terminal_client(base_url, user.id)
    try:
        async with session.ws_connect(
            upstream_url,
            headers=upstream_headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as upstream:
            import json as _json

            # First-message auth to upstream terminal server
            auth_type = connection.get('auth_type', 'bearer')
            if auth_type == 'bearer':
                key = normalize_bearer_token(connection.get('key', ''))
                await upstream.send_str(_json.dumps({**ws.state.auth, 'token': key}))
            elif auth_type == 'session' and is_terminal_orchestrator(connection):
                await upstream.send_str(_json.dumps({**ws.state.auth, 'token': token}))
            else:
                await upstream.send_str(_json.dumps({**ws.state.auth, 'token': ''}))

            if session_id is not None:
                await publish_event(
                    request,
                    EVENTS.TERMINAL_SESSION_OPENED,
                    actor=user,
                    subject_id=session_id,
                    subject_type='terminal.session',
                    data={'server_id': server_id},
                )
                opened = True

            async def _client_to_upstream():
                """Forward client → upstream."""
                try:
                    while True:
                        msg = await ws.receive()
                        if msg['type'] == 'websocket.disconnect':
                            break
                        elif 'bytes' in msg and msg['bytes']:
                            await upstream.send_bytes(msg['bytes'])
                        elif 'text' in msg and msg['text']:
                            await upstream.send_str(msg['text'])
                except Exception:
                    with suppress(Exception):
                        await ws.close(code=1011, reason='Terminal connection interrupted. Reconnecting.')

            async def _upstream_to_client():
                """Forward upstream → client."""
                try:
                    async for msg in upstream:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            await ws.send_bytes(msg.data)
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            await ws.send_text(msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
                    code = upstream.close_code or 1011
                    await ws.close(code=1011 if code in (1005, 1006, 1015) else code)
                except Exception:
                    with suppress(Exception):
                        await ws.close(code=1011, reason='Terminal connection interrupted. Reconnecting.')

            async def _watch_access():
                try:
                    while True:
                        # Poll current state so revocation also works across workers.
                        await asyncio.sleep(10)
                        current_access = await _resolve_terminal_access(ws, server_id, token)
                        if current_access is None:
                            return
                        try:
                            await _temporary_terminal_scope(ws, current_access[0], {'chat_id': chat_id})
                        except HTTPException as error:
                            await ws.close(
                                code=1013 if error.status_code == 503 else 4003,
                                reason='Temporary context access denied',
                            )
                            return
                        if is_saved_chat_id(chat_id) and not await Chats.get_accessible_chat_by_id(
                            chat_id, current_access[0]
                        ):
                            await ws.close(code=4003, reason='Chat access denied')
                            return
                except Exception:
                    log.exception('Terminal access recheck failed')
                    with suppress(Exception):
                        await ws.close(code=1011, reason='Terminal access check unavailable. Reconnecting.')

            # End the proxy as soon as any task finishes (e.g. a
            # graceful upstream CLOSE) and cancel the rest, which would
            # otherwise hang on a blocked ws.receive() until the browser leaves.
            tasks = [
                asyncio.create_task(_client_to_upstream()),
                asyncio.create_task(_upstream_to_client()),
                asyncio.create_task(_watch_access()),
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except Exception as e:
        log.exception('Terminal WebSocket proxy error: %s', e)
        status = getattr(e, 'status', getattr(e, 'status_code', None))
        code = {401: 4001, 403: 4003, 404: 4004}.get(status, 1011)
        with suppress(Exception):
            await ws.close(code=code)
    finally:
        await session.close()
        if opened:
            await publish_event(
                request,
                EVENTS.TERMINAL_SESSION_CLOSED,
                actor=user,
                subject_id=session_id,
                subject_type='terminal.session',
                data={'server_id': server_id},
            )
        try:
            await ws.close()
        except Exception:
            pass
