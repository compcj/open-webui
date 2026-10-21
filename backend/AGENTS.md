# Backend instructions

Applies to `backend/` and its descendants, together with the [root guide](../AGENTS.md).

## Work with the existing structure

- Use Python 3.11 or 3.12. Keep handlers in `open_webui/routers/`, persistence operations in
  `open_webui/models/`, and reusable logic in the appropriate `utils/` or `retrieval/` module.
- Reuse the router's authentication dependencies (`get_verified_user`, `get_admin_user`) and
  resource access checks. Add denied-access coverage for authorization changes; hiding UI alone
  does not authorize or protect an endpoint.
- Match existing async request, HTTP-session, and database-session patterns. Inspect
  `open_webui/internal/db.py` before changing session lifecycle or transaction boundaries.
  Avoid blocking network/database work inside async handlers.
- For persistent settings, trace environment defaults, `DEFAULT_CONFIG` in `config.py`, model
  storage, router configuration mapping, and the frontend settings form together. Do not assume an
  environment variable overrides a value already stored in the database.
- Schema changes belong in new Alembic revisions under `open_webui/migrations/versions/`. Inspect
  revision heads first, coordinate revision ownership, and do not rewrite deployed migrations.
  Test on a disposable database and describe upgrade/data-compatibility implications.
- `config.py` can run migrations and refresh `STATIC_DIR` at import time. Set isolated data,
  database, and static paths before importing the application in runtime tests. Existing lightweight
  helper tests avoid a full application import; do not remove that isolation accidentally.
- Do not commit runtime files under `data/`, `open_webui/data/`, uploads, caches, or secret-key files.
  Treat copied files in `open_webui/static/` carefully; inspect `config.py` and the corresponding
  frontend asset before deciding which source to change.

## Fork contracts

- Search1API spans `config.py`, `retrieval/web/search1api.py`, `routers/retrieval.py`, and the
  frontend `WebSearch.svelte`. Preserve response normalization, result limits, domain filtering,
  HTTP error handling, configuration persistence, and the missing-key error.
  `SEARCH1API_ENDPOINT` supplies the `search` default for `web.search.search1api_endpoint`;
  admin API `web.SEARCH1API_ENDPOINT` accepts only `search`/`ask`. Omission or `null` preserves
  the saved value, and enabled persistence retains its precedence over environment defaults.
  Both built-in search and automatic retrieval use the shared dispatch and keyword-only
  `endpoint` argument. `/search` sends `query` and `max_results`; `/ask` sends only `query`,
  unchanged. Filter results before applying the local count limit. Invalid runtime endpoints
  fail before network access; HTTP errors never trigger an automatic endpoint fallback.
  Keep the tool schema and `SearchResult` structure unchanged. See
  [verification](../docs/DEVELOPMENT.md#search1api-endpoint-verification) and the
  [decision follow-up](../docs/upstream/v0.12.2-search1api-endpoint-followup.md).
- Tavily search depth spans `config.py`, `retrieval/web/tavily.py`, and `routers/retrieval.py`.
  Preserve `TAVILY_SEARCH_DEPTH` (`basic` by default; `ultra-fast`, `fast`, `basic`, `advanced` accepted), the per-key setting
  `web.search.tavily_search_depth`, and its propagation through the shared search entry point.
  The admin configuration API accepts only these four values; omission or `null` preserves the
  current value. Stored settings keep the existing persistence precedence over environment defaults.
  Search depth is independent of `TAVILY_EXTRACT_DEPTH`; preserve result mapping, domain filtering,
  and HTTP errors. See the development guide for isolated request/config regression tests.
- Responses API payload conversion uses `utils/responses_reasoning.py` from `routers/openai.py`.
  Preserve removal of top-level `reasoning_effort` and merging into `reasoning.effort` without
  dropping other reasoning fields. Check the accompanying frontend selection/default behavior.
- OpenClaw-compatible skills span `models/skills.py` (`SkillMeta.openclaw` permissive bag),
  `routers/skills.py` (`/load/url`, `/id/{id}/install_deps`), `utils/skills_runtime.py`,
  `utils/middleware.py` (skill injection block), `tools/builtin.py` (`view_skill`), and the
  frontend `lib/utils/skills.ts`. `/load/url` resolves direct links, ClawHub refs, and GitHub
  repo/tree/blob URLs plus skills.sh links (repo zipball via codeload, `select` hint guides the
  frontend pick). Preserve gating semantics (`always` exempts `requires.*` only
  when the `os` constraint holds; `requires.config` checks the skill's own `config` bag),
  fingerprint-idempotent terminal file sync, degradation to default skill loading on any terminal
  failure, and the trust model: `/load/url` requires admin or `workspace.skills_import` and applies upstream SSRF, redirect,
  archive and download-size restrictions, `install_deps` requires skill write access and runs declared
  specs only on explicit request.

## Upstream integration contracts

- `skills.auto_discovery.ids` defaults to `[]`; admin-only `GET/POST /api/v1/configs/skills` uses
  `AUTO_DISCOVERY_SKILL_IDS`. Discovery intersects existing skill access for every role, including
  admins, and does not replace manual selection or model presets. Terminal auto-discovery trusts
  only saved admin connection `config.auto_discover_skills`, default false, never client metadata.
- Manifest generation must not sync unused OpenClaw files. Gate and prepare on actual loading;
  terminal lookup, listing, content, and AGENTS.md failures degrade without breaking chat.
- Preserve `search_snippet`, `search_result`, and `page_excerpt` evidence distinctions and stable
  mixed-source citation numbering. Empty snippets are navigation, not page evidence. Apply source
  metadata filtering and escaping together; reject unsafe link protocols.
- Preserve signed terminal file identity/context checks, response isolation, denied port/admin
  proxy paths, redirect refusal, opt-in cookie forwarding, and HTTP/WS identity consistency.
- Configured Redis revocation failure must deny access with a retryable HTTP `503` or terminal
  and Realtime WebSocket `1013`, without deleting valid login cookies. Unconfigured Redis is a separate case;
  invalid/revoked credentials remain denied. Check every caller's exception handling.
- Image verification uses `POST /api/v1/images/verify` with `{engine, url, key?}`. Preserve named
  engine settings and context-only image deduplication across normal, approved, and replayed tools.

## Chat controls and execution reliability

- Global temporary-chat and direct-API controls default on and exempt admins. Enforce them at the
  generation endpoints through `utils/chat_access.py`, including OpenAI, Ollama and Anthropic proxy
  routes. Disabling direct API chat still permits valid WebUI conversation requests; a supplied
  chat ID alone is insufficient. Missing/null fields from older admin clients preserve saved values.
- Knowledge and notes preferences control workspace discovery and tools; explicit attachments retain
  scoped readers with execution-time ACL checks. Memory opt-in governs tools, automatic context and
  post-turn review together, including model capability, global/user settings and user permissions.
  Keep `utils/tool_features.py` authoritative across those paths.
- Each concurrent model reply owns its nested payload, metadata and `Request.state`, including
  compaction/retry state, tool-schema validation and skill caches. Preserve cached immutable request
  body bytes for plugins, but never share parsed mutable JSON between replies. Keep
  `tests/test_chat_model_isolation.py` when changing multi-model dispatch.
- Treat `[DONE]` as terminal and close the active iterator promptly; a Chat Completions
  `finish_reason` still permits trailing usage before `[DONE]`. On transport failure, flush buffered
  content and persist incomplete output; cancellation preserves that output and shields cleanup.
  Never dispatch partial tool arguments or legacy code from an interrupted round. Clear an upstream error
  only after actual recovery, retaining unrecovered errors even when partial content exists.
- A repeated tool ID in one batch/replay must not execute twice. A fresh provider round may reuse
  an ID and needs a distinct stable execution identity. Deterministically split concatenated calls,
  retire their parent placeholder, preserve completed children and resume only pending children.
- Redis cancellation intent is independent of the liveness lease. Pub/Sub is a notification;
  owner polling must recover missed messages even when task TTL is disabled. Report stopped only
  after owner completion/cleanup, never after lease pruning or publication alone. Repeated stops
  must not cancel cleanup again; timeout remains retryable. Recover expired-lease tasks only from
  the requesting reply author's unfinished messages, preserving approval-pause rejection semantics.

## Image profiles and storage

- Named image profiles use request-local config snapshots and expose only ID/name to ordinary users.
  Missing/deleted IDs use the default profile. An absent paired edit profile inherits global editing;
  explicit `disabled` removes the edit tool and rejects execution. Preserve per-engine parameters
  and description suffixes without mutating cached tool schemas or leaking another profile's secrets.
- Persist compact image references; resolve authorized bytes only for vision-capable sends. Tool
  context images must not create duplicate display attachments. Retain references across approval,
  reload and follow-up turns; do not persist inline Base64 as image-context metadata.
- Preserve safe stage-specific ComfyUI errors and distinguish pre-submission failures from uncertain
  queued/executing work. Never log full workflows or return raw authenticated transport errors.
  Download credentials apply only to the configured image service's origin; redact secret fields
  from persisted image metadata.
- S3, GCS and Azure cache downloads write to unique short adjacent temporary files and atomically
  replace the final path after success. Failure removes only its temporary file and preserves any
  existing cache. Keep concurrent-reader and long multibyte-filename coverage.
- Tika requests require `Accept: application/json` so supported server versions return the metadata
  envelope expected by the loader. Preserve the version-specific endpoint and content-key handling.

## Terminal context

Terminal AGENTS.md is a synthetic user message. Enrich each user message from its own `files` in
`process_messages_with_output` before stripping those fields and injecting AGENTS.md. Do not pair
attachments by ordinal positions after output expansion or repeat the old async enrichment path.
Keep `backend/tests/test_terminal_agents_attachments.py` when changing either pipeline stage.

See [version decisions](../docs/upstream/v0.12.2-decisions.md) for rationale and upgrade boundaries.

## v0.12 data and collaboration boundaries

- `data.files` is the only mutable skill file tree. Compatibility conversion in `skill_compat.py`
  must be lossless, keep valid canonical data, preserve unknown metadata and reject divergent path
  collisions with skill ID/path. `SKILL.md` owns frontmatter; runtime projections are read adapters.
  Immutable old versions stay unchanged; restores create compatible new versions. Every new file
  edit requires `expected_version_id`; reject missing-version legacy attachment writes explicitly.
- `/skills/load/url` has optional `response_format`: default descriptor, `packages` parsed skills.
  Both paths share safe download and parse limits. Native `view_skill`/`read_skill_file`/`grep_skill_files` recheck ACLs
  and current turn selected/loaded/discovered IDs; pinned snapshots govern runtime file preparation.
- Merge revision `a120d13e0001` connects `f9a4b2d6c805` and `b8f2c6d91a04`; `a120d13e0002`
  migrates skills; `a120d13e0003` adds nullable shared-context provenance without guessing old safety.
- Authorize shared execution with the actor's model/tool/connection permissions. Approval and stop
  require reply authorship; `/compact` requires ownership. Status/raw reads use live chat read access
  and validated branch ancestry. Clone-only shares expose snapshots; imports/forks discard reusable
  context/accounting identities while preserving raw outputs and frozen citations.
- Shared V2 state has no participant key. Generate shared summaries from saved branch facts only,
  excluding personal system instructions, variables and terminal runtime context; those remain in
  the actor's counted request. Only server-marked `saved_history_v1` compressed records may replay
  in shared history. Old unproven prepared selections resolve to raw text without rewriting the row.
  Check source and sharing scope again during commit. Recheck live socket access after revocation.
- Project every user-visible chat response, export and mutation by message author; private request
  metadata belongs to its author even when the requester owns the chat. Filter clone/fork input before
  persistence while retaining raw server approval history. Never persist prepared skill root text;
  exact root pages share request-local prepared content. Terminal sync caches include the actual
  outgoing actor, connection and context identity, not just the shared chat ID.
- Voice transcripts retain public browser-source/author attribution across shared reads, events and
  copies, independently of private speech metadata. Insertions require Realtime/call permission and
  use the server-configured model and require chat ownership, including for admins. Standard/Realtime
  call entry points enforce the same ownership rule. Existing author-only metadata edits keep their
  author/privacy rules; ordinary dictation is unchanged. Generic message updates reject `voice`; the dedicated author-only
  endpoint cannot change a stored transcript's model identity.
- Exact builtin tool outputs are classified by registered type/identity, never by an external tool's
  name. `search_tools` retains complete schema entries; skill pages shrink at exact character offsets.
  Do not semantically summarize these results. If minimum valid content cannot fit, fail with an
  actionable budget error; count manifests, definitions and results in the final request.

## v0.12.2 additions

- Migration `c92da83b017f` adds Apps; `0bd252b206fb` fills only missing auth rows without changing
  existing credentials. `a122d15e0001` joins it with `a120d13e0003` and performs no business-data writes.
  Exercise both SQLite and actual PostgreSQL upgrades from fork and official databases.
- Persist `provider_function` before resolving builtin `call_tool`. Real target identity governs
  authorization, approval, original Gemini validation, execution and exact-result classification.
  Restore the wrapper for Chat/Responses replay and strip local metadata before transport.
- Tool-enabled skill bodies over 20000 characters expose 5000 initially; disabled injection caps at 100000. Overview, grep and pages share the pinned prepared body and absolute character offsets.
  Prepared text is request-local, never saved in chat metadata. Pins select snapshots after current
  context and ACL authorization; they are not grants. Preserve terminal fingerprints and soft failure.
- Trusted builtin `grep_skill_files` uses exact kind `skill_search`: retain whole entries/locations,
  absolute offsets and explicit omission counts. Never semantically summarize it. An unfit minimum
  returns an actionable error; count the complete result and citations before sending.
- App interactions apply current channel feature permission and group membership. Private interactions
  may use read access; any stored message update or public reply rechecks write access after the App call.

## Knowledge write tools

- Register `write_knowledge_file`, `replace_knowledge_file_content`, `import_knowledge_url` and
  `import_knowledge_terminal_file` through the existing builtin
  pipeline. Require explicit `knowledge_write=true`, knowledge reading, model builtin capability
  and both category permissions. Missing write opt-in denies execution.
- Bind write scope separately from read injection: union model, folder and current-chat collection
  attachments even when `file_context` is enabled. An attached collection restricts targets but does
  not grant access. Recheck collection write ACLs and refuse external collections at execution.
- Edits additionally require actual collection membership and file-level write access. Preserve
  owner-provenance checks in `has_access_to_file`; sharing a readable file into another collection
  must not grant permission to change its text.
- Use explicit users and database sessions with existing upload/process/associate and content-update
  pipelines. Confirm processed state and the persisted association before reporting create success.
  Only clean up a file created by this invocation that remains unassociated.
- Direct imports carry sources outside model context: never return full text, Base64 or raw file models.
  Web pages use the configured loader without `fetch_url` display truncation; documents preserve upload
  bytes. Reuse URL/SSRF/redirect policy, actual streamed size and allowed-type checks. Source retrieval
  must not hold a database transaction or forward WebUI credentials to arbitrary URLs.
- Terminal imports use only the current selected admin connection and model terminal capability.
  Recheck connection, actor, chat/context and KB access, fetch through the fixed terminal file API,
  and refuse redirects. Never read a supplied path from the backend host or accept a model-selected
  terminal identity. Preserve signed download and browser response-isolation rules.
- Import cancellation must settle already-started blocking persistence work before cleanup and retain
  the existing interrupted-call semantics. Distinguish retained partial files from cleaned failures;
  report saved/associated/indexed state accurately, including configured indexing bypass. Never retry
  a resolved import automatically. See the [direct import decision](../docs/upstream/v0.12.2-knowledge-direct-import-followup.md).
- Edit extracted text, retaining uploaded bytes and shared-file reindex behavior. Reject blank or
  oversized content, unsafe names, conflicting inputs and invalid ranges. Nonempty `replace_range`
  ranges require matching `expected`; use original character offsets for the entire batch.
- Distinguish saved text with incomplete indexing from a failed save. Include the file ID and
  actionable recovery guidance; do not blindly retry an append. Pre-existing equal text alone does
  not prove a save; configured retrieval bypass must report indexing as skipped. Approvals and transport retries use
  existing dispatch and saved results, never a second execution of a resolved write.
- Keep target discovery, write-access metadata and exact document reads usable with either
  `ENABLE_KB_EXEC` value. Apply collection scope before discovery count/offset/limit, preserving
  query and read-access filtering. See [verification](../docs/DEVELOPMENT.md#knowledge-write-verification)
  and the [follow-up decision](../docs/upstream/v0.12.2-knowledge-write-followup.md).

## Tool citation prefix contract

- Install fixed citation instructions before the initial native tool request. Attach dynamic sources
  to the result that produced them; never reset/rebuild the leading system message, latest user
  message or file RAG template during the native tool loop. Keep separate rounds' call/result groups.
- Persist optional `citation_context` (rendered text) and `citation_sources` (structured sources) on
  `function_call_output`. Render once and replay that text verbatim. Keep `output`, images, reasoning
  data and display files intact; do not regenerate older mappings from a cumulative source list.
- Normal and approved execution share citation attachment. Restore IDs from the current reply's
  saved `sources`, falling back to initial sources and persisted tool citation metadata; use the
  same first-seen source identity as the frontend. Earlier replies have their own numbering.
  Approval refreshes only the current reply's output projections, keeping reasoning with its calls
  and preserving prepared sidecars even when they share a V2 message ID. Capture their positions
  outside the messages and remove temporary markers before dispatch, including pipes. Never repeat
  resolved tools.
- Project citation text onto a copy for Chat Completions and both Responses replay paths. Strip local
  metadata before provider transport. Stateful Responses sends only the new batch's results;
  V2 retains its existing stateless replay rule so the full input can be measured and projected.
- Include rendered citations before input counting, fingerprints and compaction. Tool-budget source
  references cover raw text only. New budgeted citations contain ID/type/URL markers without duplicate
  snippet bodies; summaries/excerpts retain this suffix, and exact raw reads must not acquire it.
  Initial file/injected context keeps its existing placement.
- Legacy outputs without these fields retain their previous replay behavior. Preserve evidence
  kinds, escaping, safe URL filtering, source events and disabled-citation behavior. No migration or
  provider session-routing change is required. See the
  [follow-up decision](../docs/upstream/v0.11.4-tool-citation-cache-followup.md) and
  [verification](../docs/DEVELOPMENT.md#tool-citation-cache-verification).

## Context compaction

- Preserve off/legacy/V2 dispatch. `utils/context_compaction.py` uses upstream v0.12 round compaction;
  it must return unchanged for off/V2. v0.12.2 persists valid round checkpoint markers and uses
  single-round usage; malformed markers must not truncate raw history. V2 keeps final-send and retry dispatch independently.
  New settings are administrator-only; an explicit mode wins over the old enable flag. Resolve
  `meta.context_window` through base-model inheritance/global default and cap smaller Ollama num_ctx.
- V2 budget checks belong after model parameters and all prompt/tool/knowledge/skill/file injections,
  before transport conversion/send. Initial, approved-tool, tool-loop and code continuations share
  the hook. Only trusted saved-WebUI request state activates it; internal summary/background tasks
  use the ContextVar guard, never a client bypass flag. Pipes/stateless requests are outside scope.
- Browser-direct compaction uses request-local model descriptors. Manual summaries require the
  authenticated user's own live socket; stored-history usage queries have no dispatch side effect.
  Never resolve private server model parameters through a direct descriptor's ID or base-model pointer.
- Persist compaction lifecycle events in message status history, including the normalized
  `chat_message.status_history` read path, so reloads retain progress/results without altering input.
- Count final input, tools and multimodal allowances with an explicit source. Calibrate only matched
  single-call input usage, never cumulative billing totals. Ordinary chat reserves configured output
  only; internal V2 summaries use their separate percentage/absolute output calculation below.
- Append dynamic budget reminders after the existing messages/tool results, preserving their order
  and the stable prefix. Continuations replace the previous request-local reminder, never persist it
  in raw history. Responses conversion must keep late system messages in `input`, not overwrite
  leading `instructions`. Omit optional reminders when they exceed the input budget or would
  displace an assistant prefill (`continue_final_message`).
- Preserve latest real user input, runtime instructions, incomplete calls and call/result pairing.
  Compact complete groups, never truncate raw messages. Persist completed continuation outputs
  before checkpoint validation, and prevent replay from restoring already covered tool output.
- V2 checkpoints are separate from legacy summaries and validate source fingerprints plus version
  in one short transaction; no DB session may span model generation. Isolate model/branch state,
  reject stale writes, preserve original history and remove checkpoints with deleted chats.
- V2 tool budgeting uses `utils/tool_result_budget.py`, exact provenance in `tool_result_sources.py`,
  snapshots/candidates in `tool_result_store.py`/`models/chat_tool_results.py`, and adopted versions in
  `models/chat_tool_result_selections.py`. Only native/approved/injected/code execution paths register
  new pending outputs after raw persistence; missing caches never mean new work. Empty/image-only
  results still protect whole groups. Batch identity includes the source reply and actual call/result
  round; approval resumes reuse it. Do not alter raw calls, citations or checkpoint source fingerprints.
- Apply validated history checkpoints, then selected tool text. Legacy/imported outputs without a
  selection use raw text. Only pending results compete for tool budgets; selected originals/summaries
  remain fixed across task, policy and percentage changes. Whole-history V2 handles accumulated cost.
  Resolve C with inheritance/smaller num_ctx. Reserve capped incoming demand using batch allowance minus
  already selected actual text, and compact older complete history first. Retention uses the same
  projected costs; latest user, runtime instructions, incoming batch and incomplete groups stay protected.
  Fairly allocate, process the largest over-budget original first (call order breaks ties), and reclaim
  actual summary savings before touching another result. Full-payload fitting counts JSON, citations,
  repeated injection and media; search new excerpts by complete request cost, continuing across entries
  that cannot shrink. Never alter already selected history to satisfy a per-tool cap.
- Raw snapshot/candidate creation is not adoption. Freeze selections only after full validation and
  immediately before transport; a manual history action or approval wait does not prepare them. A short
  source-locked transaction revalidates every loaded source and prepared identity, including replay-only
  sends and sibling outputs, then validates canonical full input and remaining batch caps before commit. Conflict returns a retryable error, not a tool rerun or
  a locally losing summary. No transaction spans model generation. Original selections reference source
  identities without duplicate bodies. Candidate contexts stay immutable; prepared versions survive
  configuration changes, but not source/ancestor edits. Scope models/branches independently.
- Exact `read_tool_result` pages may shrink but never summarize. The auxiliary reader is server-gated;
  every read checks chat access, branch ancestry and unchanged sources. New cursors bind issuance anchor,
  source, query and position, allowing validated descendants; legacy cursors check eligible ancestors.
  Reject sibling/other-chat/source/query changes. Clean selection, raw and candidate tables on chat
  deletion, including SQLite with cascades disabled; model cleanup includes selections/checkpoints.
- Account for every result, but notify only new summaries/excerpts owned by the current reply. Silent
  original/exact/reused versions still appear in full batch accounting. End progress on failure/cancel
  with its original operation identity. Preserve source-reply notices and deduplicate both status stores.
  Persist reports atomically with adopted versions; verify effective selection/context IDs, source reply
  and fingerprints. Preserve winning operation metadata during overlapping commits. Replay must not
  refresh timestamps. Status is read-only and uses the same resolver as transport; legacy/partial reports
  must not claim completeness. Compatibility message metadata still survives later assistant/browser
  single/bulk writes. Report C, B and T separately: remaining_tokens is distance to T, and
  input_remaining_tokens is capacity to B. Off/legacy/disabled tool budgeting retains previous behavior.
- Internal history/tool summaries share an explicit purpose and trusted request-state budget guard;
  do not infer purpose from prompt text or enable guards from client task metadata. Resolve summary
  window `Cs`, effective window `Es`, `P=floor(Cs*summary_output_percent/100)` (default 5%, integer
  1–100), and `A` from explicit summary output then inherited model output then zero. Choose
  `R=max(P,A)`, and per chunk `O=min(R,floor((Es-F)/3))` where `F` includes fixed instructions/framing
  and previous handoff. Tool summaries additionally cap `O` at their allocation minus reference overhead.
  Send an explicit provider output cap and require full input <= `Es-O`, including after provider
  parameters are applied. Keep this non-recursive and detach mutable summary request state.
  Explicit context overflow rebuilds at the same source cursor with half the variable fragment budget,
  retains that smaller budget for later chunks, and uses existing timeout/retry limits. Advance the
  cursor only after success; reject fixed/media content that cannot fit. Never truncate raw history.
- On timeout/empty/no-reduction/oversized summaries, continue unchanged only when the request fits
  its input budget; otherwise return an actionable error. Retry explicit provider overflow once
  only before content is delivered; never re-execute tools. Cancellation preserves valid history.
- See [feature guide](../docs/context-compaction.md) and [isolated tests/migrations](../docs/DEVELOPMENT.md#context-compaction-verification).

## Verification

Run these from the repository root with the selected development environment:

```sh
python -m pytest backend/tests -q
```

The current tests use pytest; Search1API async tests also need `pytest-asyncio` and `aiohttp`.
`test_search1api_integration.py` checks source-level wiring; it does not call a live provider.
For runtime/API/database changes, add appropriate isolated integration or manual checks.

Use `python -m ruff format --check <changed-python-files>` and the root guide's CI-aligned Ruff
logic check. Ruff uses a 120-column limit and single quotes; follow `pyproject.toml`. Existing
pre-commit hooks and `npm run format:backend` modify files, so inspect their diffs if used.

Pytest configures the `backend` import path in `pyproject.toml`; no editable install is needed.
The active backend CI checks formatting, selected logic errors and the full pytest suite on Python
3.11/3.12 with locked dependencies. Unit/integration fixtures do not establish live provider or
deployment acceptance.

## Native provider transports

Dispatch explicit server connection `api_type` only: empty/missing or legacy `chat_completions`,
`responses`, `gemini`, `anthropic`. Reuse `OPENAI_API_*` storage; reject native+Azure. Preserve
custom compatible bases, bearer/none/session/oauth, cookies, custom headers and explicit UA/session
priority. Native `api_key` maps to `x-goog-api-key` or `x-api-key`; Anthropic supplies
`anthropic-version: 2023-06-01` unless explicitly configured. Paginate native model discovery and
retain manual IDs. Unsupported endpoints fail explicitly rather than switching protocols.

Connection UA/session controls default to existing behavior. Explicit controls override matching
custom headers case-insensitively; validate header names/values and prevent session headers from
replacing authentication or standard transport headers. Saved chats use the chat ID; temporary/local
chats use `connection_session_id` from the root message, never the reusable socket ID. Verification
and requests without conversation identity do not invent a session header.

Native text/image/tool streams and nonstreams share permissions, approval, citation and usage flows.
Keep exact native blocks/parts and signatures in optional `reasoning_details`, bound to protocol,
connection, model and original content. Validate replay; fail required missing/edited signatures and
never re-execute completed tools. Existing model controls/custom parameters configure thinking;
never infer a model generation. Apply Anthropic's absent-only `max_tokens=4096` before budgeting;
explicit output limits win. Count native replay/framing before V2 selection admission, retaining
off/legacy behavior and admin-configured context windows. Gemini defaults to `parametersJsonSchema`;
manual restricted-schema projection remains separate and explicit. See the
[guide](../docs/native-model-connections.md), [follow-up](../docs/upstream/v0.12.2-native-connections-followup.md)
and [checks](../docs/DEVELOPMENT.md#native-model-connection-verification).

## Gemini schema compatibility

`meta.tool_schema_compatibility` is an explicit `default`/`gemini` choice with base-model inheritance;
browser-direct descriptors never resolve a colliding server model. Compile copies before final
budgeting using `utils/gemini_schema.py`; never infer the profile from model/URL/provider names.
Resolve only bounded local references and preserve original JSON types and constraints for execution
validation. Unsupported or over-budget schemas fail with tool/path diagnostics, without schema values
or a truncated definition. Validate the original schema before approval, callbacks or execution;
freeze each generating round's mode across model switches and approval resumes, and strip internal
markers before transport. Model arguments cannot override `__` runtime parameters. See the
[compatibility guide](../docs/gemini-tool-compatibility.md) and its regression commands.
