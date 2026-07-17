# Implementation Plan: Message Classification, Transcript, and Summarization

Status: design finalized for next build round. Supersedes the informal staging notes in
the 2026-07-16 channel discussion. Written against the current running code in
`app/main.py` and `frontend/index.js` (Docker console on port 6891).

## 1. Why this exists

The console now proves connectivity (`/api/status`, `/api/rooms`, `/api/history` all work
against real Rocket.Chat rooms). The open problem is signal quality: the transcript and
narrator both currently treat every Rocket.Chat message as one flat kind of text. Ed's
instruction is to split messages into three kinds, each handled differently, and to make
the narrator summary genuinely goal-aware rather than a post-counter.

## 2. Message taxonomy (three lanes, not one)

Every message pulled from `/api/history` gets classified into exactly one lane before it
reaches the frontend. Classification happens server-side in `app/main.py`, not in the
browser, so the rule set has one place to live and the frontend just renders whatever
lane tag it's given.

**Lane A — System / dispatcher messages**
Source: Rocket.Chat `t` field (`au`, `ru`, etc.) and ACLI dispatcher patterns already
partially detected today (`is_routing`, "Routing to...", heartbeat pings, elapsed-time
notices, model-selection notices, stopped/attach notices).
Handling: never sent to the LLM, never read as prose. Parsed into a small structured
event and rendered as a compact UI indicator (a chip/badge in the transcript timeline),
not a paragraph.

Fields to extract per Ed's request, all deterministic regex/string parsing against known
ACLI message formats (no AI needed for this lane):
- `agent` (target agent name, e.g. `codex`, `gemini`)
- `model_provider` / `model_name` / `effort` (from routing/model-selection notices)
- `elapsed_seconds` or `waiting_since` (from heartbeat pings)
- `response_time_seconds` (time between `routing_ts` and the matching `agent_response`,
  derivable today from `acli/transcript_log.jsonl` fields already present:
  `routing_ts` vs `ts`)
- `stopped: bool` (from stop/attach/cancel notices)

Output shape (one object per system message):
```json
{
  "lane": "system",
  "kind": "heartbeat" | "routing" | "model_selected" | "stopped" | "attachment" | "membership",
  "agent": "codex",
  "model": {"provider": "openai", "name": "gpt-5", "effort": "high"},
  "elapsed_seconds": 42,
  "stopped": false,
  "raw_text": "..."
}
```
The UI renders this as an icon + one-line status, not a chat bubble. This directly
answers Ed's ask: "extract the information already in those messages and show it,"
not just hide it.

**Lane B — ACLI agent replies (substantive output)**
Source: any Rocket.Chat message from an agent identity (`codex`, `gemini`, `agy`,
`claude`, `pplx`, `cursor`, `grok`, or `acli_bot` relaying an agent) that is not itself a
system/dispatcher notice.
Handling:
- Rendered in the transcript **fully, with markdown formatting intact** — this is the
  concrete gap today: `index.js` currently injects `text` as escaped/plain content.
  Needs a markdown renderer (e.g. `marked.js` client-side, sanitized) so headings,
  code blocks, links, and bold text actually render instead of showing literal `**`/`#`.
- Fed into the summarization pipeline (Lane B is the primary substantive input to the
  narrator — this is "what changed / what completed / what is blocked" material).

**Lane C — Ed's own messages**
Source: messages where `u.username == "ed"` (or whatever the human account is).
Handling: not summarized as "content to analyze," just carried through the digest as
context markers — i.e., they establish what was *asked*, so the summary can say
"you asked X, and the result was Y." Rendered in the transcript like Lane B (full
formatting), but tagged distinctly so the summarizer knows to treat them as
instructions/questions rather than results.

Net rule: **System → indicator only. Agent replies → full render + summarize. Ed's
messages → full render + carry as intent/context, not as "output to summarize."**

## 3. Transcript rendering changes (`frontend/index.js`, `index.html`, `index.css`)

- Add a lane-aware renderer: system events become a small horizontal status chip
  (icon + agent + short fact, e.g. "codex · gpt-5/high · 42s elapsed"); agent/Ed
  messages become full chat-style cards.
- Add markdown rendering for Lane B/C text (client-side `marked` + a minimal sanitizer,
  since this is same-origin trusted content from Rocket.Chat, but still worth stripping
  `<script>`/event handlers defensively).
- Keep the full unfiltered transcript available (nothing is deleted, per Ed's "entire
  transcript should be readable" requirement) — system messages are compacted in
  presentation, not removed from the feed.

## 4. Summarization pipeline (the narrator)

Two-stage design, matching Codex's earlier proposal and Ed's "read previous messages +
project docs, give an overall picture" ask:

**Stage 1 — Deterministic reduction (no AI, cheap, already mostly buildable today)**
Input: raw `/api/history` messages for the room.
Output: the three-lane split above, plus a compact "event summary" of Lane A (e.g.
"codex worked 3 times today, avg response 38s, no stops") that doesn't need an LLM at
all.

**Stage 2 — AI summary over a context bundle, not just the latest batch**
Replace today's `generate_digest()` (which only sees the current message batch) with a
call that assembles:
1. Lane B + C messages from the current window (the "what just happened" material).
2. A short rolling memory of **prior summaries** for this room (so the narrator can say
   "continuing from earlier: the bug fix mentioned last hour is now confirmed done").
   Store this as a small JSON file per room, e.g. `acli/summary_history/<room_id>.json`,
   append-only, capped to last N summaries.
3. Matter context docs — `NORTH_STAR.md`, `OBJECTIVES.md`, `PROJECT_HANDOFF.md` — read
   once and cached, not re-read per request (they change rarely).
4. A single-purpose prompt asking explicitly for: what changed, what completed, what is
   blocked, what the current goal is, what's next — this is a rewrite of the existing
   prompt in `generate_digest()`, not a new endpoint.

Auditability requirement (Ed/Codex both flagged this): the API response includes the
exact list of source message IDs that were fed into the summary, so the narrator panel
can show "based on 6 messages" with an expandable list — no black-box digest.

```json
{
  "digest": "...",
  "included_message_ids": ["abc123", "def456"],
  "prior_summary_used": true
}
```

## 5. Concrete build order (smallest useful increments first)

1. **Backend: system-message parser.** Add a `classify_message()` function in
   `main.py` that tags each history item with `lane` and, for Lane A, a parsed `kind`
   + extracted fields (model/effort/elapsed/stopped). Ship as an addition to
   `/api/history`'s response — do not remove existing fields, just add `lane`/`event`.
2. **Frontend: lane-aware rendering.** Update `index.js`/`index.css` to render Lane A as
   compact chips and Lane B/C with markdown (`marked.js`, CDN or vendored, plus basic
   sanitization). This is the fix for both today's escaped-markdown complaint and the
   noisy-transcript complaint, without deleting any messages.
3. **Backend: response-time + rolling stats.** Compute `response_time_seconds` by
   matching `routing_started` → next `agent_response` per agent, expose as part of the
   Lane A event so the UI can show it without new AI calls.
4. **Backend: context-bundle summarizer.** Rewrite `/api/digest` to pull in the matter
   docs + prior summary file + Lane B/C messages, return `included_message_ids`. Persist
   each generated summary to `acli/summary_history/<room_id>.json`.
5. **Frontend: auditable narrator panel.** Show the digest text plus a collapsible
   "sources" list resolved from `included_message_ids` against the transcript already in
   memory.

Step 1–2 are pure signal/rendering fixes and ship without any AI changes — matches the
existing project preference (seen in Codex's staging notes) to keep deterministic
parsing separate from and prior to AI summarization. Steps 3–5 build the "second overall
overview" capability Ed described (project-aware, cross-turn narrator) on top of that
foundation rather than trying to do it in one leap.

## 6. Explicit non-goals for this round

- No change to the realtime/voice transport layer — still text/digest/TTS-in-browser.
- No multi-room cross-summarization yet — one room's context bundle at a time.
- No new persistence system beyond a flat JSON file per room for summary history; not a
  database migration.
