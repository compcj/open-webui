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

## Implementation boundaries

- Match nearby patterns and the checked-in formatter configuration. Keep diffs focused; do not
  reformat unrelated files or introduce dependencies for a small change without a concrete need.
- Preserve fork behavior when refactoring or merging upstream. In particular, check Search1API and
  model reasoning-effort behavior; [the development guide](docs/DEVELOPMENT.md) maps their files/tests.
- Workspace skills support two formats: native OpenWebUI skills and OpenClaw-compatible `SKILL.md`
  skills. `SkillMeta.openclaw` is a permissive JSON bag (`frontmatter`, `files`, `source`, `config`,
  `env`) — keep it schema-tolerant and preserve its round-trip through import, edit, and
  "Export SKILL.md". Import paths (file/zip plus `POST /skills/load/url` for direct links, GitHub,
  and ClawHub refs) prefill the create editor. At chat time `utils/skills_runtime.py` enforces
  OpenClaw gating against the configured Open Terminal, syncs bundled files there, and substitutes
  `{baseDir}`; any terminal failure must degrade to the previous default loading, never break the
  chat. `POST /skills/id/{id}/install_deps` runs declared install specs only on explicit user
  action. Secrets never go into the shareable skill meta.
- Trace frontend/backend contracts together: request fields, defaults, permissions, streaming events,
  persistent settings, and error responses. Test the affected boundary, not only one side.
- Keep `package.json` and `package-lock.json` consistent. For Python dependency changes, inspect
  `pyproject.toml`, `uv.lock`, and `backend/requirements.txt`; also check `requirements-min.txt` when
  applicable. Use the relevant package manager to update locks rather than editing resolution data.
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
