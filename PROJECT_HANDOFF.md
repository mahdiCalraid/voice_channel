# Project Handoff: Voice Channel

## Starting Point
This matter exists to replace the earlier video-call direction with a simpler voice-first channel workflow.

The previous attempt lives at `/Users/ed/King/clawd_2/video_call`. Its useful artifacts are the problem framing and Rocket.Chat bridge ideas. Its risky pattern is the LiveKit-heavy video-room implementation path.

## Current Setup Completed
- Created Rocket.Chat channel `#voice_channel`.
- Invited `ed`, `acli_bot`, and all ACLI agent users.
- Initialized ACLI matter files under `/Users/ed/King/clawd_2/voice_channel/acli`.
- Documented the North Star and objectives for future agents.
- Registered the matter with the ACLI runtime registry.
- Restarted the local ACLI daemon so it resolves and listens to `#voice_channel`.

## Agents In This Matter
- `gemini`
- `agy`
- `codex`
- `claude`
- `pplx`
- `cursor`
- `grok`

## Recommended Next Work
1. Verify ACLI daemon sees the new matter.
2. Send a short test mention in `#voice_channel` and confirm one agent replies.
3. Build a minimal Rocket.Chat history reader for this channel.
4. Build a local digest generator over recent messages.
5. Add simple TTS playback.
6. Only then evaluate OpenAI Realtime, Gemini Live, Pipecat, LiveKit, or ElevenLabs for the interactive layer.

## Guardrails
- Keep Rocket.Chat as the source of truth.
- Keep writes confirmation-gated.
- Avoid a video-call architecture until the audio-only path is useful.
- Avoid making LiveKit a required dependency for milestone 1.
- Prefer a small working loop over a polished media experience.

## Known Setup Incident
During initial daemon restart, Rocket.Chat membership system events (`t: "au"`) were briefly routed as default Codex requests because the ACLI poller did not ignore system messages. The local queue was cleared, the `voice_channel` poll cursor was anchored past those setup messages, and the poller was patched to mark system events processed without routing them.

## Useful Context From The Current Discussion
Ed wants the channels to feel more interactive and faster to work with: he wants to sit with the agents, interview them, talk through research, hear summaries, and avoid manually selecting text to read aloud.

The strongest current recommendation is an audio-first Rocket.Chat voice console: start with narration and command routing, then add realtime speech once the channel tools are stable.
