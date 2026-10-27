"""Temporary-context transport/lifecycle orchestration; no SQL history writes."""

from __future__ import annotations

import asyncio
import copy
import logging
from uuid import uuid4

from fastapi import HTTPException
from open_webui.utils.context_scope import ContextScope
from open_webui.utils.context_store_errors import (
    ContextConflict,
    ContextGone,
    ContextUnavailable,
    ContextStoreError,
    ContextCapacity,
)
from open_webui.utils.context_store_temporary import TemporaryContextAdapter
from open_webui.utils.temporary_context_store import append_event_to_document
from open_webui.utils.temporary_contexts import (
    temporary_repository,
    resolve_temporary_scope,
    scope_metadata,
    store_from_metadata,
)

log = logging.getLogger(__name__)
# Completion retries contain identities only, never outputs or credentials.
_pending_completions = {}


async def authenticate_socket(actor_id, context_id, session_id):
    from open_webui.socket.main import get_event_call_session_error

    if not isinstance(session_id, str) or not session_id:
        raise HTTPException(403, 'Temporary context requires an authenticated browser session.')
    error = await get_event_call_session_error(
        session_id,
        {
            'user_id': actor_id,
            'chat_id': f'temporary:{context_id}',
            'message_id': 'scope-lease',
        },
    )
    if error:
        if 'unavailable' in error.lower() or 'retry' in error.lower():
            raise ContextUnavailable()
        raise HTTPException(403, error)


async def validate_scope_socket(metadata, *, settlement=False):
    store = await store_from_metadata(metadata, settlement=settlement)
    if store is None:
        return None
    doc = await store.history._read(settlement=settlement)
    if (
        not doc['socket']['connected']
        or doc['socket']['id'] != metadata.get('session_id')
        or doc['socket']['generation'] != metadata['temporary_context'].get('socket_generation')
    ):
        raise ContextConflict()
    return store


async def register_temporary_generation(request, user, identity, metadata, entries):
    store, doc = await resolve_temporary_scope(request, user, identity, require_revision=True)
    scope = store.scope
    if metadata['chat_id'] != scope.store_key or metadata.get('internal'):
        raise ContextConflict()
    branch_id = identity.get('branch_id')
    messages = doc['data'].get('snapshot', {}).get('messages_map', {})
    source = messages.get(branch_id, {})
    if source.get('role') != 'user' or metadata.get('user_message_id') != branch_id:
        raise ContextConflict()
    await authenticate_socket(user.id, scope.context_id, metadata.get('session_id'))
    if not doc['socket']['connected'] or doc['socket']['id'] != metadata.get('session_id'):
        raise ContextConflict()
    prepared = []
    for entry in entries:
        reply_id, model_id = entry.get('message_id'), entry.get('model_id')
        reply = messages.get(reply_id, {})
        if (
            not isinstance(model_id, str)
            or not model_id
            or reply.get('role') != 'assistant'
            or reply.get('model') != model_id
            or reply.get('parentId') != branch_id
            or reply.get('done') is not False
            or ((reply.get('content') or reply.get('output')) and metadata.get('assistant_message_id') != reply_id)
        ):
            raise ContextConflict()
        prepared.append(
            dict(
                task_id=f'temporary.{scope.context_id}.{uuid4().hex}',
                reply_id=reply_id,
                model_id=model_id,
                branch_id=branch_id,
                worker_nonce=uuid4().hex,
            )
        )
    _, tasks = await store.history.repository.register_tasks(
        scope.context_id, user.id, scope.epoch, prepared, expected_revision=scope.revision
    )
    return {
        task['reply_id']: {
            'scope': scope.for_reply(
                reply_id=task['reply_id'], model_id=task['model_id'], task_id=task['task_id'], fence=task['fence']
            ),
            'socket_generation': doc['socket']['generation'],
        }
        for task in tasks
    }


def bind_temporary_reply(request, metadata, reservation):
    scope = reservation['scope']
    request.state.context_scope = scope
    request.state.context_store = TemporaryContextAdapter(temporary_repository(request), scope).bundle()
    metadata['temporary_context'] = {**scope_metadata(scope), 'socket_generation': reservation['socket_generation']}
    return scope


def reduce_temporary_event(message, event):
    """Apply raw events before publishing their sequence; preserve replay sidecars."""
    kind, data = event.get('type'), event.get('data') or {}
    if kind in ('chat:completion', 'response:completion'):
        if isinstance(data.get('output'), list):
            message['output'] = copy.deepcopy(data['output'])
        elif kind == 'response:completion':
            from open_webui.utils.response import handle_responses_streaming_event

            message['output'], _ = handle_responses_streaming_event(data, copy.deepcopy(message.get('output') or []))
        for key in ('content', 'done', 'error', 'usage', 'finish_reason', 'selected_model_id', 'sources'):
            if key in data:
                message[key] = copy.deepcopy(data[key])
        if 'output' in message and 'content' not in data:
            from open_webui.utils.misc import get_output_text

            message['content'] = get_output_text(message['output'])
    elif kind == 'message':
        message['content'] = str(message.get('content') or '') + str(data.get('content') or '')
    elif kind == 'replace':
        message['content'] = data.get('content', '')
    elif kind in ('status', 'context_compaction', 'tool_result_compaction'):
        message.setdefault('statusHistory', []).append(copy.deepcopy(data))
    elif kind in ('files', 'embeds'):
        message[kind] = copy.deepcopy(data.get(kind) or []) + ([] if data.get('replace') else message.get(kind, []))
    elif kind in ('source', 'citation') and data.get('type') is None:
        message.setdefault('sources', []).append(copy.deepcopy(data))
    elif kind == 'context_usage':
        message['context_usage'] = copy.deepcopy(data)
        if data.get('tool_budget_accounting'):
            message.setdefault('meta', {})['tool_budget_accounting'] = copy.deepcopy(data['tool_budget_accounting'])
    elif kind == 'chat:message:error':
        message.update(copy.deepcopy(data))
    elif kind == 'chat:tasks:cancel':
        message['done'] = True
        message['interrupted'] = True
        for item in message.get('output', []):
            if item.get('status') in ('in_progress', 'pending', 'queued', 'requires_approval'):
                item['status'] = 'incomplete'
    elif kind == 'chat:message:follow_ups':
        message['followUps'] = copy.deepcopy(data)


async def persist_temporary_event(metadata, event, *, update_raw=True):
    store = await store_from_metadata(metadata, settlement=True)
    if store is None:
        raise ContextConflict()
    adapter, scope = store.history, store.scope

    def write(doc, now):
        task = adapter._task(doc, settlement=True)
        message = None if task.get('exclusive') else adapter._reply(doc, scope.reply_id)
        if update_raw and message is not None:
            reduce_temporary_event(message, event)
        return append_event_to_document(
            doc,
            event,
            adapter.repository.config,
            task_id=scope.task_id,
            reply_id=scope.reply_id,
            model_id=scope.model_id,
        )

    doc, envelope = await adapter._write(write, source_changed=update_raw, settlement=True)
    envelope['chat_id'] = scope.store_key
    return doc, envelope


async def emit_temporary_event(metadata, event, *, update_raw=True):
    if event.get('type') == 'tool_result_compaction':
        data = event.get('data') or {}
        if data.get('source_message_id') not in (None, metadata.get('message_id')):
            return
        if not data.get('error') and (data.get('method') in ('original', 'read') or data.get('reused')):
            return
    doc, envelope = await persist_temporary_event(metadata, event, update_raw=update_raw)
    socket_id = doc['socket']['id']
    if not socket_id or not doc['socket']['connected']:
        return
    from open_webui.socket.main import sio, get_event_call_session_error

    routing = {**metadata, 'session_id': socket_id}
    # Rebinding changes only delivery; it never grants a callback to a new page.
    routing.pop('temporary_context', None)
    if await get_event_call_session_error(socket_id, routing):
        return
    await sio.emit('events', envelope, to=socket_id)


MAX_PENDING_RPCS = 32
RPC_PUBLIC_FIELDS = ('rpc_id', 'task_id', 'reply_id', 'model_id', 'socket_generation', 'seq', 'expires_at', 'claimed')


def valid_temporary_rpc(doc, marker, now):
    task = doc['tasks'].get((marker or {}).get('task_id'))
    return bool(
        marker
        and task
        and doc['status'] == 'open'
        and doc['socket']['connected']
        and task['status'] != 'done'
        and marker['expires_at'] > now
        and marker['socket_generation'] == doc['socket']['generation']
        and marker['fence'] == doc['fence'] == task['fence']
        and marker['reply_id'] == task['reply_id']
        and marker['model_id'] == task['model_id']
    )


def pending_temporary_rpcs(doc, now):
    return [
        {key: marker[key] for key in RPC_PUBLIC_FIELDS}
        for marker in doc.get('data', {}).get('rpc_calls', {}).values()
        if valid_temporary_rpc(doc, marker, now)
    ]


async def temporary_rpc_envelope(metadata, event, *, timeout=None):
    store = await validate_scope_socket(metadata)
    if store is None:
        raise ContextConflict()
    adapter, scope = store.history, store.scope
    rpc_id = uuid4().hex
    lifetime = min(max(float(timeout or 300), 1), 3600)

    def write(doc, now):
        if (
            doc['socket']['id'] != metadata.get('session_id')
            or not doc['socket']['connected']
            or doc['socket']['generation'] != metadata['temporary_context'].get('socket_generation')
        ):
            raise ContextConflict()
        pending = doc['data'].setdefault('rpc_calls', {})
        for key in list(pending):
            if not valid_temporary_rpc(doc, pending[key], now):
                del pending[key]
        if len(pending) >= MAX_PENDING_RPCS:
            raise ContextCapacity(limit='pending_rpcs')
        # Sequence is shared with notifications, but RPC arguments never enter
        # the journal: they can contain request-local browser credentials.
        doc['event_seq'] += 1
        marker = dict(
            rpc_id=rpc_id,
            task_id=scope.task_id,
            reply_id=scope.reply_id,
            model_id=scope.model_id,
            fence=scope.execution_fence,
            socket_generation=doc['socket']['generation'],
            seq=doc['event_seq'],
            expires_at=now + lifetime,
            claimed=False,
        )
        if not valid_temporary_rpc(doc, marker, now):
            raise ContextConflict()
        pending[rpc_id] = marker
        return marker

    doc, marker = await adapter._write(write)
    return {
        'temporary_context': {
            'id': doc['id'],
            'epoch': doc['epoch'],
            'revision': doc['revision'],
            'socket_generation': marker['socket_generation'],
        },
        'chat_id': scope.store_key,
        'message_id': scope.reply_id,
        'model_id': scope.model_id,
        'task_id': scope.task_id,
        'seq': marker['seq'],
        'rpc_id': rpc_id,
        'socket_generation': marker['socket_generation'],
        'data': copy.deepcopy(event),
    }


async def validate_temporary_rpc(metadata, rpc_id):
    store = await validate_scope_socket(metadata)
    if store is None:
        raise ContextConflict()
    doc = await store.history._read()
    marker = doc.get('data', {}).get('rpc_calls', {}).get(rpc_id)
    if (
        not valid_temporary_rpc(doc, marker, await store.history.repository.backend.now())
        or marker['task_id'] != store.scope.task_id
    ):
        raise ContextConflict()
    return marker


async def claim_temporary_rpc(repository, context_id, actor_id, epoch, rpc_id, identity):
    def claim(doc, now):
        marker = doc.get('data', {}).get('rpc_calls', {}).get(rpc_id)
        if (
            not valid_temporary_rpc(doc, marker, now)
            or marker['claimed']
            or any(
                marker[key] != identity.get(key)
                for key in ('socket_generation', 'task_id', 'reply_id', 'model_id', 'seq')
            )
        ):
            raise ContextConflict()
        marker['claimed'] = True
        return {
            'rpc_id': rpc_id,
            'socket_generation': marker['socket_generation'],
            'claimed': True,
            'remaining_ms': max(0, int((marker['expires_at'] - now) * 1000)),
        }

    _, result = await repository.transact(context_id, actor_id, epoch, claim)
    return result


async def complete_temporary_rpc(metadata, rpc_id):
    store = await store_from_metadata(metadata, settlement=True)
    if store is None:
        return

    def complete(doc, now):
        pending = doc.get('data', {}).get('rpc_calls', {})
        marker = pending.get(rpc_id)
        if marker and marker['task_id'] == store.scope.task_id:
            pending.pop(rpc_id, None)

    await store.history.repository._transact(
        store.scope.context_id, store.scope.actor_id, store.scope.epoch, complete, lifecycle=True
    )


async def acknowledge_temporary_task(metadata):
    identity = metadata['temporary_context']
    key = (identity['id'], metadata['user_id'], identity['epoch'], metadata['task_id'])
    try:
        repository = temporary_repository()
        doc = await repository.acknowledge_task_done(*key[:3], task_id=key[3])
        if any(entry['task_id'] == key[3] and entry.get('publication_pending') for entry in doc['executions'].values()):
            store = await store_from_metadata(metadata, settlement=True)
            await store.results.recover_publications()
            doc = await repository.read(*key[:3], allow_closed=True)
    except ContextStoreError:
        _pending_completions[key] = {
            name: copy.deepcopy(metadata.get(name))
            for name in ('user_id', 'chat_id', 'message_id', 'task_id', 'session_id', 'temporary_context')
        }
        return False
    _pending_completions.pop(key, None)
    # A hint only. Clients must reconcile persisted state before advancing queues.
    try:
        if doc['socket']['connected'] and doc['socket']['id']:
            from open_webui.socket.main import sio, get_event_call_session_error

            routing = {k: metadata[k] for k in ('user_id', 'chat_id', 'message_id')}
            if not await get_event_call_session_error(doc['socket']['id'], routing):
                event = next(
                    (
                        item
                        for item in reversed(doc['events'])
                        if item.get('task_id') == key[3] and item.get('data', {}).get('type') == 'temporary:task-done'
                    ),
                    None,
                )
                if event is not None:
                    await sio.emit('events', {**event, 'chat_id': metadata['chat_id']}, to=doc['socket']['id'])
    except Exception:
        log.debug('Temporary task completion hint was not delivered; durable completion is retained')
    return True


async def abort_temporary_reservations(request, metadata, reservations, started):
    """Release every never-started reservation if a fanout cannot be launched."""
    if not reservations:
        return
    first = next(iter(reservations.values()))['scope']
    try:
        await temporary_repository(request).request_stop(first.context_id, first.actor_id, first.epoch)
    except ContextStoreError:
        log.warning('Temporary fanout stop intent will be retried during cleanup')
    for task in started.values():
        if not task.done():
            task.cancel()
    cancellation = None
    for reservation in reservations.values():
        scope = reservation['scope']
        if scope.task_id not in started:
            bound = {
                **metadata,
                'message_id': scope.reply_id,
                'task_id': scope.task_id,
                'temporary_context': {
                    **scope_metadata(scope),
                    'socket_generation': reservation['socket_generation'],
                },
            }
            try:
                await acknowledge_temporary_task(bound)
            except asyncio.CancelledError as error:
                cancellation = error
                _pending_completions[(scope.context_id, scope.actor_id, scope.epoch, scope.task_id)] = {
                    name: copy.deepcopy(bound.get(name))
                    for name in ('user_id', 'chat_id', 'message_id', 'task_id', 'session_id', 'temporary_context')
                }
            except Exception:
                _pending_completions[(scope.context_id, scope.actor_id, scope.epoch, scope.task_id)] = {
                    name: copy.deepcopy(bound.get(name))
                    for name in ('user_id', 'chat_id', 'message_id', 'task_id', 'session_id', 'temporary_context')
                }
                log.warning('Temporary reservation release will retry independently')
    if cancellation is not None:
        raise cancellation


async def stop_temporary_context(request, user, context_id, epoch, *, close=False):
    repository = temporary_repository(request)
    doc = await repository.read(context_id, user.id, epoch, allow_closed=True)
    doc = await (
        repository.close(context_id, user.id, epoch) if close else repository.request_stop(context_id, user.id, epoch)
    )
    from open_webui.tasks import stop_task

    task_ids = [task['task_id'] for task in doc['tasks'].values() if task['status'] != 'done']
    await asyncio.gather(*(stop_task(request.app.state.redis, task_id, allow_missing=True) for task_id in task_ids))
    # Never trust the generic task index: its lease can expire before a worker.
    doc = await repository.read(context_id, user.id, epoch, allow_closed=True)
    done = all(task['status'] == 'done' for task in doc['tasks'].values())
    settled = all(
        entry['status'] == 'resolved' and not entry.get('publication_pending') for entry in doc['executions'].values()
    )
    return doc, {'status': done and settled, 'retryable': not (done and settled), 'task_ids': task_ids}


async def scope_from_headers(request, user, *, chat_id=None, task_id=None):
    context_id, epoch = request.headers.get('X-Temporary-Context-Id'), request.headers.get('X-Temporary-Context-Epoch')
    if not context_id or not epoch or (chat_id is not None and chat_id != f'temporary:{context_id}'):
        raise HTTPException(404, 'Temporary context not found.')
    doc = await temporary_repository(request).read(context_id, user.id, epoch, allow_closed=True)
    if task_id is not None and task_id not in doc['tasks']:
        raise HTTPException(404, 'Task not found.')
    return doc


async def temporary_context_maintenance(app):
    from open_webui.tasks import stop_task
    from open_webui.socket.main import get_event_call_session_error

    async def socket_alive(doc):
        if not doc['socket']['id']:
            return False
        error = await get_event_call_session_error(
            doc['socket']['id'],
            {
                'user_id': doc['actor_id'],
                'chat_id': f'temporary:{doc["id"]}',
                'message_id': 'scope-lease',
            },
        )
        if error and ('unavailable' in error.lower() or 'retry' in error.lower()):
            return None
        return error is None

    while True:
        try:
            for metadata in list(_pending_completions.values()):
                try:
                    await acknowledge_temporary_task(metadata)
                except Exception:
                    log.warning('Temporary completion retry failed; continuing other scopes')
            for result in await app.state.temporary_contexts.sweep(socket_alive=socket_alive):
                for task in result.get('publications', []):
                    scope = ContextScope(
                        kind='temporary',
                        context_id=result['id'],
                        actor_id=result['actor_id'],
                        epoch=result['epoch'],
                        revision=0,
                        branch_id=task['reply_id'],
                        reply_id=task['reply_id'],
                        model_id=task['model_id'],
                        task_id=task['task_id'],
                        execution_fence=task['fence'],
                    )
                    try:
                        await TemporaryContextAdapter(app.state.temporary_contexts, scope).recover_publications()
                    except Exception:
                        log.warning('Temporary publication recovery failed; continuing cancellation')
                for task in result.get('tasks', []):
                    try:
                        await stop_task(app.state.redis, task['task_id'], allow_missing=True)
                    except Exception:
                        log.warning('Temporary task cancellation will retry independently')
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning('Temporary context cleanup will retry; completion remains unconfirmed', exc_info=True)
        await asyncio.sleep(5)
