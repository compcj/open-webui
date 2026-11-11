"""Opaque, login-bound Chrome control tickets; page previews use another Host."""

from fastapi import HTTPException
from urllib.parse import urlsplit


async def browser_ticket(server_id, user_id, metadata, session, request):
    from open_webui.routers.terminal_preview import login_token, terminal_access
    from open_webui.utils.auth import get_verified_user_by_token
    from open_webui.utils.terminal_preview import ticket_store

    token, claims, login_session = login_token(request)
    if claims.get('id') != user_id:
        raise HTTPException(403, 'Browser session mismatch')
    user = await get_verified_user_by_token(token, getattr(request.app.state, 'redis', None))
    if not user or user.id != user_id:
        raise HTTPException(403, 'Browser session was revoked')
    parent = urlsplit(str(request.base_url))
    origin = f'{parent.scheme}://{parent.netloc}'
    record = {
        'kind': 'chrome',
        'user_id': user_id,
        'login_session': login_session,
        'auth_token': token,
        'server_id': server_id,
        'session_id': session['id'],
        'upstream_token': session['token'],
        'chat_id': metadata.get('chat_id'),
        'temporary_context': metadata.get('temporary_context'),
        'base_url': origin,
        'parent_origin': origin,
        'oauth_session_id': request.cookies.get('oauth_session_id'),
    }
    base, _, _, _ = await terminal_access(request, user, record)
    record['terminal_base'] = base
    capability, _ = await ticket_store(request).issue_named(record)
    return capability


async def browser_access(server_id, sid, ticket, request):
    from open_webui.routers.terminal_preview import terminal_access
    from open_webui.utils.auth import get_verified_user_by_token
    from open_webui.utils.terminal_preview import ticket_store

    record = await ticket_store(request).get(ticket)
    if record.get('kind') != 'chrome' or record.get('server_id') != server_id or record.get('session_id') != sid:
        raise HTTPException(403, 'Browser ticket belongs to another target')
    user = await get_verified_user_by_token(record['auth_token'], getattr(request.app.state, 'redis', None))
    if not user or user.id != record['user_id']:
        raise HTTPException(403, 'Browser session was revoked')
    base, headers, cookies, context_id = await terminal_access(request, user, record)
    if base != record['terminal_base']:
        raise HTTPException(403, 'Browser terminal changed')
    return user, {
        **record,
        'chat': record.get('chat_id'),
        'capability': record['upstream_token'],
        'context_id': context_id,
        'relay': {'authorization': headers.get('Authorization'), 'cookies': cookies},
    }
