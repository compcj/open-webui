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
- Put user-visible text through `$i18n.t(...)`. Keep static `// t('key')` extraction markers for
  messages looked up dynamically, such as image-engine validation errors passed to `t(error.message)`.
  Adding, changing, moving or deleting a `t(...)` call can change every configured locale. The parser
  scans `src/**/*.{js,ts,svelte}` and drops keys with no static reference. Review generated additions
  and removals across all locales and preserve existing translations and `{{placeholders}}`.
- Skills edit only `data.files`. `lib/utils/skills.ts` adapts legacy OpenClaw packages and preserves
  unknown metadata; canonical `SKILL.md` supplies frontmatter. Keep non-secret env/config controls,
  gating display, export and explicit dependency install. Use `expected_version_id`; surface 409
  conflicts/refresh-required errors and never retry a stale edit by dropping its version. New URL
  imports request `response_format=packages`; keep multi-skill selection and file/history UI.
  The integrations menu filters `user-invocable: false` from OpenClaw compatibility metadata;
  do not assume every mention picker applies that filter or treat it as access control.
- Reuse existing components and styles. Check keyboard interaction, focus, loading/error states,
  narrow screens, and dark mode when affected by a UI change.

## Integration contracts

- Ordinary models use upstream model controls. Use `model-controls.ts` to migrate old reasoning
  configuration, custom values and remembered choices to option IDs; explicit new-format maps win.
  Effective order: chat control → remembered control → chat advanced → personal advanced →
  control/model default → global. Reset consumes old memory. Direct/pipe models retain the legacy
  fallback. Show the effective value, not just an empty control label.
- Shared participants use their own preferences and variables. Do not load/save the owner's private
  advanced settings as participant defaults. Approval/stop UI belongs to the reply author; `/compact`
  is owner-only. Shared V2 counts are scoped to the chat/model/branch, with no participant dimension.
- Preserve remembered controls in the tabbed workspace, split panes, Controls menu and + menu.
  Selecting an @ model switches the chat's model for subsequent turns. Regenerate with the
  current model in the selected column. Show Input Selections defaults to off and never clears
  the selected tools, skills, filters or capabilities. Check Tab suggestions, IME and narrow screens.
- Below the `md` breakpoint, keep the composer's plus menu and input selections on their own row,
  above the model selector, model controls and voice/send actions. From `md` up, use one row.
- `meta.intelligence_score` is optional display metadata. The model editor saves integers from 0 to
  99 and clears the field to `null`. Show zero and hide absent or invalid values. Use the same
  accessible badge before the name in the model picker and `@` suggestions. Keep the score out of
  inference parameters.
- Submit only changed top-level UI settings: omission preserves a value, explicit `null` resets it.
  Nested preference maps remain whole fields. Keep ordered saves, account isolation, and error
  feedback; do not send a stale snapshot of every setting.
- Remember explicit tool-feature choices per user and single model (including an `@` override);
  multi-model changes stay local and availability intersects every selected model. Existing chat
  drafts override remembered choices, while new chats start from account preferences. Preserve
  paused choices when capability or master switches disable a feature. A remembered search toggle
  is not consent: keep confirmation and pending-enable invalidation on model/account changes.
- Gemini tool-schema conversion is explicit: server models use metadata/base-model inheritance;
  browser-direct models use the selected connection's map keyed by unprefixed provider model ID.
  Default means no conversion. Never infer it from a model name, URL, provider metadata or a
  same-ID server model; keep separate personal connections isolated. See the
  [compatibility guide](../docs/gemini-tool-compatibility.md).
- Model request compatibility keeps `meta.reasoning_content_replay` default on and
  `meta.extra_body_compatibility` default off. Preserve explicit false through edit/save/reload,
  keep read-only controls disabled and describe top-level precedence for extra-body expansion.
  These switches are independent of reasoning controls, tool-schema profiles and native signatures.
- Workspace auto-discovery is administered through `GET/POST /api/v1/configs/skills` using
  `AUTO_DISCOVERY_SKILL_IDS`. The UI is not an access-control boundary. Expose terminal
  `config.auto_discover_skills` only for admin-managed connections, and clear stale discovery
  results on terminal changes. Preserve manual selections and OpenClaw metadata round trips.
- Admin Web Search exposes a global Tavily `ultra-fast`/`fast`/`basic`/`advanced` search-depth selection through
  `web.TAVILY_SEARCH_DEPTH`. Preserve its saved value on reload and provider changes, and keep it
  independent of the web loader's `TAVILY_EXTRACT_DEPTH`. New installations default to `basic`;
  this is an admin setting, not a per-user or per-conversation override.
- Admin Web Search exposes Search1API `/search` and `/ask` through `web.SEARCH1API_ENDPOINT`
  (`search`/`ask` values, default `search`). Preserve the global selection on save, reload and
  provider changes; an absent/null field displays `search` for older configurations. Keep English
  and Chinese descriptions aligned: `/ask` selects sources automatically and returns up to ten
  results. This adds no per-user, conversation or tool-call override. See
  [verification](../docs/DEVELOPMENT.md#search1api-endpoint-verification).
- Personal `direct.integrations.enable` is a hard switch: hide configuration and omit saved personal
  tools/terminals when disabled, including admins. Retain settings for re-enable; keep Direct API Chat independent.
- Tether uses the separate `enable_tether` capability from the server. Integrations writes
  `ENABLE_TETHER` and `TERMINAL_PREVIEW_BASE_URL` as partial admin Connections updates.
  Tether terminal operations also require tool servers. The presence of a desktop bridge grants
  no connection capability.
- Image verification uses `POST /api/v1/images/verify` with `{engine, url, key?}`. Keep named engine
  generation/edit settings, descriptions, edit disablement, attachment deduplication, and compact
  image context for replay.
- Citation cards and inline numbers use the existing `source` events in first-seen order within
  each assistant reply. Keep tool output's optional `citation_context` and `citation_sources` through
  save/load, output updates and temporary-chat requests. The backend owns frozen model citation
  text; the frontend must not regenerate it or reorder sources. See the
  [citation follow-up](../docs/upstream/v0.11.4-tool-citation-cache-followup.md).
- Proxy browser and port previews require a backend ticket for an independently configured
  hostname. Renew active tickets every minute and clear the iframe on failure or expiry. Sandbox
  the iframe without same-origin or top-level navigation and omit referrers. Validate bridge
  messages against the current frame, opaque origin and ticket nonce. Chrome uses a separate
  authorized control stream and renews it every minute. System-terminal HTML displays source and
  SVG images use signed delivery. Keep preview URLs, credentials and temporary IDs out of layouts.
- When stopping backend tasks, end local generation and release a queued prompt only after acknowledgement.
  Preserve retryable state on failure and reject results after navigating to another chat. Only an
  explicit stream `error: null` clears a prior error. Hide superseded split-tool placeholders only
  in rendered output; preserve their raw replay data. Show Thinking for pending empty output,
  including empty native envelopes, but not while waiting for `ask_user` or after completion.
- Builtin `ask_user` accepts 1–3 questions. Each offers 2–4 presets plus a write-in answer, or a
  multiline field when options are absent or empty. Enter inserts a newline. Require a nonblank
  answer per question and preserve pasted whitespace and leading zeros. In main and side chats,
  bind restored prompts to the reply and tool call. Enable write-ins on older builtin prompts in
  the UI while preserving stored arguments and honoring native and external `allow_other` flags.

See [version decisions](../docs/upstream/v0.13.3-decisions.md) for rationale and future merge checks.

## v0.12.2 UI boundaries

- Start Standard/Realtime calls only for the chat owner and end active calls on switches to another
  owner's chat. Preserve author-filtered historical transcripts and dedicated persistence APIs.
- Before applying a revision comparison, verify both the current draft and selected version are
  unchanged. Preserve optimistic skill versions, incremental settings saves and model-control precedence.
- Terminal reads, comparison reads, saves, notebook execution, batch deletes and ZIP downloads carry
  captured terminal and chat identities. Cancel invalidated reads and reject late completions on
  connection/chat changes; dispose a notebook kernel through its original connection even if it
  finishes starting after unmount. Signed delivery URLs retain their original identity; HTML/SVG
  stay isolated.
- Skills support `.skill` as ZIP-compatible packages. Do not turn overview truncation or search omissions
  into editable content; file edits still operate on canonical `data.files` with `expected_version_id`.

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
  counts from backend usage, including after refresh, rather than counting visible notices. Selected
  historical tool versions are immutable replay context; current caps only admit new results. Keep
  original/exact/cache totals in accounting without emitting new conversation notices or changing
  the last admission clock. Pair cancellation/failure completion with its source and operation IDs,
  localize saved-output retry guidance, and keep errors visible even when ordinary tool statuses are off.
  In `/status`, label `tokens / threshold` and `percent` as the automatic compaction trigger fraction;
  distinguish configured window `C`, input budget `B`, distance to trigger `T`, and available input
  `B - tokens`. Derive absent compatible input-remaining values only from known budget/counts;
  otherwise omit them. Show last-batch source reply, historical/unknown provenance, saved nanosecond
  decision time, completeness, then-effective limits, allocations and reduction reasons in compact
  expandable details. Do not present a prior decision as a new reduction under current settings.
  Surface backend `context_usage.accounting_error` as a statistics warning, not a tool notice.
  Original-result viewing uses authenticated, branch-scoped bounded
  pages from `/api/v1/chats/{chat_id}/tool-results/{result_id}` for saved chats or
  `/api/v1/temporary-contexts/{id}/tool-results/{result_id}` for temporary scopes; render text without HTML execution,
  handle stale requests/errors and distinguish summaries, incomplete excerpts and exact rereads.
- Expose `CONTEXT_COMPACTION_V2_SUMMARY_OUTPUT_PERCENT` as an admin-only integer 1–100, default 5.
  Its denominator is the resolved summary-model window. Explain that percentage tokens and the
  explicit/inherited absolute output setting take the larger value, then capacity/tool limits may lower
  the actual allowance. Ordinary chat output reservations are unchanged. Keep Chinese/English in sync.
- `/compact` is owner-only, follows the global mode and stays disabled during generation. Maintain Chinese and
  English labels, parameter validation feedback, inherited values and narrow-screen controls.
- See [feature guide](../docs/context-compaction.md) and [verification map](../docs/DEVELOPMENT.md#context-compaction-verification).

## Knowledge write controls

`knowledge_write` is a separate context-tool preference, default false in old and new chat inputs.
Model `meta.builtinTools.knowledge_write` defaults to permitted selection, but never implicitly
enables the outgoing request. Apply builtin capability, knowledge-read and write-category gates
across every selected model. Turning reading off masks writing without erasing the remembered
choice; explain the dependency in the control.

Keep the request field, home/chat composers, restored inputs, reset flows and model controls aligned.
Persist explicit choices per user/model through the existing ordered settings queue; multi-model
choices stay local and account switches cancel stale saves and clear prior-account overrides.
Maintain English and Chinese labels, descriptions and dependency hints, including keyboard and
narrow/dark layouts. See [user guide](../docs/knowledge-write-tools.md) and
[verification](../docs/DEVELOPMENT.md#knowledge-write-verification).

## Formatting and tests

- `.prettierrc` defines tabs, single quotes, no trailing commas, 100-column print width, and LF.
  Use the installed Prettier and Svelte plugin for targeted checks during editing. For UI text or
  catalog changes, run the [full format and translation checks](../docs/DEVELOPMENT.md#frontend-format-and-translation-verification).
  Include required repository-wide formatting and generated catalogs with the source change. Local
  Prettier checks and hand edits to `en-US` and `zh-CN` can miss changes elsewhere. A second pass
  of `npm run format` and `npm run i18n:parse` must produce no further tracked changes.
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

## Native server connection controls

Keep protocol selection explicit in admin Connections: Chat Completions (empty/default), Responses,
Gemini and Anthropic. Accept legacy `chat_completions`; never guess from model names or URLs.
Fill protocol default URLs only when blank, retain manual model IDs and custom gateways, and reject
native+Azure. Native `api_key` authentication is distinct from existing bearer/none/session/oauth.
Preserve custom header and UA/session controls: explicit values override matching custom headers;
an absent opt-in does not synthesize a connection session header. Keep temporary-chat session identity stable from its original
root message, independent of socket reconnections, and reset it for the next chat. Do not expose
native protocols as new personal browser-direct options. Preserve optional native `reasoning_details` through streaming, saves,
approvals and reload; display text must not replace exact signed blocks. See the
[native guide](../docs/native-model-connections.md) and
[verification](../docs/DEVELOPMENT.md#native-model-connection-verification).

## Temporary V2 and v0.12.4 controls

Own a separate `TemporaryContextController` in each mounted Chat and SideChat. Never persist its identity or raw history
to account/localStorage stores. Sync the full raw parent graph before generation; every model owns
its reply/task. Validate actor, id, epoch, model, task and sequence before applying events; callbacks
also require the current socket generation and an unclaimed live RPC. Rebinding cancels old callbacks.
Manual compact may target a user leaf. Its callback identity is local to that request.

State reconciliation may recover buffered raw output, never repeat tools or resume temporary
approvals. New chat/navigation/account changes invalidate pending results. Close and stop wait for
executor acknowledgement; keep page history on failure. Promotion and JSON export share the raw
projection, preserving provider functions, citations and native signatures while stripping derived
identities. Retry promotion with the same idempotency key; a lost response must not create duplicates.

Side chats project inherited messages through the same raw copy rules. Strip execution, checkpoint,
selection and Agent resume state. Keep temporary side drafts and history in memory only. Agent
models require a saved chat and a single selection. An unavailable Agent keeps its saved identity
and offers a manual model switch. Agents own their context and disable WebUI compaction,
regeneration, continuation of old replies and in-place branching.

Controls/Params permissions hide panels but retain valid values. Skills eagerly load selected/model
preset entries by default; mentions remain eager with lazy enabled. Access saves immediately through
dedicated endpoints. Shared regeneration creates a branch without taking another viewer's selection.
Register `knowledge_write` among visible composer controls; hiding it never clears the saved choice.
See [temporary V2](../docs/temporary-context-v2.md) and
[current decisions](../docs/upstream/v0.13.3-decisions.md).

## v0.13.4 workspace controls

- Chat tabs own their selected models, draft, running task IDs and temporary context. Persist saved view
  layout only. Project raw history before opening a side conversation. Keep executable Agent, steering,
  compaction and result-selection state out of that projection.
- Direct and Tether model choices coexist. Preserve offline Agent IDs until an authorized live catalog offers
  one verified mapping. Terminal attachment upload defaults to WebUI for both connection types. Pin terminal
  targets and file operations to the actor, model, instance and chat captured at selection.
- Explicit image models and named shortcuts are mutually exclusive. Check the current user's access before
  listing them. Display fork and official update status independently and link each to its own release.
- Scope DOM listeners, browser approvals, stop requests and compaction progress to the originating reply or
  tab. Preserve the independent preview origin and terminal directory/background-file behavior.

## v0.13.3 workspace and preview controls

Keep the upstream tabs/split view, Controls and plus-menu interactions. Show Input Selections defaults
off. A model mention changes subsequent turns; regenerate with the current slot model while preserving
explicit choices, remembered settings, reset and account isolation. Each mounted Chat owns its tasks,
models and temporary controller; persisted layouts contain neither temporary raw history nor scope,
execution, selection, Agent resume or preview capability identities.

Admin Connections exposes default-off Tether independently of Direct Integrations. Omitted fields
must not clear saved configuration. Saved manual personal terminals/tools still obey Direct Integrations;
server terminals and server-authorized Tether discovery have their own permissions.

Only the global admin flag `show_files_on_terminal_select` (default false) opens Files when a user
selects a system or direct terminal at 1024px or wider. Ignore the old personal preference.
`terminal:run_command` refreshes mounted directory tabs for the selected terminal in place, preserving
their paths and focus. In tab mode, `display_file` opens or reuses the requested file in the background
of the originating chat and terminal. Preserve the requested page, active pane and navigation history.
Inline results stay in the message. A matching filesystem attachment only refreshes its existing preview.

Advertise `terminal_browser_tab` only from the main chat for its selected, enabled terminal. Handle
`browser_open_tab` on the reply author's socket, claiming the RPC for temporary chats. Recheck the
actor, chat and selected terminal once the view and terminal features load. Open one blank tab,
request Chrome if available and return that tab's session ID and actual mode. Model interaction
requires Chrome. If creation is unconfirmed, inspect `browser_tabs` before retrying.

Proxy previews require the configured independent host. Request opaque tickets from the main API,
renew active views every 60 seconds, and invalidate stale asynchronous callbacks on terminal/context,
actor or pane changes. Port requests pass the Browser tab's existing session ID, including renewal;
normal tab closure deletes that same session. Do not create a hidden duplicate session per port tab.
Chrome control tickets remain separate. Never use same-origin terminal HTML or port proxies as fallback.

Keep both iframe sandbox and response CSP without allow-same-origin, top navigation or DOM storage.
Use no-referrer and check postMessage source window, opaque origin, ticket identity and message shape.
The terminal's private Cookie jar and internal WS redirects follow the confirmed upstream browser
semantics; a frame still cannot access WebUI storage or credentials. Report proxy failures explicitly.

Native Agents retain their saved identity when offline. Forced stop may preserve partial output and
mark a missing remote tail; do not clear its pending state or advance the queue until the backend has
confirmed remote termination. Never infer completion from a local socket close, 404 or timeout.
See [preview deployment](../docs/terminal-preview.md) and [Agent lifecycle](../docs/tether-agents.md).
