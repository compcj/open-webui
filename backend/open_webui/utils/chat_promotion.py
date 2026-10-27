"""Raw chat promotion and request idempotency without new context SQL tables."""

from __future__ import annotations

from uuid import UUID, NAMESPACE_URL, uuid5, uuid4
import copy
from fastapi import HTTPException
from open_webui.utils.chat_context_copy import copy_chat_without_context_state
from open_webui.utils.context_store_rules import source_digest
from open_webui.utils.temporary_contexts import temporary_repository


def creation_identity(request, user, form):
    key = request.headers.get('Idempotency-Key')
    if key is None:
        return None
    try:
        key = str(UUID(key))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, 'Invalid chat creation idempotency key.') from None
    raw = copy_chat_without_context_state(form.chat)
    # A retried click may have a new wall-clock timestamp, but never new content.
    raw.pop('timestamp', None)
    raw.pop('id', None)
    return str(uuid5(NAMESPACE_URL, f'open-webui:chat-create:{user.id}:{key}')), source_digest(
        {
            'chat': raw,
            'variables': form.variables or {},
            'folder_id': form.folder_id,
        }
    )


def same_creation(chat, user, fingerprint):
    return chat and chat.user_id == user.id and (chat.meta or {}).get('creation_fingerprint') == fingerprint


async def begin_temporary_promotion(request, user, form=None):
    context_id = request.headers.get('X-Temporary-Context-Id')
    epoch = request.headers.get('X-Temporary-Context-Epoch')
    if context_id is None and epoch is None:
        return None
    try:
        revision = int(request.headers['X-Temporary-Context-Revision'])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, 'Temporary context revision is required.') from None
    repository = temporary_repository(request)
    doc = await repository.read(context_id, user.id, epoch)
    snapshot = doc['data'].get('snapshot') or {}
    branch = snapshot.get('current_id')
    if not branch:
        raise HTTPException(400, 'No temporary conversation to save.')
    task_id = f'temporary.{context_id}.{uuid4().hex}'
    if form is not None:
        # The reservation is revision-bound; server buffered output is authoritative.
        # Build only from the full snapshot, never from selected summaries or a
        # stale page copy received before the last completion event.
        raw_messages = copy.deepcopy(snapshot.get('messages_map') or {})
        chain, current, seen = [], branch, set()
        while current is not None:
            if current in seen or current not in raw_messages:
                raise HTTPException(409, 'Temporary history changed.')
            seen.add(current)
            message = raw_messages[current]
            chain.append(message)
            current = message.get('parentId')
        form.chat = copy_chat_without_context_state(
            {
                **form.chat,
                'history': {'messages': raw_messages, 'currentId': branch},
                'messages': list(reversed(chain)),
                'files': copy.deepcopy((snapshot.get('chat') or {}).get('files', form.chat.get('files', []))),
            }
        )
    reserved, _ = await repository.register_tasks(
        context_id,
        user.id,
        epoch,
        [dict(task_id=task_id, reply_id=branch, model_id='promotion', branch_id=branch, worker_nonce=uuid4().hex)],
        expected_revision=revision,
        exclusive=True,
    )
    return repository, context_id, user.id, epoch, task_id, branch


async def finish_temporary_promotion(reservation):
    if reservation:
        repository, context_id, actor_id, epoch, task_id, branch = reservation
        from open_webui.utils.temporary_context_runtime import acknowledge_temporary_task

        await acknowledge_temporary_task(
            {
                'user_id': actor_id,
                'chat_id': f'temporary:{context_id}',
                'message_id': branch,
                'task_id': task_id,
                'temporary_context': {'id': context_id, 'epoch': epoch},
            }
        )
