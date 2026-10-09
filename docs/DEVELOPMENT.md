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

This is the frozen-lock install used by frontend unit-test CI and Docker. It replaces `node_modules`;
coordinate it with other workers in a shared checkout. The format/build CI job instead uses
`npm install --force`. Do not switch package managers or regenerate the lockfile incidentally.

For a source-only Python environment with uv installed:

```sh
uv sync --locked --no-install-project --python 3.11
```

This installs locked dependencies and the default development group into `.venv` while omitting the
project itself. `--locked` checks lock consistency; `--no-install-project` avoids this project's
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

| Purpose                    | Command (repository root)                                                                       | Meaning / limitation                                                       |
| -------------------------- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Frontend suite, one run    | `npm run test:frontend -- --run`                                                                | Confirm expected tests were collected; the script permits no tests         |
| Focused frontend test      | `npm run test:frontend -- --run src/lib/utils/reasoning-effort.test.ts`                         | Model reasoning selection/default helpers                                  |
| Svelte/TypeScript          | `npm run check`                                                                                 | Runs SvelteKit sync and svelte-check; writes generated `.svelte-kit` files |
| Frontend production build  | `npm run build`                                                                                 | Runs Pyodide preparation, then Vite; network and memory may be required    |
| Backend helper tests       | `python -m pytest backend/tests -q`                                                             | No live model/search service required by current tests                     |
| Focused backend regression | `python -m pytest backend/tests/test_openai_responses_payload.py -q`                            | Isolated Responses payload conversion                                      |
| Python format              | `python -m ruff format --check <changed-python-files>`                                          | Non-mutating; replace placeholder with explicit paths                      |
| Python CI logic subset     | `python -m ruff check --select=F --ignore=F401,F403,F405,F541,F811,F841 <changed-python-files>` | Same selected rules as active backend CI                                   |
| Frontend lint              | `npx --no-install eslint <changed-frontend-files>`                                              | Non-mutating; use installed tooling and explicit paths                     |
| Markdown/frontend format   | `npx --no-install prettier --check <changed-files>`                                             | Uses `.prettierrc` and its Svelte plugin                                   |
| Patch hygiene              | `git diff --check`                                                                              | Whitespace/conflict-marker check on tracked diffs; inspect new files too   |

On restricted Windows checkouts, Vitest may fail while scanning an inaccessible ignored directory
such as `.pytest_cache`. `npm run test:frontend -- --run --dir src` limits discovery to frontend
sources. Report that narrower scope and the original error; do not delete shared caches or change
permissions as an incidental test fix. Do not use this scope if the task introduces tests elsewhere.

`npm run build` invokes [scripts/prepare-pyodide.js](../scripts/prepare-pyodide.js), which downloads
packages and refreshes `static/pyodide/`. Review changes to the tracked `pyodide-lock.json`.
CI gives the build an 8192 MB Node heap; on a memory-constrained machine report the limitation
instead of claiming the build passed. `hatch_build.py` also invokes npm install/build when packaging
the Python project; `pip install .` is not a lightweight Python-only setup.

## What the existing CI actually checks

- [Frontend CI](../.github/workflows/frontend.yaml) runs Node 22. One job runs installation,
  `npm run format`, `npm run i18n:parse`, `git diff --exit-code`, and a production build. Another
  runs Vitest. The active workflow does not run `npm run check` or ESLint.
- [Backend CI](../.github/workflows/backend.yaml) runs Ruff formatting and selected `F` logic checks
  on Python 3.11/3.12. It does not currently run pytest or start the application.
- Files ending in `.disabled` under `.github/workflows/` are not active workflows.
- `npm run lint:frontend`, `npm run format`, `npm run format:backend`, `npm run i18n:parse`, and the
  configured pre-commit hooks modify files. Run targeted checks first and inspect generated diffs.
- Docker/release workflows can publish artifacts on configured branches/tags. Inspect the relevant
  workflow before treating a push or manual dispatch as a validation-only operation.

For documentation-only changes, validate links, paths, formatting, and the diff. Application startup
and a full dependency install are not needed. For behavior changes, select meaningful tests and
exercise the changed workflow. Record baseline failures separately; never widen a task into a
repository-wide cleanup solely to make an unrelated check green.

## Fork regression map

Read [the upstream merge index](upstream/README.md) and the
[v0.11.4 decisions](upstream/v0.11.4-decisions.md) before comparing or merging upstream.
Older documents in `docs/superpowers/` describe historical tasks and do not set the current baseline.
Official commit IDs and actual ancestry, not local tag names alone, determine an upgrade target.

| Behavior                              | Implementation boundary                                                          | Regression starting point                                                                                                                                                                    |
| ------------------------------------- | -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Search1API                            | `retrieval/web/search1api.py`, retrieval/config routes, admin WebSearch          | `backend/tests/test_search1api.py`, `backend/tests/test_search1api_integration.py`                                                                                                           |
| Tavily search depth                   | `config.py`, `retrieval/web/tavily.py`, retrieval/config routes, admin WebSearch | `backend/tests/test_tavily.py`, `backend/tests/test_tavily_config.py`                                                                                                                        |
| Reasoning effort and Responses        | Model metadata/defaults, chat controls, `utils/responses_reasoning.py`           | `src/lib/utils/reasoning-effort.test.ts`, `backend/tests/test_openai_responses_payload.py`                                                                                                   |
| Skills / OpenClaw                     | Admin discovery config, skill ACL, middleware, runtime, import/editor/export     | `backend/tests/test_skill_discovery_policy.py`, `backend/tests/test_skills_runtime.py`, `src/lib/utils/skills.test.ts`, `src/lib/utils/skill-discovery.test.ts`                              |
| Tool preferences and settings         | Chat/SettingsModal, queued top-level UI patches, backend user settings           | `src/lib/components/chat/SettingsModal.test.ts`, `src/lib/utils/ui-settings.test.ts`, `src/lib/utils/tool-feature-preferences.test.ts`, `src/lib/components/chat/Chat.tool-features.test.ts` |
| Knowledge, memory, notes              | Feature controls, explicit attachments, builtin-tool permission checks           | `backend/tests/test_chat_tool_features.py`                                                                                                                                                   |
| Search/page evidence                  | Builtin web tools, source conversion, escaped metadata, stable numbering         | `backend/tests/test_builtin_web_tools.py`, `backend/tests/test_web_tool_citations.py`                                                                                                        |
| Image engines and context             | Admin engine editor, POST verify, generation/edit tools, persisted tool output   | `backend/tests/test_image_generation_engines.py`, `backend/tests/test_image_tool_context.py`, `backend/tests/test_comfyui_errors.py`                                                         |
| Terminal delivery and identity        | Proxy routes, signed references, HTTP/WS ownership and context                   | `backend/tests/test_terminal_file_routes.py`, `backend/tests/test_terminal_websocket.py`, `src/lib/apis/terminal/index.test.ts`                                                              |
| Terminal instructions and attachments | AGENTS.md per-turn loading after real user attachment pairing                    | `backend/tests/test_terminal_agents_attachments.py`, `backend/tests/test_skill_discovery_policy.py`                                                                                          |
| Composer suggestion interaction       | Real ProseMirror plugin, Tab, composition events and touch                       | `src/lib/components/common/RichTextInput/AutoCompletion.test.ts`                                                                                                                             |
| Redis revocation failures             | HTTP authentication, login cookies, terminal WebSocket retry errors              | `backend/tests/test_auth_revocation.py`                                                                                                                                                      |
| Tika versions and JSON                | Extraction loader response negotiation and version-specific endpoint             | `backend/tests/test_tika_loader.py`                                                                                                                                                          |

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
[upgrade record](upstream/v0.11.4-decisions.md#升级与回滚). Existing PostgreSQL vector indexes require
explicit inspection before deciding on a rebuild; this merge does not automatically rebuild them.

The fork also adjusts `.github/workflows/docker.yaml`: the current image workflow publishes through
GHCR and has removed Helm chart notification and Docker Hub copy steps. During upstream workflow
merges, review the current triggers, destinations, permissions, and recent history before restoring
any removed publishing steps.

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
