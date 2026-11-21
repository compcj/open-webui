# Agent collaboration guide

This is the shared, tool-neutral instruction source for this repository. Read it before editing.
It applies to the whole repository. Also read [src/AGENTS.md](src/AGENTS.md) for frontend work and
[backend/AGENTS.md](backend/AGENTS.md) for backend work; their rules specialize this guide within
those directories. Load applicable files explicitly if your tool does not discover them automatically.
User instructions and the host tool's safety requirements take precedence over repository guidance.

## Start here

1. Read `git status --short`, `git branch --show-current`, and the relevant diff. Preserve existing
   user and agent changes; a dirty working tree is not permission to reset or clean it.
2. Inspect the implementation, nearby tests, and applicable instructions before proposing changes.
   Use `rg` and targeted file reads; avoid scanning dependencies, caches, and bundled assets.
3. Define the intended outcome, affected paths, and suitable checks. For multi-step work, keep a
   short plan and update it when scope changes. Do not impose a separate approval round for routine,
   reversible work already requested by the user.
4. Check runtime versions before installing or running the application. See
   [development commands](docs/DEVELOPMENT.md) and [collaboration workflow](docs/agents/README.md).

## Repository map

This is an Open WebUI fork: a SvelteKit frontend and a Python FastAPI backend. Read versions from
`package.json` and `pyproject.toml`; historical merge plans describe past work, not the current baseline.

| Area                                       | Main locations                                                                                      |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| Pages and layouts                          | `src/routes/`                                                                                       |
| UI, API clients, shared state              | `src/lib/components/`, `src/lib/apis/`, `src/lib/stores/`                                           |
| Frontend helpers and translations          | `src/lib/utils/`, `src/lib/i18n/`                                                                   |
| Backend application and endpoints          | `backend/open_webui/main.py`, `backend/open_webui/routers/`                                         |
| Persistence and schema changes             | `backend/open_webui/models/`, `backend/open_webui/internal/db.py`, `backend/open_webui/migrations/` |
| Retrieval, providers, shared backend logic | `backend/open_webui/retrieval/`, `backend/open_webui/utils/`                                        |
| Skill formats and terminal runtime         | `backend/open_webui/utils/skills_runtime.py`, `src/lib/utils/skills.ts`                             |
| Tests                                      | Colocated `src/**/*.test.ts`, `backend/tests/`                                                      |
| Build and automation                       | `package.json`, `pyproject.toml`, `vite.config.ts`, `Dockerfile`, `.github/workflows/`              |
| Assets                                     | `static/`; read local `BRANDING.md` before editing branding assets                                  |

## Upstream integration decisions

Before merging another upstream release, read [the merge index](docs/upstream/README.md) and the
[active v0.13.4 decisions](docs/upstream/v0.13.4-decisions.md). Verify official commit IDs and actual
ancestry rather than relying on local version tags or historical merge plans. Re-check each retained
behavior against its tests; record new or superseding decisions in a new version record.

For a full fork audit, use the [v0.13.4 difference inventory](docs/upstream/v0.13.4-fork-audit.md)
as the last dated snapshot, then recompute both the complete upstream-to-HEAD tree diff and all
reachable fork-only commits, including merged side branches and the dirty working-tree diff.
Record exact SHAs and shallow-history limits; verify missing history through read-only remote commit
and compare APIs when available. Classify current additions, behavior/policy differences,
important fixes, and upstream convergence separately; a historical fork commit alone does not prove
a feature is still unique. README carries the complete user-facing inventory, this guide and scoped
guides carry current invariants, the audit/decision records carry evidence, and DEVELOPMENT carries checks.

Current shared contracts: automatic skill discovery is separately authorized from skill access;
workspace discovery uses an admin allowlist (empty by default, including for admins), and terminal
discovery uses only a server-stored admin connection opt-in (false by default). Manual skills and
model presets keep their semantics. Terminal AGENTS.md loading is independent and fails softly.
Preserve terminal file isolation/no same-origin port previews and fail closed with retryable errors
when configured Redis revocation checks are unavailable. Keep user-facing descriptions in README,
current rules in scoped guides, rationale in the decision record, and commands in DEVELOPMENT.md.

## v0.12.2 integration boundaries

- Standard and Realtime calls require chat ownership, even for non-owner admins. Recheck ownership
  at backend call/transcript entry points and close calls when switching to another owner's chat.
  Historical voice metadata remains author-private; ordinary dictation retains its existing permissions.
- Tool dispatch saves the original `provider_function` for provider replay. Resolve the registered
  target before permission, original Gemini schema validation, approval, execution and result classification.
  Retry never repeats execution; failed dispatch results must reach stateful Responses continuations too.
- Tool-enabled skill content/preview limits are configurable, defaulting to 20000/5000 characters; tool-disabled
  injection remains bounded at 100000. Overview/search/pages use the same pinned prepared text and
  absolute character offsets. Search reduction keeps complete entries with explicit omissions.
  Only a server-registered builtin identity receives exact-result processing; snapshot pins do not grant access.
- Apps default off. Private interactions allow read-only channel members; public replies and message
  updates require live write permission after the App responds. Apply channel feature and group-member checks.
- The migration head is `a133d11e0001`, joining `a122d15e0001` and `9f6c2a8d1b43`; do not rewrite old revisions.

## Reply lifecycle and concurrent models

Each parallel model reply owns its nested input, metadata and mutable request state; V2 branch
identity, tool selections, retries and caches must not leak between replies. Preserve consumed
request bytes for plugins while keeping parsed JSON and direct-model objects independent.
Treat the SSE `[DONE]` event as completion independently of HTTP EOF. A transport interruption must
persist buffered partial output and prior tool results, mark unfinished items incomplete and avoid
executing partial tool calls or code. Clear a recovered stream error only through an explicit recovery signal.

Stopping a backend task requires completion confirmation from the executing worker. Redis Pub/Sub is a
notification, not proof of cancellation; persisted stop intent survives missed delivery and lease pruning.
Repeated stops must not interrupt cleanup. On timeout, preserve retryable state and do not advance
queued requests. Navigation or a newer reply must not be mutated by an earlier stop response.
Keep reply-author permissions for approval and stop. See [chat access and stopping](docs/chat-access-controls.md),
[backend rules](backend/AGENTS.md), and [regression checks](docs/DEVELOPMENT.md#chat-lifecycle-and-request-isolation-verification).

## Tool citation prefix contract

Native tool continuations append new results and their citation context without rebuilding earlier
system/user messages or file RAG context. Install fixed citation rules before the first request;
preserve frozen citation text, raw tool output and existing call/result boundaries during replay.
Citation IDs follow first appearance within the current assistant reply, including approval resumes.
See [backend rules](backend/AGENTS.md#tool-citation-prefix-contract),
[verification](docs/DEVELOPMENT.md#tool-citation-cache-verification), and the
[follow-up decision](docs/upstream/v0.11.4-tool-citation-cache-followup.md).

## Tool continuation prompts and Gemini signatures

Before changing provider dispatch or replay, preserve the [tool continuation and Gemini signature
contract](backend/AGENTS.md#tool-continuation-prompts-and-gemini-signatures). Prepare a server model's
system prompt once per selected model in the reply request. Request filters run each round on detached,
unfiltered input; their output receives the frozen model prompt at dispatch. Intentional filter rewrites remain
supported. Preserve content-block cache annotations and final-stop tool definitions. Keep Gemini Chat
Completions `extra_content.google.thought_signature` opaque and
separate from `reasoning_details`, with original message/call and batch boundaries. Preserve signed
text and earlier stream output without repeating tools. If stream indices are missing or reused,
pair fragments by provider call ID within the round. Ambiguous fragments leave the affected calls
incomplete. See the [replay guide](docs/gemini-cache-replay.md)
and [regression checks](docs/DEVELOPMENT.md#gemini-cache-replay-verification).
OpenAI-compatible model metadata defaults `reasoning_content_replay` on and
`extra_body_compatibility` off. Replay only actual upstream reasoning content; keep `reasoning_details`
independent. Enabled extra-body expansion precedes final budgeting, with explicit top-level precedence
and no nested metadata authority. See [input-cache checks](docs/DEVELOPMENT.md#input-cache-compatibility-verification).

Retain critical fork difference **D04a**: server Chat Completions projects only automatic context
pairs identified by `request.state.private_tool_context` into system context before model role
conversion and final budgeting. Preserve single-leading-system normalization and the frozen internal
pairs for approval and legacy summary exclusion. Never fabricate reasoning or classify by tool name,
client metadata, URL or model name; real calls, Responses and native protocols keep their replay rules.
Recheck this boundary when merging upstream hidden-context changes. See the
[technical decision and upstream evidence](docs/upstream/v0.13.3-private-context-followup.md) and
[backend contract](backend/AGENTS.md#input-cache-compatibility).

## Context compaction contracts

- Compaction is globally `off`, `legacy`, or `v2`; an absent mode maps the old enable flag to
  off/legacy. Never auto-enable V2 on upgrade. Legacy includes upstream v0.12.4 temporary/short-history behavior and tool-round checkpoints, retaining threshold overrides. Never run legacy and V2 on the same request.
- Shared chats reuse V2 state by chat/model/validated branch, never by participant. Only saved shared branch history enters shared summaries; personal instructions, variables and terminal context remain request-local and counted. Require server `source_scope=saved_history_v1` before reusing compressed records in shared history; unproven older records replay raw without rewriting the stored selection. Manual `/compact` is owner-only; approval and stop operations belong to the reply author. Live updates and raw/status reads recheck current chat access.
- Keep original chat messages intact. Legacy `contextSummary` and V2 `chat_context_checkpoint`
  storage are independent; off replays full raw history and mode changes apply next request.
- V2 lives in `backend/open_webui/utils/context_compaction_v2.py`, validated settings in
  `context_compaction_config.py`, final-send/retry hooks in `context_compaction_dispatch.py`, and
  persistence through explicit SQL/temporary store adapters; saved checkpoints use
  `backend/open_webui/models/chat_context_checkpoints.py`. Isolate checkpoints by
  model and validated branch ancestry/content; stale or competing writes must never win.
- V2 tool budgets apply only to server-registered new results in trusted saved or scoped temporary chats. Apply valid
  history checkpoints and fixed tool selections first; historical originals/summaries are fixed input,
  not competitors for a new batch's allowance. Reserve capped new demand, compact older history first,
  then allocate fairly and reclaim actual savings after each reduction. Count the complete final request,
  including media, references, repeated injection and JSON framing; only unselected new results can shrink.
  Include Responses `max_output_tokens` in the output reserve using the transport's field precedence.
- `backend/open_webui/models/chat_tool_result_selections.py` owns saved pending/prepared versions;
  the temporary adapter preserves the same states atomically in its cache. Prepare
  atomically after full budget validation and before transport, revalidating sources and concurrent
  winners (including batch caps) before commit. Candidates, approval pauses and manual history compaction
  do not adopt versions. Preparation means selected for send, not confirmed delivery. Preserve raw data,
  source identities and pairing, including empty/image-only outputs. Never re-execute tools on retry.
- Replay and status share the read-only selection resolver, isolated by model, valid ancestry and source.
  Upgrade/import without a selection uses raw content, not a guessed cache. Later task/policy changes do
  not rewrite selected history; source edits invalidate it. Off/legacy/disabled behavior is unchanged.
- Keep accounting separate from notices. Only current-reply new summaries/excerpts and necessary errors
  notify; originals, exact reads and reuse remain silent. End failed/cancelled operations using the same
  operation identity. Persist complete batch accounting with selection IDs, source reply, decision time,
  then-effective limits and reduction reasons; never retime replay or label partial legacy data complete.
  Distinguish window C, input budget B and trigger T: remaining_tokens is T-based; input_remaining_tokens
  is B-based. Exact raw cursors allow source-validated descendant branches, never siblings/other chats.
- Internal V2 summaries keep recursion protection and an independent final-send budget guard. Resolve
  the summary model's own window, reserve bounded output, and shrink source chunks on explicit overflow;
  ordinary chat budgets remain independent. Never discard raw history or rerun tools on summary retries.
- Trace model `meta.context_window`, admin settings, provider sends, continuation flows and usage
  events together. See [feature guide](docs/context-compaction.md), scoped frontend/backend rules,
  and [verification commands](docs/DEVELOPMENT.md#context-compaction-verification).

## Knowledge write tools

Knowledge writes require an explicit `features.knowledge_write=true` opt-in, default off, together
with knowledge reading and model builtin-tool permission. Reading or attaching a resource never
grants write access. Preserve the user's write preference when reading is temporarily disabled.
Limit writes to attached model/folder/chat collections when present, independently of `file_context`;
otherwise require current write access to the chosen collection. Recheck collection access at execution,
and membership plus file write access for edits. External collections are unsupported.

Create UTF-8 Markdown/text files in existing collection roots; edit extracted retrieval text while
preserving original uploads and existing shared-file index propagation. Report persisted text with
failed indexing as partial completion, and never repeat a resolved tool during approval/retry replay.
URL and terminal imports keep source bodies outside model/tool messages and reuse file processing.
Terminal import binds the current actor and selected admin connection/context; never open a supplied
terminal path on the WebUI host or accept a model-supplied connection/credential. Preserve URL network
policy, size/type limits, live permissions, original document bytes and compact verified results.
See [backend rules](backend/AGENTS.md#knowledge-write-tools),
[frontend rules](src/AGENTS.md#knowledge-write-controls),
[user guide](docs/knowledge-write-tools.md), and
[verification](docs/DEVELOPMENT.md#knowledge-write-verification).

## Implementation boundaries

- Match nearby patterns and the checked-in formatter configuration. Keep diffs focused; do not
  reformat unrelated files or introduce dependencies for a small change without a concrete need.
- Preserve fork behavior when refactoring or merging upstream. In particular, check Search1API
  endpoint selection, Tavily search depth, and model reasoning-effort behavior. The
  [frontend](src/AGENTS.md#integration-contracts) and [backend](backend/AGENTS.md#fork-contracts)
  guides define their contracts; [Search1API verification](docs/DEVELOPMENT.md#search1api-endpoint-verification)
  and the development guide's regression map identify their files/tests.
- Skills use upstream `data.files` as the sole editable file tree for native and OpenClaw packages.
  Legacy `meta.openclaw.files`/frontmatter are compatibility input/projections; preserve unknown
  frontmatter, source/config/env and original bytes without keeping a second mutable copy. Canonical
  collisions fail with skill ID/path; migration is transactional and historical versions stay immutable.
  Use `expected_version_id` for edits; missing legacy attachment versions require a client refresh.
  URL imports keep GitHub, skills.sh, ClawHub, direct links and multi-skill selection. Admin or
  `workspace.skills_import` authorization still uses upstream network/redirect/archive/size checks.
  `/skills/load/url` defaults to the legacy descriptor; new clients request `response_format=packages`.
  Runtime gating, terminal sync, `{baseDir}` and explicit dependency installation use pinned canonical
  files. Discovery manifests never sync unused skills; terminal failures degrade gracefully.
- Ordinary model reasoning uses upstream model controls. Existing new configuration wins; old
  presets/custom strings, saved chat choices and user memory migrate to valid option IDs. Precedence:
  chat explicit control, remembered control, chat advanced, personal advanced, control/model default,
  global default. Reset clears explicit choices without reviving legacy memory. Direct/pipe fallback
  keeps its existing input. Preserve ordered incremental saves and account isolation.
- Trace frontend/backend contracts together: request fields, defaults, permissions, streaming events,
  persistent settings, and error responses. Test the affected boundary, not only one side.
- When a change adds, renames, moves or removes UI translation keys, regenerate all configured
  catalogs and include their generated changes with the source. Review existing translations and
  interpolation variables. Verify the proposed commit with the same format, extraction and clean-tree
  checks as [Frontend Build](docs/DEVELOPMENT.md#frontend-format-and-translation-verification).
- Keep `package.json` and `package-lock.json` consistent. For Python dependency changes, inspect
  `pyproject.toml`, `uv.lock`, and `backend/requirements.txt`; also check `backend/requirements-slim.txt` for shared pins and intentional exclusions. Use the relevant package manager to update locks rather than editing resolution data.
- Do not hand-edit build output, `.svelte-kit/`, dependency directories, or downloaded Pyodide files.
  `static/pyodide/pyodide-lock.json` is tracked: review any change produced by asset preparation.
- Keep secrets and user data out of code, prompts, logs, tests, and handoffs. Use synthetic fixtures.
  Do not commit `.env`, API keys, `.webui_secret_key`, databases, uploads, or model caches.
- Backend imports can run database migrations and copy static assets. Use an isolated development
  database/data directory for application checks; do not import the app merely to inspect a helper.
- Follow `LICENSE`, `LICENSE_NOTICE`, and asset `BRANDING.md` files. Preserve notices and branding
  requirements. Use [docs/SECURITY.md](docs/SECURITY.md) for security reporting.

## Verification

Run commands from the repository root unless stated otherwise. Node 22 matches CI; Python 3.11
and 3.12 are supported. Install prerequisites using [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

| Change                          | Verification starting point                                                                                                      |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Documentation/instruction files | Check relative links and command/path accuracy; `npx --no-install prettier --check <changed-markdown-files>`; `git diff --check` |
| Frontend behavior               | `npm run test:frontend -- --run`; `npm run check`; manually exercise changed UI                                                  |
| Frontend build/assets           | `npm run build` (prepares/downloads Pyodide assets first)                                                                        |
| Backend helpers                 | `python -m pytest backend/tests -q`, or the specific affected tests                                                              |
| Python formatting               | `python -m ruff format --check <changed-python-files>`                                                                           |
| Python CI logic checks          | `python -m ruff check --select=F --ignore=F401,F403,F405,F541,F811,F841 <changed-python-files>`                                  |

Angle-bracket arguments above are placeholders: replace them with explicit paths. Use installed tools;
do not silently download a formatter or change dependencies just to run a check.

`npm run lint:frontend` uses `--fix`; `npm run format`, `npm run format:backend`, and
`npm run i18n:parse` also write files. Use targeted non-mutating checks first. The aggregate
`npm run lint` uses shell-dependent separators; execute its relevant parts separately.

Report the commands actually run, their outcomes, and any checks skipped or blocked. A test command
that discovers no tests is not verification (`test:frontend` includes `--passWithNoTests`). Separate
pre-existing failures and environment limitations from regressions. Never claim a build or runtime
check passed based only on formatting or isolated unit tests.

## Shared work and delivery

- For parallel work, agree on an owner and allowed paths for each task before editing. Give each
  agent the base revision, contract, and acceptance checks. Independent worktrees help isolate edits;
  shared files, lockfiles, migrations, and API contracts still need coordination.
- In a shared checkout, do not switch branches, reset, stash, delete, or overwrite another worker's
  changes. Re-read the current file before patching; one worker owns a shared file at a time.
- Keep temporary notes in ignored `.agent-work/<task-id>/`. For cross-tool/machine handoff, share a
  sanitized summary or deliberately track a durable document; ignored files do not travel with Git.
  Use the [handoff template](docs/agents/HANDOFF.template.md) when it helps continuity.
- Review the final diff and Git status. Summarize the result, changed paths, validation evidence,
  remaining limitations, and next steps. The integrating agent verifies the combined result.
- Local work does not imply permission to publish, deploy, push, or make a release. Follow the
  user's authorized scope and [CONTRIBUTING.md](CONTRIBUTING.md); inspect the target repository and
  existing PR policy before opening a PR.
- Keep enduring rules here or in the scoped `AGENTS.md` files. Tool adapters should only load or
  point to these sources; do not duplicate the rules or require a particular agent plugin.

## Native model connections

Server Connections select an explicit protocol: default Chat Completions, Responses, Gemini or
Anthropic. Preserve existing `OPENAI_API_*` storage and legacy `chat_completions`; never infer
protocol from a URL/model or add a migration. Native protocols are admin server connections only,
exclude Azure, and preserve custom gateways, header precedence and request identity controls.
Preserve signed native reasoning replay bound to protocol/connection/model and original content;
required missing/edited signatures fail explicitly without rerunning tools. Count native framing,
replay and output defaults before final budget validation and tool-result adoption. Keep citations,
approvals and off/legacy/V2 behavior aligned across transports. See the
[user guide](docs/native-model-connections.md), [decision record](docs/upstream/v0.12.2-native-connections-followup.md)
and [verification](docs/DEVELOPMENT.md#native-model-connection-verification).

## v0.12.4 integration and temporary contexts

- `direct.integrations.enable=false` disables saved personal tool/terminal connections for all
  users, including admins, while retaining configuration. Model Direct Connections, Direct API
  Chat and admin server connections remain independent.
- Controls/Params permissions govern panel visibility. Preserve legal option validation, model
  access, migration, memory, reset and precedence; do not filter valid values by panel permission.
- Skills load eagerly by default, including ordinary UI model presets; `skills.lazy_loading.enable`
  defaults false and mentions remain eager. Admin content/preview limits default to 20000/5000;
  the tool-disabled cap remains 100000. Discovery never grants access.
- Shared participants may create branches from others' messages. Preserve each viewer's branch;
  reply authors own approvals/stops and chat owners own calls and shared manual compaction.
- Access updates use dedicated endpoints and take effect immediately; reject unauthorized grant
  changes with 403 without altering grants. Content updates cannot mutate access; creation filters grants.
- Temporary V2 binds a server-owned `ContextScope` and explicit `ContextStore` to each request.
  Never select storage with a request-global switch. Saved SQL and temporary cache share source,
  selection and budget rules. No temporary history/checkpoint/result/selection SQL writes.
- The page retains id/epoch/revision only in memory. Actor, branch, task, model and original fence
  are authoritative; socket identity only routes delivery. Client history never grants execution
  provenance. Temporary approvals cancel on disconnect; direct model RPCs never auto-replay.
- Persist started before effects. Resolved-but-unpublished results remain blocking until canonical
  raw publication commits atomically; task completion alone cannot discard them. Recover stored
  originals without executing again. Quota failures do not truncate raw data or evict active work.
- Closing fences new effects and waits for executing workers. Never infer completion from Redis
  Pub/Sub, TTL, missing keys or an expired lease. Redis failures remain retryable 503 without memory
  fallback; memory mode requires one worker/instance. Promotion reserves an idle scope, saves raw
  history idempotently and releases only after success. Export strips derived context identities.
- Encrypted channels default off globally and for ordinary users. The historical v0.12.4 head
  `a122d15e0001` remains unchanged beneath the v0.13.3 merge head. Use coordinated drained-worker upgrades, not unverified
  mixed-reader rolling updates. See [temporary contexts](docs/temporary-context-v2.md) and
  [version decisions](docs/upstream/v0.12.4-decisions.md).

Temporary control records may retire only after executor-confirmed completion and full raw-result
publication. Preserve a server-only, byte-charged task-ID retirement ledger against reuse, and keep
all raw history and selected versions. Control count caps must not become a fixed conversation
turn limit; unsettled work and active-task retry protection remain intact.

## v0.13.4 integration contracts

- Use `0.13.4+f1` in application, npm, Python and OCI version metadata and `v0.13.4+f1` for GitHub.
  GHCR uses `0.13.4-f1` and the series tag `0.13.4`, each with its existing variants.
- Personal Direct Connections, Direct Integrations, Direct API Chat and Tether have independent controls.
  Resolve each actor's device models and terminal grants at use time. A chat's access grants confer no terminal grants.
- Native Agent forks require a verified checkpoint, actor, original saved chat, terminal instance and working
  directory. Copies, exports and side chats project raw visible content. Provider-native Auto has its own
  switch, defaulting to WebUI review. Rebind unproven model IDs explicitly.
- Bind steering to the chat owner's own running reply and branch. Resume its queue only after durable raw
  publication and worker-confirmed exit. Retain unsettled approvals and existing temporary-context fences.
- See [current release decisions](docs/upstream/v0.13.4-decisions.md) and
  [verification](docs/upstream/v0.13.4-validation.md) for migration, image ACLs and upgrade checks.

## v0.13.3 integration contracts

- Use upstream attachment/retrieval defaults (preloading off, Top K and reranker K 10,
  chunk 1024, full-text threshold 50000), 1024 tool rounds and unlimited default subagent limits.
  Explicit saved settings win; do not rebuild existing knowledge indices during upgrade.
- Model mentions switch subsequent chat turns; regeneration uses the current slot model. Preserve
  remembered controls, reset, precedence and account isolation. Use upstream tabs/split views and
  Controls/+ menu, with Show Input Selections off by default and all fork controls retained.
- Keep `meta.intelligence_score` optional and display-only (integer 0–99). Builtin `ask_user` accepts
  1–3 questions with 2–4 presets or a text field. Preserve write-in answers, including whitespace and
  leading zeros, through approval and saved-chat resume.
- Terminal command completion refreshes existing directory views in place. File display events can
  open a background file tab. Only the admin's desktop selection setting opens Files automatically
  on terminal selection. Main-chat `browser_open_tab` returns the session it actually opened. See
  [terminal file and browser behavior](docs/TERMINAL_FILES.md).
- Each Chat instance owns models, task IDs and temporary scope. Layout persistence contains no
  temporary history, scope identity or preview capability. Side chats use raw history projections,
  stripping execution/checkpoint/selection/Agent resume identities before creating a new chat.
- Automatic private/memory/skill-runtime/terminal hidden context stays request-local and frozen
  across a reply's continuations. Count it in final budgets, never persist it in output/shared
  summaries or treat it as a newly executed tool result. Approval resumes require the server's
  ephemeral snapshot; expiry/source changes fail explicitly without executing tools again.
- Batch tool calls retain stable child identities, original provider functions and batch boundaries.
  Resolve real targets before permission, original-schema validation, approval and execution. Keep
  failures in native continuations; retry is replay, never duplicate execution.
- `ENABLE_TETHER` / `tether.enable` is an independent default-off admin switch. Old-client omitted
  fields preserve saved values. Disable handshake, discovery and new HTTP/WS/automation operations
  and close channels when disabled. Manual personal connections still require Direct Integrations;
  Tether never inherits admin skill-discovery opt-in or knowledge terminal-import authorization.
- Native Agents use saved chats, a single Agent and their own context (no WebUI V2). Preserve offline
  model identity. Bind sessions to author/chat/terminal/working directory; copies/shares never inherit
  resumable identity. Persist start before effects and stop intent before cancellation. Only confirmed
  remote termination plus complete raw publication releases the durable execution record. Timeout,
  404, lost response, coroutine exit, expired lease or missing registry entry is not confirmation.
  Persist exact sequenced Agent output before ACK; keep its private journal out of public projections.
  Capacity refusal keeps the current frame unacknowledged and stop pending. Resume signatures also
  bind the actual terminal URL. Never equate a missing worker with complete output publication.
- Independent preview uses `TERMINAL_PREVIEW_BASE_URL` / `terminal.preview.base_url` (empty disables),
  a distinct hostname and HTTPS except explicit loopback development. The preview Host only serves
  preview HTTP/WS; main-site APIs/auth/Socket.IO and same-origin port/HTML proxy remain inaccessible.
  Tickets are opaque, server-held, user/login/terminal/target/context bound, five minutes long and
  reauthorized every minute. Redis failure is retryable without memory fallback; memory requires one
  worker/instance. Recheck live access during streams. Never persist capabilities or credentials in
  layouts, chat output or logs. Keep sandbox without allow-same-origin/top navigation/storage, exact
  postMessage origin/source/ticket checks, no-referrer/no-store and main-site credential rejection.
  Retired preview hosts remain reserved across configuration resets. Reuse verified port sessions;
  durable create claims prevent duplicates after ambiguous responses. Recheck actor-visible `/ports`.
  The terminal may retain its internal target Cookie jar while DOM storage stays inaccessible.
  Port WebSocket opening targets are checked, but terminal-internal redirects use upstream browser
  network permissions; do not claim end-to-end port confinement.
- Consult [Tether and Agents](docs/tether-agents.md), [preview deployment](docs/terminal-preview.md),
  [current decisions](docs/upstream/v0.13.3-decisions.md) and [verification](docs/DEVELOPMENT.md#v0133-integration-verification).
