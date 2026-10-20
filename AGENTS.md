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
[active v0.12.2 decisions](docs/upstream/v0.12.2-decisions.md). Verify official commit IDs and actual
ancestry rather than relying on local version tags or historical merge plans. Re-check each retained
behavior against its tests; record new or superseding decisions in a new version record.

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
- Tool-enabled skills longer than 20000 characters initially expose 5000 characters; tool-disabled
  injection remains bounded at 100000. Overview/search/pages use the same pinned prepared text and
  absolute character offsets. Search reduction keeps complete entries with explicit omissions.
  Only a server-registered builtin identity receives exact-result processing; snapshot pins do not grant access.
- Apps default off. Private interactions allow read-only channel members; public replies and message
  updates require live write permission after the App responds. Apply channel feature and group-member checks.
- The migration head is `a122d15e0001`, joining `a120d13e0003` and `0bd252b206fb`; do not rewrite old revisions.

## Tool citation prefix contract

Native tool continuations append new results and their citation context without rebuilding earlier
system/user messages or file RAG context. Install fixed citation rules before the first request;
preserve frozen citation text, raw tool output and existing call/result boundaries during replay.
Citation IDs follow first appearance within the current assistant reply, including approval resumes.
See [backend rules](backend/AGENTS.md#tool-citation-prefix-contract),
[verification](docs/DEVELOPMENT.md#tool-citation-cache-verification), and the
[follow-up decision](docs/upstream/v0.11.4-tool-citation-cache-followup.md).

## Context compaction contracts

- Compaction is globally `off`, `legacy`, or `v2`; an absent mode maps the old enable flag to
  off/legacy. Never auto-enable V2 on upgrade. Legacy uses upstream v0.12.2 tool-round checkpoints and per-round usage, retaining threshold overrides. Never run legacy and V2 on the same request.
- Shared chats reuse V2 state by chat/model/validated branch, never by participant. Only saved shared branch history enters shared summaries; personal instructions, variables and terminal context remain request-local and counted. Require server `source_scope=saved_history_v1` before reusing compressed records in shared history; unproven older records replay raw without rewriting the stored selection. Manual `/compact` is owner-only; approval and stop operations belong to the reply author. Live updates and raw/status reads recheck current chat access.
- Keep original chat messages intact. Legacy `contextSummary` and V2 `chat_context_checkpoint`
  storage are independent; off replays full raw history and mode changes apply next request.
- V2 lives in `backend/open_webui/utils/context_compaction_v2.py`, validated settings in
  `context_compaction_config.py`, final-send/retry hooks in `context_compaction_dispatch.py`, and
  persistence in `backend/open_webui/models/chat_context_checkpoints.py`. Isolate checkpoints by
  model and validated branch ancestry/content; stale or competing writes must never win.
- V2 tool budgets apply only to server-registered new results in trusted saved chats. Apply valid
  history checkpoints and fixed tool selections first; historical originals/summaries are fixed input,
  not competitors for a new batch's allowance. Reserve capped new demand, compact older history first,
  then allocate fairly and reclaim actual savings after each reduction. Count the complete final request,
  including media, references, repeated injection and JSON framing; only unselected new results can shrink.
- `backend/open_webui/models/chat_tool_result_selections.py` owns pending/prepared versions. Prepare
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
