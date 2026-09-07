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
`backend/requirements-min.txt` is marked WIP; do not treat it as the full development environment.

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

The current history includes upstream merges and local Search1API/reasoning-effort changes. Treat
the older documents in `docs/superpowers/` as historical task records. Re-read current files and
`git log` before an upstream merge; do not apply an old version target to a new task.

| Behavior                                                          | Files to inspect together                                                                                                                                                                                                                                                             | Regression coverage                                                                |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Search1API                                                        | `backend/open_webui/retrieval/web/search1api.py`, `backend/open_webui/config.py`, `backend/open_webui/routers/retrieval.py`, `src/lib/components/admin/Settings/WebSearch.svelte`                                                                                                     | `backend/tests/test_search1api.py`, `backend/tests/test_search1api_integration.py` |
| Model reasoning options, effective default, per-user/model memory | `src/lib/utils/reasoning-effort.ts`, `src/lib/components/workspace/Models/ModelEditor.svelte`, `src/lib/components/chat/Chat.svelte`, `src/lib/components/chat/MessageInput.svelte`, `src/lib/components/chat/Placeholder.svelte`, `src/lib/stores/index.ts`, `src/lib/apis/index.ts` | `src/lib/utils/reasoning-effort.test.ts`; manual model/settings/chat checks        |
| Responses reasoning payload mapping                               | `backend/open_webui/utils/responses_reasoning.py`, `backend/open_webui/routers/openai.py`                                                                                                                                                                                             | `backend/tests/test_openai_responses_payload.py`                                   |

Search1API's file named `test_search1api_integration.py` checks source-level contracts. Its passing
result does not prove a live provider, persisted settings, or the UI works. Add runtime evidence when
those boundaries change. Existing frontend helper tests also cover shortcuts and Markdown colon
fences; inspect the actual test files when changing those behaviors.

The fork also adjusts `.github/workflows/docker.yaml`: the current image workflow publishes through
GHCR and has removed Helm chart notification and Docker Hub copy steps. During upstream workflow
merges, review the current triggers, destinations, permissions, and recent history before restoring
any removed publishing steps.
