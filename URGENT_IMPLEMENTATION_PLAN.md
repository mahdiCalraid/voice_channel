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

Status: `NOT STARTED`

**Do:**

1. Order channel rail **recent-first** (last activity / last opened; stable fallback for unknown).
2. Modestly **increase base UI text size** for long sessions (CSS tokens / root font size; not a redesign).
3. Add a **Settings** entry (placeholder page or panel is enough): “Settings” in chrome.

**Done when:**

- Channels that have been used recently appear first.
- Text is easier to read without breaking layout.
- Settings is visible and openable (even if mostly placeholder).
- `node --check frontend/index.js` passes; product-only commit.

### U-02. Narrator digest contract

Status: `NOT STARTED`

**Do:**

1. Digest prompt/output contract: **exactly two short paragraphs** (not free-form essay).
2. Configurable **last N messages** for narrator context (default 20; stored in settings/localStorage).
3. Keep digest **manual** until U-03.

**Done when:**

- Generate Digest produces two paragraphs consistently enough for daily use.
- N is adjustable from Settings (or a simple control).
- Unit or deterministic test covers “two paragraph” shaping if practical.

### U-03. Auto-narrate only real agent replies

Status: `NOT STARTED`

**Do:**

1. When history polls, detect a **new agent response** for the active room (lane/agent reply).
2. Auto-run narrator **only** for that class of message.
3. **Never** auto-narrate routing, heartbeat, system/control, or gateway envelope noise.

**Done when:**

- A real agent reply triggers optional auto-digest/speech.
- Routing lines do not trigger speech.
- Deterministic frontend/backend test for “agent vs system” filter.

### U-04. Next-message suggestions (draft only)

Status: `NOT STARTED`

**Do:**

1. After a digest (or agent reply), offer **2–3 AI suggestions** for Ed’s next message.
2. Click inserts into the **editable composer** only.
3. **Never** auto-send. Confirm gate remains mandatory.

**Done when:**

- Click-to-draft works.
- Confirm path still requires explicit confirm.
- No suggestion posts without confirm.

### U-05. Default agent and simple routing prefs

Status: `NOT STARTED`

**Do:**

1. Settings: default agent for this console (e.g. `codex`).
2. Composer / gateway interact uses that default when no `@agent` is typed (already partially present server-side).
3. Document: per-room ACLI `default_agent` still owns ACLI-side defaults; this is console default only.

**Done when:**

- Default agent persists and is used for bare drafts.
- `@other` still overrides.

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
