"""Chat browser tools and login-bound Chrome control tickets."""

from fastapi import HTTPException
from urllib.parse import urlsplit


def register_browser_open_tab(tools, metadata, event_call):
    terminal_id = metadata.get('terminal_id')
    if (
        not terminal_id
        or not event_call
        or not metadata.get('session_id')
        or not metadata.get('chat_id')
        or metadata.get('internal')
        or metadata.get('automation_id')
        or (metadata.get('features') or {}).get('terminal_browser_tab') is not True
        or 'browser_open_tab' in tools
    ):
        return

    def on_selected_terminal(name):
        tool = tools.get(name) or {}
        server = tool.get('server') or {}
        return (tool.get('type') == 'terminal' and tool.get('tool_id') == f'terminal:{terminal_id}') or (
            tool.get('direct') and server.get('is_terminal') is True and server.get('url') == terminal_id
        )

    if not on_selected_terminal('browser_action'):
        return

    async def open_tab():
        result = await event_call(
            {
                'type': 'request:open_browser_tab',
                'data': {'terminal_id': terminal_id, 'session_id': metadata['session_id']},
            }
        )
        if isinstance(result, dict) and result.get('status') == 'unavailable':
            return {'error': 'The browser workspace is unavailable. Check browser_tabs for this chat.'}
        result = result if isinstance(result, dict) else {}
        session_id = result.get('session_id')
        mode = result.get('mode')
        if not isinstance(session_id, str) or not 0 < len(session_id) <= 128 or mode not in ('chrome', 'proxy'):
            # A lost callback may follow a successful create. Never issue a second open here.
            raise RuntimeError('Browser tab creation could not be confirmed. Check browser_tabs before retrying.')
        session = {'session_id': session_id, 'mode': mode}
        if mode == 'proxy':
            session['error'] = 'This tab opened in Proxy mode. Browser interaction requires Chrome on this terminal.'
        return session

    tools['browser_open_tab'] = {
        'type': 'terminal',
        'tool_id': f'terminal:{terminal_id}',
        'callable': open_tab,
        'spec': {
            'name': 'browser_open_tab',
            'description': (
                'Open a new blank Browser tab in this chat. When a task needs an interactive browser and has no '
                'suitable tab, call this tool yourself. Use the returned session_id with browser_action to navigate '
                'and interact. Reuse that tab for subsequent steps.'
            ),
            'parameters': {'type': 'object', 'properties': {}},
        },
    }
    for name in ('browser_tabs', 'browser_action'):
        if on_selected_terminal(name):
            spec = tools[name]['spec']
            description = spec.get('description') or ''
            tools[name] = {
                **tools[name],
                'spec': {
                    **spec,
                    'description': f'{description}\nIf you need a new tab, call browser_open_tab.'.strip(),
                },
            }


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
