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
- Tavily search depth spans `config.py`, `retrieval/web/tavily.py`, and `routers/retrieval.py`.
  Preserve `TAVILY_SEARCH_DEPTH` (`basic` by default, `advanced` opt-in), the per-key setting
  `web.search.tavily_search_depth`, and its propagation through the shared search entry point.
  The admin configuration API accepts only these two values; omission or `null` preserves the
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
  failure, and the trust model: `/load/url` requires admin or `workspace.skills_import` (same
  trusted-SSRF rationale as tools), `install_deps` requires skill write access and runs declared
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
  WebSocket `1013`, without deleting valid login cookies. Unconfigured Redis is a separate case;
  invalid/revoked credentials remain denied. Check every caller's exception handling.
- Image verification uses `POST /api/v1/images/verify` with `{engine, url, key?}`. Preserve named
  engine settings and context-only image deduplication across normal, approved, and replayed tools.

Terminal AGENTS.md is a synthetic user message. Enrich real user attachment context before injecting
it; otherwise ordinal pairing with stored chat history can attach one turn's files to another turn.
Keep `backend/tests/test_terminal_agents_attachments.py` when changing either pipeline stage.

See [version decisions](../docs/upstream/v0.11.4-decisions.md) for rationale and upgrade boundaries.

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

- Preserve off/legacy/V2 compatibility and the original implementation in `utils/context_compaction.py`.
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
  single-call input usage, never cumulative billing totals. Reserve configured output only.
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
- V2 tool budgeting uses `utils/tool_result_budget.py`, exact provenance in `tool_result_sources.py`
  and branch/source-validated storage in `tool_result_store.py`/`models/chat_tool_results.py`.
  Resolve `C` with inheritance and smaller `num_ctx`; derive individual/batch caps from `C`, reserve
  incoming desired tokens and compact older complete history before summarizing tools. Protect the
  incoming batch/latest user/incomplete groups, allocate fairly and verify the full final input.
  Native, approved, injected and code results keep original content and call pairing. Apply historical
  checkpoints before tool projections; keep raw source identities for checkpoint validation.
  Summary failure falls back to explicitly incomplete exact excerpts with a raw result ID; exact
  `read_tool_result` passages may be resized but never summarized. Register this auxiliary tool only
  from trusted saved-chat V2 state. Raw reads require chat access, current branch ancestry and unchanged
  sources. Cache projections by model/branch/task/policy/budget; no transaction spans model generation.
  Persist tool-result status metadata, clean both tool tables on chat deletion, and preserve off/legacy
  and disabled-tool-budget behavior. Neither projection nor overflow retry re-executes tools.
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

The active backend CI checks formatting and selected logic errors; it currently does not run pytest.
Do not use a green CI badge as proof that backend behavior was tested.
