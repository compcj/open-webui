"""Global controls for temporary conversations and direct generation APIs."""

from fastapi import Depends, HTTPException, status
from open_webui.models.config import Config
from open_webui.utils.auth import get_verified_user
from open_webui.utils.chat_id import is_temporary_chat_id


def _nonempty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


async def check_chat_access(form_data: dict, user) -> None:
    if user.role == 'admin':
        return

    config = await Config.get_many('chat.temporary.enable', 'chat.direct_api.enable')
    chat_id = form_data.get('chat_id')
    if isinstance(chat_id, str) and is_temporary_chat_id(chat_id) and not config.get('chat.temporary.enable', True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Temporary chats are disabled.')

    if config.get('chat.direct_api.enable', True):
        return

    # A chat ID alone is insufficient: the main handler needs message IDs to
    # persist output, and a user message to persist/reconstruct saved chat input.
    # Ownership and channel membership are checked by the main handler afterwards.
    is_new_chat = 'parent_id' in form_data and form_data['parent_id'] is None and not chat_id
    has_context = _nonempty_string(chat_id) or (is_new_chat and chat_id in (None, ''))
    entries = form_data.get('message_ids')
    if isinstance(entries, list):
        has_message_ids = bool(entries) and all(
            isinstance(entry, dict)
            and _nonempty_string(entry.get('model_id'))
            and _nonempty_string(entry.get('message_id'))
            for entry in entries
        )
    elif isinstance(entries, dict):
        has_message_ids = bool(entries) and all(
            _nonempty_string(model_id) and _nonempty_string(message_id) for model_id, message_id in entries.items()
        )
    else:
        has_message_ids = _nonempty_string(form_data.get('id'))

    user_message = form_data.get('user_message') or form_data.get('parent_message')
    has_user_message = (
        isinstance(user_message, dict)
        and _nonempty_string(user_message.get('id'))
        and user_message.get('role') == 'user'
    )
    # Channel messages are stored/authorized separately, and temporary chats do
    # not promise persistence. Both still require the conversation response IDs.
    non_saved_context = isinstance(chat_id, str) and (is_temporary_chat_id(chat_id) or chat_id.startswith('channel:'))
    if not (has_context and has_message_ids and (has_user_message or non_saved_context)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Direct API chat is disabled. Use a chat conversation with message IDs.',
        )


async def get_direct_chat_user(user=Depends(get_verified_user)):
    # Only public HTTP routes resolve this dependency. Internal provider calls
    # pass an already verified user, without a client-controlled bypass flag.
    if user.role != 'admin' and not await Config.get('chat.direct_api.enable', True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Direct API chat is disabled.')
    return user
