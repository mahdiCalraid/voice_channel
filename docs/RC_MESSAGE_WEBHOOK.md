# Rocket.Chat message webhook (U-11E-c)

Wake the Voice Gateway when Rocket.Chat stores a message. The Gateway then
**re-fetches that room from Rocket.Chat** (source of truth), filters operational
noise, optionally starts narrator/suggestion preparation, and notifies open
browsers over SSE. The webhook body is **not** stored as a second transcript.

## 1. Configure the Gateway

In the Gateway environment (`.env` or compose env — **do not commit secrets**):

```bash
# At least 32 characters. Same value as the Rocket.Chat integration token.
GATEWAY_RC_WEBHOOK_SECRET='replace-with-a-long-random-secret-at-least-32-chars'
```

Confirm:

```bash
curl -sS http://localhost:6891/api/status | python3 -c 'import sys,json; print(json.load(sys.stdin).get("rc_webhook"))'
# expect: {"status": "configured", "path": "/api/rc/webhook/message"}
```

Restart the Gateway after setting the secret.

## 2. Configure Rocket.Chat outgoing integration

1. Administration → Workspace → Integrations → **Outgoing**
2. **Event:** Message Sent
3. **Enabled:** true
4. **Channel:** include Rocket.Chat's prefix, for example `#TV_and_memory` (not `TV_and_memory`)
5. **URL:** `http://host.docker.internal:6891/api/rc/webhook/message`
   (or the host URL that reaches the Gateway from the Rocket.Chat process)
6. **Post as:** an existing username without `@`; the local verified value is `acli_bot`
7. **Token / Secret:** exactly `GATEWAY_RC_WEBHOOK_SECRET`
8. **Trigger words:** leave empty (all messages) for the experiment
9. Start with **one** test channel that is eligible for response-assistant prewarming
10. Save

The **Responding** JSON shown by Rocket.Chat is documentation, not a field to
fill in. The integration ignores the Gateway's successful JSON response because
it has no `text` property, so no response message is posted back to the room.

Rocket.Chat typically posts a JSON body that includes `token`, `channel_id`,
`channel_name`, `message_id`, `user_name`, `text`, and sometimes `bot`.

## 3. Live experiment (do this first)

With the browser closed (or on another channel):

| # | Action | Expected Gateway result |
|---|---|---|
| 1 | Ed posts a normal user message | HTTP 200; `new_real_agent_replies: 0`; no digest prewarm |
| 2 | ACLI posts a **real agent reply** (e.g. Codex) | HTTP 200; `new_real_agent_replies >= 1`; prewarm scheduled if channel is narration-active |
| 3 | Heartbeat / routing system noise | No prewarm (even if webhook fires) |

### Verified local result (2026-08-10)

Rocket.Chat 8.5.2 fired the integration for Ed, ACLI routing, and the real
ACLI/Codex reply in `#TV_and_memory`. The final acceptance run produced:

- Ed message: `new_real_agent_replies: 0`, `prewarm_scheduled: 0`
- ACLI routing notice: `new_real_agent_replies: 0`, `prewarm_scheduled: 0`
- real ACLI reply: `new_real_agent_replies: 1`, `prewarm_scheduled: 1`
- duplicate message ID: `replayed: true`, with no repeated work

Webhook processing is serialized per room and preparation is scoped to the
exact authenticated Rocket.Chat `message_id`; a short browser history cache can
no longer turn older replies into a narration batch.

Watch Gateway logs for `Ignoring replayed Rocket.Chat webhook` on RC retries (OK).

**Critical:** if step 2 never hits the Gateway because Rocket.Chat skips bot-flagged
messages, **stop designing around webhooks** for agent replies and use the existing
DDP path (`GATEWAY_RC_EVENTS=1`) instead.

Manual probe (without RC):

```bash
SECRET='your-32+-char-secret'
curl -sS -X POST "http://localhost:6891/api/rc/webhook/message" \
  -H "Content-Type: application/json" \
  -d "{\"token\":\"$SECRET\",\"channel_id\":\"<room_id>\",\"channel_name\":\"voice_channel\",\"message_id\":\"test-$(date +%s)\",\"user_name\":\"codex\",\"text\":\"probe\"}"
```

## 4. Security notes

- Shared secret comparison is constant-time; secret must be ≥ 32 characters.
- Replay protection keys on Rocket.Chat `message_id` under `acli/gateway_state/`
  (gitignored). Retries return success without re-running work.
- Do not expose `/api/rc/webhook/message` on the public internet without the
  existing Cloudflare Access / private network boundary used for the console.
- Prefer not expanding committed compose tokens; inject secrets via env.

## 5. Browser refresh baseline

Independent of webhooks/DDP, the console keeps a snappy poll baseline:

- Active conversation history: **~4s**
- Channel rail + attention: **~7s**
- Status: **~5s**

SSE/webhook events make updates immediate when present; they do not replace the baseline.
