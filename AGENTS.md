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
[active v0.11.4 decisions](docs/upstream/v0.11.4-decisions.md). Verify official commit IDs and actual
ancestry rather than relying on local version tags or historical merge plans. Re-check each retained
behavior against its tests; record new or superseding decisions in a new version record.

Current shared contracts: automatic skill discovery is separately authorized from skill access;
workspace discovery uses an admin allowlist (empty by default, including for admins), and terminal
discovery uses only a server-stored admin connection opt-in (false by default). Manual skills and
model presets keep their semantics. Terminal AGENTS.md loading is independent and fails softly.
Preserve terminal file isolation/no same-origin port previews and fail closed with retryable errors
when configured Redis revocation checks are unavailable. Keep user-facing descriptions in README,
current rules in scoped guides, rationale in the decision record, and commands in DEVELOPMENT.md.

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
  off/legacy. Never auto-enable V2 on upgrade. Preserve the legacy algorithm and override rules.
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

## Implementation boundaries

- Match nearby patterns and the checked-in formatter configuration. Keep diffs focused; do not
  reformat unrelated files or introduce dependencies for a small change without a concrete need.
- Preserve fork behavior when refactoring or merging upstream. In particular, check Search1API,
  Tavily search depth, and model reasoning-effort behavior. The scoped guides define their contracts;
  [the development guide](docs/DEVELOPMENT.md) maps their files/tests.
- Workspace skills support two formats: native OpenWebUI skills and OpenClaw-compatible `SKILL.md`
  skills. `SkillMeta.openclaw` is a permissive JSON bag (`frontmatter`, `files`, `source`, `config`,
  `env`) — keep it schema-tolerant and preserve its round-trip through import, edit, and
  "Export SKILL.md". Import paths (file/zip plus `POST /skills/load/url` for direct links, GitHub
  repo/tree URLs, skills.sh links, and ClawHub refs) prefill the create editor. GitHub tree URLs and
  bare repo URLs download the repo zipball (codeload, no token); skills.sh links map to their
  backing GitHub repo. When an archive contains multiple skills and the URL carries no select hint,
  the frontend shows a picker instead of guessing. At chat time `utils/skills_runtime.py` enforces
  OpenClaw gating against the configured Open Terminal, syncs bundled files there, and substitutes
  `{baseDir}`; any terminal failure must degrade to the previous default loading, never break the
  chat. `POST /skills/id/{id}/install_deps` runs declared install specs only on explicit user
  action. Secrets never go into the shareable skill meta.
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
