"""Explicit saved-chat adapter; SQL initialization stays behind this factory."""

from open_webui.utils.context_scope import ContextScope, ContextStore


def saved_context_store(scope: ContextScope) -> ContextStore:
    if scope.kind != 'saved':
        raise ValueError('SQL context storage requires a saved-chat scope')
    from open_webui.models.chat_context_checkpoints import ChatContextCheckpoints
    from open_webui.models.chat_tool_results import ChatToolResults
    from open_webui.models.chat_tool_result_selections import ChatToolResultSelections

    return ContextStore(scope, ChatContextCheckpoints, ChatToolResults, ChatToolResultSelections)
