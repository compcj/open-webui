# Contributing to this checkout

Start with [AGENTS.md](AGENTS.md) for repository conventions, whether you are working manually or
with an agent. Use [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) to set up the environment and choose
checks. For work shared across agents, tools, or sessions, use the
[collaboration workflow](docs/agents/README.md).

## Prepare and validate a change

1. Inspect the branch and existing changes. Define the user-facing problem and the scope of the fix.
2. Read the relevant frontend/backend guide and nearby implementation/tests. Preserve fork-specific
   behavior when updating upstream code.
3. Make a focused change and add behavior coverage where appropriate. Update documentation when
   commands, configuration, or user workflows change.
4. Run applicable checks and review the complete diff, including generated files and lockfiles.
5. Report exact commands and results, manual verification, and any blocked or omitted checks.
   For UI changes, include screenshots or recordings when they help review.

## Pull requests and upstream policy

This checkout contains fork changes as well as upstream Open WebUI history. Verify the intended
remote and target branch before publishing; local changes do not automatically belong in an
upstream pull request.

The existing [pull request template](.github/pull_request_template.md) describes the upstream policy:
target `dev`, begin with an Issue or Discussion, and open a code PR only when requested by a
maintainer or for an i18n/localization-only change. Follow that policy for upstream submissions.
For a fork-local PR, establish the intended target and maintainer policy rather than assuming the
upstream target is correct. Keep the CLA section intact and leave personal acceptance to the
contributor; an agent must not accept it on someone else's behalf.

Respect the [Code of Conduct](CODE_OF_CONDUCT.md), [license](LICENSE),
[license notices](LICENSE_NOTICE), and [Contributor License Agreement](CONTRIBUTOR_LICENSE_AGREEMENT).
For security reports, follow [docs/SECURITY.md](docs/SECURITY.md).

## Maintain agent guidance

Keep shared rules in `AGENTS.md` and scoped rules in `src/AGENTS.md` or `backend/AGENTS.md`.
When scripts, paths, CI, or architecture change, update the corresponding guidance in the same
change. Keep tool entry files small. Put task-specific logs and temporary notes in `.agent-work/`,
and record lasting decisions in `docs/` only when they are useful to future contributors.
