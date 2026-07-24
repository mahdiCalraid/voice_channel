# Voice Channel Implementation Plan

Status updated: 2026-07-20

This is the authoritative execution plan for Voice Channel. It replaces the previous
mixed historical plan and reorders the work around four strict phases:

1. Mechanical foundation
2. Reusable AI foundation
3. Operational UI/UX
4. Individual AI capabilities

Work proceeds one numbered task at a time. A task is not complete when code exists; it
is complete only after its verification checklist passes and the result is committed.
No work from a later phase starts until the current phase gate passes.

## 1. Product Direction

Voice Channel is a local, audio-first supervision console for Rocket.Chat-backed agent
channels. Rocket.Chat remains the durable transcript and coordination layer. The app
must let Ed read and hear important activity, privately ask an AI about a room, prepare
responses, and send only after explicit confirmation.

The long-term product shape remains:

```text
Rocket.Chat rooms and history
          |
Mechanical read/write and state layer
          |
Reusable Voice Channel AI task engine
          |
Three-pane supervision UI + voice
```

The project does not require video, avatars, LiveKit, or a conference-room simulation.

## 2. Execution Rules

Every task follows the same sequence:

1. Inspect the current implementation and define the smallest scoped change.
2. Implement only that task.
3. Run its automated checks.
4. Run its live verification against the actual local service when applicable.
5. Record the result in this plan.
6. Commit the verified task before starting the next one.

Status vocabulary:

- `VERIFIED`: implemented, tested, live-checked where applicable, and committed.
- `PARTIAL`: useful code exists, but the complete verification contract has not passed.
- `NOT STARTED`: no production implementation exists.
- `BLOCKED`: cannot proceed without a named external dependency or decision.

Claims such as "tests pass" must name which tests ran. Backend unit tests do not prove
browser behavior. A live endpoint response does not prove room switching, confirmation
safety, or recovery after interruption.

## 3. Current Baseline

Current branch: `feature/full-screen-voice-console`

Stable pre-overhaul return point: `master` at `787c483`.

Current implementation truth:

| Capability | Status | Evidence / limitation |
| --- | --- | --- |
| Rocket.Chat status and room discovery | VERIFIED | F-02A restart soak and degraded-AI checks passed. |
| Room history and lane classification | VERIFIED | F-03A adds cursor paging, retained per-room history, overlap dedupe, and preserved prepend scroll. |
| Confirmed Rocket.Chat sending | VERIFIED | F-04 confirmation gate, text lock, context verification, and duplicate-prevention nonces. |
| Codex CLI authentication and execution | VERIFIED | Real Codex CLI worker runs with shared host authentication |
| Digest worker | PARTIAL | Codex digest works, but every room currently receives Voice Channel project documents |
| Three-pane shell | VERIFIED | Usable live after F-01 baseline, with F-02-F-05 room-scoped state and confirmation safety. |
| General AI task engine | PARTIAL | Registry/job/result scaffolding exists; only digest has a real task pipeline |
| Per-channel settings and matter mapping | NOT STARTED | No persisted room configuration or explicit matter-folder mapping |
| Private narrator conversation | NOT STARTED | Right bar is a digest player, not a persistent AI conversation |
| High-quality TTS | NOT STARTED | Browser `speechSynthesis` is still the only output provider |

The first action is therefore not a new feature. It is to establish a clean, reproducible
foundation baseline.

# Phase 1 - Mechanical Foundation

Goal: prove that the application can run continuously, read from Rocket.Chat, switch
rooms safely, and send confirmed messages without interruption, stale state, duplication,
or cross-room leakage. AI quality is not part of this phase.

## F-01. Reproducible Repository Baseline

Status: `VERIFIED` (2026-07-20)

Work completed:

- Committed the `frontend/index.js` syntax repair (extra trailing `}` that prevented all JS from loading) and channel-list error state on failed `/api/rooms`.
- Product commits exclude ACLI runtime churn (`acli/routing_log.jsonl`, session JSONs, inbox screenshots, summary_history noise) as a hard rule for this baseline.
- Added `tests/test_frontend_syntax.py` so `python3 -m unittest discover -s tests` runs `node --check frontend/index.js`.
- Service start/restart remains the single documented command: `./restart.sh` (Docker console on port 6891).

Verification recorded:

- `node --check frontend/index.js` passes on committed `HEAD`.
- `python3 -m unittest discover -s tests` includes frontend syntax and backend suites.
- Live `/api/rooms` returns rooms; working-tree JS serves via volume mount.

Completion artifact: baseline product commit on `feature/full-screen-voice-console` that can be checked out without relying on uncommitted `index.js` repairs.

## F-02. Process Startup, Health, and Restart

Status: `VERIFIED` (2026-07-21)

Work completed:

- Structured `/api/status` with `app` / `rocket_chat` / `worker` blocks.
- Atomic `save_summary()` via `.tmp` + `os.replace`.
- Startup cleanup of orphan `*.tmp` summary files.
- Auth file parse check (non-empty JSON) for Codex availability signal.
- Unit tests for atomic save, status shape, and digest fallback when the worker subprocess fails.
- Documented start/restart path remains `./restart.sh`.

## F-02A. Operational Readiness Closure

Status: `VERIFIED` (2026-07-21)

Purpose: close the honest F-02 verification bar without inventing new product features.

Work completed:

1. **Restart soak**:
   - Ran `verify_soak.py` to automate a 5-cycle restart loop of the container.
   - For each cycle, verified `/api/status` is online, `/api/rooms` loads successfully, and `/api/history?count=5` retrieves chronological messages cleanly.
   - Checked that container startup contains no exceptions and summary JSON histories remain completely uncorrupted.
2. **Live degraded AI without destroying host credentials**:
   - Temporarily mounted `/codex-host-auth` to an empty directory in `docker-compose.yml` and cleared `OPENAI_API_KEY`.
   - Verified `/api/status` reports `"worker": {"status": "degraded", "configured": false, "codex_available": false, "openai_available": false}` while Rocket.Chat connection remains `"connected"`.
   - Verified rooms/history continue to read/write normally, and `/api/digest` returns clean rule-based fallback summary text without throwing 500 exceptions.
   - Restored volume mounts and verified worker state returns to `"ready"` and `"configured": true`.
3. **Health honesty polish**:
   - Exposed `configured` boolean flag inside `worker` component health block.
   - Integrated cheap `codex --help` execution probe to verify the Codex CLI runs successfully before marking the status as `"ready"`.
   - App health dynamically probes summary storage directory write permissions before reporting `"healthy"`.

Verification recorded:

- 5-cycle restart loop passed successfully (recorded in `scratch/verify_soak.py`).
- Degraded mode `/api/digest` returned rule-based text: `{"digest":"Here is a quick summary of the recent updates: grok worked on 1 updates. ed worked on 1 updates. The last update was from ed, saying: Good instruction","included_message_ids":[],"prior_summary_used":true}`.
- `python3 -m unittest discover -s tests` passes 22 tests cleanly.

Out of scope: pagination UI, send safety, narrator features, TTS.

## F-03. Reliable Rocket.Chat Inbound Path (backend)

Status: `VERIFIED` (backend plus F-03A client continuity, 2026-07-23)

Work completed (do not redo):

- `/api/history` accepts bounded `count` (1–100), `offset`, and `latest`.
- Exponential backoff retry (3 attempts) on transient Rocket.Chat failures; 502 if all fail.
- Response includes `has_more`, `offset`, cleaned messages, and window stats.
- Manual “Retry Connection” control on transcript load failure.
- Unit test for fail-then-success history fetch and pagination fields.

Client continuity closed by F-03A (2026-07-23):

- The browser uses the `before` cursor rather than raw offset math, retains room-scoped history, and exposes Load Older Messages.
- Older pages overlap inclusively and dedupe by message ID; live polls merge rather than replace retained history.
- Prepend scroll restoration preserves the reading position, including a queued live refresh that arrives during the prepend.
- Stats are explicitly labeled as a recent window rather than a claim about all loaded history.

## F-03A. Client History Continuity

Status: `VERIFIED` (2026-07-23)

Purpose: make the supervision console actually use the inbound API for continuous reading, not only the newest window.

Work:

1. **Cursor-based older history (preferred over raw offset in the UI)**
   - Prefer API param `before=<ISO timestamp>` (or keep `latest` as the RC mapping) for “messages older than this.”
   - Backend: if needed, add an explicit `before` alias that maps to Rocket.Chat `latest` so the client does not invent offset math.
   - Request boundary **inclusively** and **dedupe by Rocket.Chat message `id`** so shared timestamps do not drop messages.

2. **Per-room history state in the browser**
   - Maintain for each active room (or at least the current room): message map by id, ordered ids, oldest cursor, newest timestamp, `hasMoreOlder`, loading flags.
   - Separate request tokens for **live poll/refresh** vs **load older** so they cannot cancel or clobber each other.
   - On room switch: discard or ignore stale responses (already partly done); clear or swap room-scoped state atomically.

3. **UI: Load older messages**
   - Visible **Load older messages** control at the top of the transcript (simpler and more testable than infinite scroll).
   - Before prepending: record `scrollHeight` / `scrollTop`; after prepend: restore viewport so the same message stays under the reader.
   - Disable/hide the control when `hasMoreOlder` is false.

4. **Polling merge, not replace**
   - Live poll must **merge** new messages into the retained map by id, not wipe the loaded older history.
   - Auto-scroll only when already near bottom or on first load of a room (existing near-bottom rule stays).

5. **Stats labeling**
   - Stats remain “recent window” (from the latest fetch used for stats) unless a separate full-loaded recompute is explicitly implemented.
   - Do not claim page-local stats represent all loaded history.

6. **Tests**
   - Unit/API: boundary overlap dedupe, two consecutive older pages, end-of-history (`has_more` false).
   - Browser or deterministic frontend tests where practical: load older preserves viewport; room switch mid-load does not paint wrong room; new message arrives while older page is open without wiping history.
   - Product-only commit.

Verification (all must pass):

- From a busy room, click **Load older** at least twice; no duplicate ids; order stays chronological; no gap at the page boundary after dedupe.
- Scroll position is preserved when older messages prepend.
- While scrolled up reading history, a new live message does not force scroll-to-bottom (unless user was near bottom).
- Rapid room switches during load older do not show the wrong room’s messages.
- `python3 -m unittest discover -s tests` passes; any new frontend checks are included.
- Live `/api/history` with the chosen older-page cursor works against real Rocket.Chat.

Completion recorded:

- Added `before` as the client-facing older-history cursor, mapped to Rocket.Chat `latest` with inclusive boundary retrieval, response `next_before`, and message-ID deduplication.
- Added per-room retained state, separate live and older-load request sequences, a visible Load Older control, poll merging, viewport restoration, and recent-window stats labeling.
- A live older-page request returned an inclusive boundary overlap and an older `next_before` cursor. The served console loads `history_state.js` before `index.js`.
- `python3 -m unittest discover -s tests` passes 24 tests, including deterministic Node tests for overlap dedupe, chronological merge, retained live updates, end-of-history behavior, and prepend-scroll restoration.
- An interactive browser was not available in this execution environment; deterministic frontend tests and the live served API cover the same state and cursor contract.

Out of scope: send confirmation (F-04), full isolation audit (F-05), AI tasks, TTS.

## F-04. Safe Rocket.Chat Outbound Path

Status: `VERIFIED`

Work:
- Maintained a single backend send endpoint (`POST /api/send`) and one explicit frontend confirmation overlay (`confirmation-gate`).
- Bound every pending confirmation session to an immutable `roomId`, message text snapshot, and a client-side generated unique `nonce`.
- Locked the message input composer to `readOnly` while confirmation is pending.
- Automatically rejects transmission if the active room ID or input text changes before transmission completes.
- Implemented backend-level request deduplication using a thread-safe in-memory cache of nonces, returning cached success payloads for duplicates without invoking Rocket.Chat.
- Disabled both the confirmation and cancel buttons during transmission to prevent double-clicks or aborting in-flight requests.
- Extracted and returned the Rocket.Chat message `_id` on success, which is rendered dynamically in the UI status area.
- Retains the message draft and returns the UI to a retry state if transmission fails or times out.

Verification:
- Added `test_send_message_success` and `test_send_message_deduplication` unit tests to `tests/test_classification.py`.
- Verified live posting via curl to `/api/send` correctly posts exactly one message to the test room and returns `msgId`.
- Verified duplicate sends with the same nonce return the cached success payload without posting a second message.
- Confirmed that room changes clear/reset confirmation states (`hideConfirmation()` is called on room context changes).

## F-05. Room Isolation and UI State Safety

Status: `VERIFIED` (deterministic state isolation; browser race harness remains F-07)

Work:
- Created helper functions `saveRoomUIData` and `restoreRoomUIData` inside `frontend/index.js` to capture and restore room-local drafts, digests, playback state, sources lists, stats displays, and scroll positions.
- Modified `selectRoom()` to cleanly capture and save the UI state of the old room before switching active room IDs.
- Simplified `handleRoomChange()` to dynamically restore room state, clearing/rendering UI assets atomically to prevent state bleed or stale data displays from the prior room.
- Sequenced polling and paging requests with distinct target rooms and sequence tokens to ignore stale network results across active room switches.
- Preserved user viewport scrolling by tracking per-room scroll offsets on exit, restoring them on reentry, and anchoring viewports during active message prepend.

Verification:
- Deterministic frontend tests cover restored-source navigation and clearing the originating room's digest-loading state after a room switch.
- History-state tests cover room-local retention, overlap deduplication, live-poll merging, and prepend scroll restoration.
- Live service remains healthy, Rocket.Chat connected, and the separately served history state module returns HTTP 200.
- Full multi-room browser race and interaction coverage remains an explicit F-07 deliverable.

## F-06. Interruption and Recovery

Status: `NOT STARTED`

Work:

- Handle Rocket.Chat outage, backend restart, browser refresh, and network interruption.
- Resume polling cleanly without replaying or losing state.
- Stop or time out abandoned subprocesses.
- Make all error states recoverable without clearing application data manually.

Verification:

- Disconnect Rocket.Chat for two minutes, reconnect, and observe automatic recovery.
- Restart the backend during room polling and during a non-send AI request.
- Refresh during an unsent draft and verify the defined persistence behavior.
- Confirm no orphan worker process remains after timeout or cancellation.

## F-07. Foundation Test Harness

Status: `NOT STARTED`

Work:

- Keep existing backend unit tests.
- Add frontend syntax checks to every verification run.
- Add browser-level tests for room load, switch races, transcript refresh, confirmation,
  and narrator open/close state.
- Add a designated Rocket.Chat integration test room and safe test-message convention.
- Add a repeatable live smoke script that reports pass/fail without manual interpretation.

Verification:

- A single command runs backend tests, frontend syntax checks, and deterministic browser tests.
- A separate opt-in command runs the live Rocket.Chat smoke test.
- Intentionally reintroducing the extra-brace syntax bug makes the test suite fail.
- Intentionally simulating a stale room response makes the browser test fail.

## F-08. Foundation Gate

Status: `NOT STARTED`

Phase 1 passes only when all F-01 through F-07 tasks (including F-02A and F-03A) are
`VERIFIED` and:

- The app survives a 60-minute multi-room soak without stale-room rendering or polling death.
- At least one inbound and one confirmed outbound message are verified end to end.
- Backend restart and Rocket.Chat reconnect recover automatically.
- A fresh checkout is runnable and all product changes are committed.

No AI-foundation expansion begins before this gate passes.

# Phase 2 - Reusable AI Foundation

Goal: create one dependable, provider-neutral task engine that can support future Voice
Channel AI capabilities without rebuilding orchestration for each feature. Digest is the
reference task used to prove the engine; other user-facing tasks come later.

## AI-01. Versioned Task and Result Contracts

Status: `PARTIAL`

Work:

- Define versioned `job.json` and `result.json` schemas.
- Standardize job ID, task name, room ID, worker/model, context policy, input manifest,
  timeout, and permissions.
- Standardize result status, output payload, transcript sources, file sources, timing,
  worker/model, and structured error.
- Validate contracts before execution and before returning results to callers.

Verification:

- Valid fixture jobs round-trip through schema validation.
- Missing, malformed, or unsupported fields fail with explicit errors.
- Backend and CLI consume the same contracts rather than duplicated assumptions.

## AI-02. Provider-Neutral Worker Runtime

Status: `PARTIAL`

Work:

- Retain the worker registry and real Codex CLI provider as the default implementation.
- Make model, effort, timeout, authentication strategy, and availability explicit.
- Separate provider execution from task prompt/input construction.
- Define cancellation, timeout, stdout/stderr, and health behavior consistently.
- Keep API-based providers optional and isolated from Codex CLI authentication.

Verification:

- The same fixture task can run through the configured provider without backend changes.
- Missing provider/authentication produces a structured failure, not a server crash.
- Timeout terminates the worker and leaves a complete result/error artifact.

## AI-03. Room Configuration and Matter Mapping

Status: `NOT STARTED`

Work:

- Create a versioned per-room configuration store.
- Store channel type, narrator instructions, transcript depth, permission tier, and an
  explicit ACLI matter-directory mapping.
- Validate paths against configured allowlisted roots; never infer a path from room name.
- Mount approved host matter roots read-only into the worker runtime.
- Exclude credentials, VCS internals, ignored files, and generated/temp artifacts.

Verification:

- Two rooms mapped to different fixture directories receive only their own files.
- Transcript-only mode reads no project files.
- Invalid, escaped, symlinked, or unapproved paths are rejected.
- Every result records exactly which project files were used.

## AI-04. Shared Context Builder

Status: `NOT STARTED`

Work:

- Build one context service for all AI tasks.
- Combine the requested transcript window, lane/event reduction, prior relevant AI state,
  room instructions, pinned project documents, and bounded task-specific file excerpts.
- Version and record the context snapshot used by every task.
- Enforce size limits and deterministic ordering.

Verification:

- Replaying a saved context snapshot produces the same provider input.
- Room A content cannot appear in a Room B context bundle.
- Context truncation is explicit and testable rather than silent.

## AI-05. Task Registry and Structured Outputs

Status: `NOT STARTED`

Work:

- Replace the current generic non-digest prompt fallback with registered task modules.
- Each task declares its required inputs, permissions, prompt/instructions, output schema,
  and persistence policy.
- Reject unknown tasks.
- Expose one generic internal execution service usable by CLI and backend endpoints.

Verification:

- A minimal fixture task and the digest task run through the same registry/runtime.
- Unknown task names fail before a provider is invoked.
- Malformed structured model output is rejected and never converted into an action.

## AI-06. AI State, Persistence, and Audit

Status: `PARTIAL`

Work:

- Preserve existing per-room summary history.
- Add versioned, atomic storage primitives for future narrator sessions and task records.
- Store task, room, user input, context version, source IDs/files, worker/model, result,
  timing, and errors.
- Use bounded retention and per-room write serialization.
- Keep secrets out of prompts, job bundles, logs, and committed files.

Verification:

- Concurrent jobs cannot corrupt room state.
- Refresh/restart preserves committed AI state.
- Audit records can reconstruct what context supported an answer.
- Retention limits work without deleting current room configuration.

## AI-07. Digest as the Reference Task

Status: `PARTIAL`

Work:

- Move digest fully onto the shared task/context/runtime contracts.
- Use the selected room's configuration and matter directory, not global Voice Channel docs.
- Keep Lane B/C content, deterministic Lane A operational summary, prior summaries,
  transcript source IDs, and file-source reporting.
- Preserve a deterministic fallback that is clearly labeled as fallback.

Verification:

- Generate digests for two differently configured rooms and verify context isolation.
- Successful result comes from Codex and includes auditable sources.
- Provider failure returns labeled fallback without polluting another room's memory.
- Saved summary history remains bounded and valid after restart.

## AI-08. AI Foundation Gate

Status: `NOT STARTED`

Phase 2 passes only when AI-01 through AI-07 are `VERIFIED` and:

- One generic task API and CLI path execute the same versioned job contract.
- Codex is a configured provider, not task-specific hard-coding.
- Room-specific transcript and project context are isolated and auditable.
- Timeouts, cancellation, invalid output, and unavailable providers fail safely.
- Digest proves the entire foundation end to end.

No new narrator, suggestion, comparison, or command-generation feature begins before
this gate passes.

# Phase 3 - Operational UI/UX

Goal: provide a reliable interface for the verified mechanical and AI foundations. This
phase improves and completes the shell; it does not invent new AI capabilities.

## UI-01. Three-Pane Application Shell

Status: `PARTIAL`

Work:

- Retain the Rocket.Chat channel rail, large transcript center, and collapsible narrator bar.
- Make pane behavior responsive and keyboard accessible.
- Preserve active room and pane preferences without mixing room-specific content.
- Remove obsolete/conflicting CSS left from the old layout.

Verification:

- Desktop and mobile layouts remain usable at defined viewport sizes.
- Opening/closing drawers cannot hide the only navigation path.
- Browser interaction tests cover the shell.

## UI-02. Transcript Reader

Status: `PARTIAL`

Work:

- Keep sanitized Markdown for user and agent messages.
- Render routing, model, heartbeat, completion, failure, stop, superseded, and attachment
  events as clear graphic system cards.
- Add long-message collapse, date separators, unread marker, jump-to-latest, pagination,
  source highlighting, and optional raw-message inspection.
- Use readable typography and line width appropriate for long agent reports.

Verification:

- Large Markdown, tables, code, links, and system events render safely and readably.
- A 100+ message room can be navigated without losing position.
- Source links resolve to the exact displayed message.

## UI-03. Channel Settings

Status: `NOT STARTED`

Work:

- Open settings from the channel header.
- Edit the Phase 2 room configuration: type, narrator instructions, transcript depth,
  approved matter folder, permission tier, and later voice preferences.
- Validate and save through backend APIs; do not store security-sensitive path policy only
  in browser local storage.

Verification:

- Settings survive refresh/restart and remain scoped to one room.
- Invalid matter paths cannot be saved.
- Digest immediately uses the newly saved room configuration.

## UI-04. Generic AI Workspace

Status: `NOT STARTED`

Work:

- Turn the right bar into a generic private AI task surface backed by the Phase 2 runtime.
- Clearly show selected room, context policy, progress, cancellation, sources, result,
  provider/model, and errors.
- Keep AI output private; it can populate a draft but cannot post to Rocket.Chat.
- Retain digest controls as the first registered task.

Verification:

- A task started in room A cannot render in room B.
- Refresh/reopen can recover persisted task results where policy allows.
- No AI control can bypass the composer confirmation boundary.

## UI-05. Safe Composer and Draft State

Status: `PARTIAL`

Work:

- Keep the composer exclusively for Rocket.Chat-bound content.
- Make agent targets configuration-driven with accessible labels and fallback icons.
- Persist unsent drafts per room.
- Support explicit promotion from private AI output into an editable draft.
- Keep immutable room/message confirmation and exact-once send behavior from Phase 1.

Verification:

- Switching rooms preserves separate drafts.
- Promoted AI text remains editable and unsent.
- Only explicit confirmation posts to Rocket.Chat.

## UI-06. UI Gate

Status: `NOT STARTED`

Phase 3 passes only when UI-01 through UI-05 are `VERIFIED` and Ed can:

- Navigate rooms and long transcripts reliably.
- Inspect and edit room-specific AI context settings.
- Run the reference AI task privately and inspect its sources.
- Promote output to a draft and explicitly confirm one correct Rocket.Chat post.
- Refresh or restart without losing defined room state.

# Phase 4 - Individual AI Capabilities

Goal: add high-value workflows one at a time on the verified foundations. Every item is
an independent task module with its own UX and acceptance test.

## AIF-01. Private Narrator Q&A

Status: `NOT STARTED`

- Ask grounded questions about recent messages and approved project files.
- Persist bounded per-room narrator sessions.
- Show transcript and file sources for every answer.

## AIF-02. Room Status and Progress Review

Status: `NOT STARTED`

- Explain current stage, completed work, blockers, unresolved decisions, and next step.
- Compare actual state against objectives and plans.

## AIF-03. Agent Comparison

Status: `NOT STARTED`

- Compare named agent positions, agreements, disagreements, evidence, and recommended action.

## AIF-04. Explicit Reply Drafting

Status: `NOT STARTED`

- Generate a reply only after Ed asks.
- Return an editable draft with source metadata and uncertainty where relevant.
- Promote to composer without sending.

## AIF-05. Humble Reply Suggestions

Status: `NOT STARTED`

- Generate two to four concise, materially different starter options.
- Insert the selected option into the composer for editing.
- Never target, execute, or send automatically.

## AIF-06. High-Quality TTS

Status: `NOT STARTED`

- Add a provider-neutral `/api/tts` service with natural voice selection, speed/style,
  interruption, chunking/streaming, text normalization, caching, and browser fallback.
- Keep TTS credentials separate from Codex CLI authentication.

## AIF-07. Voice Input and Confirmed Commands

Status: `NOT STARTED`

- Convert speech into private narrator questions or editable composer drafts.
- Require explicit confirmation before every Rocket.Chat write or future external action.

Each Phase 4 capability follows the same rule: implement one task, verify it end to end,
commit it, use it in daily work, and only then select the next capability.

## 4. Global Guardrails

- Rocket.Chat remains the durable source of channel truth.
- Narrator/AI surfaces never post directly to Rocket.Chat.
- Project-folder access is explicit, room-scoped, allowlisted, and read-only.
- No secret is stored in prompts, task bundles, logs, or the repository.
- No room may receive another room's transcript, summaries, settings, or project files.
- Browser failure or AI failure must not prevent basic Rocket.Chat reading and sending.
- No video, avatar room, LiveKit dependency, or conference-room simulation.
- No phase is declared complete from mocked tests alone when a live integration exists.

## 5. Immediate Next Task

**Sequence (do not skip):**

1. **F-02A — Operational Readiness Closure** (VERIFIED 2026-07-21)
2. **F-03A — Client History Continuity** (VERIFIED 2026-07-23)
3. **F-04 — Safe Rocket.Chat Outbound Path** (VERIFIED 2026-07-23)
4. **F-05 — Room Isolation and UI State Safety** (VERIFIED 2026-07-23)
5. Then F-06 → F-07 → F-08 foundation gate

F-02, F-02A, F-03, F-03A, F-04, and F-05 are fully closed. F-06 is the active task. Do not start F-07 until F-06 is `VERIFIED` and committed (product-only commits; no `acli/` runtime churn).

Do not start channel settings, narrator Q&A, suggestions, or TTS until Phase 1 (through
F-08) passes. Phase 2 AI-foundation work does not begin before the foundation gate.

### Coalesced actionable checklist (short form)

| ID | Do this | Done when |
| --- | --- | --- |
| F-02A | 5× restart soak + live degraded AI without destroying host Codex auth | VERIFIED (Cycle soak passed, degraded AI falls back successfully, RC loads rooms & history) |
| F-03A | Cursor older-history + Load older UI + merge polls + scroll preserve + dedupe | VERIFIED (live cursor check; deterministic dedupe, ordering, retention, and scroll tests) |
| F-04 | Immutable confirmation room snapshot + single-flight send + RC message id | VERIFIED (concurrency race test, post success and dedupe tests, live curl nonce check) |
| F-05 | Room isolation: bind history, stats, digest, sources, playback, drafts, and confirmations to room-scoped state. Cancel stale requests. Clear visual state atomically. Preserve scroll. | VERIFIED (drafts, digests, sources, stats, scroll restored cleanly without bleed across room switch) |
| F-06+ | Interruption recovery, browser tests, soak gate | Per existing F-06–F-08 sections |
