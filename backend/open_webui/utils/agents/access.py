"""Live authorization for new native Agent effects on a bound terminal."""

import hashlib

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from open_webui.utils.terminals import (
    TERMINAL_CONTEXT_HEADER,
    get_terminal_server_url,
    resolve_terminal_connection,
)


UNSPECIFIED_CONTEXT = object()


async def authorize_agent_terminal(request, user, metadata, terminal_identity, *, terminal_context=UNSPECIFIED_CONTEXT):
    if request is None:
        raise HTTPException(503, 'Agent terminal authorization is unavailable.', headers={'Retry-After': '2'})
    try:
        resolved = await resolve_terminal_connection(request, user, metadata)
    except (RedisError, SQLAlchemyError):
        raise HTTPException(
            503, 'Agent terminal authorization is temporarily unavailable.', headers={'Retry-After': '2'}
        ) from None
    if not resolved:
        raise HTTPException(403, 'Agent terminal access is no longer available.')
    connection, headers, _ = resolved
    current_identity = hashlib.sha256(get_terminal_server_url(connection).encode()).hexdigest()
    if current_identity != terminal_identity or (
        terminal_context is not UNSPECIFIED_CONTEXT and headers.get(TERMINAL_CONTEXT_HEADER) != terminal_context
    ):
        raise HTTPException(409, 'The agent terminal connection changed. Stop this operation before starting another.')
    return resolved
