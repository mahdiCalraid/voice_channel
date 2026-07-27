# Adaptive Voice Gateway — Complete Project Handoff

Updated: 2026-07-27

This is the single handoff document for the new pathway of the Voice Channel project.
It is written for Ed, future Codex sessions, collaborators, and future agents who need
to understand not only what to build, but why the project changed direction.

The detailed chronological record is in `DISCOVERY_RECORD.md`. The executable task plan
is in `ADAPTIVE_IMPLEMENTATION_PLAN.md`. This handoff explains how those documents fit
together.

## 1. One-Sentence Mission

Build a self-hosted, Mac-first communication and supervision layer that lets Ed speak or
type naturally to his existing Rocket.Chat and ACLI system, receive concise grounded
updates, and control work safely from replaceable clients such as the existing browser
UI, a future Mac client, Omi, or a custom iPhone application.

The layer adds communication intelligence. It does not replace ACLI's intelligence or
workers.

## 2. Why This Project Exists

Ed already has a substantial local agent operating system:

- CLI workers perform actual coding, research, file operations, and other work.
- Rocket.Chat provides the practical terminal through which requests reach those workers.
- ACLI routes work to agents such as Codex and Claude Code.
- nc2 provides a smaller OpenClaw-style agent and memory layer.
- Local folders contain plans, source code, research, and other durable artifacts.

The problem is not the absence of intelligence. The problem is the interface around it.

Ed wants to:

- talk to the system more naturally than by manually composing every detailed message;
- use the system comfortably from the Mac where he spends most of his time;
- eventually use a phone or wearable voice interface;
- avoid reading every long agent response in full;
- hear what changed, what is blocked, and what decision is needed;
- ask for smart suggestions without surrendering control;
- supervise several agents without confusing their states;
- preserve the full Rocket.Chat transcript and local project context.

The original Voice Channel UI was a reasonable solution to that problem. The new insight
is that the custom UI should not be the only possible client. The durable investment
should be the local gateway and its contracts.

## 3. The Correct System Model

The systems have distinct roles and must not be collapsed into one “assistant brain.”

### ACLI: primary intelligence and execution

Location: `/Users/ed/King/clawd_2/development_channel`

ACLI is the main work system. It dispatches CLI workers, receives their progress, and
performs substantive work. It should remain the primary executor.

The gateway should not reimplement Codex, Claude Code, or the ACLI routing system. It
should communicate with ACLI through Rocket.Chat first, then interpret the resulting
events.

### Rocket.Chat: communication terminal and operational transcript

Location: `/Users/ed/King/clawd_2/rc`

Rocket.Chat is the durable operational conversation and coordination layer. It is the
place where requests are posted, agent responses arrive, worker events are recorded, and
the coordination history can be audited.

The first gateway should send and read through Rocket.Chat rather than bypassing it with
direct worker execution. That preserves current ACLI behavior and minimizes integration
risk.

### nc2: secondary agent and memory system

Location: `/Users/ed/King/clawd_2/nc2`

nc2 is useful and may become a future participant, but it is less mature than ACLI and
must not become a required dependency for the first local loop.

### Local files and folders: project truth

Plans, source code, research, memory, and artifacts remain on Ed's filesystem. Any AI
context builder must explicitly identify which files it read and keep room/project data
isolated.

### Voice Gateway: communication intelligence and safety

The gateway sits between clients and Rocket.Chat. It handles:

- speech/text input normalization;
- room and worker interpretation;
- request clarification;
- private drafts;
- confirmation gates;
- task correlation;
- event reduction;
- summaries and suggestions;
- client sessions and streaming events;
- local audit metadata;
- provider fallbacks.

It must remain smaller and more bounded than ACLI.

## 4. Final Priority Order

The project priorities were clarified after considering Omi, Vellum, EverOS, and Fabric:

1. Complete and verify all mechanics locally on the Mac.
2. Add small AI capabilities for routing, summarization, draft refinement, supervision,
   and follow-up suggestions.
3. Harden the client-neutral gateway and its remote security boundary.
4. Test Omi on mobile as an optional client.
5. If Omi is unreliable, too coupled, too cloud-dependent, or too difficult to maintain,
   build a smaller Apple-native client.
6. Return to the custom Voice Channel UI/UX when it becomes the best client experience.

Mobile is not the first milestone. Omi is not the architecture. The Mac loop is the
first proof that the idea works.

## 5. What We Are Building

The target interaction is:

```text
Ed speaks or types naturally
        |
Gateway interprets intent and shows destination
        |
Ed confirms if an external write is involved
        |
Gateway posts through Rocket.Chat
        |
ACLI routes to the appropriate worker
        |
Gateway tracks events and task state
        |
Gateway summarizes and optionally speaks the result
```

The gateway should support:

- list/select rooms;
- identify or confirm an ACLI worker;
- read recent history;
- summarize a room or task;
- ask an agent through Rocket.Chat;
- identify blockers and decisions;
- create editable reply drafts;
- request explicit confirmation;
- send exactly once;
- track several active tasks;
- cancel or stop work where the ACLI contract supports it;
- repeat, slow down, pause, or read the complete response;
- offer two or three possible follow-ups.

## 6. What We Are Not Building

- A replacement for ACLI workers.
- A second general-purpose autonomous agent that competes with ACLI.
- A video conference or avatar room.
- A required Omi or Vellum account.
- A new independent memory authority.
- An ambient-recording system as the first milestone.
- An unrestricted remote shell.
- Automatic Rocket.Chat posting without confirmation.
- A giant mobile project before the Mac flow works.

## 7. Architectural Shape

```text
Existing browser UI / Mac client / CLI / Omi / custom iPhone client
                              |
                     Client-neutral gateway
                              |
             STT + routing + summaries + supervision + TTS
                              |
                         Rocket.Chat
                         /         \
                      ACLI          nc2
                         \         /
                    local files and memory
```

The stable center is the gateway contract, not any individual client or model vendor.

Replaceable adapters include:

- browser, native Mac, CLI, Omi, and Swift/Flutter clients;
- Apple speech, local Whisper, and hosted STT;
- macOS system voice, iPhone system voice, local neural TTS, and hosted TTS;
- OpenAI, another API provider, Ollama, LM Studio, or another local model;
- Cloudflare Tunnel, a private VPN, or another authenticated remote transport.

## 8. Source-of-Truth and Privacy Rules

### Durable truth

- Rocket.Chat: operational communication and agent activity.
- ACLI: worker dispatch and execution.
- Local files: project and artifact truth.
- Existing approved memory system: durable personal memory.
- Gateway database/files: interaction metadata, task IDs, approvals, and audit records.

### Ephemeral by default

- raw audio;
- TTS audio;
- temporary STT files;
- temporary prompt bundles;
- provider debug payloads.

Audio should normally be transcribed, used, and deleted. Durable audio requires an
explicit future decision.

### Provider boundary

Using an OpenAI or other model API for transient routing, summarization, STT, or TTS is
acceptable if configured by Ed. However:

- API keys stay on the Mac;
- clients never receive provider secrets;
- vendor history is not authoritative;
- local adapters remain possible;
- provider prompts and outputs are bounded and audited according to retention policy.

### Remote boundary

The phone or future client connects only to the gateway. It never receives Rocket.Chat
bot credentials and never directly exposes ACLI or a privileged shell.

Cloudflare may be used like the existing Rocket.Chat deployment, but the public surface
must be a narrow authenticated gateway. Device pairing, revocable tokens, expiration,
rate limits, replay protection, and request-size limits are required before exposing it.

## 9. Communication Intelligence Boundary

The small AI layer may:

- classify intent;
- select a room or worker;
- refine a draft while preserving constraints;
- summarize event streams and completed responses;
- identify blockers, decisions, and missing information;
- suggest follow-up questions or actions.

It may not silently:

- execute work that ACLI did not receive;
- invent completion or progress;
- send a message without the required confirmation;
- choose a risky room at low confidence;
- read unapproved folders;
- mix context from separate rooms or projects;
- mutate durable memory just because a model suggested a fact.

Every interpretation should include:

- action;
- room;
- worker;
- draft text;
- confidence;
- alternatives;
- clarification requirement;
- source context identifiers.

## 10. Task Lifecycle

Each interaction receives a local ID and follows a visible state machine:

```text
captured
  -> interpreting
  -> needs_clarification
  -> awaiting_confirmation
  -> posted
  -> routed
  -> working
  -> completed / failed / cancelled / superseded
  -> summarized
  -> spoken
```

This is required for simultaneous work. A heartbeat or completion message must not be
allowed to update the wrong task merely because two agents are active in the same room.

The state record should contain:

- interaction ID;
- client and actor identity;
- room and worker;
- Rocket.Chat source message IDs;
- task timestamps;
- current state;
- cancellation state;
- final response and concise summary;
- file/source references;
- provider/model metadata;
- error and fallback information.

## 11. Safety Tiers

| Tier | Examples | Requirement |
| --- | --- | --- |
| Read | room history, status, approved file search | authenticated request |
| Prepare | route, summarize, refine, draft, suggest | private output only |
| Commit | Rocket.Chat send, cancellation, approved file mutation | immutable confirmation |
| Privileged | shell, credentials, deployment, deletion | separate future design |

Voice input never lowers the permission tier. A spoken command still requires the same
confirmation as a typed command.

## 12. Candidate Tools and Their Proper Roles

### Omi

Omi is a candidate mobile/Mac/wearable client. Its open-source Flutter app, audio path,
custom backend URL, custom tools, and on-device speech options make it worth testing.

The normal Omi setup uses Omi-hosted services, and the full backend is substantial. Omi
must therefore be evaluated as a client experiment, not adopted as required storage or
the main brain.

The Omi gate is:

- block Omi service domains;
- use our gateway URL;
- prove text, speech, and playback still work;
- prove no durable conversation data leaves Ed's infrastructure;
- prove no Omi subscription/API key is required;
- measure maintenance and device reliability.

If Omi fails, the gateway remains unchanged and we build a smaller Apple-native client.

### Vellum

Vellum is a strong assistant runtime candidate with skills, permissions, Mac access, and
multiple communication channels. It is less attractive for the first path because its
mobile experience and cloud/local hosting boundaries must be proven against Ed's strict
self-hosting requirement.

It may be tested later as another client adapter, never as the required system center.

### EverOS

EverOS may be evaluated as a memory/retrieval component, but only in shadow mode. It
must not replace Rocket.Chat, local project files, or the existing memory authority until
retrieval quality, ownership, retention, and write behavior are proven.

### Fabric

Fabric can contribute selected reasoning patterns for summaries, comparisons, decisions,
and research extraction. Those patterns should become versioned gateway tasks, not an
additional runtime or memory authority.

## 13. Speech Design

### Mac first

Start with the simplest local path that can be tested reliably:

- existing browser speech for immediate proof;
- macOS system speech helper if needed;
- Mac-hosted Whisper if better technical accuracy is required;
- hosted STT/TTS only when deliberately selected.

### iPhone later

The phone application would be owned by our fork or our own small client. Its job is to
capture, display, approve, and play—not to own the history.

First STT choice: Apple's on-device speech recognition. It avoids a large downloaded
Whisper model and avoids sending audio to Omi.

Second STT choice: send audio to a private Whisper service on the Mac.

Third STT choice: a configured hosted API.

First TTS choice: Apple's local system voice. It synthesizes and plays text without
durable audio storage.

Higher-quality TTS can be added later as a provider returning temporary audio or a
stream. The gateway should still preserve the spoken text and source references locally.

## 14. Repository and Branch State

Working folder:

`/Users/ed/King/clawd_2/voice_channel`

Original UI branch:

`feature/full-screen-voice-console`

Adaptive branch currently checked out:

`codex/adaptive-voice-gateway`

Phase 0 commit:

`1c7a017 docs: establish adaptive voice gateway phase 0`

The existing UI implementation plan remains available as historical evidence in
`IMPLEMENTATION_PLAN.md`. The adaptive plan is `ADAPTIVE_IMPLEMENTATION_PLAN.md`.

Pre-existing ACLI runtime changes in `acli/`, screenshots, and scratch artifacts are
user-owned working data. Do not stage or delete them as part of product commits.

## 15. What Phase 0 Completed

Phase 0 established the new pathway without changing product code:

- created and checked out the adaptive branch;
- preserved the original UI branch;
- recorded the substantive discussion and corrected system model;
- rewrote the North Star, objectives, README, and handoff references;
- created the adaptive implementation plan;
- documented interfaces, tools, limitations, privacy rules, tests, and acceptance gates;
- ran 29 Python tests with one intentional live dependency skip;
- ran 11 Node frontend tests;
- ran documentation consistency and diff checks;
- committed only the seven intended documentation files.

## 16. Phased Pathway

### Phase 0: direction and preservation — complete

No client-specific commitment. No mobile implementation.

### Phase 1: local Mac mechanics

Build the versioned gateway contract, adapt the existing Mac client, correlate ACLI
events, add local TTS/STT adapters, and prove text/voice interactions without AI routing.

Gate: Ed can complete one Mac interaction end to end with deterministic room/worker
selection and safe confirmation.

### Phase 2: small communication AI

Add structured routing, draft refinement, grounded summaries, follow-up suggestions, and
provider fallback.

Gate: AI is measurably useful, source-grounded, room-isolated, and cannot bypass safety.

### Phase 3: supervision and concurrency

Add simultaneous task tracking, cancellation, notifications, attention state, and source
navigation.

Gate: two or more active ACLI tasks remain correctly separated through completion,
failure, interruption, and restart.

### Phase 4: gateway hardening

Stabilize the external API, event stream, client identities, scoped tokens, audit view,
limits, replay protection, and remote-access boundary.

Gate: a second local fixture client works without frontend-specific assumptions and no
privileged endpoint is publicly exposed.

### Phase 5: Omi feasibility

Pin an Omi commit, inventory its network and storage behavior, build the app, point it to
our gateway, test local speech/playback, and block Omi domains.

Gate: Omi passes the self-hosted, reliability, and maintenance tests.

### Phase 6: mobile decision

If Omi passes, minimize and maintain the fork. If it fails, build a small Swift or
Flutter client with Apple Speech and AVFoundation against the unchanged gateway API.

The client decision does not rewrite the core architecture.

### Future UI track

Return to the three-pane Voice Channel UI when it offers enough value. It becomes one
client of the gateway, alongside the Mac/CLI/mobile options.

## 17. Technical Test Requirements

### Unit and contract tests

- schema validation and version compatibility;
- confidence and ambiguity behavior;
- state transitions;
- confirmation immutability;
- nonce/idempotency;
- source retention;
- provider timeouts and fallback;
- cleanup and retention;
- all client/provider adapters against common fixtures.

### Live tests

- real room discovery and history;
- one real confirmed test message;
- exact Rocket.Chat message ID and duplicate prevention;
- real ACLI routing and completion correlation;
- gateway restart while work is active;
- AI-provider failure while basic operations continue.

### Privacy/security tests

- no raw audio left after cleanup;
- no credentials in mobile builds, prompts, or logs;
- Omi domains blocked during Omi feasibility;
- room/project context isolation;
- expired/replayed confirmations rejected;
- revoked devices rejected;
- path traversal and symlink escapes rejected;
- payload limits and rate limits enforced.

### Human tests

- technical names and project terms spoken naturally;
- interruption, repeat, pause, and full-response requests;
- ambiguous destinations;
- concurrent agents;
- network loss;
- Mac sleep/restart;
- iPhone foreground/background behavior when mobile begins.

## 18. Known Risks

- A default Rocket.Chat token remains in `docker-compose.yml`; remove and rotate it
  before remote exposure.
- The current digest path still has global project-context assumptions; room-scoped
  context belongs to the adaptive AI foundation.
- Browser speech varies by browser and operating system.
- Speech recognition may mishear agent, room, and project names.
- Model confidence does not guarantee routing correctness.
- Rocket.Chat event formats may not contain enough explicit task correlation data.
- Cloudflare adds an external transport dependency and metadata boundary.
- iOS background audio and signed-build workflows require physical-device testing.
- Omi's hosted defaults and large backend make it possible for a promising demo to fail
  the self-hosted maintenance gate.

## 19. Immediate Next Action

Implement `M1-01 Gateway Contract Skeleton` from
`ADAPTIVE_IMPLEMENTATION_PLAN.md`.

The first implementation should define and validate the interaction, interpretation,
confirmation, task-event, and result contracts. It should not install Omi, expose the
gateway through Cloudflare, add ambient recording, or build a mobile app.

## 20. Handoff Checklist for the Next Agent

Before making changes:

1. Read this file.
2. Read `NORTH_STAR.md`, `OBJECTIVES.md`, and `ADAPTIVE_IMPLEMENTATION_PLAN.md`.
3. Confirm the branch is `codex/adaptive-voice-gateway`.
4. Run `git status --short` and preserve existing ACLI runtime churn.
5. Inspect the current gateway/backend before adding new abstractions.
6. Define the smallest test before implementing `M1-01`.
7. Do not begin mobile work ahead of the Mac gate.
8. Do not stage credentials, logs, sessions, screenshots, or scratch artifacts.

The guiding question is:

> Does this change make Ed's existing ACLI/Rocket.Chat system easier and safer to use,
> while keeping every client and provider replaceable?
