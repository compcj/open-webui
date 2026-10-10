# Frontend instructions

Applies to `src/` and its descendants, together with the [root guide](../AGENTS.md).

## Work with the existing structure

- The frontend uses Svelte 5, SvelteKit 2, TypeScript, Vite, and Tailwind. Match the component's
  existing style; do not convert unrelated legacy Svelte components to runes as part of a small fix.
- Route composition belongs in `routes/`; reusable UI belongs in `lib/components/`. Reuse API clients
  in `lib/apis/`, URL constants in `lib/constants.ts`, stores in `lib/stores/`, and pure helpers in
  `lib/utils/`. Use the existing `$lib` alias.
- Preserve authentication headers and existing API error handling. The frontend uses relative URLs;
  Vite proxies backend routes according to `WEBUI_BACKEND_URL` (default `http://localhost:8080`).
- Coordinate changes to model metadata, user settings, request payloads, and websocket/streaming
  events with their backend producer or consumer. Distinguish absent values from explicit overrides.
- Put user-visible text through the existing i18n context (`$i18n.t(...)`). Preserve translation
  interpolation variables and avoid mass changes to locale files unrelated to the task.
- `lib/utils/skills.ts` owns OpenClaw `SKILL.md` parsing/serialization and zip bundle extraction
  (single parsing pipeline for file, zip, and URL imports). The workspace skill editor keeps
  `meta.openclaw` intact on submit and renders its OpenClaw section (gating summary, non-secret
  `env`/`config` bags, explicit dependency install). The `$` mention picker (`IntegrationsMenu.svelte`)
  hides `user-invocable: false` skills. Preserve these contracts when touching skills UI.
- `npm run i18n:parse` regenerates and formats catalogs from `src/**/*.{js,ts,svelte}`. Inspect
  added and removed keys, preserve existing translations and placeholders, and verify a second run
  produces no changes before pushing UI text changes. Dynamic message keys (for example, image-engine
  validation errors passed to `t(error.message)`) need static `// t('key')` extraction markers.
- Reuse existing components and styles. Check keyboard interaction, focus, loading/error states,
  narrow screens, and dark mode when affected by a UI change.

## Integration contracts

- Preserve the fork composer layout and remembered controls when adding upstream interactions.
  Check follow-up suggestions with Tab, IME composition, and narrow screens.
- Submit only changed top-level UI settings: omission preserves a value, explicit `null` resets it.
  Nested preference maps remain whole fields. Keep ordered saves, account isolation, and error
  feedback; do not send a stale snapshot of every setting.
- Workspace auto-discovery is administered through `GET/POST /api/v1/configs/skills` using
  `AUTO_DISCOVERY_SKILL_IDS`. The UI is not an access-control boundary. Expose terminal
  `config.auto_discover_skills` only for admin-managed connections, and clear stale discovery
  results on terminal changes. Preserve manual selections and OpenClaw metadata round trips.
- Admin Web Search exposes a global Tavily `basic`/`advanced` search-depth selection through
  `web.TAVILY_SEARCH_DEPTH`. Preserve its saved value on reload and provider changes, and keep it
  independent of the web loader's `TAVILY_EXTRACT_DEPTH`. New installations default to `basic`;
  this is an admin setting, not a per-user or per-conversation override.
- Personal direct-integration configuration follows `direct.integrations.enable`; hiding the entry
  must not disable existing connections or change the independent Direct API Chat policy.
- Image verification uses `POST /api/v1/images/verify` with `{engine, url, key?}`. Keep named engine
  generation/edit settings, descriptions, edit disablement, attachment deduplication, and compact
  image context for replay.
- Citation cards and inline numbers use the existing `source` events in first-seen order within
  each assistant reply. Keep tool output's optional `citation_context` and `citation_sources` through
  save/load, output updates and temporary-chat requests. The backend owns frozen model citation
  text; the frontend must not regenerate it or reorder sources. See the
  [citation follow-up](../docs/upstream/v0.11.4-tool-citation-cache-followup.md).
- Terminal previews remain isolated regardless of remembered user preferences; the backend rejects
  same-origin port proxies. Do not restore a setting that grants terminal HTML the WebUI origin.

See [version decisions](../docs/upstream/v0.11.4-decisions.md) for rationale and future merge checks.

## Context compaction

- Admin Interface settings expose off/legacy/V2 and preserve the inactive algorithm's values.
  New V2 parameters are global, not user/chat overrides; existing threshold overrides apply only
  to legacy. Saved admin mode wins over the compatibility enable flag.
- Model `meta.context_window` is optional positive integer metadata, inherited from the base model
  then the global default. Keep it out of inference params and preserve import/export and model ACL.
- Use backend `context_usage`/`context_compaction` events and model-specific usage queries as the
  source of V2 counts. Reject stale model/mode/branch results on selection changes; refresh after history edits. Off must not display
  a legacy checkpoint as full-history usage. Distinguish saved-history estimates and provider input
  usage; never substitute cumulative billing totals for V2 occupancy.
- Browser-direct manual compaction sends the selected descriptor and current socket ID; usage queries
  send the descriptor without depending on socket readiness. Keep server-model requests compatible.
- Render compaction lifecycle events in the corresponding response's localized status history,
  including when tool status updates are disabled. `/status` shows backend budget details; do not
  confuse cumulative billing tokens with the automatic trigger.
- Expose V2 tool budgets as admin-only settings (enabled by default within V2, 10% individual/30%
  batch of backend-resolved `C`; integer `1 <= individual <= batch <= 100`). Preserve inactive values.
  Render only new current-reply `tool_result_compaction` summary/excerpt operations and necessary
  errors, even when tool statuses are disabled. Original/exact-read/cache bookkeeping is silent.
  Filter by `source_message_id`; legacy output/call IDs may prove another owner, while unknown legacy
  records stay in place. Apply the same filter to raw-view buttons. Upsert `operation_id` progress/results
  without regressing completed operations; preserve valid records on earlier replies. Read full batch
  counts from backend usage, including after refresh, rather than counting visible notices. Original-result viewing uses authenticated, branch-scoped bounded
  pages from `/api/v1/chats/{chat_id}/tool-results/{result_id}`; render text without HTML execution,
  handle stale requests/errors and distinguish summaries, incomplete excerpts and exact rereads.
- Expose `CONTEXT_COMPACTION_V2_SUMMARY_OUTPUT_PERCENT` as an admin-only integer 1–100, default 5.
  Its denominator is the resolved summary-model window. Explain that percentage tokens and the
  explicit/inherited absolute output setting take the larger value, then capacity/tool limits may lower
  the actual allowance. Ordinary chat output reservations are unchanged. Keep Chinese/English in sync.
- `/compact` follows the global mode and stays disabled during generation. Maintain Chinese and
  English labels, parameter validation feedback, inherited values and narrow-screen controls.
- See [feature guide](../docs/context-compaction.md) and [verification map](../docs/DEVELOPMENT.md#context-compaction-verification).

## Formatting and tests

- `.prettierrc` defines tabs, single quotes, no trailing commas, 100-column print width, and LF.
  Use the installed Prettier and Svelte plugin on explicit changed paths.
- Add focused Vitest coverage beside helpers as `*.test.ts` for changed behavior. Prefer observable
  inputs/outputs over assertions tied to implementation text. Existing source-contract tests are
  limited evidence and do not replace an end-to-end check of an affected user workflow.
- From the repository root, run the relevant tests, then `npm run check` for type/component changes.
  Example: `npm run test:frontend -- --run src/lib/utils/reasoning-effort.test.ts`.
  Confirm that the intended test file and assertions ran.
- Run `npm run build` for changes affecting bundling, routing, workers, or assets. It prepares
  Pyodide assets and can require network access and substantial memory; report failures accurately.
- Preserve per-model reasoning options, effective defaults, remembered user selections, and
  override precedence when touching `Chat.svelte`, `MessageInput.svelte`, `Placeholder.svelte`,
  the model editor, stores, or `lib/utils/reasoning-effort.ts`.

See [development commands and fork regression locations](../docs/DEVELOPMENT.md) for details.
