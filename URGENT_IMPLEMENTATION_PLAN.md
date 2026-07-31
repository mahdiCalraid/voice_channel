# Urgent Daily-Use Implementation Plan

Status established: 2026-07-30  
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

### U-03. Auto-narrate only real agent replies

Status: `VERIFIED` (2026-07-30)

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

### U-06. Gateway membership checklist (docs + optional UI)

Status: `NOT STARTED`

**Do:**

1. Short in-app or Settings note: to dispatch via gateway, room must include **`voice_gateway`**.
2. Optional: status chip or room badge when membership is unknown (no hard dependency).
3. Do **not** bulk-join all channels.

**Done when:**

- Ed has a clear checklist for “can this room receive gateway dispatch?”

### U-07. Smoke and daily-use checkpoint

Status: `NOT STARTED`

**Do:**

1. `python3 -m unittest discover -s tests`
2. `node --test tests/*.test.js`
3. Read-only RC smoke.
4. One manual confirm in `#voice_channel` (low impact).
5. Record: usable for daily supervision — yes/no + remaining annoyances.

**Done when:**

- Green suites + one successful human confirm in the real channel.

## 7. After urgent plan (return to adaptive / Omi)

1. Merge or cherry-pick `urgent/daily-use-console` → `codex/adaptive-voice-gateway`.
2. Resume `ADAPTIVE_IMPLEMENTATION_PLAN.md` from the honest next adaptive task
   (do not re-open closed mechanical ingress unless regressions appear).
3. Omi remains a **later client** on the same gateway contracts — not a rewrite.

## 8. Immediate next task

**Implement U-01** on `urgent/daily-use-console`:

- recent-first channel ordering  
- modestly larger text  
- Settings placeholder  

Then U-02 narrator contract.

## 9. Operating rules for agents

1. Default repo for product work: **`voice_channel`**.
2. Touch **`development_channel`** only with explicit need + write grant.
3. Product commits only; never commit ACLI runtime logs/sessions.
4. Prefer smallest slice that makes the console more usable tonight.
5. Do not start Omi, mobile, or cloud exposure under this urgent plan.
