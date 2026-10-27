"""Authenticated temporary scopes, raw source synchronization and public state.

The deployment repository is installed once by application lifespan for socket
callbacks. It never switches storage based on a request. Request algorithms
receive an explicit immutable ContextStore instead.
"""

from __future__ import annotations

import copy

from fastapi import HTTPException
from open_webui.utils.chat_context_copy import copy_chat_without_context_state
from open_webui.utils.context_scope import ContextScope
from open_webui.utils.context_store_errors import ContextConflict, ContextGone, ContextNotFound, ContextUnavailable
from open_webui.utils.context_store_rules import branch_chain, source_digest
from open_webui.utils.context_store_temporary import TemporaryContextAdapter

_deployment_repository = None
MAX_GRAPH_MESSAGES = 8192


def configure_temporary_repository(repository):
    """Application-start/stop hook only; requests must never call this setter."""
    global _deployment_repository
    _deployment_repository = repository


def temporary_repository(request=None):
    value = getattr(getattr(getattr(request, 'app', None), 'state', None), 'temporary_contexts', None)
    if value is None:
        value = _deployment_repository
    if value is None:
        raise ContextUnavailable()
    return value


def _invalid_graph():
    return HTTPException(400, detail={'code': 'temporary_context_invalid_history', 'retryable': False})


def _identifier(value, *, optional=False):
    return (value is None and optional) or (
        isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 128 and '\x00' not in value
    )


def validate_temporary_history(history):
    if not isinstance(history, dict) or not isinstance(history.get('messages'), dict):
        raise _invalid_graph()
    messages = history['messages']
    if len(messages) > MAX_GRAPH_MESSAGES:
        raise _invalid_graph()
    current = history.get('currentId')
    if (messages and current not in messages) or (not messages and current is not None):
        raise _invalid_graph()
    for identity, message in messages.items():
        if (
            not _identifier(identity)
            or not isinstance(message, dict)
            or message.get('id') != identity
            or message.get('role') not in {'user', 'assistant'}
            or not _identifier(message.get('parentId'), optional=True)
            or ('parent_id' in message and message['parent_id'] != message.get('parentId'))
            or (message.get('parentId') is not None and message['parentId'] not in messages)
            or not isinstance(message.get('content', ''), (str, list))
            or ('output' in message and not isinstance(message['output'], list))
            or ('files' in message and not isinstance(message['files'], list))
            or ('tool_results' in message and not isinstance(message['tool_results'], list))
            or ('meta' in message and not isinstance(message['meta'], dict))
            or any(not isinstance(item, dict) for item in message.get('files', []))
        ):
            raise _invalid_graph()
    # Iterative traversal validates every node, including inactive branches,
    # without recursion limits or quadratic full-chain scans.
    complete = set()
    for identity in messages:
        path, seen = [], set()
        cursor = identity
        while cursor is not None and cursor not in complete:
            if cursor in seen:
                raise _invalid_graph()
            seen.add(cursor)
            path.append(cursor)
            cursor = messages[cursor].get('parentId')
        complete.update(path)
    sanitized = copy_chat_without_context_state({'history': history})['history']
    for message in sanitized['messages'].values():
        message['childrenIds'] = []
        message.pop('parent_id', None)
        for key in ('temporary_context', 'context_scope', 'execution_id', 'selection_id', 'checkpoint_id', 'cursor'):
            message.pop(key, None)
        if isinstance(message.get('meta'), dict):
            for key in (
                'temporary_context',
                'context_scope',
                'context_checkpoint',
                'chat_context_checkpoint',
                'tool_result_selections',
                'context_usage',
                'execution_id',
                'task_id',
                'selection_id',
                'cursor',
            ):
                message['meta'].pop(key, None)
        for item in message.get('output', []):
            if not isinstance(item, dict):
                raise _invalid_graph()
            for key in (
                'temporary_context',
                '_temporary_execution_id',
                'execution_id',
                'selection_id',
                'context_id',
                'cursor',
            ):
                item.pop(key, None)
        for item in message.get('tool_results', []):
            if not isinstance(item, dict):
                raise _invalid_graph()
            for key in ('kind', 'execution_id', 'selection_id', 'context_id', 'source_hash', 'source_fingerprint'):
                item.pop(key, None)
    for identity, message in sanitized['messages'].items():
        if message.get('parentId') is not None:
            sanitized['messages'][message['parentId']]['childrenIds'].append(identity)
    return sanitized


def _raw_identity(message):
    if not isinstance(message, dict):
        return None
    clean = copy_chat_without_context_state({'messages': [message]})['messages'][0]
    for item in clean.get('tool_results') or []:
        if isinstance(item, dict):
            for key in ('kind', 'execution_id', 'selection_id', 'context_id', 'source_hash', 'source_fingerprint'):
                item.pop(key, None)
    return source_digest(
        {
            key: clean.get(key)
            for key in ('id', 'role', 'parentId', 'content', 'output', 'files', 'tool_results', 'model')
        }
    )


def _sync_snapshot(doc, history, params, files):
    previous = doc['data'].get('snapshot') or {}
    old = previous.get('messages_map') or {}
    incoming = copy.deepcopy(history['messages'])
    unsettled = {
        entry['task_id']
        for entry in doc['executions'].values()
        if entry['status'] != 'resolved' or entry.get('publication_pending')
    }
    for task in doc['tasks'].values():
        if task['status'] == 'done' and task['task_id'] not in unsettled:
            continue
        chain = branch_chain(old, task['reply_id'])
        if not chain:
            raise ContextConflict()
        for message in chain:
            if _raw_identity(message) != _raw_identity(incoming.get(message['id'])):
                raise ContextConflict()
    # Echoed output does not become client execution proof. Preserve the exact
    # existing server copy/provenance only when the raw source is unchanged.
    for identity, message in incoming.items():
        if identity in old and _raw_identity(message) == _raw_identity(old[identity]):
            for key in ('output', 'tool_results'):
                if key in old[identity]:
                    message[key] = copy.deepcopy(old[identity][key])
    chat = copy.deepcopy(previous.get('chat') or {})
    if params is not None:
        chat['params'] = copy.deepcopy(params)
    if files is not None:
        chat['files'] = copy.deepcopy(files)
    return {
        'chat': chat,
        'messages_map': incoming,
        'current_id': history['currentId'],
        'owner_id': doc['actor_id'],
        'shared_history': False,
    }


async def sync_temporary_history(
    repository, actor_id, context_id, epoch, expected_revision, history, *, params=None, files=None
):
    if type(expected_revision) is not int or expected_revision < 0:
        raise _invalid_graph()
    if (params is not None and not isinstance(params, dict)) or (files is not None and not isinstance(files, list)):
        raise _invalid_graph()
    history = validate_temporary_history(history)
    doc = await repository.read(context_id, actor_id, epoch)
    if doc['revision'] != expected_revision:
        raise ContextConflict()
    snapshot = _sync_snapshot(doc, history, params, files)
    if snapshot == doc['data'].get('snapshot'):
        return public_identity(doc, repository.config)

    def write(current, now):
        old = (current['data'].get('snapshot') or {}).get('messages_map', {})
        snapshot = _sync_snapshot(current, history, params, files)
        changed = {
            key
            for key, value in old.items()
            if _raw_identity(value) != _raw_identity(snapshot['messages_map'].get(key))
        }
        if changed:
            # Source edits revoke server admission for that reply and descendants.
            # Appending a new branch does not revoke established selections.
            affected = {key for key in old if any(m['id'] in changed for m in branch_chain(old, key) or [])}
            data = current['data']
            import json

            data['result_admissions'] = {
                key: value
                for key, value in data.get('result_admissions', {}).items()
                if json.loads(key)[0] not in affected
            }
            removed_results = {key for key, row in data.get('results', {}).items() if row.get('message_id') in affected}
            for name in ('results', 'selections'):
                data[name] = {
                    key: row for key, row in data.get(name, {}).items() if row.get('message_id') not in affected
                }
            data['contexts'] = {
                key: row for key, row in data.get('contexts', {}).items() if row.get('result_id') not in removed_results
            }
            data['checkpoints'] = {
                key: row
                for key, row in data.get('checkpoints', {}).items()
                if not any(ref.get('id') in changed for ref in row.get('source_refs', []))
            }
        current['data']['snapshot'] = snapshot

    doc, _ = await repository.transact(
        context_id, actor_id, epoch, write, expected_revision=expected_revision, source_changed=True
    )
    return public_identity(doc, repository.config)


def public_identity(doc, config):
    return {
        key: doc.get(key)
        for key in (
            'id',
            'epoch',
            'revision',
            'status',
            'idle_deadline',
            'maximum_deadline',
            'disconnected_deadline',
            'rebuild_allowed',
        )
    } | {
        'socket_generation': doc['socket']['generation'],
        'lease_seconds': config.lease_seconds,
        'limits': {
            'context_bytes': config.context_bytes,
            'user_bytes': config.user_bytes,
            'max_contexts_per_user': config.max_contexts_per_user,
        },
    }


def public_state(doc, config, *, after_seq=0):
    from time import time
    from open_webui.utils.temporary_context_runtime import pending_temporary_rpcs

    snapshot = (doc.get('data') or {}).get('snapshot') or {}
    return {
        **public_identity(doc, config),
        'event_seq': doc['event_seq'],
        'pending_rpcs': pending_temporary_rpcs(doc, time()),
        'events_dropped_through': doc['events_dropped_through'],
        'events': [copy.deepcopy(event) for event in doc['events'] if event['seq'] > after_seq],
        'tasks': [
            {key: task.get(key) for key in ('task_id', 'reply_id', 'model_id', 'branch_id', 'status', 'done_at')}
            for task in doc['tasks'].values()
        ],
        'uncertain': any(
            entry['status'] == 'uncertain' or entry.get('publication_pending') for entry in doc['executions'].values()
        ),
        'history': {
            'messages': copy.deepcopy(snapshot.get('messages_map', {})),
            'currentId': snapshot.get('current_id'),
        },
    }


async def resolve_temporary_scope(request, user, identity, *, model_id=None, require_revision=False):
    if (
        not isinstance(identity, dict)
        or not _identifier(identity.get('id'))
        or not _identifier(identity.get('epoch'))
        or not _identifier(identity.get('branch_id'), optional=True)
        or type(identity.get('revision')) is not int
        or identity['revision'] < 0
    ):
        raise HTTPException(400, detail={'code': 'temporary_context_refresh_required', 'retryable': False})
    repository = temporary_repository(request)
    doc = await repository.read(identity['id'], user.id, identity['epoch'])
    if require_revision and doc['revision'] != identity['revision']:
        raise ContextConflict()
    snapshot = doc['data'].get('snapshot') or {}
    branch_id = identity.get('branch_id')
    if branch_id is not None and not branch_chain(snapshot.get('messages_map', {}), branch_id):
        raise ContextConflict()
    scope = ContextScope(
        'temporary',
        doc['id'],
        user.id,
        doc['epoch'],
        doc['revision'],
        branch_id,
        model_id=model_id,
        execution_fence=doc['fence'],
    )
    store = TemporaryContextAdapter(repository, scope).bundle()
    request.state.context_scope, request.state.context_store = scope, store
    return store, doc


def scope_metadata(scope):
    return {
        **scope.identity(),
        'task_id': scope.task_id,
        'reply_id': scope.reply_id,
        'model_id': scope.model_id,
        'fence': scope.execution_fence,
    }


async def store_from_metadata(metadata, *, settlement=False):
    identity = metadata.get('temporary_context')
    if not isinstance(identity, dict):
        return None
    actor_id = metadata.get('user_id')
    if not actor_id or metadata.get('chat_id') != f'temporary:{identity.get("id")}':
        raise ContextNotFound()
    repository = temporary_repository()
    doc = await repository.read(identity['id'], actor_id, identity['epoch'], allow_closed=settlement)
    if identity.get('reply_id') != metadata.get('message_id') or identity.get('task_id') != metadata.get('task_id'):
        raise ContextConflict()
    scope = ContextScope(
        'temporary',
        doc['id'],
        actor_id,
        doc['epoch'],
        doc['revision'],
        identity.get('branch_id'),
        identity.get('reply_id'),
        identity.get('model_id'),
        identity.get('task_id'),
        identity.get('fence'),
    )
    adapter = TemporaryContextAdapter(repository, scope)
    adapter._task(doc, settlement=settlement, allow_done=settlement)
    return adapter.bundle()
