# Urgent Daily-Use Implementation Plan

Status established: 2026-07-30  
Last updated: 2026-08-03
Branch: `urgent/daily-use-console`  
Parent (return-to): `codex/adaptive-voice-gateway`  
Historical UI foundation: `feature/full-screen-voice-console` / `IMPLEMENTATION_PLAN.md`  
Long-range adaptive plan: `ADAPTIVE_IMPLEMENTATION_PLAN.md` (Omi remains Phase 5 — **not tonight**)

## 1. Why this plan exists

We are past deadline for product exploration. The adaptive/gateway path already proved
live confirm → Rocket.Chat (`voice_gateway`) → ACLI → agent reply. Tonight’s goal is
**daily-usable console polish on top of that working loop**, without abandoning the Omi
architecture and without checking out the pre-adaptive UI branch.

This plan is temporary and narrow. When urgent daily use is good enough, merge or cherry-pick
back into `codex/adaptive-voice-gateway` and resume the adaptive/Omi sequence.

## 2. Branch and repo map (do not confuse these)

| Repo / path | Role |
|-------------|------|
| **`/Users/ed/King/clawd_2/voice_channel`** | **Primary product.** Voice Gateway UI + FastAPI backend + Docker console on `:6891`. This is the matter workspace for this project. |
| **`/Users/ed/King/clawd_2/development_channel`** | **ACLI runtime** (poller, agents, RC bot). Separate git repo. Touched only when gateway/ACLI ingress requires it. |

| Branch | Purpose |
|--------|---------|
| `master` | Old baseline |
| `feature/full-screen-voice-console` | Pre-adaptive three-pane UI foundation (historical) |
| **`codex/adaptive-voice-gateway`** | Adaptive/gateway + signed RC ingress; **parent for Omi path** |
| **`urgent/daily-use-console`** | **Tonight’s branch** — fork of adaptive for immediate use UX |

**Do not** switch back to `feature/full-screen-voice-console` for daily use. That branch lacks
the live-proven `voice_gateway` dispatch path.

**Return to Omi path:** stay on or merge into `codex/adaptive-voice-gateway`, then continue
`ADAPTIVE_IMPLEMENTATION_PLAN.md` Phase 5. Do not rewrite architecture tonight.

## 3. Permissions (what “ACLI write access” was)

- Normal ACLI agent sessions for this matter work in **`voice_channel`** (UI + gateway backend).
- Ed once granted **extra write access** to **`development_channel`** so agents could fix
  ACLI-side signed-envelope ingress. That was a one-time cross-repo grant.
- Cross-repo ACLI work is **not** the default. UI and gateway product work stay in
  **`voice_channel`**.
- If a turn’s access packet lists only `development_channel` under “Allowed write
  directories,” that is an ACLI-session sandbox hint — it does **not** mean the product
  lives in ACLI. Prefer product edits under `voice_channel` for this matter.

## 4. What is already usable (do not rebuild)

Verified live and/or in product:

1. Console on **`:6891`** (Docker `voice-channel-console`).
2. Room list + history + digests + browser speech for digests.
3. Confirm-send through **gateway** (`/api/gateway/interact` → `/api/gateway/confirm`).
4. Dedicated RC user **`voice_gateway`** in **`#voice_channel`** with signed envelope.
5. ACLI accepts verified envelopes only; rejects unsigned/tampered/replayed posts.
6. Live E2E: interaction reached ACLI agent and completed (`LIVE_GATEWAY_E2E_OK`).
7. Draft auto-`@agent` prepend when agent is selected (`822c049`).

**Working daily path today:**

```text
Browser console → confirm → Voice Gateway → Rocket.Chat as voice_gateway
→ ACLI verifies → agent runs → reply in RC → console history
```

## 5. What is out of scope tonight

- Omi install, mobile, Cloudflare, ambient STT.
- Perfect Mac TTS/STT productization (backend stubs may exist; UI can keep Web Speech).
- Adding `voice_gateway` to every ACLI channel (only rooms where gateway dispatch is wanted).
- Replacing ACLI or Rocket.Chat.
- Large refactors, harness perfection, multi-hour soaks.

## 6. Urgent task sequence (smallest useful commits)

Work **one task at a time**. Product-only commits. No `acli/` runtime churn
(`routing_log`, sessions, summary_history, inbox screenshots).

### U-00. Branch and plan lock

Status: `VERIFIED` (2026-07-30)

- Create `urgent/daily-use-console` from `codex/adaptive-voice-gateway`.
- Commit this plan.
- Leave adaptive branch untouched as the return point for Omi work.

### U-01. Console readability and channel list

Status: `VERIFIED` (2026-07-30)

- Ordered channel rail by **most recent activity timestamp** (`_updatedAt`/`lm` descending).
- Added interface font size scaling CSS classes (`font-normal`, `font-medium`, `font-large`, `font-xlarge`) and increased base font sizes.
- Added interactive **Settings Modal** (accessible via header ⚙️ button) allowing font size, context depth, default agent, and auto-narrate configuration.

Acceptance:
- Channels with recent activity appear at top of list.
- Text scale is configurable and persistent in `localStorage`.
- Settings modal opens, saves, and updates settings dynamically.

### U-02. Narrator digest contract

Status: `VERIFIED` (2026-07-30)

- Configured prompt rules in `app/main.py` to structure digests into **EXACTLY TWO SHORT PARAGRAPHS** (Paragraph 1: High-level request context; Paragraph 2: Progress and status).
- Added customizable **narrator context depth (`history_limit` N)** in Settings (last 10, 20, 30, or 50 messages).
- Added unit and contract tests in `tests/test_urgent_features.py`.

Acceptance:
- Generate Digest produces two-paragraph summaries.
- Context window depth N is configurable via Settings.

### U-03. Single-Process Idempotency & Lease Budget Follow-up:
  - `app/main.py`: Documented single-container single-worker deployment constraint for in-memory idempotency.
  - `frontend/index.js`: Added `touchAutomaticAssistanceClaim` in-flight heartbeat every 15s so active owner tabs never lose lease ownership during long model runs (>45s).
  - `frontend/index.js`: Added no-storage replayed response UI hydration (`digest` + `suggested_message`) while preserving audio narration suppression (`autoPlay: false`).
  - `tests/test_response_assistant.py`: Added `test_lease_timeout_budget_invariant` asserting `AUTOMATIC_ASSISTANCE_LEASE_MS` (90s) > max backend budget (60s).
  - `tests/frontend_harness.test.js`: Added test coverage verifying no-storage hydration without duplicate audio.
  - **Status**: `VERIFIED` (2026-07-30)

- Implemented `checkForAutoNarrate` in `frontend/index.js`, triggering only when
  the backend classifies a newly arrived item as `event.kind=agent_response`.
- Initial room history is treated as a baseline, so opening or refreshing a room
  does not narrate old replies.
- Routing, heartbeat, gateway-envelope, membership, model-status, and other system
  events cannot trigger the response assistant.
- The Settings toggle now controls the combined automatic narration + draft flow,
  which is enabled by default and retries transient failures after a short delay.

Acceptance:
- Real agent responses trigger auto-narration when enabled.
- Routing and heartbeat system messages are ignored.

### U-04. Next-message suggestions (draft only)

Status: `VERIFIED` (2026-07-31)

- Added `/api/response-assistant`, which makes one bounded AI call for both the
  two-paragraph narrator digest and one strategic next-message draft.
- Automatic response assistance defaults to the authenticated Codex worker on the
  lightweight Luna model for low latency. `VC_RESPONSE_ASSISTANT_WORKER` and
  `VC_RESPONSE_ASSISTANT_MODEL` can select another registered provider/model without
  changing the regular narrator worker.
- The call is grounded in the configured last N real chat messages, the triggering
  agent response, prior narrator summaries, registered matter documents, channel
  classification, and Ed's coding/non-coding workflow rules.
- Coding suggestions infer planning, implementation, review, remediation, or
  checkpoint phase and rotate among Codex, Claude, Grok, and AGY accordingly.
- Non-coding channels use their registered default worker and a conservative,
  low-risk next step.
- The generated message is inserted only when the composer is empty (or still holds
  the previous generated suggestion); Ed's own in-progress draft is never overwritten.
- Suggestions are never posted automatically. They still go through the existing
  immutable preview and explicit confirmation gate.

### U-05. Default agent and simple routing prefs

Status: `VERIFIED` (2026-07-30)

- Added **Default Target Agent** selector in Settings (`codex`, `claude`, `grok`, `gemini`).
- Input drafts without an explicit `@agent` tag automatically resolve and format to `@defaultAgent` on gateway preparation and confirmation.
- Direct `@agent` tags in raw text override default settings.
- Backend `/api/rooms` updated to expose `_updatedAt` & `lm` recency fields and sort channels recency-first.
- Backend `generate_digest` updated to enforce `history_limit` context truncation and format fallback digests into two paragraphs.

Acceptance:
- Default agent persists and is applied automatically to bare text inputs.
- Explicit `@agent` mentions override default agent.
- `/api/rooms` returns recency timestamps and sorts channels recency-first.

### Delivered console refinements (after U-05)

Status: `VERIFIED` (2026-07-31)

These small daily-use improvements were completed without changing the Rocket.Chat
dispatch, response-assistant, or confirmation contracts:

1. **Split live typography controls** (`fab7e0a`): separate System presets (16px,
   20px, 26px, 35px) from chat controls for font family, size, weight, line height,
   paragraph spacing, and letter spacing. Changes preview immediately and persist on
   Save.
2. **Manual narration and vertical workspace resize** (`57e2ee9`): automatic digest
   generation and smart drafts remain on, but `DISABLE_AUTO_NARRATION = true` keeps
   speech manual; the conversation/composer divider persists a bounded height.
3. **Editable narration with explicit playback** (`3e5dd0d`): narrator text can be
   edited, **Play** never generates a digest, and **Generate Digest** still creates
   both narration and a suggested reply.
4. **Composer layout correction** (`43972e9`): the writing field flexes with the
   vertical divider, while **Draft Message** remains bottom-anchored and can claim
   space without obscuring the editor.
5. **Narrator-pane width resize** (`d7c6a6c`): a persisted, keyboard-accessible
   divider allows the narrator pane to widen while preserving a minimum narrator
   width, channel rail, and readable conversation area.

The targeted Python and Node suites passed after each slice (latest reported result:
84 Python / 29 Node), along with browser interaction checks. The remaining U-06 and
U-07 tasks below are still the closure work for the urgent plan.

### U-06. Gateway membership checklist (docs + optional UI)

Status: `VERIFIED` (2026-08-01)

1. **Gateway Dispatch Checklist UI (`frontend/index.html`)**:
   - Added dedicated Gateway Dispatch Checklist section in Console Settings explaining room membership requirements for signed dispatches:
     - `voice_gateway` service account user must be a room member for signed dispatches.
     - `ed` remains in normal channels for interactive ACLI routing.
     - Channels are authorized individually (e.g. `#voice_channel`); no bulk-joining.
2. **Channel Settings Hygeine & Snooze Retention (`frontend/index.js`)**:
   - Case-fold channel configuration lookup prevents casing conflicts on stored keys.
   - Snooze retention option (`keep`) prevents accidental truncation of multi-hour snoozes when editing other channel attention parameters.

### U-07. Smoke and daily-use checkpoint

Status: `VERIFIED` (2026-08-03; user-confirmed daily-use checkpoint)

**Do:**

1. `python3 -m unittest discover -s tests`
2. `node --test tests/*.test.js`
3. Read-only RC smoke.
4. One manual confirm in `#voice_channel` (low impact).
5. Record: usable for daily supervision — yes/no + remaining annoyances.

**Done when:**

- Green suites + one successful human confirm in the real channel.

### U-08. Chatterbox local TTS feasibility gate

Status: `VERIFIED` (Ed approved the chunked voice quality and server-side design)

Trial evidence: [`U08_FEASIBILITY.md`](U08_FEASIBILITY.md)

**Purpose:** determine whether a local Chatterbox service on Ed's Mac is a materially
better narrator than the current browser Web Speech voice, without changing the
working digest, suggestion, or confirmation flows.

**Do:**

1. Record the selected Chatterbox release/model, license, download size, and Apple
   Silicon runtime requirements from the official project before installing anything.
2. Confirm enough free disk and memory headroom for one model, its Python/PyTorch
   environment, model cache, and temporary audio. Do not install multiple model
   variants during the first trial.
3. Run one model in an isolated local environment as a localhost-only service, with a
   health endpoint and a bounded request timeout. It must not be exposed directly to
   the LAN or internet.
4. Generate a small approved narration fixture with the default/non-cloned voice;
   measure cold-start and warm-request latency, memory use, and subjective quality.
   Do not use voice cloning or a reference clip without Ed's explicit approval and a
   source he is entitled to use.
5. Confirm that generated audio is transient only: no source text or audio saved in
   the repository, ACLI runtime folders, logs, or long-lived local storage.
6. Record a go/no-go decision. Keep browser Web Speech as the daily-use fallback if
   the local service is slow, unstable, or not clearly better.

**Done when:**

- A localhost-only Chatterbox trial has a repeatable health check and measured
  quality/latency evidence on this Mac.
- The result explicitly says whether to proceed with U-09; no narrator UI or default
  provider changes are made merely by passing the trial.

### U-09. Chatterbox provider integration

Status: `IMPLEMENTED — local playback verification pending`

**Do:**

1. Extend the existing gateway TTS adapter behind a provider-neutral contract:
   `browser` remains the default/fallback and `chatterbox` is an optional local
   provider. Provider configuration stays server-side and is injected, never bundled
   into the browser.
2. Add authenticated Gateway endpoints for Chatterbox status, synthesis, and stop.
   The browser communicates only with the Gateway; the Chatterbox service stays on
   loopback. Enforce text-size, timeout, content-type, and error handling limits.
3. Return temporary audio for explicit **Play Summary** requests and support stop,
   replay, and speed behavior without reintroducing automatic speech. Keep
   `DISABLE_AUTO_NARRATION = true` until Ed deliberately enables a future setting.
4. Preserve the current browser Web Speech path as an immediate fallback whenever
   Chatterbox is unhealthy or a synthesis request fails. Digest generation, suggested
   drafts, confirmation-bound sending, and response-assistant idempotency must remain
   independent of TTS availability.
5. Add focused unit/contract coverage for provider selection, malformed/unavailable
   local service responses, stop/cancel behavior, transient-audio cleanup, and
   fallback. Add a manual local playback check using editable narrator text.
6. Document the local deployment contract: one warm Chatterbox process on the Mac,
   Gateway-to-service loopback only, no raw-audio retention by default, and no public
   port exposure.

**Done when:**

- Ed can press **Play Summary** and hear Chatterbox audio from editable narration
  text, then stop or replay it.
- A failed/unavailable Chatterbox service transparently leaves the console usable with
  the browser voice fallback.
- Tests and one local live playback verify that there is no automatic readout and no
  durable generated-audio artifact.

## U-09 deployment contract

- Run one MLX-Audio Chatterbox service on the Mac/server at `127.0.0.1:8765`.
- The checked-in launcher is `./start_chatterbox.sh`; point
  `VC_CHATTERBOX_PYTHON` at the Python environment containing `mlx-audio`.
- If the Gateway is Dockerized on that same Mac, set `VC_CHATTERBOX_URL` to
  `http://host.docker.internal:8765`; do not expose port 8765 publicly.
- The Gateway performs bounded, sentence-sized requests and never writes generated
  audio to the repository, ACLI runtime, or durable storage.
- A remote phone downloads only the current transient WAV chunk and plays it locally;
  no Chatterbox package, model, or voice files are installed on the phone.
- When Chatterbox is unavailable, the Mac Gateway tries the configured transient
  `macOS say` voice (default `Ava (Premium)`), then the phone's browser voice.
- `VC_TTS_PROVIDER=browser` is an explicit emergency switch that disables the server
  provider while retaining the browser voice path.

### U-10. Attention scheduler in the channel list

Source: `attention_scheduler_design.docx`. Goal: the left channel rail answers
"which channel should I work on next?" without Ed re-deriving it each time.

Two things are shown per channel, and only one of them at a time:

- **Busy channels** show elapsed working time (e.g. `working 14m`) and no score.
  They are not actionable, so they are not ranked against ready channels.
- **Non-busy channels** show an attention number and are ranked by it.

Scoring inputs (all from the design brief): base importance (1–5), urgency,
waiting age since `ready_since`, deadline pressure, blocking-other-work, and
today's focus flag. The score is **derived at request time**, never stored, so
the age term cannot go stale.

Status: `PLANNED` — slices below land in order.

#### U-10a. Attention state + editable config

**Status**: `VERIFIED` (2026-08-01)

1. Created `app/attention_config.py` to manage durable operator-owned channel attention configuration at
   `acli/gateway_state/channel_attention_config.json` (supporting `CHANNEL_ATTENTION_CONFIG_PATH`),
   with schema validation, file locking, atomic save, and registry status merging without mutating registries.
2. Extended `app/contracts.py` with `AttentionState` enum (`unknown`, `none`, `needs_review`, `needs_decision`, `needs_help`, `ready_for_instruction`)
   and added `attention_state`, `working_since`, and `ready_since` to `TaskRecord` with backward compatibility.
3. Updated `app/task_supervisor.py` to derive `attention_state` and track timestamps while keeping existing execution `TaskState` semantics intact.
4. Added channel attention summary aggregation (`get_channel_attention_summary`).
5. Added unit test suites `tests/test_attention_config.py` and extended `tests/test_task_supervisor.py` (98 Python unit tests passing).

#### U-10b. Scoring service + endpoint

**Status**: `VERIFIED` (2026-08-01)

1. Created `app/attention_scoring.py` implementing `calculate_channel_attention_score` and `build_attention_queue`.
   - Computes deterministic score and factor breakdown at request time without persisting score or rank to disk.
   - Evaluates eligibility gates: operator config (`attention_active`, `base_importance`, `urgency`, `deadline`, `blocking`, `boost`), snoozed status, busy status, and actionable attention state.
   - Categorizes items into explicit categories: `ranked`, `busy`, `unknown`, `unconfigured`, `idle`, `snoozed`, `inactive`.
   - Ranks `ranked` items 1..N by total score descending; busy items sorted by working time ascending (longest busy first).
2. Added `GET /api/attention/queue` endpoint to `app/main.py` supplying channel attention queue with factor breakdown and optional `now` timestamp for testing.
3. Addressed Claude's U-10a remediation in `app/task_supervisor.py`: non-busy task aggregation prefers the oldest actionable task (`needs_review`, `needs_decision`, `needs_help`) so waiting age reflects earliest unmet obligation.
4. Added unit test suite `tests/test_attention_scoring.py` (103 total Python tests passing).

#### U-10c. Channel rail integration & attention config UI

**Status**: `VERIFIED` (2026-08-01)

1. Created `GET /api/attention/config` and `PUT /api/attention/config` endpoints in `app/main.py`.
   - Atomically updates channel attention configuration with schema validation (base_importance 1..5, urgency, blocking, boost, snooze, deadline).
2. Integrated attention queue rendering & polling in `frontend/index.js`, `frontend/index.html`, and `frontend/index.css`.
   - **Busy channels** render `Busy {elapsed}` badge (no score badge).
   - **Ranked channels** render `#rank · {score}` badge with factor breakdown tooltip.
   - **Unconfigured / Unknown channels** render `Unconfigured` or `?` badge without fake score.
   - **Mid-turn Preemption Freeze**: Active composer input or confirmation gate freezes rail re-sorting and displays `[Queue updated · Click to refresh]` notice to prevent rail shifting under cursor.
   - **Attention Settings Modal**: Form for editing attention parameters for any channel, persisting to disk via `PUT /api/attention/config`.
3. Added unit tests in `tests/test_attention_endpoint.py` and `tests/frontend_harness.test.js` (107 Python tests, 31 Node tests passing 100%).

Voice commands for the scheduler are explicitly **out of scope** for U-10.

## 7. After urgent plan (return to adaptive / Omi)

1. Merge or cherry-pick `urgent/daily-use-console` → `codex/adaptive-voice-gateway`.
2. Resume `ADAPTIVE_IMPLEMENTATION_PLAN.md` from the honest next adaptive task
   (do not re-open closed mechanical ingress unless regressions appear).
3. Omi remains a **later client** on the same gateway contracts — not a rewrite.

## 8. Immediate next task

**U-08** (Chatterbox local TTS feasibility) is the current active task. **U-07**, **U-10** (U-10a state overlay, U-10b scoring, U-10c rail integration and UI fixes), and **U-06** (Gateway membership checklist) are complete and verified. U-09 remains blocked until Ed makes the U-08 quality decision. Do not start Omi, mobile, cloud exposure, or voice cloning under this urgent plan.

## 9. Operating rules for agents

1. Default repo for product work: **`voice_channel`**.
2. Touch **`development_channel`** only with explicit need + write grant.
3. Product commits only; never commit ACLI runtime logs/sessions.
4. Prefer smallest slice that makes the console more usable tonight.
5. Do not start Omi, mobile, or cloud exposure under this urgent plan.
