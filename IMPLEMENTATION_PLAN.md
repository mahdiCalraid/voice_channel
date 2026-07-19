# Comprehensive Implementation Plan: Voice Channel

Status (updated 2026-07-19): The technical foundation is working end to end. Message
classification, lane-aware transcript rendering, routing statistics, auditable sources,
summary memory, and the independent worker layer have shipped. The default narrator now
uses the real Codex CLI with shared host authentication; live `/api/digest` calls produce
Codex-written summaries and the test suite passes. Parts I and II below preserve the
design and implementation history. Part III defines the next chapter: a full-screen,
three-pane supervision app with a channel-aware narrator, durable private AI sessions,
guided reply suggestions, explicit read-only matter-folder context, and high-quality TTS.

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

---

# Part II — Voice Channel Worker Layer (the AI brain)

Added 2026-07-17, incorporating Ed's directive, Codex's architecture reply, and this
agent's independent review. This supersedes Section 4's "Stage 2 = rewrite the OpenAI call
inside `generate_digest()`" approach: the AI stage no longer lives inline in the FastAPI
process. It moves to a separate, modular, CLI-first worker layer.

## 7. Why a separate worker layer (the problem it solves)

Today `/api/digest` hard-codes one path: build a prompt, call `openai_client` with
`gpt-4o-mini`, and on any failure drop to rule-based post-counting. Grok's live review
found this path is currently broken in production (`401 Unauthorized` → the "goal-aware"
narrator never runs; the user hears "ACLI Dispatcher worked on N updates"). That single
failure exposes three structural problems worth fixing properly, not patching:

1. **Provider lock-in.** The AI brain is welded to one SDK, one key, one model. Ed wants
   `agy` (a separate, cheaper CLI worker — *not* an ACLI room agent) as an option, and
   wants the choice of worker/provider to be config, not code.
2. **Single-purpose.** The AI call only knows how to make a digest. Ed explicitly asked
   that the engine not be limited to summarization — reply drafting, room status, and
   progress-vs-objectives reviews are coming.
3. **Untestable inline logic.** We have repeatedly shipped tests that re-implement
   production loops instead of exercising them (caught in Steps 1, 3). A file-bundle
   worker contract fixes this structurally: a job directory is a snapshot you can replay
   and assert on.

The design target: **the backend prepares a job (files on disk), invokes a selected
worker, and consumes structured output.** The worker is swappable; the task is a
parameter; credentials are passed by environment/config, never embedded in prompt files.

### Important distinction (Ed stated this explicitly)
- `ACLI` is the Rocket.Chat agent *execution/dispatch* system. The Voice Channel reads
  from RC but must **not** couple to ACLI's dispatch loop.
- This new worker layer is the Voice Channel's *own* AI task runner. It borrows ACLI's
  good ideas (worker registry, model/effort selection, session/transcript artifacts) but
  runs independently.
- The `agy` worker referenced here is a standalone CLI worker Ed can invoke with
  credentials — a different CLI worker from the ACLI room agent that happens to share the
  name.

## 8. Architecture (modular, learn-from-ACLI, don't-couple-to-ACLI)

Four layers, mirroring Codex's proposal, kept deliberately small for the first build:

```
backend (/api/digest, later /api/worker/*)
      │  builds a job bundle on disk, shells out
      ▼
Job Builder ──► tmp/jobs/<job_id>/   (transcript.json, messages_for_llm.json,
      │                               system_events.json, matter_context.md,
      │                               task_instructions.md, room_context.json, job.json)
      ▼
Worker Runner (workers/run_worker.py)
      │  reads job.json, resolves worker from registry, injects creds via env,
      │  runs the selected CLI/provider, writes result.json + stdout.log + stderr.log
      ▼
Provider Adapter Layer (workers/providers/*)   one adapter per worker style: agy,
      │                                          openai, claude, gemini
      ▼
Task Layer (workers/tasks/*)   digest, reply_draft, room_status, progress_review
```

**8.1 Worker registry** — `workers/registry.json`. Learn directly from ACLI's
`acli/acli_settings.json → agent_models` shape (default_model, available_models, aliases,
effort levels). One entry per worker declares: provider adapter to use, default model,
available models, default effort, and the name of the env var holding its credential
(e.g. `"credential_env": "AGY_API_KEY"`). Nothing here is a secret — only the *name* of
where the secret lives.

**8.2 Job bundle (`job.json`)** — the stable contract between backend and worker. Carries:
`task` (e.g. `"digest"`), `worker` (e.g. `"agy"`), `model`/`effort` overrides, `room_id`,
and **paths** to the input files — never raw secrets, never raw auth in transcript text.
The backend chooses `worker`/`task`; everything else is data.

**8.3 Result contract (`result.json`)** — stable regardless of task or worker so the
backend consumes it uniformly:
```json
{
  "ok": true,
  "task": "digest",
  "worker": "agy",
  "room": "production_repo",
  "output": "…the digest / draft / analysis text…",
  "included_message_ids": ["…"],
  "used_context_files": ["NORTH_STAR.md", "OBJECTIVES.md", "PROJECT_HANDOFF.md"],
  "error": null
}
```
`output` is generic (not `summary`) precisely because the engine is not summarization-only.

**8.4 Auth handling** — credentials reach the worker as **environment variables passed to
the subprocess**, or a local config file *outside* prompt content, referenced by name in
`job.json`. Never in `transcript.json`, `task_instructions.md`, or any file the model
reads as content. `tmp/jobs/` and any credential file must be gitignored (the repo already
commits `acli/summary_history/*` — worker job dirs must not follow that pattern with
secrets in them).

## 9. Integration + failure semantics

- **Backend call site.** `/api/digest` becomes a thin caller: build the bundle, run
  `workers/run_worker.py digest --worker <configured> --job <path>`, read `result.json`,
  return `{digest: output, included_message_ids, prior_summary_used}`. The existing
  Lane-B/C filtering, matter-doc loading, and prior-summary persistence (already in
  `app/main.py`) move into the Job Builder / task layer largely unchanged.
- **Invocation model.** Start with Codex's option 1 (backend shells out to a local CLI,
  synchronous) — simplest, matches the project's "small working loop" preference. Design
  `result.json` so a later async job-queue variant (option 2) is a drop-in.
- **Graceful degradation is mandatory** (this is the recurring lesson — a bad key must
  never 500 or go silent). Worker non-zero exit, timeout, or malformed `result.json` →
  backend logs it and returns the deterministic rule-based fallback, but attributed by
  `event.agent` (fixing Grok's "counts under ACLI Dispatcher" bug), not the RC display
  name. `openai_available`/worker-available flags must reflect a real successful call, not
  merely "a key string is present."
- **Stage 1 stays deterministic and feeds Stage 2.** The compact Lane-A stats summary
  from Section 4 (e.g. "codex 3 runs, avg 38s, no stops") — which today's digest bundle
  omits — should be written into the job as `system_events.json` / folded into
  `task_instructions.md` so the worker's narration can reference operational reality, not
  just prose.

## 10. Build order for Part II (smallest useful increment first)

1. **Registry + one-shot digest runner.** `workers/registry.json` (with an `agy` entry
   and an `openai` entry) and `workers/run_worker.py` that handles exactly `task=digest`
   for one worker, reading a job bundle and writing `result.json`. Prove it from the CLI
   alone: `python workers/run_worker.py digest --worker agy --job tmp/jobs/<id>/job.json`.
2. **Job Builder.** A backend helper that turns the current room history + matter docs +
   prior summaries into a job bundle directory. Reuses the Lane-B/C filtering already in
   `/api/digest`.
3. **Wire `/api/digest` to the runner** behind the same response shape, with the
   graceful-fallback + `event.agent` attribution fixes. UI unchanged.
4. **Generalize the task layer** once digest is good: add `reply_draft`, `room_status`,
   `progress_review` as additional `task` values reusing the same bundle/runner/result
   contract. No new plumbing per task.

Deliverable 1 is CLI-only and touches no browser code — matching the standing project
preference (Codex's staging notes, and the earlier "validate summarization usefulness
outside the browser first" guidance) to prove the AI pipeline on the terminal before the
UI depends on it.

## 11. Non-goals for Part II
- Not rebuilding ACLI's dispatcher, poller, or room-routing — the Voice Channel worker
  runs on demand from the backend only.
- No multi-worker orchestration/chaining in the first build — one task, one worker, one
  job at a time.
- No secrets in the repo, in job bundles, or in prompt files — env/config reference only.
- Realtime voice transport remains out of scope until this text/worker loop is dependable.

---

# Part III — Full-Screen Channel Supervision App

Added 2026-07-19 after the narrator, Rocket.Chat integration, and Codex worker path were
proven live. This is deliberately different from the failed `video_call` direction: the
core read, classify, summarize, source-audit, and voice loop already works. The next
chapter reshapes those working capabilities into a daily-use application and adds new AI
tasks incrementally. It does not introduce video, avatars, LiveKit, or a media-room
illusion.

## 12. Product definition and interaction contract

The app is a **voice-enabled supervision console for Rocket.Chat-backed agent work**. Its
visual model combines:

- Telegram-style channel navigation on the left.
- A large, editorial transcript in the center.
- A Gemini-in-Gmail-style narrator workspace on the right.

The most important UX rule is a hard boundary between private AI work and durable channel
communication:

| Surface | Purpose | Can post to Rocket.Chat? |
| --- | --- | --- |
| Narrator bar | Ask questions, request summaries, compare opinions, inspect project state, generate drafts | No |
| Bottom composer | Edit, target, confirm, and send a message to the selected channel | Yes, after explicit confirmation |

The narrator may offer a draft or suggestion, but it can only **promote** that text into
the composer. It never sends directly. The composer remains the single confirmation gate
and Rocket.Chat remains the durable transcript and source of truth.

## 13. Information architecture and layout

Desktop uses a full-height application shell with three independent panes:

```text
┌──────────────────┬───────────────────────────────────────┬──────────────────────┐
│ Channels         │ #voice_channel              AI  Info │ Narrator             │
│ Search           ├───────────────────────────────────────┤ Context: 50 messages │
│                  │                                       │ + project files      │
│ ● voice_channel  │  Ed                                   │                      │
│   coding · 2     │  Please review the implementation…    │ What is happening?   │
│                  │                                       │ What did Grok mean?  │
│ ○ planning       │  ┌ Codex · completed in 38s ───────┐ │ What should I do?    │
│   planning       │  │ Structured, readable agent reply │ │                      │
│                  │  └───────────────────────────────────┘ │ [private answer]     │
│ ○ research       │                                       │ [Use as reply]       │
│                  │  [routing/status event card]           │                      │
│                  ├───────────────────────────────────────┤ ──────────────────── │
│                  │ Suggested starts: Review · Ask · Plan │ Ask about this room… │
│                  │ [agent target]  Write a message… Send │ [mic]          [ask] │
└──────────────────┴───────────────────────────────────────┴──────────────────────┘
```

The narrator bar is collapsible. Closing it gives the transcript the full working width.
Pane widths should be resizable within sensible bounds and remembered locally. On narrow
screens, the channel list and narrator become drawers so the transcript and composer
remain usable; the interaction contract does not change.

### 13.1 Left channel rail

The channel rail is driven by the existing Rocket.Chat room list, not a second channel
database. Each row should show the room name, channel type, unread or changed state, a
short last-activity preview, and optional narrator attention state. It should support:

- Fast search and keyboard navigation.
- Pinned/recent ordering without altering Rocket.Chat membership.
- Unread count and a jump-to-unread action.
- A quiet status indicator when an agent is working or when a meaningful update arrived.
- Config-driven channel icons/initials, with no hard-coded assumption about room count.

Selecting a room switches the transcript, composer target, narrator session, summary
memory, and channel configuration as one atomic context change.

### 13.2 Center transcript

The transcript is the primary surface and must be useful with the narrator closed. It is
not a raw Rocket.Chat clone. Existing lane and event metadata drives an opinionated,
readable hierarchy:

- Ed's messages are visually direct and easy to find.
- Agent results use generous typography, sanitized Markdown, strong headings, readable
  code blocks, tables, citations, and restrained width for comfortable reading.
- Long replies initially show a useful lead section with **Expand** / **Collapse** and
  preserve full-text search and source linking.
- System messages become compact graphic event cards: routing, model/effort, heartbeat,
  elapsed time, completion, failure, stop, superseded run, and attachment.
- Date separators, unread markers, current-working state, and jump-to-latest maintain
  orientation in long rooms.
- Source links from the narrator scroll to and highlight the exact transcript item.

Formatting should improve comprehension without changing the underlying message text.
The raw message remains inspectable for troubleshooting.

### 13.3 Channel header and settings

Clicking the channel header opens a settings sheet, similar to Telegram channel details.
Settings are persisted per Rocket.Chat room and contain:

- Display metadata: label, icon/accent, optional description.
- Channel type: `coding`, `planning`, `consultation`, `research`, `execution`, or custom.
- Narrator instructions: what the room is for, what matters, what to ignore, desired
  summary style, and any known agent workflow.
- Linked ACLI matter directory: an explicit, verified path; never guessed from room name.
- Context policy: recent-message count, summary-memory depth, relevant matter documents,
  and whether project-file reading is enabled.
- Assistant permission tier: transcript only; transcript + approved project files; or
  transcript + files + reply/command suggestions.
- Narration preferences: voice, speed, verbosity, auto-read policy, and interruption.

Channel settings are operational context for the AI engine, not merely visual
preferences. Changes must flow into future worker job bundles.

## 14. Narrator bar: private room intelligence

The narrator bar owns every **ask the AI** interaction. It is always scoped to the
selected room and clearly displays what context it can currently see: message window,
summary history, channel instructions, and approved matter directory.

Initial task set:

- Summarize important changes since a time or unread marker.
- Explain a specific message or review finding.
- Answer "where are we?", "what is blocked?", and "what should happen next?"
- Compare named agent positions and identify agreement or conflict.
- Relate current discussion to objectives, plans, and actual project files.
- Draft a reply on explicit request.
- Generate a small set of humble, distinct reply starters.

Answers retain `included_message_ids` and add `used_context_files`, allowing a sources
drawer to distinguish transcript evidence from project-file evidence. Every answer should
make uncertainty visible when the available context is incomplete.

The bar supports text and speech input, stop/interruption, replay, speed control, and
follow-up questions. It is private by default. **Use as reply** copies an answer or
suggestion into the bottom composer for editing; it does not send.

## 15. Bottom composer and guided suggestions

The composer is for outward communication only. It should feel like a capable chat
composer, not a second AI chat box:

- Multiline Markdown editor with attachments and keyboard send controls.
- Config-driven agent targeting using recognizable brand-inspired icons plus accessible
  labels/tooltips and a neutral fallback icon for unknown workers.
- Explicit target preview before sending, especially for `@agent` messages.
- Existing confirmation gate immediately before the Rocket.Chat write.
- Draft persistence per channel so switching rooms never loses work.

Above an empty composer, the AI engine may generate two to four short starter actions
based on recent messages, room type, objectives, and the declared agent workflow. Examples
might be "Ask Grok to review", "Have Codex fix the two verified issues", or "Request a
status comparison". These are suggestions, not autonomous actions. Clicking one inserts
editable text into the composer. Suggestions should be:

- Concise and materially different from each other.
- Labeled by intent where useful (`Review`, `Implement`, `Clarify`, `Plan`).
- Regenerated only on request or after meaningful channel changes, not continuously.
- Traceable to the context version used to create them.
- Never posted, targeted, or executed until Ed edits/accepts and confirms.

Start pull-based and conservative. Proactive recommendation banners are a later option
only after suggestion quality is demonstrably trustworthy.

## 16. Per-channel context and read-only matter access

The narrator needs ACLI-level situational context without becoming part of ACLI's
dispatcher. Each room configuration explicitly maps `room_id` to one approved matter
directory. The mapping may be initially imported from ACLI's registry, but Voice Channel
stores and validates its own configuration and does not infer paths from names.

Access rules:

1. Matter access is opt-in per channel and read-only for narrator tasks.
2. The resolved path must exist, be allowlisted, and remain inside configured roots.
3. The worker receives only that room's approved directory for that job.
4. Job metadata records which files were read; secrets, credential files, VCS internals,
   generated artifacts, and configured ignore patterns are excluded.
5. Transcript-only questions do not mount or scan the matter directory unnecessarily.
6. Any future write-capable command task requires a separate design and explicit
   authorization; it is not implied by narrator read access.

The first implementation should use a bounded context builder rather than dumping an
entire repository into every prompt. It should combine pinned project documents,
task-specific file search, current git/status metadata where relevant, and compact file
excerpts. Codex CLI continues in a read-only sandbox for narrator work.

Container deployment requires a deliberate path bridge: approved host matter roots are
mounted read-only and translated to stable container paths. This mapping belongs in
configuration and must be covered by path-validation tests.

## 17. Persistence model

The redesigned app must survive refreshes and channel switches without losing its mental
state. Keep storage simple and replayable for this phase, but separate different kinds of
state:

```text
data/
  channel_config/<room_id>.json       channel type, prompt, path, permissions, voice
  narrator_sessions/<room_id>.json    private Q&A turns and source metadata
  composer_drafts/<room_id>.json      unsent editable draft and selected targets
  ui_state/<user_id>.json             pins, pane widths, collapsed state, last room
acli/
  summary_history/<room_id>.json      existing digest memory
```

Narrator turns should store timestamp, task, question, answer, worker/model, included
message IDs, used files, context version, and error state. Store bounded histories with a
clear retention setting; do not silently feed every old turn back into every request.
Session context uses the latest relevant turns plus summaries, while the full local log
remains available for review.

Writes should be atomic (temporary file then replace) and serialized per room. Define the
JSON contracts and migration/version field before building the settings UI so future
schema changes do not strand existing channels. If concurrent use outgrows flat files,
the same contracts can move behind SQLite without changing frontend behavior.

## 18. Worker-engine expansion

Reuse the independent engine from Part II; do not create a second AI stack. Add real task
implementations with dedicated instructions and output contracts:

| Task | Purpose | Primary output |
| --- | --- | --- |
| `digest` | Existing narrator summary | prose + sources |
| `narrator_qa` | Private grounded room questions | answer + transcript/file sources |
| `reply_draft` | Explicitly requested editable reply | draft + rationale/source metadata |
| `reply_suggestions` | Two to four conservative composer starters | structured suggestion array |
| `room_status` | Stage, completed work, blockers, next step | structured status + prose |
| `compare_agents` | Contrast named agent positions | agreements, differences, recommendation |

Create one shared Job Builder that accepts task, room configuration, user input, selected
messages, narrator-session context, and approved matter path. Task modules decide which
inputs they need. The provider registry remains swappable; Codex is the default, not a
hard-coded dependency in endpoints or UI.

Structured tasks must return JSON validated against a task-specific schema. Free-form
Codex prose must not be parsed heuristically into sendable actions. Worker errors, timeout,
or malformed output should be visible in the narrator bar and must never trigger a write.

## 19. High-quality voice layer

Browser `speechSynthesis` remains an offline fallback, not the target voice. Add a
provider-neutral TTS service behind `/api/tts` with a small registry separate from the
text worker registry. This matters because Codex CLI authentication covers Codex text
work, while hosted speech providers may require their own credentials and billing.

The first quality TTS integration should support:

- Several natural voices with an in-app preview and per-channel/default selection.
- Speed and narration-style controls (briefing, calm reader, fast monitor).
- Streaming or low-latency playback when supported, with immediate stop/interruption.
- Paragraph-level chunking so long digests can start quickly and resume reliably.
- Local cache keyed by content, voice, speed, and provider to avoid repeated charges.
- Text normalization that does not read Markdown symbols, raw URLs, code blocks, source
  IDs, or system metadata aloud unless requested.
- Browser TTS fallback when the provider is unavailable.

Auto-read remains opt-in and should initially apply only to explicit digests or important
updates, not every Rocket.Chat event.

## 20. Backend and API changes

Keep Rocket.Chat as the source of truth and expose application state through narrow APIs:

- `GET /api/rooms`: enrich existing room data with local config, unread/activity state,
  and narrator attention metadata.
- `GET /api/history`: preserve lane/event data and add pagination/unread anchors needed by
  the new transcript.
- `GET|PUT /api/channels/{room_id}/config`: validate and persist typed-workspace settings.
- `GET /api/channels/{room_id}/narrator/session`: load bounded private AI history.
- `POST /api/channels/{room_id}/narrator/ask`: execute `narrator_qa` and persist result.
- `POST /api/channels/{room_id}/narrator/suggestions`: return structured reply starters.
- `POST /api/channels/{room_id}/narrator/draft`: execute an explicit `reply_draft` task.
- `POST /api/channels/{room_id}/composer/promote`: optional local draft operation only;
  no Rocket.Chat write.
- Existing confirmed send endpoint: remain the only channel-write boundary.
- `POST /api/tts`: synthesize/cache audio with no coupling to narrator task execution.

All room-scoped endpoints must verify Rocket.Chat membership and local matter permissions.
Long-running AI/TTS requests should gain cancellation and request IDs; synchronous calls
are acceptable for the first shell milestone if the UI shows honest progress and can stop
playback/work.

## 21. Delivery sequence

The overhaul should proceed as independently usable milestones:

1. **Application shell.** Build the full-screen three-pane layout using current room,
   history, digest, sources, and composer capabilities. Move current narrator features
   into the collapsible right bar. Add responsive drawers and preserve all existing flows.
2. **Transcript quality.** Add editorial message hierarchy, long-message collapse,
   graphical system cards, date/unread navigation, source highlighting, and raw-message
   inspection.
3. **Channel configuration.** Define versioned persistence and build the settings sheet
   for room type, narrator instructions, message window, permissions, and voice settings.
4. **Matter context.** Import/confirm ACLI room mappings, implement read-only path
   validation and container mounts, then expose file sources in narrator results.
5. **Durable narrator Q&A.** Implement `narrator_qa`, per-room sessions, source display,
   cancellation, and private text/voice interaction in the right bar.
6. **Guided replies.** Implement explicit drafts and humble structured suggestions, then
   promotion into persistent composer drafts with the existing confirmation gate.
7. **Voice quality.** Add provider-based TTS, voice selection, streaming/chunking, cache,
   interruption, and browser fallback.
8. **Hardening.** Add integration tests for room switching, persistence, path isolation,
   worker failure, confirmation safety, and an end-to-end live smoke test.

Each milestone must preserve a usable application. The shell must remain valuable with
the narrator closed; the narrator must remain useful if voice synthesis is unavailable;
and no AI failure may prevent reading or posting through Rocket.Chat.

## 22. Acceptance criteria for the new chapter

The overhaul is successful when Ed can:

- Switch among real Rocket.Chat rooms from a fast, readable channel rail.
- Read long ACLI conversations with clear agent, user, and system hierarchy.
- Open a room's settings and define its purpose, narrator behavior, approved matter
  folder, context depth, and voice.
- Ask privately "what is happening?", "what did Grok mean?", or "where are we on this
  feature?" and receive an answer grounded in messages and approved project files.
- Return later and continue the room-specific narrator conversation after a refresh.
- Request or click a conservative AI suggestion, edit it in the composer, choose an agent,
  and explicitly confirm before anything is posted.
- Hear a natural, interruptible narration voice while retaining browser TTS fallback.
- Inspect the transcript messages and project files used for an AI answer.

The product is not successful merely because the three panes look polished. It is
successful when the combination of readable transcript, grounded narrator, safe guided
composer, and high-quality audio materially reduces the time and cognitive load required
to supervise agent channels.

## 23. Non-goals and guardrails for Part III

- No video, avatars, virtual conference room, or LiveKit dependency.
- No autonomous posting or execution from narrator answers or suggestions.
- No implicit repository access based on a room name; every matter path is explicit and
  read-only.
- No cross-channel AI memory by default; sessions and context are room-scoped.
- No proactive suggestion spam; start with explicit requests and empty-composer starters.
- No replacement of Rocket.Chat as the durable coordination transcript.
- No wholesale frontend framework migration in the first shell milestone unless the
  current implementation proves unable to support the layout and state model.
