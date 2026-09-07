# Working across agents, tools, and sessions

[AGENTS.md](../../AGENTS.md) is the shared entry point. The frontend and backend have scoped guides;
this document explains how to load them and transfer work. No particular model, plugin, account,
or orchestration framework is required.

## One source of repository rules

| Tool / environment                  | Repository entry                                  | How to use it                                                                            |
| ----------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Tools with native AGENTS.md support | `AGENTS.md`, `src/AGENTS.md`, `backend/AGENTS.md` | Load root guidance and applicable directory guidance                                     |
| Claude Code                         | `CLAUDE.md`                                       | Imports root guidance with `@AGENTS.md`; explicitly read scoped guides for touched paths |
| Gemini CLI                          | `GEMINI.md`                                       | Imports root guidance with `@AGENTS.md`; explicitly read scoped guides for touched paths |
| Cursor                              | Root and scoped `AGENTS.md` files                 | Uses native support; no duplicate `.cursorrules` or `.mdc` policy is needed              |
| GitHub Copilot                      | `.github/copilot-instructions.md`                 | Points to the same guides; load referenced files when not already in context             |
| Other tools / manual handoff        | Explicitly attach or read the Markdown files      | Use the startup prompt below; do not assume automatic discovery                          |

The adapter choices follow the documented [Claude Code import mechanism](https://code.claude.com/docs/en/memory#agentsmd),
[Gemini CLI context imports](https://geminicli.com/docs/cli/gemini-md/#modularize-context-with-imports),
[Cursor AGENTS.md support](https://cursor.com/docs/rules), and
[Copilot repository instructions](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-repository-instructions).
Support varies by tool version and surface. A Markdown link is not a universal automatic import.
Confirm that the tool can state the applicable guide paths and planned verification commands before
relying on discovery. The adapters are plain files, so Windows symlink privileges are not required.

For a tool without automatic discovery, start with:

```text
Read AGENTS.md at the repository root. For every path you will edit, read applicable nested
AGENTS.md files (currently src/AGENTS.md and backend/AGENTS.md). Inspect git status and relevant
diffs. Use docs/DEVELOPMENT.md to select commands. State the affected paths and verification
plan, then carry out the requested work. Preserve changes already present in the checkout.
```

Keep durable rules in the canonical guides. Put command detail in `docs/DEVELOPMENT.md`. Do not
copy rules into every tool file or commit personal model choices, credentials, permission bypasses,
or machine-specific absolute paths. Historical specs and handoffs describe their own tasks and
must not silently override the current user's request or repository guidance.

## Ownership and parallel work

Small tasks can be completed by one agent. When work is delegated or parallelized:

1. **Define the boundary.** Record the task ID, outcome, base commit, owned paths, excluded paths,
   shared interfaces, acceptance checks, and dependencies. Use the
   [handoff template](HANDOFF.template.md) as a starting point.
2. **Assign one owner per shared file.** Agree on request/response fields and defaults before
   frontend and backend edits diverge. Give one worker ownership of lockfiles, migrations, locale
   regeneration, and other files multiple tasks might touch.
3. **Select the checkout.** Use separate worktrees for independent changes when supported. Verify
   the starting revision; do not assume a new worktree includes uncommitted edits. `.worktrees/`
   is ignored for optional in-repository worktrees. Keep separate runtime ports/data directories.
4. **Coordinate before expanding scope.** A worker that needs another owner's file should report
   the dependency and agree on ownership before editing. In a shared checkout, re-read files before
   applying patches and never undo another worker's changes to resolve a conflict.
5. **Integrate and verify.** Review the actual diffs, resolve contracts, and run checks on the
   combined result. An individual worker's passing checks are useful evidence, not proof of the
   final integrated state. Only publish or change remote history within the user's authorization.

Explicitly stage only intended paths if a commit is requested. Avoid `git add .` in a shared dirty
checkout. Do not remove worktrees until their owner confirms the work is safely preserved.

## Handoff that survives context loss

Keep scratch notes and local handoffs in `.agent-work/<task-id>/`. This directory is ignored, so
another worktree, machine, or remote agent will not receive those notes automatically. To transfer
work, send a sanitized summary through the authorized task channel, attach the note, or deliberately
track an appropriate document under `docs/`. Do not commit the entire scratch directory.

A useful handoff contains:

- Goal and acceptance criteria, current status, and the next concrete action.
- Base/current revision, branch or worktree identifier, touched paths, and uncommitted changes.
- Ownership boundaries, relevant decisions, and any interface changes another worker must consume.
- Exact validation commands, environment versions, results, and checks not run with reasons.
- Blockers, pre-existing failures, open questions, and any running local service's port/owner.

Keep facts distinct from assumptions. Avoid transcripts, secrets, real user content, and stale
claims of success. When receiving a handoff, inspect the current Git state, read the referenced
files, and check whether the recorded revision or validation evidence still applies.

## Keep guidance accurate

Update guidance alongside changes to scripts, CI, folder structure, persistence, or fork behavior.
Before finishing an instruction change:

1. Check every referenced local path and import resolves from its containing file.
2. Compare documented commands with `package.json`, `pyproject.toml`, scripts, and active workflows.
3. Check that adapter files still point to root/scoped guides and do not introduce conflicting rules.
4. Format changed Markdown using installed Prettier and run `git diff --check`.
5. Inspect new files as well as tracked diffs; confirm shared adapters are not ignored by Git.
6. Report what was validated and which tool-loading/runtime checks were not exercised.
