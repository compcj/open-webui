"""Copy raw chat history without identities belonging to the original context store."""

from __future__ import annotations

import copy


def copy_chat_without_context_state(chat: dict) -> dict:
    result = copy.deepcopy(chat)
    for key in (
        'share_mode',
        'context_usage',
        'context_checkpoint',
        'chat_context_checkpoint',
        'tool_result_selections',
    ):
        result.pop(key, None)
    messages = list(((result.get('history') or {}).get('messages') or {}).values())
    messages.extend(result.get('messages') or [])
    for message in messages:
        if not isinstance(message, dict):
            continue
        for key in (
            'contextSummary',
            'context_summary',
            '_context_message_id',
            '_context_original_key',
            '_tool_result_refs',
        ):
            message.pop(key, None)
        if isinstance(message.get('meta'), dict):
            message['meta'].pop('tool_budget_accounting', None)
        for item in message.get('output') or []:
            if isinstance(item, dict):
                item.pop('tool_result', None)
        for key in ('statusHistory', 'status_history'):
            if isinstance(message.get(key), list):
                message[key] = [
                    event
                    for event in message[key]
                    if event.get('action') not in {'context_compaction', 'tool_result_compaction'}
                ]
    return result
