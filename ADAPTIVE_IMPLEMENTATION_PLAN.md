# Adaptive Voice Gateway Implementation Plan

Status established: 2026-07-27
Reconciled with the daily-use branch: 2026-08-07

This is the authoritative long-range architecture and phase plan for the adaptive
gateway. The previous `IMPLEMENTATION_PLAN.md` remains the historical plan and
evidence for the custom Voice Channel UI foundation.

The current checkout is `urgent/daily-use-console`, a descendant of
`codex/adaptive-voice-gateway`. Its completed daily-use work is recorded in
`URGENT_IMPLEMENTATION_PLAN.md`. That plan is the current execution ledger; this
document remains the source of truth for the eventual gateway phases and gates.
Do not treat an old "Immediate Next Task" in a historical document as active work.

## 1. Direction

Build a Mac-first, self-hosted, client-neutral gateway over Rocket.Chat and ACLI. Add
bounded communication intelligence only after deterministic mechanics work. Test Omi
and mobile clients later through the same contracts.

The project proceeds through seven gated phases:

0. Direction, preservation, and contracts
1. Local Mac mechanical loop
2. Small communication AI
3. Operational supervision and concurrency
4. Client-neutral gateway hardening
5. Omi mobile feasibility
6. Apple-native fallback or mobile productization

The custom UI/UX remains a parallel future client track. It is not deleted or declared
obsolete.

## 2. Execution Rules

Every numbered task follows this sequence:

1. Define the smallest vertical change and its acceptance test.
2. Implement only that change.
3. Run deterministic automated tests.
4. Run the real local integration when one exists.
5. Record evidence and known limitations.
6. Commit product files without ACLI runtime churn.
7. Proceed only after the task gate passes.

Status vocabulary:

- `VERIFIED`: implementation, tests, applicable live checks, documentation, and commit
  are complete.
- `PARTIAL`: useful implementation exists but the full acceptance contract has not
  passed.
- `NOT STARTED`: production implementation does not exist.
- `BLOCKED`: a named external dependency or decision prevents further progress.
- `IMPLEMENTED (UNCOMMITTED)`: code and automated checks are present in the working
  tree, but the task cannot be `VERIFIED` until its product files are committed and
  any required live check is recorded.
- `EXPERIMENT REJECTED`: a candidate failed its gate and the documented pivot is active.

No provider, client, or integration is promoted because its demo looks promising. It
must pass the project's own privacy, reliability, and interoperability gates.

## 3. Stable Core and Adapter Boundaries

### Stable core

- Versioned interaction, task, event, confirmation, and result contracts.
- Rocket.Chat room/history/send adapter.
- ACLI event reduction and task supervision.
- Room/project isolation.
- Local state and audit storage.
- Safety policy and confirmation enforcement.

### Replaceable adapters

- Mac browser client.
- Mac native or menu-bar client.
- CLI client.
- Omi client.
- Custom Swift/Flutter iPhone client.
- STT providers.
- TTS providers.
- Small-model providers.
- Remote transport and authentication.

### Proposed internal interfaces

```text
ClientAdapter
  submit_text()
  submit_audio()
  show_interpretation()
  request_confirmation()
  stream_events()
  play_audio()

SpeechToTextProvider
  transcribe_file()
  open_stream()
  health()

TextToSpeechProvider
  synthesize()
  speak_local()
  interrupt()
  health()

CommunicationAI
  route_intent()
  refine_draft()
  summarize_result()
  suggest_followups()

ChannelAdapter
  list_rooms()
  read_history()
  send_confirmed()
  stream_or_poll_events()

TaskSupervisor
  create_interaction()
  correlate_events()
  get_status()
  cancel()
  complete()
```

The first implementation may be Python protocols or ordinary service classes. The
contract matters more than the language mechanism.

## 4. Versioned Data Contracts

### Interaction request

Required fields:

- `schema_version`
- `interaction_id`
- `actor_id`
- `client_id`
- `input_mode` (`text` or `voice`)
- `raw_input`
- `created_at`
- optional requested room/agent

### Interpretation

Required fields:

- selected action;
- selected room and agent, if relevant;
- refined draft;
- confidence score;
- alternative destinations;
- clarification requirement;
- explanation suitable for audit, not hidden model reasoning;
- source context identifiers.

### Confirmation snapshot

Required fields:

- immutable interaction ID;
- room ID;
- agent;
- exact outgoing message;
- permission tier;
- expiry;
- nonce/idempotency key.

### Task state

States:

```text
captured
interpreting
needs_clarification
awaiting_confirmation
posted
routed
working
completed
failed
cancel_requested
cancelled
superseded
summarized
spoken
```

### Result

Required fields:

- status;
- selected room and agent;
- Rocket.Chat source message IDs;
- task events;
- full final response;
- concise summary;
- file references;
- suggested follow-ups;
- provider/model metadata;
- timing;
- structured error;
- fallback label when applicable.

## 5. Permissions

Four initial tiers:

| Tier | Examples | Default behavior |
| --- | --- | --- |
| Read | rooms, history, status, approved file search | May run after authenticated request |
| Prepare | route, summarize, refine, draft, suggest | Private output; no external write |
| Commit | Rocket.Chat send, task cancellation, approved file mutation | Exact confirmation required |
| Privileged | shell, deployment, credentials, destructive operations | Out of scope until separately designed |

Speech never reduces the confirmation requirement. Low-confidence routing cannot cross
from Prepare to Commit.

## 6. Storage and Retention

### Durable

- configuration;
- device/client identities;
- confirmation and send audit records;
- task correlation metadata;
- user-approved summaries or session state;
- references to Rocket.Chat message IDs.

### Not duplicated by default

- all Rocket.Chat messages;
- full project trees;
- external model prompts and outputs beyond the bounded audit need.

### Ephemeral by default

- raw audio;
- TTS audio;
- temporary STT files;
- uncommitted context bundles;
- provider debug payloads.

Temporary artifacts require bounded directories, restrictive permissions, and cleanup
after success, error, timeout, cancellation, and restart.

## 7. Technical Options

### Local application/backend

- Existing FastAPI backend: preferred starting point.
- Existing browser client: preferred first Mac client.
- `httpx`: Rocket.Chat REST calls.
- SSE initially for gateway event delivery; WebSockets only where bidirectional streaming
  materially helps.
- SQLite or versioned atomic JSON for new local state; choose SQLite when concurrent task
  and audit writes begin.

### Rocket.Chat

- REST for room discovery, history, and confirmed sends.
- Existing ACLI routing/system event formats for task correlation.
- Realtime API may be added if polling latency prevents acceptable supervision.
- Rocket.Chat remains the only required worker-communication adapter in early phases.

### Mac STT

Candidates, in preferred evaluation order:

1. Existing browser/native speech for fastest mechanical proof.
2. Apple Speech framework through a small native helper.
3. Mac-hosted whisper.cpp or another local OpenAI-compatible STT server.
4. Configured API provider.

Acceptance decides the winner; the contract remains unchanged.

### Mac TTS

Candidates:

1. Current browser `speechSynthesis`.
2. macOS system voice through `say` or a native speech helper.
3. Local neural TTS service.
4. Configured API provider returning streamable audio.

### Small AI

Initial:

- API-backed structured output through a server-side credential.

Optional local alternatives:

- Ollama;
- LM Studio or another OpenAI-compatible local endpoint;
- a dedicated small classifier when training data becomes available.

Every task must have:

- a typed input;
- a structured output schema;
- a timeout;
- bounded context;
- explicit sources;
- a deterministic fallback where safe.

### Mobile

Omi experiment:

- Flutter iOS client;
- custom backend URL;
- Apple on-device STT;
- local system TTS;
- HTTPS/SSE or HTTPS/WebSocket connection to the gateway.

Fallback:

- small Swift client using Apple's Speech and AVFoundation frameworks;
- or a minimal independent Flutter client using only audited packages.

### Remote access

Initial candidate:

- Cloudflare Tunnel using an outbound connector from the Mac.

Required additions:

- device pairing;
- revocable scoped token;
- origin and audience checks;
- rate limits;
- replay protection;
- request expiry;
- no Rocket.Chat or model secrets in the client;
- no direct public ACLI or shell endpoint.

## 8. Known Limitations and Risks

### Existing repository

- A Rocket.Chat credential currently appears as a default in `docker-compose.yml`. It
  must be removed and rotated before remote work.
- Current digest context uses Voice Channel project documents globally rather than
  explicit per-room matter mapping.
- The existing UI and backend contain useful but coupled assumptions around digest state.
- Browser speech behavior varies by platform and browser.

### Routing and summarization

- Agent names, room names, and project terminology may be transcribed incorrectly.
- A model can route confidently and still be wrong.
- Long responses can lose important detail during summarization.
- Multiple overlapping worker events may be difficult to correlate without ACLI emitting
  explicit task IDs.
- Model-provider availability and output formats can change.

### Omi

- Official quick-start paths use Omi infrastructure.
- Full upstream backend deployment is operationally heavy.
- Firebase authentication and other vendor services are woven into the current app.
- The App Store application is not proof of a fully self-hosted path.
- Background microphone, audio session, and iOS lifecycle behavior require device tests.
- Maintaining a large fork may cost more than building a small client.
- Existing MIT source remains usable, but hosted services and future upstream releases
  can change or become paid.

### iPhone/local speech

- Apple's on-device recognition availability varies by language, device, and installed
  assets.
- Local STT uses battery and may be less accurate on technical language.
- Large Whisper models can exceed practical mobile memory, heat, and latency budgets even
  when their files fit on disk.
- Reliable iPhone distribution and background behavior involve Apple signing and platform
  constraints.

### Remote connectivity

- Cloudflare may process connection metadata and terminate TLS depending on configuration.
- Mobile background connections may be suspended.
- Push notifications may require Apple infrastructure even when application data remains
  self-hosted.
- Exposing any command surface increases the security burden.

## 9. Test Strategy

### Unit tests

- contract validation;
- confidence thresholds;
- room/agent allowlists;
- event-to-task state transitions;
- summary source retention;
- confirmation snapshot immutability;
- nonce/idempotency behavior;
- retention and cleanup.

### Contract tests

- every client against the same gateway fixtures;
- every STT/TTS/model provider against common success/error fixtures;
- Rocket.Chat adapter against recorded response fixtures;
- schema compatibility across versions.

### Component tests

- local text client to gateway;
- gateway to mocked Rocket.Chat;
- event reducer with interleaved agent traffic;
- speech fallback behavior;
- provider timeout and cancellation.

### Live integration tests

- real Rocket.Chat room discovery and history;
- one confirmed test message;
- exact message ID and nonce deduplication;
- real ACLI routing and completion event correlation;
- gateway restart during active work;
- model-provider failure while basic reads and sends continue.

### Privacy tests

- no raw audio after configured cleanup;
- no credentials in job bundles, logs, or client packages;
- outbound-domain capture for Mac and mobile;
- Omi domains blocked during the mobile self-host test;
- project/room cross-context probes;
- retention boundary tests.

### Security tests

- expired/replayed confirmation;
- changed room or draft after confirmation;
- invalid client token;
- revoked client;
- unauthorized room;
- path traversal and symlink escape;
- rate limiting;
- malformed and oversized audio/text payloads.

### Human acceptance tests

- natural speech with project and agent names;
- interruption and repeat;
- “give me the short version” versus “read the full answer”;
- two simultaneous workers;
- ambiguous destination;
- network loss;
- Mac sleep/restart;
- mobile foreground/background behavior when that phase begins.

## 10. Phase 0 - Direction, Preservation, and Contracts

Goal: preserve the existing work, establish the adaptive branch, document the mission,
and prevent client/provider lock-in before implementation resumes.

### P0-01. Preserve UI Track and Create Adaptive Branch

Status: `VERIFIED`

- Original branch retained: `feature/full-screen-voice-console`.
- Adaptive branch created and checked out: `codex/adaptive-voice-gateway`.
- Existing ACLI runtime changes left untouched.

### P0-02. Discovery Record

Status: `VERIFIED`

- `DISCOVERY_RECORD.md` records the complete substantive conversation, corrections,
  candidate analysis, priorities, decisions, and open questions.

### P0-03. Mission and Objectives

Status: `VERIFIED`

- `NORTH_STAR.md` and `OBJECTIVES.md` reflect the Mac-first, client-neutral direction.
- ACLI and Rocket.Chat roles are explicit.
- Mobile and Omi are later experiments.

### P0-04. Adaptive Architecture and Plan

Status: `VERIFIED`

- Adapter boundaries, contracts, permissions, storage policy, tools, limitations, tests,
  gates, and pivots are documented here.

### P0-05. Baseline Verification

Status: `VERIFIED` (2026-07-27)

Evidence:

- `python3 -m unittest discover -s tests` passed 29 tests with one intentional live
  dependency skip.
- `node --test tests/*.test.js` passed 11 tests.
- `git diff --check` was run after documentation cleanup.
- Documentation consistency scanning confirmed that Mac-first mechanics precede small AI
  and mobile work.
- Phase 0 product scope is limited to the branch and documentation files. Pre-existing
  ACLI runtime changes remain unstaged and untouched.

### Phase 0 Gate

Pass when P0-01 through P0-05 are verified and the current documentation contains no
contradictory claim that mobile or Omi is the first milestone.

## 11. Phase 1 - Local Mac Mechanical Loop

Goal: complete one useful text-first and voice-capable loop entirely on the Mac without
requiring communication AI or mobile.

### M1-01. Gateway Contract Skeleton

Status: `VERIFIED` (2026-07-27)

- Implemented versioned interaction (`InteractionRequest`), interpretation (`Interpretation`), confirmation (`ConfirmationSnapshot`), task state (`TaskState`), task event (`TaskEvent`), and result (`GatewayResult`) models in `app/contracts.py`.
- Added schema version validation (`validate_schema_version`), rejecting unsupported versions cleanly with explicit errors.
- Surfaced gateway schema version in `/api/status` endpoint while preserving full endpoint compatibility.
- Added comprehensive unit and contract test suite `tests/test_gateway_contracts.py`.

Acceptance:
- Valid fixtures round-trip through JSON/dict serialization.
- Invalid fields and unsupported schema versions fail clearly with validation errors.
- Existing frontend and backend endpoints preserve full compatibility.

### M1-02. Local Client Adapter

Status: `VERIFIED` (2026-07-27)

- Added `/api/gateway/interact` endpoint in `app/main.py` converting text interaction requests into `Interpretation` and immutable `ConfirmationSnapshot` instances inside `GatewayResult`.
- Added `/api/gateway/confirm` endpoint in `app/main.py` executing confirmed snapshots with nonce deduplication, expiration enforcement, and returning `rocket_chat_msg_ids`.
- Implemented `cli/gateway_client.py` CLI fixture client adapter for deterministic command-line end-to-end execution.
- Adapted the existing browser composer to prepare a gateway interaction before confirmation and submit the returned immutable snapshot to `/api/gateway/confirm` rather than `/api/send`.
- Added comprehensive unit and integration test suite `tests/test_gateway_adapter.py`.

Acceptance:
- One text request can select a room, create a draft, confirm, send, and return a message ID.
- Executed deterministically without requiring an external AI model.

### M1-03. Task Supervisor

Status: `VERIFIED` (2026-07-30)

- Added a file-backed `TaskSupervisor` that binds every gateway confirmation to its creating
  interaction, persists task state atomically under the mounted ACLI state directory, and
  rejects unknown or forged confirmation snapshots.
- Converts ACLI routing, heartbeat, completion, failure, stop, and same-agent supersession
  into room-scoped task transitions while preserving Rocket.Chat source message IDs and
  deduplicating replayed history events.
- Added `GET /api/gateway/tasks/{interaction_id}` for clients to retrieve durable task state.
- Live E2E verification (2026-07-30): confirmed gateway dispatch `int_live_e2e_20260730_03` posted
  via `voice_gateway` user, ACLI verified HMAC signature, routed to `@claude`, echoed exact interaction ID,
  and worker completed with `LIVE_GATEWAY_E2E_OK`. Task reached terminal state `completed`.

Acceptance:
- Recorded interleaved fixtures remain correctly isolated;
- Refresh/restart recovers persisted state cleanly.
- One live confirmed ACLI request reaches a terminal state with source-linked routing.

### M1-03A. Rocket.Chat Dispatch Ingress

Status: `VERIFIED` (2026-07-30)

- Defined dedicated Rocket.Chat ingress identity `voice_gateway` with HMAC-SHA256 signed message envelopes.
- Gateway posts directly via `voice_gateway` into `#voice_channel` with visible audit headers.
- Carries gateway `interaction_id` in the signed envelope, which ACLI verifies, strips, and echoes back into routing trailers.
- Verified live signed envelope verification, replay/tamper protection, and exact interaction ID correlation.

Acceptance:
- One confirmed gateway request posted through Rocket.Chat produces an ACLI routing event with the same interaction ID;
- That request reaches a terminal ACLI event and the persisted task remains terminal after a gateway restart;
- Forged or replayed gateway confirmations cannot dispatch a second ACLI task.

### M1-04. Mac TTS Adapter

Status: `VERIFIED` (2026-07-30)

- Implemented `app/tts_adapter.py` providing native macOS `say` subprocess execution and Web Speech API abstraction without storing audio files on disk.
- Added `/api/gateway/tts/status`, `/api/gateway/tts/speak`, and `/api/gateway/tts/stop` endpoints in `app/main.py`.
- Added immediate playback interruption (`stop_tts()`) terminating active subprocesses cleanly.
- Added comprehensive unit and contract test suite `tests/test_tts_adapter.py`.

Acceptance:
- Live Mac playback works via `say` subprocess execution and Web Speech API fallback.
- Interruption via `stop_tts()` is immediate.
- Text remains usable and formatted when TTS is stopped or unavailable.

### M1-05. Mac STT Adapter

Status: `IMPLEMENTED (UNCOMMITTED)` (2026-08-07)

- The browser client now uses the Web Speech API for opt-in push-to-talk capture.
- Recognized text is appended to the editable composer and persisted through the
  existing room draft state. Recognition completion never sends a message; the typed
  confirmation-bound gateway flow remains the only send path.
- Current automated evidence: the focused Node harness verifies transcript append,
  draft sync, and no automatic send; the full suites passed 152 Python / 61 Node on
  2026-08-07.

Remaining closure:

- record one supported-browser Mac microphone check, including a cancellation/error
  path;
- commit the already-present product changes. No wake word or ambient capture is in
  scope.

### M1-06. Local Recovery

Status: `PARTIAL`

- Existing verified pieces cover draft persistence and restoration, file-backed task
  state across Gateway restart, startup orphan cleanup, response-assistant recovery,
  and deterministic provider fallbacks.
- A single formal Mac recovery scenario has not yet exercised browser refresh,
  Gateway restart, provider failure, and Rocket.Chat interruption together. This is
  a closure test, not a reason to rebuild the completed recovery mechanisms.

### Phase 1 Gate

Ed can complete one Mac voice/text interaction end to end without mobile and without a
model making routing decisions.

Pivot options:

- browser remains the Mac client;
- add a small native helper only for speech;
- pursue a native menu-bar client later without changing the gateway.

## 12. Phase 2 - Small Communication AI

Goal: add bounded intelligence without weakening deterministic mechanics.

### A2-01. Room and Agent Catalog

Status: `PARTIAL`

- Explicit room aliases, agent aliases, pronunciations, permission scopes, and recent-use
  hints.

### A2-02. Structured Router

Status: `NOT STARTED`

- Produce action, room, agent, draft, confidence, and alternatives.
- Configurable confidence threshold.
- No direct tool execution.

Acceptance:

- labeled routing fixture set;
- zero cross-room context leakage;
- ambiguous fixtures request clarification;
- deterministic manual selection remains available.

### A2-03. Draft Refiner

Status: `NOT STARTED`

- Improve clarity while displaying original and refined text.
- Preserve constraints, negation, file paths, and requested worker.

### A2-04. Grounded Summarizer

Status: `PARTIAL`

- The daily-use response assistant already produces a bounded two-paragraph digest
  from room-local context, with deterministic fallback. The formal source-inspection
  and outcome/blocker/decision contract remains to be closed.

- Use message/task IDs and bounded context.
- Separate “what happened,” “result,” “blockers,” and “next decision.”
- Provide full response on demand.

### A2-05. Follow-Up Suggestions

Status: `PARTIAL`

- The daily-use response assistant prepares one editable suggested message and never
  posts it automatically. The formal multiple-suggestion contract is not implemented.

- Two or three materially different suggestions.
- Insert into an editable draft only.
- Never send automatically.

### A2-06. Provider and Fallback Layer

Status: `PARTIAL`

- The response assistant has a bounded worker selection and deterministic fallback;
  the generic provider contract remains incomplete.

- API provider first if selected.
- local OpenAI-compatible endpoint option.
- deterministic rule-based status summary.
- health and timeout behavior.

### Phase 2 Gate

Routing and summaries are measurably useful on real ACLI conversations, grounded in
sources, and unable to bypass confirmation.

Pivot options:

- API model;
- local general model;
- hybrid deterministic alias matching plus model fallback;
- task-specific classifier.

## 13. Phase 3 - Operational Supervision and Concurrency

Goal: supervise several ACLI workers and rooms without confusion.

Tasks:

- persistent interaction queue;
- simultaneous task isolation;
- compact task dashboard;
- cancel/stop mapping;
- notification policy;
- unread/attention state;
- daily or on-demand operational briefing;
- task/source navigation.

Acceptance:

- two simultaneous real tasks remain correctly correlated;
- stale events do not complete the wrong task;
- restart does not lose active task identity;
- summaries identify uncertainty when correlation is incomplete.

Pivot:

- if Rocket.Chat messages cannot reliably correlate tasks, add explicit ACLI correlation
  IDs before expanding supervision features.

## 14. Phase 4 - Client-Neutral Gateway Hardening

Goal: make the gateway safe for a second client and future remote access.

Tasks:

- stable external API;
- SSE event stream;
- client registration;
- scoped revocable tokens;
- rate and size limits;
- audit UI/CLI;
- secret removal and rotation;
- Cloudflare threat model and configuration;
- network-loss and replay tests.

Acceptance:

- a second local fixture client works without frontend-specific logic;
- external access exposes no privileged internal endpoint;
- credential and privacy scans pass;
- basic Rocket.Chat operation survives all AI-provider outages.

## 15. Phase 5 - Omi Mobile Feasibility

Goal: answer whether Omi is a practical self-hosted client before committing to a large
fork.

### O5-01. Source and Network Inventory

- Pin an Omi commit.
- Inventory Firebase, analytics, crash reporting, authentication, push, backend, STT, TTS,
  and model endpoints.
- Produce an outbound-domain allowlist.

### O5-02. Build and Install

- Build the Flutter application from source.
- Install on a physical iPhone.
- Point it at a test gateway URL.

### O5-03. Self-Hosted Text Loop

- Text request and event response through the gateway.
- Block Omi service domains.

### O5-04. Local Speech Loop

- Apple on-device STT.
- Local iPhone system TTS.
- No durable audio.

### O5-05. Rocket.Chat Vertical Slice

- Read one room.
- Summarize.
- Draft and explicitly confirm one request.
- Track and speak the response.

### Omi Acceptance Gate

Pass only when:

- Omi service domains are blocked and the core flow still works;
- durable data stays on Ed-controlled systems;
- no Omi account/subscription/API key is required;
- background/foreground behavior is acceptable;
- maintenance burden is lower than a small custom client;
- network and device tests are documented.

If rejected, record `EXPERIMENT REJECTED` and proceed to Phase 6 without changing the
gateway.

## 16. Phase 6 - Mobile Productization or Apple-Native Fallback

If Omi passes:

- minimize the fork;
- remove unused memory/lifelogging/backend features;
- create a reproducible signed build;
- add notifications and mobile recovery;
- maintain a narrow upstream merge policy.

If Omi fails:

- build a small Swift client first;
- use Apple Speech and AVFoundation;
- implement only chat, push-to-talk, approvals, task list, event stream, and playback;
- reuse the exact Phase 4 API.

The choice is a client decision, not an architecture rewrite.

## 17. Deferred Custom UI Track

The original three-pane Voice Channel UI can later:

- become the full Mac supervision console;
- reuse all gateway contracts and provider adapters;
- add settings, source inspection, accessibility, and refined layout;
- coexist with mobile and CLI clients.

No adaptive-fork decision prevents that work.

## 18. Current Planning Boundary

`M1-01` through `M1-04`, including signed Rocket.Chat ingress, are complete and must
not be reopened absent a regression. The daily-use console has also completed U-00
through U-10. The still-open mechanical closure is limited to committing and live-checking
M1-05 and recording the composite M1-06 recovery scenario.

The per-channel narration work is tracked in `URGENT_IMPLEMENTATION_PLAN.md` as U-11.
Its polling proof-of-concept is not the final architecture: the closing implementation
must use Gateway-owned Rocket.Chat event delivery, with only bounded watermark-based
reconciliation after reconnect. Channel-list recency must likewise update from message
events rather than a periodic refresh loop. U-12 then covers safe VS Code file links,
per-message Fast-voice read aloud, system-noise filtering, and image handoff through
the existing ACLI/Rocket.Chat inbox contract. Rocket.Chat remains the durable log; no
new full-text journal is planned.

Automatic Nice Voice preparation remains deliberately separate: `voice_active` currently
enforces the narration dependency but does not yet synthesize or cache audio.

Do not begin Omi installation, Cloudflare exposure, ambient audio, or mobile signing
before Ed selects the next bounded phase and the affected work is committed.
