# NORTH STAR: Voice Channel

## Vision
Create an audio-first command and conversation layer for Rocket.Chat channels so Ed can listen to agent activity, interrupt, ask questions, interview agents, and send confirmed replies without manually reading long channel transcripts.

The goal is not to recreate a video meeting. The goal is a practical voice console for existing agent channels: fast summaries, selective narration, live Q&A, and spoken control over Rocket.Chat-backed work.

## Primary User
- Ed, as a solo power user coordinating multiple CLI and Rocket.Chat agents.

## Core Experience
1. Ed opens or speaks to a dedicated voice interface.
2. The system can read important Rocket.Chat channel updates aloud.
3. Ed can ask questions such as "what changed in codinglab?", "ask Codex what is blocked", or "summarize the last hour".
4. The voice layer can query Rocket.Chat history and ask agents through the channel.
5. Any message sent back to Rocket.Chat is confirmed before posting.
6. The Rocket.Chat channel remains the durable transcript and coordination layer.

## Strategic Direction
- Build audio-first, not video-first.
- Start from a small reliable narrator and command console.
- Add realtime speech-to-speech only after text/history/tool routing is stable.
- Treat Rocket.Chat as the source of truth for channel history.
- Prefer APIs and explicit tools over UI scraping.
- Keep the first version local, debuggable, and restartable.

## Technical Preference
The current preferred architecture is:

```text
Rocket.Chat REST/Reatime APIs
        |
Voice orchestration backend
        |
OpenAI Realtime API or another voice provider
        |
Local browser or desktop voice console
```

Provider posture:
- OpenAI Realtime API plus WebRTC is the default candidate for low-latency, interruptible voice interaction with tool calls.
- LiveKit may be useful later as transport infrastructure, but it should not be the first critical dependency because the previous LiveKit-heavy video-call attempt was extremely buggy.
- Gemini Live and Pipecat are useful alternatives to evaluate after the baseline workflow exists.
- ElevenLabs is attractive for high-quality narration, but should not own the core channel orchestration unless that tradeoff is explicit.
- Browser `speechSynthesis` is acceptable for a quick local narrator MVP.

## Lessons From Prior `video_call` Attempt
The previous project at `/Users/ed/King/clawd_2/video_call` pursued a LiveKit/Next.js video-conference illusion with agent presence, avatar/video tracks, bridge services, and transcript sync. It became too brittle and did not reach a dependable daily-use workflow despite extensive back and forth.

This channel should avoid repeating that path:
- Do not make video or agent avatars part of the first milestone.
- Do not require LiveKit for the first working version.
- Do not put a complex media bridge before basic Rocket.Chat read/write, summarization, and confirmation flows.
- Do not chase the "conference room" illusion until the voice console is already useful.

## Definition Of Useful
The first useful version lets Ed say or click:
- "Read the important updates from voice_channel."
- "Summarize codinglab since 2 PM."
- "Ask Codex what it recommends."
- "Repeat that more slowly."
- "Skip this channel."
- "Draft a reply, but do not send until I confirm."

Success means Ed can supervise and converse with agent channels faster than reading walls of text.
