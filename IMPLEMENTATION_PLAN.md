# Implementation Plan: Message Classification, Transcript, and Summarization

Status (updated 2026-07-17): Steps 1–3 shipped and verified; Steps 4–5 shipped in
structure (lane-filtered digest, matter docs, prior-summary memory, `included_message_ids`,
sources UI) but the AI narrator is **not yet delivering plan-quality digests in
production** — the digest currently calls OpenAI directly and falls back to post-counting
when that key is invalid (live `401 Unauthorized`). Section 7 below adds the next major
piece of work: an **independent CLI worker layer** to become the AI brain for the Voice
Channel, replacing the hard-coded OpenAI call inside `/api/digest`. Written against the
current running code in `app/main.py` and `frontend/index.js` (Docker console on port 6891).

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
