# Upstream merge records

This fork tracks Open WebUI releases while keeping explicit product decisions. Read the
[root agent guide](../../AGENTS.md) and the record for the currently integrated version before
starting another upstream merge. Historical records explain decisions; current source, current
AGENTS.md instructions, and the current user request govern implementation.

| Upstream release | Design and upgrade record                                                      |
| ---------------- | ------------------------------------------------------------------------------ |
| v0.12.2          | [Current decisions, compatibility and validation](v0.12.2-decisions.md)        |
| v0.12.0          | [Historical decisions, compatibility and validation](v0.12.0-decisions.md)     |
| v0.11.4          | [Decisions, implementation evidence, and upgrade checks](v0.11.4-decisions.md) |

The current release decision record is v0.12.2. The [2026-10-22 fork audit](v0.12.2-fork-audit.md)
extends its coverage through `c21cfb72b`, with feature/fix evidence, all 73 fork-only commits,
remote verification of two commits missing locally, and a [475-path net difference inventory](v0.12.2-fork-files.md).
These are dated snapshots; recompute the tree/history comparison for later work.
Earlier release records and their validation numbers remain historical references.

The v0.11.4 [tool citation cache follow-up](v0.11.4-tool-citation-cache-followup.md) changes source
placement while retaining D05's evidence and numbering policy. Check it alongside the original
release record; it has its own implementation and validation status.

## Workflow for the next merge

1. Verify the fork HEAD, official upstream target SHA, actual merge base, shallow boundaries, and
   working-tree state. Resolve official tags against the upstream repository; do not assume a local
   same-named tag identifies the official release.
2. Review every active decision in the previous record. Compare upstream changes with the retained
   behavior and its regression tests, including frontend/backend contracts and defaults. For citations,
   verify evidence semantics, stable request prefixes, approval/history replay and tool-budget ranges
   against the follow-up record's tests.
3. Classify each result: adopt upstream, retain a fork difference, adapt an interface, or converge an
   equivalent fix. A clean textual merge is not evidence of behavioral compatibility.
4. Preserve manual choices and existing access controls when changing automatic discovery. Review
   terminal isolation, authentication failure behavior, user preferences, and serialized skill data
   as complete flows.
5. Verify focused regressions, combined suites, type/build checks, and isolated runtime checks.
   Record actual counts, failures, skipped checks, and environment limits. Do not equate a skipped
   upstream regression workflow with a passing run.
6. Add a record for the new release, referencing stable decision IDs in the previous record. Record
   why a decision is retained or superseded and the replacing decision. Do not overwrite historical
   decisions or claim implementation before it exists.
7. Update README for user-visible behavior, AGENTS.md for current invariants, and DEVELOPMENT.md for
   commands/test locations. Keep rationale here rather than duplicating it in each instruction file.
   Check the full tree diff and all reachable fork-only commits, including side parents. Distinguish
   surviving changes from upstream equivalents and fork-specific merge regressions; do not infer
   current uniqueness from an old commit subject. Keep pre-existing uncommitted changes separate.

Use repository-relative source and test links. Store real full commit IDs once available; never
invent a future merge or validation commit. Do not include credentials, real chat data, private
connection URLs, local machine paths, or scratch artifacts in these records.

## Native connections follow-up

The [v0.12.2 native connection follow-up](v0.12.2-native-connections-followup.md) records explicit
Gemini/Anthropic message transports, signed reasoning replay and cross-transport budget/header
contracts. Read it alongside v0.12.2 decisions before merging provider, connection or compaction changes.

## Search1API endpoint follow-up

The [v0.12.2 Search1API endpoint follow-up](v0.12.2-search1api-endpoint-followup.md) records the
admin-global `/search`/`/ask` selection, default and persistence compatibility, request differences
and validation evidence. Read it before merging search-provider or retrieval-settings changes.

## Knowledge write follow-up

The [v0.12.2 knowledge write follow-up](v0.12.2-knowledge-write-followup.md) records separate
default-off authorization, attachment scope, extracted-text editing and partial indexing failures.
Read it before merging builtin tools, knowledge/file permissions or chat preference changes.

The [v0.12.2 knowledge direct import follow-up](v0.12.2-knowledge-direct-import-followup.md) extends
that authorization to URL and admin-terminal file imports without routing source bodies through the
model. Read it before merging URL ingestion, terminal file access or knowledge processing changes.

## Reply lifecycle follow-up

The [current audit](v0.12.2-fork-audit.md#近期回复生命周期修复) records confirmed cancellation across
Redis workers, preservation of interrupted streams/SSE completion, and isolation of concurrent model
requests. Read it before merging task scheduling, streaming handlers or multi-model fan-out. Current
user behavior is in [chat access and stopping](../chat-access-controls.md), with checks in
[DEVELOPMENT](../DEVELOPMENT.md#chat-lifecycle-and-request-isolation-verification).
