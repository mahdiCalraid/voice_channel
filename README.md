# Adaptive Voice Gateway

A self-hosted, Mac-first communication and supervision layer for Ed's existing
Rocket.Chat, ACLI, nc2, and local project systems.

This branch preserves the original Voice Channel implementation while changing its
architectural role. Voice Channel is now a client-neutral gateway and future client
platform, not a replacement for ACLI.

## Current Branch Direction

- Branch: `codex/adaptive-voice-gateway`
- Original UI branch: `feature/full-screen-voice-console`
- First priority: complete the local Mac control loop.
- Second priority: add bounded AI routing, summarization, and suggestions.
- Third priority: test Omi or a smaller Apple-native mobile client.

Read these documents first:

- `ADAPTIVE_HANDOFF.md`
- `NORTH_STAR.md`
- `OBJECTIVES.md`
- `DISCOVERY_RECORD.md`
- `ADAPTIVE_IMPLEMENTATION_PLAN.md`
- `PROJECT_HANDOFF.md`

`IMPLEMENTATION_PLAN.md` preserves the original custom UI plan and its verification
history.

## Stable System Model

```text
Mac browser / CLI / future Mac client / Omi / custom iPhone client
                              |
                      Adaptive Voice Gateway
                              |
          speech + routing + summaries + supervision
                              |
                         Rocket.Chat
                         /         \
                      ACLI          nc2
                         \         /
                   approved local files
```

- ACLI does the substantive work.
- Rocket.Chat is the canonical communication transport and transcript.
- The gateway adds communication intelligence and safety.
- Clients and model/speech providers remain replaceable.

## Existing Features

- Rocket.Chat health and room discovery.
- Paginated room history with deduplication.
- Per-room state isolation.
- ACLI routing and operational event classification.
- Confirmation-gated message sending.
- Nonce-based duplicate prevention.
- Digest generation with provider failure fallback.
- Browser speech playback and basic speech input.
- Draft and interruption recovery.
- Python, Node, live smoke, and soak tests.

## Directory Structure

```text
app/
  main.py                     FastAPI backend
frontend/
  index.html                  Existing Mac browser client
  index.css
  index.js
  history_state.js
workers/
  run_worker.py               Current AI worker runner
  registry.json
  providers/
tests/
  Python and Node test suites
acli/
  Runtime matter/session data; exclude churn from product commits
```

## Local Development

The existing service can be built and started with:

```bash
./restart.sh
```

It listens on port `6891`.

## Isolated Production

The public deployment is a separate Compose project and does not restart or replace the
local development service. See `PRODUCTION_DEPLOYMENT.md` and deploy it with:

```bash
./deploy-production.sh
```

Production has no host port and is reachable only from the existing Rocket.Chat
Cloudflare Tunnel network. `vice.peyvastegi.uk` must be protected by Cloudflare Access
before its tunnel hostname is enabled.

`restart.sh` also provisions the Mac-local Chatterbox environment on first use
and starts its MLX-Audio server on loopback port `8765` before the Gateway. The
first narration downloads the configured model into the user's Hugging Face
cache; subsequent Play actions use the warm server. Set
`VC_CHATTERBOX_AUTOSTART=0` only when deliberately disabling the preferred
narrator. Phones and other clients receive transient audio from the Gateway and
never install the model or voice files.

Run the automated suites with:

```bash
python3 -m unittest discover -s tests
node --test tests/*.test.js
```

Live Rocket.Chat checks remain opt-in and require the local services and credentials:

```bash
python3 tests/smoke_live_rocket_chat.py
python3 tests/soak_test_runner.py --cycles 30
```

## Configuration

The Docker wrapper maps these host variables into the backend:

- `ACLI_RC_URL`
- `ACLI_RC_USER`
- `ACLI_RC_USER_ID`
- `ACLI_RC_AUTH_TOKEN`
- `GATEWAY_RC_USER_ID`
- `GATEWAY_RC_AUTH_TOKEN`
- `GATEWAY_RC_INGRESS_SECRET`
- `OPENAI_API_KEY`
- `VC_WORKER`

Do not commit credentials. The existing default Rocket.Chat token in
`docker-compose.yml` is a known security issue and must be removed and rotated before
remote exposure.

`GATEWAY_RC_*` is a separate Rocket.Chat service identity for confirmed gateway dispatch.
It must not reuse `acli_bot`; when absent, gateway confirmation intentionally returns `503`
instead of posting an ACLI-undeliverable message. See
[`docs/ROCKET_CHAT_DISPATCH_INGRESS.md`](docs/ROCKET_CHAT_DISPATCH_INGRESS.md).

## Current Milestone

Phase 0 establishes the adaptive direction and documentation. The next implementation
task is `M1-01 Gateway Contract Skeleton`.

Mobile development, Cloudflare exposure, continuous audio, and Omi integration are
deliberately deferred until the local Mac gate passes.
