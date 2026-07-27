# Project Handoff: Adaptive Voice Gateway

Updated: 2026-07-27

## Current Direction

This branch is an adaptive fork of the Voice Channel project.

The previous branch remains available for completion of the custom three-pane UI:

`feature/full-screen-voice-console`

The current checked-out branch is:

`codex/adaptive-voice-gateway`

The fork reuses the tested Rocket.Chat foundation but changes the priority:

1. Complete the local Mac mechanics.
2. Add small communication AI.
3. Harden a client-neutral gateway.
4. Test Omi or another mobile client later.

## System Roles

- ACLI at `/Users/ed/King/clawd_2/development_channel` is the primary worker dispatch
  and execution system.
- Rocket.Chat at `/Users/ed/King/clawd_2/rc` is the communication terminal and durable
  operational transcript.
- nc2 at `/Users/ed/King/clawd_2/nc2` is a smaller secondary agent and memory system.
- Voice Gateway is the bounded communication, speech, routing, summarization, and
  supervision layer.
- Omi, a custom iPhone application, the existing browser UI, and future clients are
  replaceable adapters.

## Existing Foundation to Preserve

- Rocket.Chat status and room discovery.
- Cursor-based history paging and message deduplication.
- Per-room frontend state and room-switch isolation.
- Confirmation-bound sends.
- Nonce/idempotency duplicate prevention.
- ACLI routing and operational event classification.
- Digest generation and deterministic fallback.
- Draft persistence and interruption recovery.
- Python and Node test harnesses.
- Live smoke and soak scripts.

## Phase 0 Artifacts

- `NORTH_STAR.md`: mission and architectural guardrails.
- `OBJECTIVES.md`: scoped product, engineering, privacy, Mac, AI, and mobile objectives.
- `DISCOVERY_RECORD.md`: the complete substantive discovery conversation.
- `ADAPTIVE_IMPLEMENTATION_PLAN.md`: authoritative adaptive plan, interfaces, tools,
  limitations, tests, gates, and pivot paths.
- `IMPLEMENTATION_PLAN.md`: preserved historical UI plan.

## Immediate Next Task

After Phase 0 verification, implement `M1-01 Gateway Contract Skeleton` from
`ADAPTIVE_IMPLEMENTATION_PLAN.md`.

Do not start mobile work yet. The first release must be useful on the Mac.

## Critical Security Action

`docker-compose.yml` contains a Rocket.Chat authentication token as a default value.
Before the service is remotely exposed:

1. Remove the committed default.
2. Rotate/revoke the credential.
3. Require secret injection.
4. Verify no credential is present in client bundles, logs, or repository history used
   for distribution.

## Product Commit Rule

Do not include ACLI runtime churn in product commits, including:

- routing and transcript logs;
- session files;
- generated summary history;
- inbox screenshots;
- temporary jobs and scratch output.

These files may be active and user-owned. Preserve them unless Ed explicitly requests a
separate cleanup.

## Gate Philosophy

- A passing unit test is not a live integration test.
- A successful Omi demo is not proof of self-hosting.
- A model response is not proof of correct routing.
- A mobile build is not proof of acceptable background reliability.
- Every claim must identify the test, environment, and remaining limitation.
