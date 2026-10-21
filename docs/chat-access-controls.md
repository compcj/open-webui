# Chat access controls

## Global entry controls

Administrators can configure two independent switches under **Admin Panel → Settings → Authentication**.
Both default to enabled for compatibility. Administrators are exempt from both restrictions.

| Setting               | When disabled for non-admin users                                                                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Allow Temporary Chats | Rejects `temporary:` and legacy `local:` conversations, and hides temporary chat controls. Overrides personal defaults and group settings, including forced temporary chats.       |
| Allow Direct API Chat | Rejects chat completions without conversation/message context and blocks public OpenAI/Ollama generation and OpenAI passthrough routes. Applies to JWT and API key authentication. |

The second switch covers `/api/chat/completions`, `/api/v1/chat/completions`,
`/api/message`, `/api/v1/messages`, `/openai/chat/completions`, `/openai/responses`, the OpenAI catch-all passthrough,
and Ollama chat, generate, completions, messages and responses routes, including indexed variants.
Saved conversations still require current ownership or permitted shared-chat access; channels retain
their membership and write-access checks. Normal new chats are saved by the backend before generation. Temporary chats
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

The personal **Integrations** entry for tool/terminal connections has a separate administrator setting,
`direct.integrations.enable`, which defaults to hidden. Hiding that entry does not revoke saved
connections. Model **Direct Connections** uses the independent `enable_direct_connections` control;
neither is the same as disabling context-free Direct API Chat. Cookie forwarding also remains a
separate, default-off connection option.

## Shared conversations and reply ownership

For a shared chat or folder, a `read` grant defines who can access it; `share_mode=continue` allows
those members to continue the conversation using upstream sharing semantics. Clone-only sharing
retains snapshot behavior. Read or continue access does not authorize replacing the entire chat,
approving/stopping another participant's reply, or running owner-only manual `/compact`.

Each participant uses their own current model/tool/terminal permissions, variables and credentials.
Private request and speech metadata are filtered by message author in responses, exports, clones and
live updates. Saved public transcript text and author/browser-source labels remain shareable; the
browser-source label does not attest that the provider generated the submitted transcript text.
Updating old voice metadata uses its dedicated author-checked endpoint, not a general message update.

Standard and Realtime calls require chat ownership, including for administrators who do not own that
chat. Switching to another owner's chat closes an active call. Existing private voice metadata is not
rewritten, and ordinary dictation keeps its existing permissions. See the
[v0.12.2 decision record](upstream/v0.12.2-decisions.md#d03--所有者通话与旧转写).

Shared V2 summaries and selected tool results belong to a chat, model and validated branch. Only
saved shared history enters a shared summary; personal instructions and terminal context stay in the
current request. Old summaries without proven shared provenance replay raw history. See
[context compaction](context-compaction.md) for modes, status and exact result access.

## Stopping and interrupted responses

For backend tasks, pressing Stop waits for the executing worker to finish cancellation. In a Redis deployment, a missed
Pub/Sub message does not lose the saved stop request. Removing an expired task lease does not count
as confirmation. If cancellation is still unfinished after the wait, the UI reports a retryable error,
keeps the reply/task state and does not send the next queued prompt. Repeating Stop waits for cleanup
without cancelling that cleanup again. A late stop response cannot modify a newly selected reply or
another chat. Client-side generation without a backend task uses the browser's abort path.
Approval pauses retain their rejection flow and reply-author checks.

A successful stop acknowledges task completion, not rollback of work a tool already performed.
For example, knowledge text may already have been saved before indexing was interrupted; inspect
the result before retrying a write. See [knowledge write recovery](knowledge-write-tools.md).

If the provider transport breaks, the backend retains buffered response text and completed earlier
tool results in saved chats, marks unfinished output incomplete and surfaces the failure. It does not
execute an unfinished tool call or partial generated code from that interrupted round. A protocol SSE
`[DONE]` event ends the stream without waiting for the HTTP connection to close; the same text inside
an ordinary answer is not a completion event. A later successful continuation clears an earlier stream
error only when the backend explicitly signals recovery.

Parallel model comparisons use separate mutable payloads and request state for each reply, including
when the same model is selected twice. Tool results, branch identity, retry state and V2 bookkeeping
must stay with their originating reply. This preserves the upstream comparison feature while fixing
cross-reply interference.

## Validation and related policies

Commands and test boundaries are in the [development regression map](DEVELOPMENT.md#fork-regression-map)
and [lifecycle checks](DEVELOPMENT.md#chat-lifecycle-and-request-isolation-verification).
These contracts are supported by focused source/transport tests; documentation maintenance does not
constitute live Redis failover, provider, browser or deployment acceptance.

Apps and terminal file access have additional boundaries: private App interactions may be available
to read-only members, but public replies and stored message updates recheck write permission after
the App responds. A [signed terminal file link](TERMINAL_FILES.md) remains bound to its original
user/terminal/context; sharing a chat does not transfer that file authorization.
