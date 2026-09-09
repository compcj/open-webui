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
  failure, and the trust model: `/load/url` requires admin or `workspace.skills_import` (same
  trusted-SSRF rationale as tools), `install_deps` requires skill write access and runs declared
  specs only on explicit request.

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

The active backend CI checks formatting and selected logic errors; it currently does not run pytest.
Do not use a green CI badge as proof that backend behavior was tested.
