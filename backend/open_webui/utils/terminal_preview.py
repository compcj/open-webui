"""Isolated preview Host and short-lived server-owned capabilities.

This module deliberately has no application/config/database import side effects.
The gateway never falls through to the main application on the preview hostname.
"""

import asyncio
import copy
import hashlib
import ipaddress
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.responses import JSONResponse

TICKET_TTL = 300
# Open Terminal reaps unclaimed sessions after 24 hours idle. Add time for
# transport and the reaper before an ambiguous create can be tried again.
PORT_CREATE_RETENTION = 25 * 60 * 60
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')
PREVIEW_PATH = re.compile(r'^/p/([A-Za-z0-9_-]{43})(?:/|$)')
CHROME_TICKET_PATH = re.compile(r'(/browser/view/[^/]+/)[^/]+')
FILE_DELIVERY_PATH = re.compile(r'/api/v1/terminals/[^/]+/files/(?:download|image)/?')
IDENTITY_FIELDS = (
    'user_id',
    'login_session',
    'server_id',
    'kind',
    'session_id',
    'port',
    'chat_id',
    'model_id',
    'instance_user_id',
    'terminal_base',
    'terminal_context',
    'temporary_context',
    'base_url',
    'parent_origin',
)


def origin_hostname(value):
    try:
        hostname = urlsplit(value).hostname
        if not hostname:
            return None
        if ':' in hostname:
            return ipaddress.IPv6Address(hostname).compressed
        return hostname.rstrip('.').encode('idna').decode('ascii').lower()
    except (TypeError, ValueError, UnicodeError, AttributeError):
        return None


def validate_preview_base_url(value, *main_urls):
    if not isinstance(value, str):
        raise ValueError('Preview base URL must be a string')
    value = value.strip().rstrip('/')
    if not value:
        return ''
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise ValueError('Invalid preview port') from None
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {'https', 'http'}
    ):
        raise ValueError('Preview requires an HTTP(S) origin without a path, credentials, query or fragment')
    hostname = origin_hostname(value)
    if not hostname or port == 0 or '%' in hostname or any(char.isspace() for char in parsed.netloc):
        raise ValueError('Invalid preview hostname or port')
    local = hostname == 'localhost' or hostname.endswith('.localhost')
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*', hostname):
            raise ValueError('Invalid preview hostname') from None
    else:
        local = local or address.is_loopback
        if address.version == 6:
            hostname = f'[{address.compressed}]'
    if parsed.scheme != 'https' and not local:
        raise ValueError('Preview requires HTTPS outside explicit local development')
    if any(origin_hostname(url) == origin_hostname(value) for url in main_urls if url):
        raise ValueError('Preview requires a different hostname; changing the port is insufficient')
    return f'{parsed.scheme}://{hostname}' + (f':{port}' if port is not None else '')


def unavailable():
    return HTTPException(
        503, 'Preview authorization is temporarily unavailable; retry later', headers={'Retry-After': '2'}
    )


class TicketStore:
    def __init__(self, redis=None, *, redis_required=False, workers=1, prefix='open-webui', clock=time.time):
        self.redis = redis
        self.redis_required = redis_required
        self.workers = workers
        self.prefix = prefix + ':terminal-preview:'
        self.clock = clock
        self.memory = {}
        self.aliases = {}
        self.lock = asyncio.Lock()

    def key(self, token):
        # Redis administration/logging never needs the bearer capability itself.
        return self.prefix + hashlib.sha256(token.encode()).hexdigest()

    def check_storage(self):
        if self.redis is None and (self.redis_required or self.workers != 1):
            raise unavailable()

    async def check_connection(self):
        self.check_storage()
        if self.redis is not None:
            try:
                if not await self.redis.ping():
                    raise unavailable()
            except Exception:
                raise unavailable() from None

    async def get(self, token):
        self.check_storage()
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise HTTPException(401, 'Preview ticket is invalid or expired')
        if self.redis is not None:
            try:
                raw = await self.redis.get(self.key(token))
                record = json.loads(raw) if raw else None
            except Exception:
                raise unavailable() from None
        else:
            record = self.memory.get(self.key(token))
        if not isinstance(record, dict) or record.get('expires_at', 0) <= self.clock():
            raise HTTPException(401, 'Preview ticket is invalid or expired')
        return copy.deepcopy(record)

    async def issue(self, record, *, previous_token=None):
        self.check_storage()
        async with self.lock:
            if previous_token:
                previous = await self.get(previous_token)
                if any(previous.get(field) != record.get(field) for field in IDENTITY_FIELDS):
                    raise HTTPException(403, 'A preview ticket cannot change identity or target')
                token = previous_token
            else:
                token = secrets.token_urlsafe(32)
            record = copy.deepcopy(record)
            record['expires_at'] = int(self.clock()) + TICKET_TTL
            if self.redis is not None:
                try:
                    await self.redis.set(self.key(token), json.dumps(record, separators=(',', ':')), ex=TICKET_TTL)
                except Exception:
                    raise unavailable() from None
            else:
                self.memory = {key: item for key, item in self.memory.items() if item['expires_at'] > self.clock()}
                if self.key(token) not in self.memory and len(self.memory) >= 4096:
                    raise HTTPException(429, 'Too many active preview tickets')
                self.memory[self.key(token)] = record
            return token, copy.deepcopy(record)

    async def issue_named(self, record):
        """Refresh a Chrome control ticket without changing its active socket URL."""
        self.check_storage()
        identity = json.dumps([record.get(field) for field in IDENTITY_FIELDS], sort_keys=True)
        key = self.prefix + 'alias:' + hashlib.sha256(identity.encode()).hexdigest()
        try:
            previous = await self.redis.get(key) if self.redis is not None else self.aliases.get(key)
            if isinstance(previous, bytes):
                previous = previous.decode()
            if previous:
                try:
                    await self.get(previous)
                except HTTPException as error:
                    if error.status_code != 401:
                        raise
                    previous = None
            token, saved = await self.issue(record, previous_token=previous)
            if self.redis is not None:
                await self.redis.set(key, token, ex=TICKET_TTL)
            else:
                self.aliases = {alias: value for alias, value in self.aliases.items() if self.key(value) in self.memory}
                self.aliases[key] = token
            return token, saved
        except HTTPException:
            raise
        except Exception:
            raise unavailable() from None


def ticket_store(request):
    from open_webui.env import REDIS_URL, REDIS_SENTINEL_HOSTS, REDIS_KEY_PREFIX, UVICORN_WORKERS

    state = request.app.state
    if not hasattr(state, 'terminal_preview_tickets'):
        state.terminal_preview_tickets = TicketStore(
            getattr(state, 'redis', None),
            redis_required=bool(REDIS_URL or REDIS_SENTINEL_HOSTS),
            workers=UVICORN_WORKERS,
            prefix=REDIS_KEY_PREFIX,
        )
    return state.terminal_preview_tickets


async def preview_settings():
    from open_webui.models.config import Config

    return await Config.get_preview_settings()


async def preview_dispatch(scope, receive, send):
    from open_webui.routers.terminal_preview import preview_http, preview_websocket

    await (preview_websocket if scope['type'] == 'websocket' else preview_http)(scope, receive, send)


class PreviewHostMiddleware:
    def __init__(self, app, *, settings=preview_settings, preview_app=preview_dispatch):
        self.app, self.settings, self.preview_app = app, settings, preview_app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        original_path = scope.get('path', '')
        redacted = PREVIEW_PATH.sub('/p/[redacted]/', original_path)
        redacted = CHROME_TICKET_PATH.sub(r'\1[redacted]', redacted)
        route_path = original_path.removeprefix(scope.get('root_path', ''))
        # The signed ref is the only input on these two delivery routes. Keep
        # the original query for authorization and omit it from access/audit logs.
        if redacted != original_path or (scope.get('query_string') and FILE_DELIVERY_PATH.fullmatch(route_path)):
            routed_scope = {**scope, 'safe_log_path': redacted}
            scope['path'], scope['raw_path'], scope['query_string'] = redacted, redacted.encode(), b''
            scope = routed_scope
        try:
            settings = await self.settings()
            base = validate_preview_base_url(
                settings.get('terminal.preview.base_url', ''), settings.get('webui.url', '')
            )
        except Exception:
            return await self.reject(scope, receive, send, 503)
        incoming_headers = scope.get('headers', [])
        authorities = [value for key, value in incoming_headers if key.lower() == b'host']
        if len(authorities) > 1:
            return await self.reject(scope, receive, send, 400)
        headers = {key.lower(): value for key, value in incoming_headers}
        try:
            authority = headers.get(b'host', b'').decode('ascii')
        except (ValueError, UnicodeError):
            return await self.reject(scope, receive, send, 400)
        host = origin_hostname('//' + authority)
        preview_host = origin_hostname(base)
        isolated_hosts = set(settings.get('isolated_hosts', ()))
        if preview_host:
            isolated_hosts.add(preview_host)
        if host in isolated_hosts:
            if host != preview_host:
                return await self.reject(scope, receive, send, 404)
            expected_scheme = urlsplit(base).scheme
            actual_scheme = {'wss': 'https', 'ws': 'http'}.get(scope.get('scheme'), scope.get('scheme'))
            if actual_scheme != expected_scheme:
                return await self.reject(scope, receive, send, 403)
            match = PREVIEW_PATH.match(scope.get('path', ''))
            if not match:
                return await self.reject(scope, receive, send, 404)
            forwarded = {
                **scope,
                'headers': [
                    (key, value)
                    for key, value in scope.get('headers', [])
                    if key.lower() not in {b'cookie', b'authorization', b'proxy-authorization', b'referer'}
                ],
                'preview_token': match[1],
                'preview_path': scope['path'][match.end() :],
            }
            # Uvicorn access logging reads this original scope after the response.
            scope['path'] = '/p/[redacted]'
            scope['raw_path'] = b'/p/[redacted]'
            scope['query_string'] = b''
            return await self.preview_app(forwarded, receive, send)
        origin = headers.get(b'origin', b'').decode('latin-1')
        if origin and (origin == 'null' or origin_hostname(origin) in isolated_hosts):
            # This runs outside CORS, so broad main-site CORS cannot override it.
            return await self.reject(scope, receive, send, 403)
        return await self.app(scope, receive, send)

    @staticmethod
    async def reject(scope, receive, send, status):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1013 if status == 503 else 1008})
        else:
            await JSONResponse(
                {'detail': 'Preview access unavailable' if status == 503 else 'Access denied'},
                status_code=status,
                headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'},
            )(scope, receive, send)
