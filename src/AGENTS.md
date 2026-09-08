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
- `npm run i18n:parse` regenerates and formats catalogs. Inspect its diff; the current parser input
  is `src/**/*.{js,svelte}`, so do not assume a string added only to a `.ts` file will be extracted.
- Reuse existing components and styles. Check keyboard interaction, focus, loading/error states,
  narrow screens, and dark mode when affected by a UI change.

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
