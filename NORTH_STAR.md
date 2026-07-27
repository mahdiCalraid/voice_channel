# NORTH STAR: Adaptive Voice Gateway

## Mission

Create a self-hosted, client-neutral communication and supervision layer for Ed's
existing Rocket.Chat, ACLI, nc2, and local-file systems.

The primary intelligence and work already exist:

- ACLI dispatches work to CLI workers such as Codex and Claude Code.
- nc2 provides a smaller OpenClaw-style agent and memory layer.
- Rocket.Chat is the durable communication terminal, coordination transcript, and
  transport into those systems.
- Local project folders contain the authoritative plans, code, research, and artifacts.

This project must not replace those systems. It must make them easier to use by voice
and ordinary language, first on Ed's Mac and later through replaceable mobile clients.

## Primary User

Ed, a solo power user who spends most of his working time at his Mac and coordinates
multiple agents, projects, and Rocket.Chat rooms.

## Priority Order

1. Make the complete local Mac workflow reliable and useful.
2. Add small, bounded AI capabilities for routing, summarization, command refinement,
   task supervision, and follow-up suggestions.
3. Test mobile access after the local mechanics and client-neutral gateway contract work.
4. Use Omi if it passes practical self-hosting, privacy, reliability, and integration
   gates; otherwise build or adopt a simpler Apple-native client.
5. Resume and finish the custom Voice Channel UI/UX as a separate client track when it
   becomes the best use of effort.

## Core Experience

Ed can speak or type naturally and the system will:

1. Determine the intended Rocket.Chat room, ACLI worker, and communication action.
2. Ask for clarification when confidence is too low.
3. Show the exact destination and message before any consequential transmission.
4. Post through the existing Rocket.Chat/ACLI path.
5. Track routing, heartbeat, completion, failure, cancellation, and supersession events.
6. Summarize long agent responses without losing important decisions, cautions, blockers,
   or file references.
7. Offer a small number of useful next actions without taking them automatically.
8. Read the result aloud through a replaceable TTS provider.

## Architectural Principle

The stable center is the local Voice Gateway and its contracts. Every external surface is
an adapter:

```text
Mac UI / CLI / Omi / custom iPhone client / future wearable
                         |
                 Client-neutral gateway
                         |
       Speech + routing + summarization + supervision
                         |
                    Rocket.Chat
                    /         \
                 ACLI          nc2
                    \         /
                 Approved local files
```

No client, speech provider, model vendor, or hosted service may become necessary for the
core Mac workflow.

## Source-of-Truth Policy

- Rocket.Chat is the source of truth for operational communication and agent activity.
- ACLI is the source of truth for worker dispatch and execution state.
- Local project files are the source of truth for project content and plans.
- Existing approved memory systems remain authoritative for durable personal memory.
- Gateway state records interaction metadata and audit information, but does not create a
  competing copy of all Rocket.Chat history.
- Raw audio is not durably stored by default.

## Intelligence Boundary

The gateway may use a small model to:

- classify intent;
- select a room or agent;
- improve a draft while preserving meaning;
- reduce event streams into task state;
- summarize results;
- suggest possible follow-ups.

It must not silently:

- replace ACLI as the work engine;
- invent completed work;
- infer a risky destination at low confidence;
- send a message or execute an action without the required confirmation;
- read unapproved project folders;
- merge context from different rooms or projects;
- create an independent personal-memory authority.

## Self-Hosting and Privacy

- Durable transcripts, gateway state, summaries, configuration, and audio artifacts stay
  on Ed-controlled infrastructure.
- Model APIs may be used for transient inference when explicitly configured.
- Provider interfaces must allow later replacement with local models.
- Remote clients connect to the gateway, never directly to ACLI credentials or a
  privileged shell.
- The first remote design may use the existing Cloudflare-domain pattern, with
  authenticated device access and a narrowly exposed API.
- Omi-hosted storage or another vendor's durable conversation service is not a required
  dependency.

## Definition of Useful

The first useful release runs entirely on the Mac and lets Ed:

- speak or type a natural request;
- see the interpreted room, agent, and message;
- confirm the request;
- observe the real ACLI task state;
- receive a concise grounded summary;
- ask for the full response or a suggested follow-up;
- continue using basic Rocket.Chat reading and sending when AI or speech fails.

Mobile usefulness is a later gate, not a prerequisite for proving the system.

## Strategic Guardrails

- Mac first; mobile later.
- Contracts before client-specific code.
- Read-only before confirmed writes.
- Deterministic mechanics before probabilistic routing.
- One small vertical slice before feature breadth.
- Every external dependency must have an adapter and a fallback.
- Omi and Vellum are experiments, not architectural commitments.
- Preserve the existing custom UI/UX work as an independent future client.
- Verify claims against live Rocket.Chat and actual devices when those integrations exist.
