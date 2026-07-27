# Adaptive Voice Gateway Objectives

## Immediate Objective

Establish an adaptive fork of Voice Channel that reuses the verified Rocket.Chat
foundation while changing the development priority to:

1. a complete local Mac control loop;
2. bounded communication intelligence;
3. optional mobile clients after the gateway contract is stable.

## System Context

- Working folder: `/Users/ed/King/clawd_2/voice_channel`
- Original UI branch: `feature/full-screen-voice-console`
- Adaptive fork branch: `codex/adaptive-voice-gateway`
- ACLI implementation: `/Users/ed/King/clawd_2/development_channel`
- Rocket.Chat implementation: `/Users/ed/King/clawd_2/rc`
- nc2 implementation: `/Users/ed/King/clawd_2/nc2`
- Primary Rocket.Chat matter: `#voice_channel`

## Product Objectives

1. Provide one client-neutral gateway for Mac, CLI, web, Omi, Apple-native, and future
   clients.
2. Reuse Rocket.Chat as the canonical communication and agent-event transport.
3. Reuse ACLI as the primary dispatch and execution engine.
4. Make natural voice or text requests safe to route to the correct room and worker.
5. Track several concurrent requests without mixing their state or responses.
6. Summarize long replies and operational events into grounded spoken updates.
7. Produce optional follow-up suggestions and editable drafts.
8. Preserve explicit confirmation for consequential Rocket.Chat writes.
9. Keep the system useful when speech or AI providers are unavailable.

## Mac-First Objectives

1. Run the gateway and primary client locally on Ed's Mac.
2. Support local text input before speech.
3. Support replaceable local or API-backed STT and TTS.
4. Provide task status, interruption, cancellation, repeat, full-response, and summary
   controls.
5. Avoid requiring an Omi, Vellum, or other hosted account.

## Small-AI Objectives

1. Define structured routing output rather than accepting free-form model guesses.
2. Record confidence, alternatives, and reasons for ambiguous routing.
3. Ground summaries in explicit Rocket.Chat message IDs and task events.
4. Keep room and project context isolated.
5. Preserve the original user meaning when refining a command.
6. Never let suggestion generation invoke or send an action.
7. Allow OpenAI or another API initially while preserving local-provider adapters.

## Mobile Objectives

1. Begin only after the local Mac and gateway gates pass.
2. Test an Omi-derived client against the self-hosted gateway.
3. Require the client to work without Omi-hosted conversation storage.
4. Prefer Apple on-device STT and TTS for the first iPhone experiment.
5. Retain the option to build a smaller Swift/Flutter client if Omi is too coupled,
   unreliable, heavy, or difficult to distribute.
6. Keep the same gateway API and task semantics regardless of the selected client.

## Privacy and Security Objectives

1. Keep durable history and state on Ed-controlled systems.
2. Store no raw audio by default.
3. Keep Rocket.Chat, model, and provider credentials off mobile clients.
4. Pair remote devices and issue revocable, scoped credentials.
5. Expose only the gateway through the remote-access layer.
6. Audit reads, drafts, confirmations, sends, cancellations, and privileged actions.
7. Enforce allowlisted rooms, folders, and tool permissions.
8. Test that vendor domains can be blocked without breaking the self-hosted core.

## Engineering Objectives

1. Create explicit interfaces for clients, speech providers, edge-AI tasks, Rocket.Chat,
   and persistence.
2. Use versioned request, event, task, and result contracts.
3. Make long-running work observable and cancellable.
4. Use idempotency keys and immutable confirmation snapshots for writes.
5. Add deterministic unit, contract, integration, privacy, recovery, and live tests.
6. Keep ACLI runtime logs, sessions, inbox artifacts, and generated summaries out of
   product commits.

## Non-Goals

- Reimplement ACLI workers inside the gateway.
- Replace Rocket.Chat as the durable operational transcript.
- Create a new general-purpose personal-memory platform.
- Adopt all Omi, Vellum, EverOS, or Fabric features.
- Make a wearable or phone application the first milestone.
- Require continuous ambient recording.
- Store raw audio or full duplicated transcripts without an explicit retention policy.
- Allow unconfirmed posting or unrestricted remote shell execution.

## Phase 0 Completion Criteria

Phase 0 is complete when:

- the adaptive branch exists and is checked out;
- the prior UI branch remains intact;
- the new North Star and objectives are documented;
- the discovery conversation and decisions are recorded;
- the adaptive implementation plan defines phases, pivot points, tests, and acceptance
  criteria;
- documentation names known technical limitations and unresolved decisions;
- existing automated tests still pass;
- no unrelated ACLI runtime artifacts are included in the Phase 0 product changes.
