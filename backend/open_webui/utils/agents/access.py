"""Live authorization for new native Agent effects on a bound terminal."""

import hashlib

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from open_webui.utils.json_codec import JSONCodec
from open_webui.models.chats import Chats
from open_webui.utils.terminals import (
    resolved_terminal_context,
    get_terminal_server_url,
    resolve_terminal_connection,
)


UNSPECIFIED_CONTEXT = object()


def terminal_instance(connection):
    """Additional identity for an orchestrated instance or a non-default Tether service."""
    binding = {
        key: connection[key]
        for key in ('template_id', 'scope', 'context_id', 'terminal_context', 'instance_id')
        if connection.get(key) is not None
    }
    if connection.get('service', 'open_terminal') != 'open_terminal':
        binding['service'] = connection['service']
    if binding:
        return hashlib.sha256(JSONCodec.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return None


def check_terminal_binding(
    resolved, terminal_identity, terminal_context=UNSPECIFIED_CONTEXT, instance=UNSPECIFIED_CONTEXT
):
    connection, headers, _ = resolved
    current_identity = hashlib.sha256(get_terminal_server_url(connection).encode()).hexdigest()
    if (
        current_identity != terminal_identity
        or terminal_context is not UNSPECIFIED_CONTEXT
        and resolved_terminal_context(connection, headers) != terminal_context
        or instance is not UNSPECIFIED_CONTEXT
        and terminal_instance(connection) != instance
    ):
        raise HTTPException(409, 'The agent terminal connection changed. Stop this operation before starting another.')


async def authorize_agent_terminal(
    request,
    user,
    metadata,
    terminal_identity,
    *,
    terminal_context=UNSPECIFIED_CONTEXT,
    terminal_instance=UNSPECIFIED_CONTEXT,
):
    if request is None:
        raise HTTPException(503, 'Agent terminal authorization is unavailable.', headers={'Retry-After': '2'})
    try:
        if metadata.get('chat_id') and not await Chats.get_accessible_chat_by_id(
            metadata['chat_id'], user, permission='write'
        ):
            raise HTTPException(403, 'Chat reply access is no longer available.')
        resolved = await resolve_terminal_connection(request, user, metadata)
    except (RedisError, SQLAlchemyError):
        raise HTTPException(
            503, 'Agent terminal authorization is temporarily unavailable.', headers={'Retry-After': '2'}
        ) from None
    if not resolved:
        raise HTTPException(403, 'Agent terminal access is no longer available.')
    check_terminal_binding(resolved, terminal_identity, terminal_context, terminal_instance)
    return resolved
