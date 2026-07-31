# Response Assistant Strategy

## Purpose

After a newly arrived Rocket.Chat item is classified by the backend as a genuine
`agent_response`, the active-room console performs one bounded AI call that produces:

1. an exactly two-paragraph narrator digest; and
2. one complete, editable next-message draft.

The draft is never sent automatically. It remains subject to the existing immutable
preview and explicit confirmation gate.

## Evidence Used

The combined call receives:

- the triggering real agent response;
- the configured last N real user/agent messages;
- operational events as secondary evidence only;
- the last three narrator summaries;
- the room profile from `automatic_system/channels.json`; and
- fixed-name, size-bounded documents from the registered matter folder.

System routing, heartbeat, membership, model-status, and gateway-envelope messages
cannot trigger the assistant and cannot be treated as worker conclusions.

## Coding Channels

The assistant infers the current phase from evidence rather than advancing merely
because a worker replied.

1. Planning normally moves from a Codex initial plan to Claude's broader judgment,
   then Grok's independent technical criticism.
2. Once the plan is actually ready, AGY implements one bounded named step.
3. Grok independently reviews a reported implementation.
4. Codex handles diagnosis, moderation, and small verified fixes.
5. Larger review findings return to AGY as a bounded remediation step, followed by
   Grok re-review.
6. Claude gives the overall checkpoint view and explicit green light.
7. Only after a green light does AGY receive the next already-planned step.

The channel's existing task identifiers are preserved. Completion, task identifiers,
and green lights are never invented. Explicit channel assignments or Ed's latest
instruction override the normal sequence.

## Non-Coding Channels

The coding rotation is not used. The assistant drafts one conservative, low-risk next
step for the channel's registered default worker. It must not draft an instruction
that publishes, submits, contacts someone, spends money, or performs an irreversible
action without Ed making that decision.

## Composer Safety

- Insert only when the composer is empty or still contains the previous generated
  suggestion.
- Never overwrite Ed's in-progress draft.
- Never change a composer locked by the confirmation gate.
- Keep the suggestion available as room-local state when insertion is unsafe.
- Never call the send or confirmation endpoints from the assistant flow.

## Failure Behavior

Invalid, partial, timed-out, or unavailable AI output is validated at the application
boundary. Missing components use a deterministic conservative fallback, and the API
reports whether the result was `ai`, `hybrid`, or `fallback`. Automatic failures retry
after a short backoff; an initial room-history load is only a baseline and does not
narrate old replies.
