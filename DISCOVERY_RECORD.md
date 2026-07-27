# Discovery and Direction Record

Date established: 2026-07-27

This document preserves the complete substance of the discovery conversation that led to
the Adaptive Voice Gateway fork. It is a chronological decision record rather than a
verbatim transcript. The repository history and Rocket.Chat remain the durable sources
for exact implementation events.

## 1. Original Repository Review

The discussion began with confirmation that the working folder was:

`/Users/ed/King/clawd_2/voice_channel`

The repository was reviewed to understand its mission, implementation, and development
phases. At that time:

- Voice Channel was described as an audio-first console over Rocket.Chat.
- The custom browser UI provided room navigation, transcripts, a composer, confirmation,
  digest generation, and browser speech.
- Phase 1 of the existing implementation plan was documented as complete.
- The automated test suite passed.
- The next documented work was the reusable AI foundation.
- A security issue was identified: a Rocket.Chat credential was present as a default in
  `docker-compose.yml`. That credential must be removed and rotated before remote
  exposure or production use.
- The local service was not running during that review, so live health could not be
  reverified in that session.

The original project direction was sound but assumed the custom Voice Channel UI would
remain the main user-facing product.

## 2. Initial Personal-Assistant Exploration

Ed then explored whether a more developed open-source system could connect the local
work environment to his phone and personal operations. Candidates included:

- Omi;
- Vellum;
- EverOS/EverMind;
- Fabric.

The first analysis treated these as possible layers of a general personal assistant:

- Omi as a wearable/mobile capture surface;
- Vellum as a multi-channel assistant runtime;
- EverOS as a memory layer;
- Fabric as a pattern library.

That analysis correctly distinguished their technical layers, but initially assigned too
much architectural importance to the external assistant platform.

## 3. Corrected System Model

Ed clarified the actual system:

### ACLI

- Location: `/Users/ed/King/clawd_2/development_channel`
- Primary intelligence and work engine.
- Dispatches CLI workers such as Codex and Claude Code.
- Performs substantive research, coding, file operations, and project execution.

### Rocket.Chat

- Implementation location: `/Users/ed/King/clawd_2/rc`
- Communication terminal and durable operational transcript.
- Routes requests into ACLI.
- Provides additional modules such as image sharing with workers.

### nc2

- Location: `/Users/ed/King/clawd_2/nc2`
- A smaller and less mature OpenClaw-style system.
- Provides agents and memory-related functionality.
- Secondary to ACLI for primary work.

### Local files and folders

- Hold project plans, source code, research, memory, and work artifacts.
- Remain under Ed's control.

This clarification produced the central architectural correction:

> Voice Channel, Omi, Vellum, or another client is not the main intelligence. It is a
> communication and supervision shell over Rocket.Chat and ACLI.

## 4. Why Voice Channel Exists

Ed identified two reasons for building a layer above Rocket.Chat:

1. Communicate more naturally, especially by voice and from a phone.
2. Add a small amount of communication intelligence so he does not always need to read
   long responses carefully or write detailed commands manually.

The desired added intelligence includes:

- interpreting informal speech;
- selecting the intended room and worker;
- refining a request without changing its meaning;
- summarizing long agent responses;
- describing status and blockers;
- offering useful next questions or actions;
- tracking several active workers;
- reading results aloud.

This intelligence is intentionally smaller and narrower than ACLI.

## 5. External Tool Evaluation

### Omi

Omi appeared promising because it provides open-source mobile and desktop clients,
audio capture, transcription infrastructure, TTS integration, SDKs, webhooks, custom
tools, and wearable support.

Important findings:

- The normal Omi quick-start uses Omi-hosted infrastructure.
- The Flutter mobile application can be configured with a custom backend URL.
- Omi's full backend is substantial and includes services such as Firebase, databases,
  caches, transcription providers, and model integrations.
- The mobile source includes custom and self-hosted STT provider support.
- The iOS on-device transcription path currently uses Apple's native speech recognizer
  with on-device recognition requested.
- Omi's voice playback currently requests backend-generated speech and falls back to the
  device's system TTS.
- The repository is MIT-licensed, so a received version can be forked and maintained.
- Hosted Omi APIs, accounts, builds, and future upstream changes remain possible sources
  of operational dependence.

Conclusion: Omi is a strong client experiment, but its cloud and full backend must not
become required infrastructure.

### Vellum

Vellum appeared strong for a quick assistant prototype because it includes channels,
skills, tools, approvals, memory, a Mac application, and multiple communication
surfaces.

Important limitation:

- Its documented first-party iOS experience is associated with the cloud-hosted
  assistant path, while local hosting centers on the Mac.

Conclusion: Vellum remains a candidate only if a practical test proves that durable
conversation data and execution can stay on Ed-controlled infrastructure.

### EverOS

EverOS is relevant as a portable long-term memory and retrieval system. It is not a
complete replacement for the communication shell, Rocket.Chat, or ACLI.

Conclusion: evaluate only later and in shadow mode. Do not create another memory
authority during the gateway work.

### Fabric

Fabric is a reusable collection of reasoning patterns and workflows rather than a
persistent assistant shell.

Conclusion: selected patterns may later become versioned gateway AI tasks. Fabric should
not own communication, memory, or execution.

## 6. Self-Hosting Requirement

Ed requires:

- durable chat history to remain on his infrastructure;
- local project files and memory to remain local;
- no dependency on an external vendor to preserve or access conversation history;
- the option to use OpenAI or another model API for transient inference;
- the option to replace API-backed inference with local models;
- remote access through a controlled domain pattern similar to the existing
  Cloudflare-connected Rocket.Chat deployment.

The system may use a hosted model for speech recognition, summarization, or routing when
explicitly configured, but the provider must not become the authoritative history or
memory store.

## 7. Mobile Questions and Findings

The discussion examined:

- who owns the phone application;
- how the phone connects to the Mac;
- where speech and audio are stored;
- whether STT and TTS require Omi services;
- whether a roughly 1-1.5 GB offline model is necessary;
- the risk that Omi later closes its platform or charges for services.

The resulting design:

- The phone client connects to the Voice Gateway, not directly to Rocket.Chat or ACLI.
- The gateway holds credentials and runs on the Mac.
- Cloudflare may provide the authenticated remote transport.
- System TTS should be the first iPhone speech output; synthesized audio need not be
  durably stored.
- Apple on-device STT should be the first iPhone transcription path.
- Mac-hosted Whisper is the next private option if Apple STT is insufficient.
- A model API is an optional provider, not a required architectural layer.
- A large on-phone Whisper model is a later optimization, not an initial requirement.
- A fork should continue working when Omi service domains are unavailable.

## 8. Final Priority Correction

Ed clarified that mobile is not the first priority. The agreed order is:

1. Complete all mechanics locally on the Mac.
2. Add small AI capabilities for routing, summaries, suggestions, and communication.
3. Test Omi on mobile.
4. If Omi fails practical tests, develop or adopt a simpler Apple-native client.
5. Keep all interfaces and providers replaceable so the system can pivot without
   replacing the core.

The custom Voice Channel UI/UX remains valuable and should be finished later. It is now
one possible client of the gateway rather than the only intended product surface.

## 9. Fork Decision

The existing UI branch is preserved:

`feature/full-screen-voice-console`

The new adaptive branch is:

`codex/adaptive-voice-gateway`

The adaptive branch begins from the verified Voice Channel foundation and changes the
development priority and architecture. It does not discard the existing backend or
future UI work.

## 10. Settled Decisions

- Mac-first development.
- Rocket.Chat remains the canonical operational transport.
- ACLI remains the primary intelligence and work engine.
- nc2 remains a secondary agent/memory system.
- The gateway provides only bounded communication intelligence.
- Mobile clients are adapters.
- Omi is a candidate, not a dependency.
- Durable data remains self-hosted.
- Raw audio is not stored by default.
- Reads precede writes.
- Writes remain confirmation-gated.
- Every provider and client receives an explicit interface and fallback.
- Existing UI/UX work is preserved for later continuation.

## 11. Open Decisions

- Best local Mac STT implementation after the current browser path is evaluated.
- Best local Mac TTS voice and playback implementation.
- Whether the first Mac interface should remain the browser console, add a native menu
  bar client, or expose both.
- Whether Rocket.Chat polling is sufficient or realtime events are needed.
- Which small model/provider gives the best routing accuracy and latency.
- Whether Cloudflare Access, device certificates, or another pairing mechanism is most
  suitable for a future mobile client.
- Whether Omi can pass the no-vendor-storage and blocked-domain tests in a daily-use
  iPhone build.
- Whether a custom Swift client is preferable to maintaining a large Flutter fork.
- Whether any EverOS or Fabric component adds enough value to justify another
  dependency.

## 12. Research References

- Omi repository and architecture: https://github.com/BasedHardware/omi
- Omi mobile custom-backend setup:
  https://docs.omi.me/doc/developer/AppSetup
- Omi backend setup:
  https://docs.omi.me/doc/developer/backend/Backend_Setup
- Omi audio streaming:
  https://docs.omi.me/doc/developer/AudioStreaming
- Omi transcription architecture:
  https://docs.omi.me/doc/developer/backend/transcription
- Omi privacy policy:
  https://docs.omi.me/doc/info/Privacy
- Vellum channels:
  https://www.vellum.ai/docs/key-concepts/channels
- Vellum hosting:
  https://www.vellum.ai/docs/hosting-options
- EverOS:
  https://github.com/EverMind-AI/EverOS
- Fabric:
  https://github.com/danielmiessler/Fabric
- Whisper:
  https://github.com/openai/whisper
