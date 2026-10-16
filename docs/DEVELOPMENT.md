# Development commands and repository map

Read [AGENTS.md](../AGENTS.md), plus [frontend](../src/AGENTS.md) or
[backend](../backend/AGENTS.md) instructions for the files you will change. Commands below run from
the repository root unless a different directory is stated. They describe source development;
deployment instructions remain in [README.md](../README.md).

## Runtime and dependency setup

| Tool                | Repository requirement / convention                                                                             | Source                                                          |
| ------------------- | --------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| Node.js             | Use 22 to match CI and Docker; manifest permits `>=18.13.0 <=22.x.x`                                            | `package.json`, `.github/workflows/frontend.yaml`, `Dockerfile` |
| npm                 | Use npm and the tracked `package-lock.json`; `.npmrc` enables strict engines                                    | `package.json`, `.npmrc`                                        |
| Python              | 3.11 or 3.12 (`>=3.11, <3.13.0a1`)                                                                              | `pyproject.toml`, `.github/workflows/backend.yaml`              |
| Python dependencies | Project metadata and lock in `pyproject.toml` / `uv.lock`; Docker also installs from `backend/requirements.txt` | `Dockerfile`, `pyproject.toml`                                  |

Check `node --version`, `npm --version`, and `python --version` first. A passing standalone helper
test on another Python version does not demonstrate that the application supports that version.

Install frontend dependencies when needed:

```sh
npm ci --force
```

This is the frozen-lock install used by frontend unit-test CI. Docker runs it through
`scripts/npm-ci.sh`, using a temporary cache for each attempt and retrying only cache-file collisions
up to three times. It replaces `node_modules`;
coordinate it with other workers in a shared checkout. The format/build CI job instead uses
`npm install --force`. Do not switch package managers or regenerate the lockfile incidentally.

For a source-only Python environment with uv installed:

```sh
uv sync --locked --no-install-project --python 3.11
```

This installs locked dependencies and the default development group into `.venv` while omitting the
project itself. Pytest adds `backend` to its import path via `pyproject.toml`, so the documented test
command works from the repository root without an editable project install or `PYTHONPATH` override.
`--locked` checks lock consistency; `--no-install-project` avoids this project's
frontend-building Hatch hook. See [uv's sync documentation](https://docs.astral.sh/uv/concepts/projects/sync/).
Dependency installation can download large ML packages and may need platform-specific native
libraries. Do not perform it for a documentation-only task or replace an existing shared environment
without checking its purpose.

Activate that environment before running `python` commands:

```sh
# Bash / WSL
. .venv/bin/activate
```

```powershell
# PowerShell, subject to the machine's existing execution policy
.\.venv\Scripts\Activate.ps1
```

If activation is unavailable, invoke `.venv/bin/python` or `.venv/Scripts/python.exe` directly.
An alternative to uv is a dedicated Python 3.11/3.12 virtual environment with dependencies installed
using `python -m pip install -r backend/requirements.txt`, followed by the test/format tools needed
for the task. This requirements-based route does not reproduce `uv.lock` exactly.
`backend/requirements-slim.txt` describes the reduced slim image. It replaces the old WIP minimum list;
do not treat it as the full development environment. Standard images remain the main fork target.

For only the current lightweight helper tests, a separate supported-Python virtual environment can
use `python -m pip install pytest pytest-asyncio aiohttp`. This is not a full application environment.

## Run the source application

The frontend dev server proxies `/api`, `/ollama`, `/openai`, `/oauth`, and `/ws` to
`http://localhost:8080` by default. Set `WEBUI_BACKEND_URL` in the frontend process environment
before startup to change the target; see [vite.config.ts](../vite.config.ts).

In one terminal at the repository root:

```sh
npm run dev
```

The usual Vite port is 5173; use the URL printed by the process. `npm run dev:5050` explicitly uses 5050. Both scripts prepare Pyodide assets before starting Vite. `dev` binds with `--host`.

Before starting the backend, select disposable local data and static directories. Application
imports can migrate the selected database and refresh static files. These examples explicitly use
local SQLite; adapt them deliberately if the task requires a different database.

```sh
# Bash / WSL, repository root, with the Python environment activated
export DATA_DIR="$PWD/.agent-work/local/data"
export STATIC_DIR="$PWD/.agent-work/local/static"
export DATABASE_URL="sqlite:///$DATA_DIR/webui.db"
mkdir -p "$DATA_DIR" "$STATIC_DIR"
python -m uvicorn open_webui.main:app --app-dir backend --host 127.0.0.1 --port 8080 --reload --reload-dir backend
```

```powershell
# PowerShell, repository root, with the Python environment activated
$env:DATA_DIR = Join-Path (Get-Location) '.agent-work/local/data'
$env:STATIC_DIR = Join-Path (Get-Location) '.agent-work/local/static'
$env:DATABASE_URL = 'sqlite:///' + ($env:DATA_DIR -replace '\\', '/') + '/webui.db'
New-Item -ItemType Directory -Force -Path $env:DATA_DIR, $env:STATIC_DIR | Out-Null
python -m uvicorn open_webui.main:app --app-dir backend --host 127.0.0.1 --port 8080 --reload --reload-dir backend
```

Use distinct data directories and ports for concurrent runtime sessions. Reuse a running service
only after confirming its owner and purpose. Configure external providers with local credentials;
never paste keys into shared logs or task notes. If `.env` is needed, consult `.env.example` and
preserve any existing `.env`. The backend loads the repository's `.env` via `env.py`.

The existing `backend/dev.sh` is another Bash entry point (run from `backend/`); it sets development
CORS origins and binds on all interfaces. `backend/start_windows.bat` is a separate startup path
whose own comment recommends WSL. These scripts can create local secret/data files.

## Choose verification by change

| Purpose                    | Command (repository root)                                                                       | Meaning / limitation                                                                 |
| -------------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Frontend suite, one run    | `npm run test:frontend -- --run`                                                                | Confirm expected tests were collected; the script permits no tests                   |
| Build/release script tests | `node --test scripts/*.test.js`                                                                 | Node 22 built-in tests; no npm install required; requires Bash (Git Bash on Windows) |
| Workflow validation        | `bash scripts/check-workflows.sh`                                                               | Installed actionlint checks workflow structure and expression contexts               |
| Focused frontend test      | `npm run test:frontend -- --run src/lib/utils/reasoning-effort.test.ts`                         | Model reasoning selection/default helpers                                            |
| Svelte/TypeScript          | `npm run check`                                                                                 | Runs SvelteKit sync and svelte-check; writes generated `.svelte-kit` files           |
| Frontend production build  | `npm run build`                                                                                 | Runs Pyodide preparation, then Vite; network and memory may be required              |
| Backend helper tests       | `python -m pytest backend/tests -q`                                                             | No live model/search service required by current tests                               |
| Focused backend regression | `python -m pytest backend/tests/test_openai_responses_payload.py -q`                            | Isolated Responses payload conversion                                                |
| Python format              | `python -m ruff format --check <changed-python-files>`                                          | Non-mutating; replace placeholder with explicit paths                                |
| Python CI logic subset     | `python -m ruff check --select=F --ignore=F401,F403,F405,F541,F811,F841 <changed-python-files>` | Same selected rules as active backend CI                                             |
| Frontend lint              | `npx --no-install eslint <changed-frontend-files>`                                              | Non-mutating; use installed tooling and explicit paths                               |
| Markdown/frontend format   | `npx --no-install prettier --check <changed-files>`                                             | Uses `.prettierrc` and its Svelte plugin                                             |
| Patch hygiene              | `git diff --check`                                                                              | Whitespace/conflict-marker check on tracked diffs; inspect new files too             |

The frontend script limits Vitest discovery to `src`, keeping Node built-in script tests in their
separate runner. This also avoids scanning unrelated ignored directories such as `.pytest_cache`
on restricted Windows checkouts. Run both commands when changing shared build/test configuration;
do not delete shared caches or change permissions as an incidental test fix.

`npm run build` invokes [scripts/prepare-pyodide.js](../scripts/prepare-pyodide.js), which downloads
packages and refreshes `static/pyodide/`. Review changes to the tracked `pyodide-lock.json`.
Transient package/HTTP download failures retry up to three times; exhausted or permanent failures
stop preparation with the original cause instead of continuing with missing wheels.
CI gives the build an 8192 MB Node heap; on a memory-constrained machine report the limitation
instead of claiming the build passed. `hatch_build.py` also invokes npm install/build when packaging
the Python project; `pip install .` is not a lightweight Python-only setup.

## What the existing CI actually checks

- [Workflow and build-script CI](../.github/workflows/actions.yaml) validates workflow structure and
  expression contexts with pinned, checksum-verified actionlint, then runs the Node 22 script tests.
  It does not install application dependencies or publish a release.
  The shared validation script narrowly suppresses actionlint 1.7.12's unsupported-key diagnostic
  for `concurrency.queue` in `release.yml` ([upstream issue](https://github.com/rhysd/actionlint/issues/657));
  all other diagnostics remain enabled.
- [Frontend CI](../.github/workflows/frontend.yaml) runs Node 22. One job runs installation,
  `npm run format`, `npm run i18n:parse`, `git diff --exit-code`, and a production build. Another
  runs Vitest. The active workflow does not run `npm run check` or ESLint.
- [Backend CI](../.github/workflows/backend.yaml) runs Ruff formatting, selected `F` logic checks,
  and the full `backend/tests` pytest suite on Python 3.11/3.12. Tests use locked dependencies with
  `--no-install-project` and isolated data/static/SQLite paths. This does not start the full application
  or verify external services.
- Files ending in `.disabled` under `.github/workflows/` are not active workflows.
- `npm run lint:frontend`, `npm run format`, `npm run format:backend`, `npm run i18n:parse`, and the
  configured pre-commit hooks modify files. Run targeted checks first and inspect generated diffs.
- Docker/release workflows can publish artifacts on configured branches/tags. Inspect the relevant
  workflow before treating a push or manual dispatch as a validation-only operation.
- [Release](../.github/workflows/release.yml) compares package versions at the push's before/head
  commits. An unchanged version or initial push skips publication. A missing nonzero before commit
  fails explicitly. Notes over the size limit use an excerpt with an immutable full-changelog link.
  [GitHub's `queue: max`](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
  retains up to 100 pending runs so a later documentation push does not replace a waiting version-change run.

For documentation-only changes, validate links, paths, formatting, and the diff. Application startup
and a full dependency install are not needed. For behavior changes, select meaningful tests and
exercise the changed workflow. Record baseline failures separately; never widen a task into a
repository-wide cleanup solely to make an unrelated check green.

## Fork regression map

Read [the upstream merge index](upstream/README.md) and the
[v0.12.2 decisions](upstream/v0.12.2-decisions.md) before comparing or merging upstream.
Older documents in `docs/superpowers/` describe historical tasks and do not set the current baseline.
Official commit IDs and actual ancestry, not local tag names alone, determine an upgrade target.

| Behavior                              | Implementation boundary                                                                | Regression starting point                                                                                                                                                                    |
| ------------------------------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Search1API                            | `retrieval/web/search1api.py`, retrieval/config routes, admin WebSearch                | `backend/tests/test_search1api.py`, `backend/tests/test_search1api_integration.py`                                                                                                           |
| Tavily search depth                   | `config.py`, `retrieval/web/tavily.py`, retrieval/config routes, admin WebSearch       | `backend/tests/test_tavily.py`, `backend/tests/test_tavily_config.py`                                                                                                                        |
| Reasoning effort and Responses        | Model metadata/defaults, chat controls, `utils/responses_reasoning.py`                 | `src/lib/utils/reasoning-effort.test.ts`, `backend/tests/test_openai_responses_payload.py`                                                                                                   |
| Skills / OpenClaw                     | Admin discovery config, skill ACL, middleware, runtime, import/editor/export           | `backend/tests/test_skill_discovery_policy.py`, `backend/tests/test_skills_runtime.py`, `src/lib/utils/skills.test.ts`, `src/lib/utils/skill-discovery.test.ts`                              |
| Tool preferences and settings         | Chat/SettingsModal, queued top-level UI patches, backend user settings                 | `src/lib/components/chat/SettingsModal.test.ts`, `src/lib/utils/ui-settings.test.ts`, `src/lib/utils/tool-feature-preferences.test.ts`, `src/lib/components/chat/Chat.tool-features.test.ts` |
| Knowledge, memory, notes              | Feature controls, explicit attachments, builtin-tool permission checks                 | `backend/tests/test_chat_tool_features.py`                                                                                                                                                   |
| Search/page evidence                  | Builtin web tools, source conversion, escaped metadata, stable numbering               | `backend/tests/test_builtin_web_tools.py`, `backend/tests/test_web_tool_citations.py`                                                                                                        |
| Tool citation cache                   | Middleware attachment/approval, output replay, provider conversion and V2 raw ranges   | `backend/tests/test_tool_citation_replay.py`, `backend/tests/test_web_tool_citations.py`, `src/lib/utils/citations.test.ts`                                                                  |
| Image engines and context             | Admin engine editor, POST verify, generation/edit tools, persisted tool output         | `backend/tests/test_image_generation_engines.py`, `backend/tests/test_image_tool_context.py`, `backend/tests/test_comfyui_errors.py`                                                         |
| Terminal delivery and identity        | Proxy routes, signed references, HTTP/WS ownership and context                         | `backend/tests/test_terminal_file_routes.py`, `backend/tests/test_terminal_websocket.py`, `src/lib/apis/terminal/index.test.ts`                                                              |
| Terminal instructions and attachments | AGENTS.md per-turn loading after real user attachment pairing                          | `backend/tests/test_terminal_agents_attachments.py`, `backend/tests/test_skill_discovery_policy.py`                                                                                          |
| Composer suggestion interaction       | Real ProseMirror plugin, Tab, composition events and touch                             | `src/lib/components/common/RichTextInput/AutoCompletion.test.ts`                                                                                                                             |
| Redis revocation failures             | HTTP authentication, login cookies, terminal WebSocket retry errors                    | `backend/tests/test_auth_revocation.py`                                                                                                                                                      |
| Shared voice persistence              | Dedicated transcript/voice routes, author ACL, branch links and raw/normalized storage | `backend/tests/test_shared_voice_messages.py`, `src/lib/apis/chats/voice.test.ts`, `src/lib/components/chat/Chat.voice.test.ts`                                                              |
| Legacy pipe reasoning                 | Save/import/restore guard, public default hints and final string payload               | `backend/tests/test_model_controls_compat.py`, `src/lib/utils/pipe-model-controls.test.ts`, `src/lib/utils/model-controls.test.ts`, `src/lib/utils/reasoning-effort.test.ts`                 |
| Skill URL MIME and TLS                | Validated redirect downloads, SSL policy and descriptor/package parsing                | `backend/tests/test_skill_url_download.py`, `backend/tests/test_skill_files_compat.py`                                                                                                       |
| S3 completed cache                    | Adjacent atomic downloads, concurrent readers and failure cleanup                      | `backend/tests/test_s3_storage_atomic.py`                                                                                                                                                    |
| Tika versions and JSON                | Extraction loader response negotiation and version-specific endpoint                   | `backend/tests/test_tika_loader.py`                                                                                                                                                          |

Search1API's test named `test_search1api_integration.py` checks source wiring, not a live provider.
Some existing tests isolate functions through AST loading to avoid importing database/config side
effects. Those tests do not prove full application startup, schema compatibility, or a complete UI
workflow. Keep actual endpoint/transport tests where available and report runtime evidence separately.

The full suite uses temporary files. On restricted Windows hosts, choose a fresh task-owned
`--basetemp` under `.agent-work/` if pytest's system temporary root is inaccessible. Never remove
another worker's temp directory to unblock a test. Use the selected supported Python interpreter.

Before upgrading, review third-party Tools/Functions requirements: upstream no longer bundles some
previously implicit packages such as `langchain-community`. Review standard/slim differences,
Cookie-forwarding defaults, direct-integration visibility, and the new skill discovery opt-ins in the
[upgrade record](upstream/v0.12.0-decisions.md#升级与回滚). Existing PostgreSQL vector indexes require
explicit inspection before deciding on a rebuild; this merge does not automatically rebuild them.

The fork also adjusts `.github/workflows/docker.yaml`: the current image workflow publishes through
GHCR and has removed Helm chart notification and Docker Hub copy steps. During upstream workflow
merges, review the current triggers, destinations, permissions, and recent history before restoring
any removed publishing steps.

### Gemini tool schema verification

The manual Gemini profile targets third-party OpenAI-compatible gateways using Google's restricted
Schema format. See the [compatibility guide](gemini-tool-compatibility.md) for conversion semantics,
original-schema validation, upstream references and limits. Use the existing backend dependencies,
including `jsonschema` from the MCP dependency tree; the minimal helper-only environment needs it too.

```sh
python -m pytest backend/tests/test_gemini_schema_compiler.py backend/tests/test_gemini_tool_schema.py backend/tests/test_model_tool_schema_compatibility.py backend/tests/test_streaming_tool_call_dedup.py backend/tests/test_streaming_tool_call_rounds.py backend/tests/test_approval_execution_helper.py backend/tests/test_tool_citation_replay.py backend/tests/test_context_compaction_model_routing.py backend/tests/test_context_compaction_dispatch.py backend/tests/test_openai_responses_payload.py backend/tests/test_chat_tool_features.py -q
```

These tests exercise pure conversion and isolated production send/execution boundaries. They do
not call a live provider or prove application startup. For gateway acceptance, enable the mode on
the actual model and exercise nested tools, argument correction, multiple rounds and approval
resumption without recording credentials or user data in fixtures.

### Tool citation cache verification

Use Python 3.11/3.12 and the existing development dependencies. These tests isolate production
helpers, the native result/dispatch blocks and approval handling; the V2 case uses disposable SQLite.
They do not start the application or send live model/search requests.

```sh
python -m pytest backend/tests/test_web_tool_citations.py backend/tests/test_tool_citation_replay.py backend/tests/test_openai_responses_payload.py backend/tests/test_image_tool_context.py backend/tests/test_builtin_web_tools.py backend/tests/test_context_compaction_v2.py backend/tests/test_context_compaction_dispatch.py backend/tests/test_tool_result_adapters.py backend/tests/test_tool_result_budget.py backend/tests/test_tool_result_budget_integration.py -q
npm run test:frontend -- --run --dir src src/lib/utils/citations.test.ts
```

If the system pytest temporary directory is inaccessible, use an unused task-owned `--basetemp`
under ignored `.agent-work/`; pytest can clear that directory, so never point it at shared files.
Report the actual interpreter, test count and any missing dependency or fixture limitation.

- Compare provider-bound input for one search followed by at least five fetches, with fixed model,
  tools and instructions. Chat and stateless Responses preserve the earlier messages/input exactly;
  system content/instructions do not grow. Stateful Responses sends only the new results and keeps
  the same instructions with `previous_response_id`.
- Check mixed file/web sources, duplicate URLs, equal titles with different URLs, parallel calls,
  empty/failed results, disabled citations, escaping and evidence kinds. UI and model IDs must follow
  the same source-event order; search snippets remain distinct from fetched page evidence.
- Serialize/reload saved and temporary output; resume approved calls and repeat the resume to prove
  tools/events are not duplicated. Preserve frozen citation text, images and reasoning details.
  Test full/partial approval with prepared sidecars sharing the reply's V2 ID, and check that temporary
  replay markers leave the messages before dispatch (including the pipe path).
  Legacy outputs without citation fields must still replay. Inspect both converted messages and
  native stored Responses items for leaked internal fields.
- Count citation text before final-send compaction. In V2, verify raw result spans exclude the
  citation suffix, projections retain it, and authenticated raw reads return only original text.
  Actual history compaction or changed tool definitions may legitimately change the prefix.

For an authorized live comparison, use synthetic prompts and the same model, provider and tool set;
record the actual provider/route, request timing, input tokens, cache reads/writes and input cost per
request from OpenRouter usage. Use the API's reported usage details (including `cached_tokens` and
`cache_write_tokens` when available), not the UI's aggregate conversation token total. Compare cold
and subsequent requests and preserve sanitized results separately. Missing usage fields are unknown,
not zero. Local prefix checks do not establish a cache hit rate or savings. See
[OpenRouter caching](https://openrouter.ai/docs/guides/best-practices/prompt-caching) and the
[implementation record](upstream/v0.11.4-tool-citation-cache-followup.md).

### Tavily search-depth verification

Run the isolated Tavily tests and neighboring Search1API regressions with the supported Python environment.
The Tavily tests need `pytest`, `pytest-asyncio`, `requests`, `fastapi`, `pydantic`, `httpx`,
`sqlalchemy[asyncio]`, and `aiosqlite`; Search1API also needs `aiohttp`. Use the repository's
versions for runtime dependencies. A helper-only environment does not need the full application stack.

```sh
python -m pytest backend/tests/test_tavily.py backend/tests/test_tavily_config.py backend/tests/test_search1api.py backend/tests/test_search1api_integration.py backend/tests/test_search1api_missing_key.py -q
```

The Tavily tests exercise request payloads, result/error handling, configuration validation and
save/read behavior, and the shared search route using isolated code and mocked external boundaries.
They do not contact Tavily or prove a complete application startup or browser workflow.

For manual acceptance, use the disposable data/static directories described above:

1. In Admin Settings → Web Search, select Tavily and confirm Search Depth starts at `basic` when no
   saved setting or environment override exists. Check the English and Chinese labels.
2. Select `advanced`, save, reload the page, and confirm the selection persists. Switch to another
   search provider and back; the stored Tavily value must remain available.
3. Run built-in chat search and web retrieval against a local Tavily-compatible test endpoint using
   `TAVILY_API_BASE_URL` and a synthetic key. Check both `/search` request bodies contain
   `search_depth: "advanced"`; switch back to `basic`, save, and repeat.
4. Configure the Tavily page loader with its own extraction depth and confirm changing search depth
   does not change `TAVILY_EXTRACT_DEPTH` or `/extract` requests.
5. On a fresh disposable database, confirm `TAVILY_SEARCH_DEPTH=advanced` supplies the initial
   default. Save `basic` through the admin UI and restart; with persistence enabled, the saved value
   must still win over the environment default.

A real-provider smoke test is separate and consumes Tavily credits. If performed, use a development
key and report it separately from mocked checks; never include the key or response data in logs.

### Context compaction verification

Use Node 22 and Python 3.11/3.12. The V2 guide is [context-compaction.md](context-compaction.md).
For manual acceptance, configure an OpenAI-compatible model window of 100000 and open `/status`:
with default ratios and no output reserve the window/trigger must be 100000/90000. Check the
compaction progress/result on its response, reload persistence, and visibility when model tool
status updates are disabled. Use synthetic history/provider replies for automated threshold tests.

New regression locations:

| Boundary                                                                                                                            | Tests                                                                                                                                           |
| ----------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Budget formulas, CJK, multimodal, grouping, summary failures and replay                                                             | `backend/tests/test_context_compaction_v2.py`                                                                                                   |
| Internal summary percentage/absolute budgets, final-send guards, lossless shrinking retries and state isolation                     | `backend/tests/test_context_compaction_summary_safety.py`                                                                                       |
| Admin summary percentage validation and request payload                                                                             | `src/lib/components/admin/Settings/Interface.context-compaction.test.ts`                                                                        |
| Tail reminders, continuation deduplication, budget accounting and assistant prefills                                                | `backend/tests/test_context_compaction_v2.py`                                                                                                   |
| Responses conversion preserves stable instructions and trailing reminders                                                           | `backend/tests/test_openai_responses_payload.py`                                                                                                |
| Checkpoint CAS, source edits, branch/model isolation and migration round trip                                                       | `backend/tests/test_context_compaction_v2_storage.py`                                                                                           |
| Chat/model checkpoint cleanup with SQLite cascades disabled                                                                         | `backend/tests/test_context_compaction_cleanup.py`                                                                                              |
| Admin config/HTTP round trip, chat/model ACL, active-response guard, metadata                                                       | `backend/tests/test_context_compaction_api.py`                                                                                                  |
| Original algorithm, legacy threshold caps and off/V2 raw replay                                                                     | `backend/tests/test_context_compaction_legacy.py`                                                                                               |
| Provider context errors, bounded retry and streaming visibility                                                                     | `backend/tests/test_context_compaction_dispatch.py`                                                                                             |
| OpenAI preset/prefix routing with persisted 100000 window and 90000 trigger                                                         | `backend/tests/test_context_compaction_model_routing.py`                                                                                        |
| Compaction socket filtering, operation upserts and status persistence in both chat stores                                           | `backend/tests/test_context_compaction_status.py`                                                                                               |
| Chat lifecycle events, notification ownership/deduplication and localized response status                                           | `src/lib/components/chat/Chat.context-compaction.test.ts`, `src/lib/components/chat/Messages/ResponseMessage.context-compaction.test.ts`        |
| Tool percentage validation, authenticated raw pages and stale viewer requests                                                       | `src/lib/utils/tool-results.test.ts`, `src/lib/components/chat/Messages/ResponseMessage/ToolResultViewer.test.ts`                               |
| Native/approved/injected/code provenance, reader registration and untrusted metadata stripping                                      | `backend/tests/test_tool_result_adapters.py`                                                                                                    |
| Raw/cache identity races, source/branch validation, exact paging, ASGI ACL and migration                                            | `backend/tests/test_tool_result_storage.py`, `backend/tests/test_context_compaction_cleanup.py`, `backend/tests/test_context_compaction_api.py` |
| Durable silent/mixed accounting, source validation, dual-store reload and concurrent single/bulk writes                             | `backend/tests/test_tool_budget_accounting.py`                                                                                                  |
| Real storage + budget orchestration replay and exact-reader shrinking                                                               | `backend/tests/test_tool_result_budget_integration.py`                                                                                          |
| History-first tool reservation, fair caps, summaries/excerpts and exact reread budgeting                                            | `backend/tests/test_tool_result_budget.py`                                                                                                      |
| New-only allocation, 149k reproduction, reclaimed savings, full JSON/repeated injection fit, cancellation and canonical race guards | `backend/tests/test_tool_result_replay.py`                                                                                                      |
| Pending/prepared source identity, atomic admission/report guards, mixed winners, no raw duplication and stable descendant replay    | `backend/tests/test_tool_result_selections.py`                                                                                                  |
| Trigger/input-capacity labels, historical decision metadata, incomplete records and localized rendered status                       | `src/lib/components/chat/MessageInput/ContextUsageStatus.test.ts`                                                                               |
| Mode compatibility, model inheritance and stale model/branch usage rejection                                                        | `src/lib/utils/context-compaction.test.ts`                                                                                                      |

Set `DATA_DIR`, `STATIC_DIR`, `DATABASE_URL`, and a synthetic `WEBUI_SECRET_KEY` to a disposable
directory before any real model/router/application imports. For helper checks, use
`ENABLE_DB_MIGRATIONS=false` to avoid import-triggered migrations. Do not point tests at a production
SQLite file, Redis namespace, uploads directory or secrets. Storage tests use their own temporary
SQLite databases and inject the session boundary; API tests mount extracted production handlers with
synthetic auth/storage dependencies, not a complete application. Provider tests use synthetic responses.

Select Node 22 and Python 3.12 before this repair's checks (Python 3.11 remains supported by the project).
For PowerShell, use an ignored disposable directory and then the commands below; activate your supported
Python environment first, and ensure installed dependencies/formatters are present. Do not use a user database.

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = (Join-Path $PWD 'backend')
$env:DATA_DIR = (Join-Path $PWD '.agent-work/v2-tool-replay/data')
$env:STATIC_DIR = (Join-Path $env:DATA_DIR 'static')
$env:DATABASE_URL = 'sqlite:///' + (Join-Path $env:DATA_DIR 'webui.db').Replace('\', '/')
$env:ENABLE_DB_MIGRATIONS = 'false'
$env:WEBUI_SECRET_KEY = 'synthetic-v2-replay-test'
node --version
python --version
```

```sh
python -m pytest backend/tests/test_context_compaction_v2.py backend/tests/test_context_compaction_v2_storage.py backend/tests/test_context_compaction_api.py backend/tests/test_context_compaction_dispatch.py backend/tests/test_context_compaction_legacy.py backend/tests/test_context_compaction_cleanup.py -q
python -m pytest backend/tests/test_context_compaction_summary_safety.py -q
python -m pytest backend/tests/test_context_compaction_model_routing.py backend/tests/test_context_compaction_status.py backend/tests/test_openai_responses_payload.py backend/tests/test_web_tool_citations.py backend/tests/test_image_tool_context.py -q
python -m pytest backend/tests/test_tool_result_budget.py backend/tests/test_tool_result_budget_integration.py backend/tests/test_tool_result_replay.py backend/tests/test_tool_result_selections.py backend/tests/test_tool_budget_accounting.py backend/tests/test_tool_result_storage.py backend/tests/test_tool_result_adapters.py backend/tests/test_context_compaction_status.py -q
python -m pytest backend/tests -q
npm run test:frontend -- --run --dir src src/lib/utils/tool-results.test.ts src/lib/components/chat/Messages/ResponseMessage/ToolResultViewer.test.ts
npm run test:frontend -- --run --dir src src/lib/components/admin/Settings/Interface.context-compaction.test.ts src/lib/utils/context-compaction.test.ts src/lib/components/chat/Chat.context-compaction.test.ts src/lib/components/chat/Messages/ResponseMessage.context-compaction.test.ts src/lib/components/chat/MessageInput/ContextUsageStatus.test.ts
npm run test:frontend -- --run --dir src
npm run check
```

The history-checkpoint Alembic revision is `c7d2e4f6a801`, descending from `d4c1a8e37b62`.
The tool-result revision `e8f3a9c2b604` descends from `c7d2e4f6a801` and creates `chat_tool_result`
and `chat_tool_result_context`; upgrade/downgrade changes only these new tables and preserves chats.
`test_tool_result_storage.py` verifies their round trip on disposable SQLite.
The selection revision `f9a4b2d6c805` descends from `e8f3a9c2b604`, creating only
`chat_tool_result_selection`. It deliberately does not guess adoption from existing candidates or
backfill old summaries. The same storage suite exercises upgrade/downgrade/re-upgrade, preserving chats,
raw results and candidates; selection tests exercise real SQLite locking/concurrent winners and report
rollback. Verify deletion with FK cascades both enabled and disabled. Check source edits and branch/model
changes invalidate only inappropriate selections, and legacy originals need no extra raw text snapshot.
The history revision creates `chat_context_checkpoint` without rewriting `chat_message.context_summary` or raw history.
Verify a single revision head. On a disposable database, upgrade the previous head, insert synthetic
legacy messages and a legacy summary, upgrade to the new head, then confirm those values are unchanged.
Exercise new table insert/model separation/version conflict, downgrade only the new revision and
upgrade again. The new-revision storage test checks the table/index lifecycle; it is not proof of the
entire application's historical migration chain or PostgreSQL behavior.

From the repository root, with the isolated environment above, the full migration entry point is:

```python
from pathlib import Path
from alembic import command
from alembic.config import Config

cfg = Config('backend/open_webui/alembic.ini')
cfg.set_main_option('script_location', str(Path('backend/open_webui/migrations').resolve()))
command.upgrade(cfg, 'head')
```

After editing documentation, use the installed Prettier without downloading a formatter:

```sh
npx --no-install prettier --check AGENTS.md src/AGENTS.md backend/AGENTS.md README.md docs/context-compaction.md docs/DEVELOPMENT.md
python -m ruff format --check backend/open_webui/utils/context_compaction_config.py backend/open_webui/utils/context_compaction_v2.py backend/open_webui/utils/context_compaction_dispatch.py backend/open_webui/models/chat_context_checkpoints.py
python -m ruff check --select=F --ignore=F401,F403,F405,F541,F811,F841 backend/open_webui/utils/context_compaction_config.py backend/open_webui/utils/context_compaction_v2.py backend/open_webui/utils/context_compaction_dispatch.py backend/open_webui/models/chat_context_checkpoints.py
git diff --check
```

Also check any other changed Python/frontend files with the same targeted tools, and validate Markdown
relative links. Do not count a no-tests result, formatting, or AST isolation as a full runtime check.
Compare existing `npm run check` failures against the same base revision before attributing them to V2.
The fresh pre-implementation baseline at `fedbb30ad5dfaef5f677ad259df539a1c63859ae` has 6808 errors
and 197 warnings in 344 files (Node 22); compare normalized
file/message diagnostics as well as totals. Record actual results and any untested live provider/browser
paths in the delivery report. Use an explicit disposable `--basetemp` if system temp ACLs prevent pytest
fixtures. The optional usage/event fields add no schema changes; adopted tool versions require the
selection-table revision above. Record actual verification results rather than treating this checklist
as proof of passage. A synthetic SSR preview cannot certify browser interaction or real supplier sends.

Manual acceptance with an isolated running application and test model connection:

1. Start with the legacy enable flag both false and true and no mode; verify off/legacy mapping.
   Select each mode, reload settings, and verify inactive-mode values remain. A non-admin cannot
   save config. Test invalid percentages. Save/reload summary output percentage (default 5) and the
   optional absolute token setting; omitted API fields must preserve stored values. For a 100000-token
   summary model and 95% effective window, no absolute cap or 2000 gives 5000 output/90000 input;
   8000 gives 8000/87000 before further capacity/tool constraints. Oversized output settings must be
   lowered by the per-chunk safety ceiling; impossible fixed/media content must fail without data loss.
2. Edit a model's `meta.context_window`, clear it to inherit, change the base model, export/import,
   and verify effective length. Confirm provider requests contain no context metadata parameter.
3. Exercise saved chats with Chinese history, attachments, retrieval, skills and tool schemas.
   Observe source-labelled backend usage, switch models and verify the badge updates. Off sends
   full history; legacy still honors its threshold overrides; V2 does not use those overrides.
   Enter the reminder range and inspect Chat Completions/Responses payloads: the reminder is last,
   earlier messages/tool pairs remain ordered, and Responses `instructions` remain unchanged.
   Continue through two tool calls and confirm only the latest reminder follows the results.
   Check final usage includes it, oversized reminders are omitted, and assistant prefills remain last.
   Compare provider cache usage where available; stable prefixes do not guarantee cache hits.
4. Compress twice, reload, edit an old branch, fork and run two models concurrently. Check original
   messages remain, summaries stay isolated and edited sources cannot reuse a stale checkpoint.
5. Run long tools, approval pause/resume and code execution across compaction. Keep calls/results
   paired and ensure already summarized output is not restored or tools re-executed on retry.
6. Exercise timeouts, empty/reasoning-only/truncated summaries, cancellation and context-limit errors
   before and after the first visible delta. A failed summary may continue only while input fits;
   over-budget requests show a recoverable error. Manual `/compact` is blocked while generating. For browser-direct models, require the caller's own
   socket for manual summary dispatch and verify stored-history usage still loads before reconnect.
   A direct model ID/base pointer must not load a private server model's parameters.
   Inspect internal summary sends after provider defaults/parameter conversion: output must be explicit
   and full input must fit its own summary-model budget. Simulate context errors in exceptions and HTTP
   responses; verify the retry has a smaller source fragment at the same cursor, later chunks retain
   the smaller limit, all original text is eventually processed, and cancellation writes no checkpoint.
7. Check settings/model forms and status UI in Chinese/English, narrow screens, light/dark mode,
   keyboard focus and input errors. Synthetic component/browser tests supplement this walkthrough;
   live provider behavior still needs an available configured model.

Tool-result budget acceptance extends the walkthrough above:

1. Set `C = 100000` with no output reserve. Verify default tool caps of 10000/30000 tokens in
   `/status`; change individual/batch percentages, reload, and reject non-integers or individual > batch.
   Verify off, legacy and disabled tool budgeting preserve their behavior.
2. Use 70000 tokens of fixed input/history and a desired 30000-token incoming batch with `B = 95000`.
   Observe older-history compaction before tool summaries. Test one long result, parallel long/short
   results, Chinese/JSON/web output, errors and empty results; all calls retain paired results and
   the complete provider input fits its budget.
3. Exercise native tools, approval resume, injected tools, code continuations and replay after reload.
   Verify raw results stay unchanged, checkpoints do not restore covered output, and overflow retries
   do not execute tools again. Try a smaller summary model and timeout/empty/oversized summaries;
   excerpts must be exact, explicitly incomplete, and include a readable raw ID inside the cap.
4. With ordinary model tool statuses both enabled and disabled, verify current new summaries/excerpts
   and necessary errors stay visible in English/Chinese. Original results, exact rereads and cached
   projections must add no compaction notice. Start a later turn: prior notices/raw-view buttons stay
   on the original reply, and refresh/approval/continuation merges one operation's progress/completion.
   Verify `/status` keeps all-original, exact-read and mixed batches after reload, and that a new silent
   batch replaces older summary statistics. Later assistant output, periodic browser saves and delayed
   normalized single/bulk writes must preserve the newest snapshot; source
   edits and sibling branches must not reuse it.
   Check window C, input budget B and trigger T labels independently. The progress fraction and
   remaining_tokens stay T-based; input_remaining_tokens must equal max(0,B-currentInput). Expand the
   batch details: source reply, immutable decision time, then-effective limits, per-result allocations
   and reduction reasons must match backend records. Partial legacy or mixed-version records must say
   incomplete. Cancel during summary generation and verify the original progress operation ends.
   Test legacy events with output/call provenance and unknown legacy records. Open the original viewer with keyboard navigation on narrow/light/dark layouts; check
   plain-text escaping, loading/error/retry, search and bounded paging. Editing source content,
   switching to sibling branches and accessing another chat must invalidate or deny raw reads.
5. Call `read_tool_result` repeatedly and verify exact bounded JSON pages, positions/cursors and no
   recursive summarization. Test concurrent projection saves and chat deletion cleanup on a disposable
   database; check the new migration upgrade/downgrade independently of the historical migration chain.
   Continue the same query/cursor on an unchanged-source descendant (new and legacy cursor formats);
   reject source/query changes, sibling branches and other chats without rewriting prior exact pages.
6. Reproduce B=223000, fixed input=120000, selected old summaries=10000, new text=19000, with sufficient
   S/Q. Confirm raw new output survives and old raw sizes never compete again. Use a large and small
   new result: the first summary's actual savings must allow the smaller raw result to remain intact.
   Same-batch approval subsets charge actual prior selected lengths; separate replies with repeated
   call IDs stay separate. Test empty/image-only outputs and old adjacent call/result rounds.
7. Change task, summary prompt and tool percentages, reload and switch models. Prior selected text must
   remain fixed per model. With no adopted record, upgraded/imported content must use raw text despite
   candidate caches. Shrink the model window and verify whole-history compaction rather than reapplying
   per-tool caps. Disable/re-enable budgets and confirm raw/valid-selected replay after history checkpoints.
8. Generate different candidates concurrently, with one request partially overlapping another. Before
   commit, revalidate the winning full request and batch caps. If either cannot fit, fresh members must
   remain pending and the request must return a retryable conflict; no tool reruns. Compare prepared text
   with the actual supplier request when a test provider is available. Simulate cancellation before
   admission and provider failure after admission: the former has no prepared rows, the latter reuses
   canonical versions. Edit an earlier sibling output while a new summary is running, and between
   loading and sending a history-only request: both must reject stale selected text before admission.
   Manual history compaction must not prepare pending tools. Status GET must never
   create selections or summaries; source history and timestamps must remain unchanged.

## v0.12.2 merge acceptance

Use Node 22 and Python 3.11/3.12. Run full frontend/backend suites and confirm their collected counts,
then `npm run check` and `npm run build`. Type diagnostics must be compared by file and diagnostic
against the pre-merge baseline; a lower total is not a pass. Install/update locks with npm and uv,
check `uv lock --check`, and inspect generated `static/pyodide/pyodide-lock.json` changes and downloaded
wheel hashes. The build writes that generated lock atomically to support Windows mapped-file readers. If Node
exhausts its default heap, set `NODE_OPTIONS=--max-old-space-size=6144` for the build (PowerShell:
`$env:NODE_OPTIONS="--max-old-space-size=6144"`). The Docker frontend build stage defaults to the same
6 GiB heap limit; override it with `docker build --build-arg NODE_OPTIONS="--max-old-space-size=8192" .`
when appropriate for the builder. This argument does not enter the Python runtime image.
Do not manually edit the lockfile dependency resolutions.

The migration graph joins fork `f9a4b2d6c805` and official `b8f2c6d91a04` at `a120d13e0001`,
then converts current skill packages at `a120d13e0002` and adds shared provenance at `a120d13e0003`.
The v0.12.2 merge adds `a122d15e0001`, joining that branch with upstream `0bd252b206fb`.
Run `python -m pytest backend/tests/test_v012_migrations.py backend/tests/test_v0122_migrations.py -q`
with isolated data/static directories and a new `--basetemp` when the system temporary directory is unavailable.
The test runs real Alembic upgrades for empty, fork and official SQLite databases, rejects conflicting
legacy attachments without advancing the head, and checks immutable snapshots and idempotence.
Model control regressions exercise actual parameter conversion for OpenAI/Ollama, including null and
non-object defaults. Shared response regressions cover exports, lists, mutations and clone persistence;
compare author metadata as well as top-level owner settings. Shared chat/folder read grants select
members; `share_mode=continue` permits all of those readers to reply without a separate write grant.
Clone-only readers retain snapshot access. Test continuation permissions separately from owner-only
whole-chat edits and author-only voice updates, approvals and stops.

PostgreSQL DDL compilation is not PostgreSQL execution: also run the upgrade cases against a disposable
PostgreSQL instance before claiming that database's runtime migration acceptance. Set `TEST_POSTGRES_URL`
to a task-only `postgresql+psycopg://.../postgres` connection with database-creation permission. The new
suite creates and drops a unique database per case; without this setting six PostgreSQL cases skip.
Compare stored checkpoint fields, NULL/saved provenance, auth, raw chats, skill versions and adopted
tool results; schema-only checks cannot establish data preservation. See the [actual acceptance report](upstream/v0.12.2-validation.md).

| Contract                                                              | Regression entry points                                                                                                                            |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Canonical skills, old snapshots and concurrent edits                  | `test_skill_compat.py`, `test_skill_files_compat.py`, `test_skill_persistence_compat.py`, `test_skills_runtime.py`, `test_skill_runtime_paging.py` |
| Shared V2 provenance, private-input exclusion, immutable raw fallback | `test_context_compaction_shared.py`, `test_chat_context_copy.py`, `test_context_compaction_api.py`, `test_chat_response_privacy.py`                |
| Controls migration, precedence and reset/account isolation            | `test_model_controls_compat.py`, `src/lib/utils/model-controls.test.ts`, `src/lib/components/chat/Chat.model-controls.test.ts`                     |
| Exact schemas and skill pages, bounded final payload                  | `test_exact_tool_results.py`, `test_tool_result_budget.py`, `test_tool_result_budget_integration.py`                                               |
| Author approvals, continuations and frozen citations                  | `test_tool_citation_replay.py`, `test_web_tool_citations.py`, `test_terminal_agents_attachments.py`                                                |
| Revocation/terminal boundaries and real Socket.IO adapter             | `test_auth_revocation.py`, `test_terminal_websocket.py`, `test_socket_chat_access.py`                                                              |

Backend test names in this table are under `backend/tests/`. Test helper stubs and synthetic provider
responses do not establish live provider, Redis, Realtime, or MFA acceptance. Exercise owner, continuation member,
clone-only reader and stranger identities; revoke a writer's group/share access and verify subsequent reads,
approvals, stops and socket events are refused. Check nested groups and group model defaults, clone
snapshot isolation, concurrent shared V2 sends, and explicit skill dependency installation.

Manually exercise model controls after import, refresh, reset, account switches and direct connections;
include keyboard/Chinese IME and narrow-screen layout. Run translation extraction twice (`npm run i18n:parse`) and compare locale hashes after each run. Validate links in changed Markdown and run
Prettier, Ruff and `git diff --check` on actual changed files. Preserve historical decision records.

Start the complete application using disposable SQLite/data/static directories and a synthetic secret,
check health/version/config and a synthetic account workflow. Standard container startup remains a
separate acceptance step, including native dependencies and mounted storage. Slim adds upstream S3
storage but does not imply standard-image processing dependencies or tested external services. Record
actual runtime/container checks and omissions in the [version record](upstream/v0.12.2-decisions.md).

### v0.12.2 integration checks

The new head `a122d15e0001` joins fork `a120d13e0003` and upstream `0bd252b206fb` without data writes.
Upstream `c92da83b017f` adds Apps and channel-member constraints; `0bd252b206fb` fills missing auth rows.
Validate empty, fork v0.12.0, official v0.12.0 and official v0.12.2 databases, preserving existing
credentials, raw chats, historical skill snapshots and adopted tool results. Repeat the upgrade and
check a single Alembic head on SQLite and a disposable PostgreSQL server; compiling DDL is insufficient.

Run the new regressions alongside the existing map above:

```sh
python -m pytest backend/tests/test_tool_dispatch_v0122.py backend/tests/test_v0122_tool_chain.py backend/tests/test_apps_channel_permissions.py -q
python -m pytest backend/tests/test_standard_voice_access.py backend/tests/test_realtime_authorization.py backend/tests/test_shared_voice_messages.py -q
python -m pytest backend/tests/test_skill_v0122_integration.py backend/tests/test_skill_runtime_paging.py backend/tests/test_exact_tool_results.py -q
node --test scripts/*.test.js
uv lock --check
```

Call ownership tests must distinguish owner, non-owner administrator and ordinary participant,
new transcript insertion and historical author metadata, live revocation and Redis outages.
Dispatch tests cover strict schemas, approval/resume, stateful errors, frozen citation replay,
cross-round IDs and no repeated execution. Skill tests cover Chinese character offsets, omissions,
prepared OpenClaw roots, pinned versions, disabled discovery and `.skill` round trips. Exercise revision
comparison with a changed draft/version, model controls, tool drafts, ratings, narrow screens and IME.

`package.json` and the root package entries in `package-lock.json` must report `0.12.2`; Python's Hatch
metadata reads that same file. Use npm for `@codemirror/merge`, Mermaid and lock resolution, and uv for
Python lock verification. Check shared standard/slim pins and preserve intentional exclusions.
`valkey-glide-sync>=2.5.2` is required only when using the optional Valkey vector backend; it is not
installed as a new mandatory dependency.

Record source `/api/version`, frontend build and Python wheel metadata checks independently. Run
the standard container separately from a source startup. The release workflow still detects version
changes on pushed `main`, queues releases, limits release notes and uses fixed commit links. Local
integration does not authorize pushing, publishing or deploying. See the current decision record
for actual results and unexecuted service checks.

### Native model connection verification

Use Node 22 and Python 3.12 with already installed dependencies from the setup section (Python 3.11 is also supported by CI).
These commands do not install or download dependencies.
Run from the repository root. Use isolated development data for any application/runtime checks.

```sh
python -m pytest backend/tests/test_native_provider_adapters.py backend/tests/test_native_connections_integration.py backend/tests/test_native_replay_budget.py backend/tests/test_connection_headers.py backend/tests/test_tool_citation_replay.py backend/tests/test_gemini_tool_schema.py backend/tests/test_context_compaction_v2.py backend/tests/test_context_compaction_dispatch.py -q
npm run test:frontend -- --run src/lib/utils/connection-protocol.test.ts src/lib/utils/connection-headers.test.ts src/lib/components/AddConnectionModal.test.ts
npm run check
```

For existing type-check failures, run `npm run check -- --output machine-verbose --no-color`
in both the working tree and an isolated snapshot of the pre-change revision. Use the same Node 22,
installed dependencies, static assets and configuration; let SvelteKit generate declarations in each
workspace and verify they agree. Compare every diagnostic's file, code, message and mapped source
position, preserving duplicate counts and normalizing workspace paths/Windows drive-letter case.
Comparing totals or only changed files does not establish that the full check has no regressions.

| Boundary                                                                              | Coverage                                                                                           |
| ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Native payloads, streams, discovery, thinking and signatures                          | `backend/tests/test_native_provider_adapters.py`                                                   |
| Mocked transport/discovery, dispatch errors and approvals without duplicate execution | `backend/tests/test_native_connections_integration.py`                                             |
| Native wire/signature/media budgeting and multi-round saved replay                    | `backend/tests/test_native_replay_budget.py`                                                       |
| Explicit protocol, defaults and saved configuration                                   | `src/lib/utils/connection-protocol.test.ts`                                                        |
| Actual modal initialization, protocol switching, verification and save handlers       | `src/lib/components/AddConnectionModal.test.ts`                                                    |
| Frontend header validation and native reserved names                                  | `src/lib/utils/connection-headers.test.ts`                                                         |
| Header priority and identity                                                          | `backend/tests/test_connection_headers.py`                                                         |
| Citation prefix/replay and manual Gemini schema profile                               | `backend/tests/test_tool_citation_replay.py`, `backend/tests/test_gemini_tool_schema.py`           |
| V2 accounting and final-send/retry guards                                             | `backend/tests/test_context_compaction_v2.py`, `backend/tests/test_context_compaction_dispatch.py` |

These tests use synthetic fixtures/mocked transports; they do not verify live provider acceptance.
Real-provider checks remain pending; record actual results separately without credentials.
Separately exercise admin connection save/verify, paginated model discovery, text/image streaming and
nonstreaming, approved tools, reload/retry of signed thinking, custom gateway/auth headers, and V2
budget rejection with the intended service. Check Chat Completions/Responses, personal direct and
Ollama regressions. Do not report live verification unless actual calls were performed. See the
[user guide](native-model-connections.md) and [merge follow-up](upstream/v0.12.2-native-connections-followup.md).
