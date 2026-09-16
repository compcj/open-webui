# Chat access controls

Administrators can configure two independent switches under **Admin Panel → Settings → Authentication**.
Both default to enabled for compatibility. Administrators are exempt from both restrictions.

| Setting               | When disabled for non-admin users                                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Allow Temporary Chats | Rejects `temporary:` and legacy `local:` conversations, and hides temporary chat controls. Overrides personal defaults and group settings, including forced temporary chats.       |
| Allow Direct API Chat | Rejects chat completions without conversation/message context and blocks public OpenAI/Ollama generation and OpenAI passthrough routes. Applies to JWT and API key authentication. |

The second switch covers `/api/chat/completions`, `/api/v1/chat/completions`,
`/api/message`, `/api/v1/messages`, `/openai/chat/completions`, `/openai/responses`, the OpenAI catch-all passthrough,
and Ollama chat, generate, completions, messages and responses routes, including indexed variants.
Saved conversations still use the existing ownership checks; channels retain their membership and
write-access checks. Normal new chats are saved by the backend before generation. Temporary chats
with conversation context remain available if their separate switch is enabled.

These settings control supported request paths and conversation context. They cannot distinguish a
browser from a client reproducing a valid WebUI request. A client can still use permitted conversation
flows, including creating a saved chat. Model access checks, API key permissions, and endpoint
restrictions continue to apply. Embeddings and task-specific helpers (such as titles, autocomplete,
and response merging) are outside the direct chat API switch. Internal provider calls for normal
chats and helper tasks continue to work.

Environment defaults are `ENABLE_TEMPORARY_CHATS=True` and `ENABLE_DIRECT_API_CHAT=True`.
They seed `chat.temporary.enable` and `chat.direct_api.enable` in the existing configuration store;
saved administrator settings take precedence. Changes apply to subsequent requests without a restart.
Other users may need to reload the page to refresh controls; backend enforcement is immediate.
Requests already generating are not cancelled. No database schema migration is required.
