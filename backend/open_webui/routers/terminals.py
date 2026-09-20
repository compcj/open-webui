"""Reverse proxy for admin-configured terminal servers.

Routes:
  GET  /                         — list terminals the user has access to
  *    /{server_id}/{path:path}  — proxy request to terminal server
"""

import asyncio
import logging
import posixpath
from urllib.parse import unquote, urlsplit

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from open_webui.config import TERMINAL_PROXY_HEADERS
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, WEBUI_SECRET_KEY
from open_webui.events import EVENTS, publish_event
from open_webui.models.config import Config
from open_webui.models.chats import Chats
from open_webui.models.groups import Groups
from open_webui.utils.access_control import has_connection_access
from open_webui.utils.auth import get_verified_user
from open_webui.utils.chat_id import is_saved_chat_id
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
    terminal_context_available,
    terminal_context_config,
    terminal_context_id,
    terminal_chat_uploads,
    terminal_contexts,
)
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect

log = logging.getLogger(__name__)

router = APIRouter()

# Response headers from a terminal are untrusted on the WebUI origin. All
# security/MIME headers are set here, including after custom proxy headers.
SAFE_PROXY_HEADERS = {'content-language', 'retry-after', 'x-accel-buffering'}


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


@router.get('/')
async def list_terminal_servers(request: Request, user=Depends(get_verified_user)):
    """Return terminal servers the authenticated user has access to."""
    connections = await Config.get('terminal_server.connections', []) or []
    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id)}

    return [
        {
            'id': connection.get('id', ''),
            'url': connection.get('url', ''),
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


async def _http_terminal_context(server_id, request, user, metadata, *, require_context=False):
    connections = await Config.get('terminal_server.connections', []) or []
    connection = next((c for c in connections if c.get('id') == server_id), None)
    if connection is None:
        raise HTTPException(404, 'Terminal server not found')
    if not connection.get('enabled', True):
        raise HTTPException(403, 'Terminal server disabled')
    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id)}
    if not await has_connection_access(user, connection, user_group_ids):
        raise HTTPException(403, 'Access denied')
    base_url = get_terminal_server_url(connection)
    if not base_url:
        raise HTTPException(503, 'Terminal server URL not configured')
    session_id = metadata.get('chat_id')
    if is_saved_chat_id(session_id) and not await Chats.get_chat_by_id_for_user(session_id, user):
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
    cookies = {}
    auth_type = connection.get('auth_type', 'bearer')
    if auth_type == 'bearer':
        headers.update(bearer_auth_header(connection.get('key', '')))
    elif auth_type == 'session':
        cookies = request.cookies
        token = getattr(request.state, 'token', None)
        credentials = token.credentials if token else request.cookies.get('token')
        if not credentials:
            credentials = request.headers.get('authorization', '').removeprefix('Bearer ')
        headers.update(bearer_auth_header(credentials))
    elif auth_type == 'system_oauth':
        cookies = request.cookies
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
async def proxy_terminal(server_id: str, path: str, request: Request, user=Depends(get_verified_user)):
    return await _proxy_http(server_id, path, request, user)


async def _proxy_http(server_id, path, request, user, *, reference=None, image_only=False):
    safe_path = _sanitize_proxy_path(path)
    if safe_path is None:
        return _terminal_error(400, 'Invalid path')
    if safe_path.rstrip('/') == 'proxy' or safe_path.startswith('proxy/'):
        return _terminal_error(403, 'Terminal web previews are disabled on this origin')
    # Do not let other methods or encoded aliases fall through to an upstream
    # endpoint with the same name as a protected delivery endpoint.
    if safe_path.rstrip('/') in {'files/download', 'files/image'}:
        return _terminal_error(405, 'Method not allowed')
    metadata = {'chat_id': request.headers.get('x-session-id')}
    payload = None
    if reference is not None:
        try:
            payload = verify_file_reference(reference, WEBUI_SECRET_KEY)
        except ValueError:
            return _terminal_error(403, 'Invalid file reference')
        if payload['owner_id'] != user.id or payload['server_id'] != server_id:
            return _terminal_error(403, 'Access denied')
        metadata = payload
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

    query = [('path', payload['path'])] if payload else list(request.query_params.multi_items())
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
    session = aiohttp.ClientSession(
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
                upstream.release()
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
    except TimeoutError:
        return _terminal_error(504, 'Terminal request timed out')
    except aiohttp.ClientError:
        return _terminal_error(502, 'Terminal unavailable')
    except Exception as error:
        # Do not expose upstream URLs, response bodies, paths or credentials.
        log.error('Terminal proxy failed (%s)', type(error).__name__)
        return _terminal_error(502, 'Terminal request failed')
    finally:
        if not streaming:
            await cleanup()


# ---------------------------------------------------------------------------
# WebSocket proxy for interactive terminal sessions
# ---------------------------------------------------------------------------


async def _resolve_authenticated_connection(ws: WebSocket, server_id: str):
    """Authenticate a WebSocket via first-message auth and resolve the terminal server.

    The client must send ``{"type": "auth", "token": "<jwt>"}`` as its first
    message after connecting.

    Returns ``(user, connection, chat_id, token)`` on success, or ``None`` after
    closing *ws* with an appropriate error code.
    """
    import asyncio

    from open_webui.utils.auth import get_verified_user_by_token

    # First-message authentication
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        payload = JSONCodec.loads(raw)
        if payload.get('type') != 'auth':
            await ws.close(code=4001, reason='Expected auth message')
            return None
        token = payload.get('token', '')
        user = await get_verified_user_by_token(token, getattr(ws.app.state, 'redis', None))
        if user is None:
            await ws.close(code=4001, reason='Invalid token')
            return None
    except (asyncio.TimeoutError, JSONCodec.JSONDecodeError):
        await ws.close(code=4001, reason='Auth timeout or invalid payload')
        return None
    except Exception:
        await ws.close(code=4001, reason='Invalid token')
        return None

    # Resolve terminal server
    connections = await Config.get('terminal_server.connections', []) or []
    connection = next((c for c in connections if c.get('id') == server_id), None)

    if connection is None:
        await ws.close(code=4004, reason='Terminal server not found')
        return None

    if not connection.get('enabled', True):
        await ws.close(code=4003, reason='Terminal server disabled')
        return None

    user_group_ids = {group.id for group in await Groups.get_groups_by_member_id(user.id)}
    if not await has_connection_access(user, connection, user_group_ids):
        await ws.close(code=4003, reason='Access denied')
        return None

    chat_id = payload.get('chat_id', '')
    if not terminal_context_available(connection, 'chat'):
        await ws.close(code=4003, reason='Terminal server is not available in chats')
        return None
    return user, connection, chat_id if isinstance(chat_id, str) else '', token


@router.websocket('/{server_id}/api/terminals/{session_id}')
async def ws_terminal(
    ws: WebSocket,
    server_id: str,
    session_id: str,
):
    """Proxy an interactive WebSocket terminal session to a terminal server.

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
    context_id = terminal_context_id(connection, {'chat_id': chat_id}, 'chat')
    upstream_headers = {}
    if terminal_context_config(connection, 'chat').get('context_id') == 'chat_id' and not context_id:
        await ws.close(code=4003, reason='A saved chat is required for this terminal')
        return
    if context_id:
        upstream_headers[TERMINAL_CONTEXT_HEADER] = context_id

    import urllib.parse

    # Encode session_id as an opaque path segment so it cannot smuggle '?'/'#'/'&' (at any
    # decode depth) and inject an attacker-chosen user_id ahead of the one appended below.
    safe_session_id = urllib.parse.quote(session_id, safe='')

    upstream_url = f'{ws_base}/api/terminals/{safe_session_id}'
    if upstream_params:
        upstream_url += f'?{urllib.parse.urlencode(upstream_params)}'

    app = ws.scope.get('app')
    opened = False
    session = aiohttp.ClientSession()
    try:
        async with session.ws_connect(
            upstream_url,
            headers=upstream_headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as upstream:
            import asyncio
            import json as _json

            # First-message auth to upstream terminal server
            auth_type = connection.get('auth_type', 'bearer')
            if auth_type == 'bearer':
                key = normalize_bearer_token(connection.get('key', ''))
                await upstream.send_str(_json.dumps({'type': 'auth', 'token': key}))
            elif auth_type == 'session' and is_terminal_orchestrator(connection):
                await upstream.send_str(_json.dumps({'type': 'auth', 'token': token}))

            await publish_event(
                app,
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
                    pass

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
                except Exception:
                    pass

            # End the proxy as soon as either direction finishes (e.g. a
            # graceful upstream CLOSE) and cancel the sibling, which would
            # otherwise hang on a blocked ws.receive() until the browser leaves.
            tasks = [
                asyncio.create_task(_client_to_upstream()),
                asyncio.create_task(_upstream_to_client()),
            ]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    except Exception as e:
        log.exception('Terminal WebSocket proxy error: %s', e)
    finally:
        await session.close()
        if opened:
            await publish_event(
                app,
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
