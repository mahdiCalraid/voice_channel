# Voice Channel Objectives

## Immediate Objective
Create a dedicated Rocket.Chat-backed matter named `voice_channel` at `/Users/ed/King/clawd_2/voice_channel` for gradual research and implementation of a voice interface over agent channels.

## Current Channel Contract
- Local folder: `/Users/ed/King/clawd_2/voice_channel`
- Rocket.Chat channel: `#voice_channel`
- Rocket.Chat room ID: `6a54579eb53b70a1d1b5bb94`
- Main user: `ed`
- Dispatcher/bot: `acli_bot`
- Active agents: `gemini`, `agy`, `codex`, `claude`, `pplx`, `cursor`, `grok`

## Product Objectives
1. Build a lightweight narrator for selected Rocket.Chat updates.
2. Build a voice command interface for channel summaries and agent interviews.
3. Support interrupt, pause, repeat, slower/faster, skip, and switch-channel controls.
4. Allow spoken or typed draft replies with explicit confirmation before posting.
5. Preserve Rocket.Chat as the canonical transcript and audit trail.
6. Keep the system useful without requiring a full video-call interface.

## Research Objectives
1. Compare OpenAI Realtime API, Gemini Live, Pipecat, LiveKit, ElevenLabs, and browser-native speech APIs for this specific use case.
2. Prototype the smallest reliable local narrator before any full realtime voice-agent integration.
3. Identify where LiveKit is genuinely useful and where it adds unnecessary complexity.
4. Define a stable Rocket.Chat tool surface for room history, summaries, agent prompts, and message posting.
5. Evaluate cost, latency, interruption handling, privacy, and operational reliability.

## Engineering Objectives
1. Read Rocket.Chat through official REST or realtime APIs.
2. Avoid scraping the Rocket.Chat UI.
3. Keep raw audio out of durable storage unless explicitly needed.
4. Keep all outgoing Rocket.Chat writes confirmation-gated.
5. Make the local dev flow startable with a clear command and health check.
6. Add tests around room lookup, history reads, deduplication, summarization prompts, and confirmed message sends.

## Non-Goals For The First Milestone
- No video conference UI.
- No agent avatar/video presence.
- No required LiveKit dependency.
- No phone/SIP support.
- No automatic posting to Rocket.Chat without confirmation.
- No broad refactor of existing ACLI or NemoClaw systems.

## First Milestone
Build a text-driven voice-console skeleton:
1. List target rooms.
2. Fetch recent messages.
3. Produce a concise digest.
4. Read the digest aloud using a simple TTS path.
5. Accept basic controls: pause, repeat, skip, summarize, ask agent, draft reply.

Only after this works should realtime speech-to-speech be introduced.
