"""Copy raw chat history without identities belonging to the original context store."""

from __future__ import annotations

import copy


DERIVED_CONTEXT_FIELDS = frozenset(
    {
        'temporary_context',
        'context_scope',
        'contextSummary',
        'context_summary',
        'context_usage',
        'context_compaction',
        'chat_context_checkpoint',
        'context_checkpoint',
        'checkpoint_id',
        'selection_id',
        'selection_ids',
        'tool_result_selections',
        'tool_result_selection',
        'tool_result_accounting',
        'tool_budget_accounting',
        'execution_ledger',
        'event_seq',
        'events_dropped_through',
        'raw_cursor',
        'cursor',
        '_tool_result_refs',
        '_context_message_id',
        '_context_original_key',
        'context_checkpoint_id',
        'tool_result_ledger',
        '_temporary_execution_id',
    }
)


def _clean(envelope):
    if isinstance(envelope, dict):
        for key in DERIVED_CONTEXT_FIELDS:
            envelope.pop(key, None)


def copy_chat_without_context_state(chat: dict) -> dict:
    result = copy.deepcopy(chat)
    _clean(result)
    result.pop('share_mode', None)
    for envelope in (result.get('meta'), result.get('metadata'), result.get('history')):
        _clean(envelope)
    if isinstance(result.get('id'), str) and result['id'].startswith(('temporary:', 'local:')):
        result.pop('id')
    messages = list(((result.get('history') or {}).get('messages') or {}).values())
    messages.extend(result.get('messages') or [])
    for message in messages:
        if not isinstance(message, dict):
            continue
        _clean(message)
        _clean(message.get('meta'))
        _clean(message.get('metadata'))
        for item in message.get('output') or []:
            if isinstance(item, dict):
                _clean(item)
                _clean(item.get('meta'))
                _clean(item.get('metadata'))
                item.pop('tool_result', None)
        for key in ('statusHistory', 'status_history'):
            if isinstance(message.get(key), list):
                message[key] = [
                    event
                    for event in message[key]
                    if isinstance(event, dict)
                    and event.get('action') not in {'context_compaction', 'tool_result_compaction'}
                ]
    return result
