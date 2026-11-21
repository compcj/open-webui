"""Read native conversation state from signed, server-persisted chat messages."""

import hashlib
import re
from urllib.parse import quote

from open_webui.utils.json_codec import JSONCodec
from open_webui.models.chats import ChatModel, Chats
from open_webui.utils.auth import decode_token


def model_id(state):
    model = re.sub(r'[^a-zA-Z0-9_-]+', '-', state['model']).strip('-')
    if model != state['model']:
        # Keeps CLI model names apart that only differ in the replaced characters.
        model = f'{model}-{hashlib.sha256(state["model"].encode()).hexdigest()[:8]}'
    return f'agent:{quote(state["terminal_id"], safe="")}:{state["provider"]}:{model}'


def legacy_model_id(state):
    return f'agent:{quote(state["terminal_id"], safe="")}:{state["provider"]}:{quote(state["model"], safe="")}'


async def ensure_chat_selections(chat):
    if all(key in (chat.chat or {}) for key in ('models', 'terminal_id', 'working_directory')):
        return chat
    state = await get_session(chat)
    if not state:
        return chat
    async with Chats._chat_transaction(chat.id) as (_, row):
        if row is None:
            return chat
        # Check missing fields under the lock so concurrent selection changes win.
        data = dict(row.chat or {})
        data.setdefault('models', [model_id(state)])
        data.setdefault('terminal_id', state['terminal_id'])
        if data['terminal_id'] == state['terminal_id']:
            data.setdefault('working_directory', state['cwd'])
        row.chat = data
        chat = ChatModel.model_validate(row)
    return chat


def content_digest(value):
    return hashlib.sha256(
        JSONCodec.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()


def branch_digest(messages, message_id):
    history, seen = [], set()
    while message_id:
        message = messages.get(message_id)
        if not message or message_id in seen:
            return None
        seen.add(message_id)
        history.append(
            [
                message_id,
                message.get('parentId'),
                message.get('role'),
                message.get('user_id'),
                message.get('content') or '',
                message.get('files') or [],
            ]
        )
        message_id = message.get('parentId')
    return content_digest(history)


def _state(message):
    token = (message.get('meta') or {}).get('agent')
    state = decode_token(token) if isinstance(token, str) else None
    if isinstance(state, dict) and (state.get('typ') == 'agent_session' or state.get('purpose') == 'terminal-agent'):
        return state
    return None


def _find(chat, messages, message_id, user_id):
    visited = set()
    while message_id and message_id not in visited:
        visited.add(message_id)
        message = messages.get(message_id)
        if not message:
            break
        if message.get('role') == 'assistant':
            state = _state(message)
            # Neither a foreign turn nor a gap in native history can be skipped.
            if (
                not state
                or message.get('done') is not True
                or (
                    state.get('chat') != chat.id
                    or state.get('user') != user_id
                    or (message.get('user_id') or chat.user_id) != user_id
                    or state.get('checkpoint_message_id', message_id) != message_id
                )
            ):
                break
            if all(
                isinstance(state.get(k), str) and state[k]
                for k in ('terminal_id', 'provider', 'model', 'cwd', 'session_id')
            ):
                return state, message_id
            break
        message_id = message.get('parentId')
    return None, None


async def get_session(chat, message_id=None, *, user_id=None):
    message_id = (
        message_id or getattr(chat, 'current_message_id', None) or (chat.chat or {}).get('history', {}).get('currentId')
    )
    if not message_id:
        return None
    state, _ = await get_resume_point(chat, message_id, user_id or chat.user_id)
    return state


async def get_resume_point(chat, message_id, user_id, exclude=None):
    """Only a settled, server-authenticated checkpoint in this actor's saved branch can be reused."""
    messages = await Chats.get_messages_map_by_chat_id(chat.id) or {}
    state, found_id = _find(chat, messages, message_id, user_id)
    if state is None:
        return None, False
    source = messages[found_id]
    if 'source_history_digest' in state and (
        not state['source_history_digest']
        or state['source_history_digest'] != branch_digest(messages, source.get('parentId'))
    ):
        return None, False
    if 'checkpoint_digest' in state and state['checkpoint_digest'] != content_digest(
        [source.get('content') or '', source.get('output') or []]
    ):
        return None, False
    execution = await Chats.get_agent_execution(chat.id, found_id) or {}
    expected = {
        'state': 'completed',
        'user_id': user_id,
        'message_id': found_id,
        'chat_id': chat.id,
        **{key: state[key] for key in ('terminal_id', 'provider', 'model', 'cwd')},
    }
    identity = execution.get('terminal_identity')
    if (
        execution.get('output_published') is not True
        or execution.get('remote_completed') is not True
        or execution.get('output_incomplete')
        or not identity
        or any(execution.get(key) != value for key, value in expected.items())
        or state.get('terminal_identity', identity) != identity
        or state.get('terminal_context', execution.get('terminal_context')) != execution.get('terminal_context')
        or state.get('terminal_instance', execution.get('terminal_instance')) != execution.get('terminal_instance')
    ):
        return None, False
    state = {
        **state,
        'terminal_identity': identity,
        'terminal_context': execution.get('terminal_context'),
        'terminal_instance': execution.get('terminal_instance'),
    }
    # If the same provider session has also recorded a turn outside this checkpoint's ancestors,
    # continuing its latest turn would cross branches. Timestamps can coincide or be edited.
    ancestors = set()
    ancestor = found_id
    while ancestor and ancestor not in ancestors and ancestor in messages:
        ancestors.add(ancestor)
        ancestor = messages[ancestor].get('parentId')
    for other_id, other in messages.items():
        if other_id in ancestors or other_id == exclude:
            continue
        other_state = _state(other)
        if other_state and all(
            other_state.get(k) == state[k] for k in ('chat', 'user', 'provider', 'terminal_id', 'session_id')
        ):
            return state, True
    return state, False
