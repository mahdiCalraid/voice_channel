# Voice Channel production deployment

The public personal-assistant hostname is `https://voice.peyvastegi.uk`.

## Isolation contract

- The development service remains `voice-channel-console` on host port `6891`.
- Production is `voice-channel-production` and has no host port.
- Production has its own Gateway state and temporary-data volumes.
- Both services may read the same approved project folders, Rocket.Chat, Codex
  authentication, and host-side Chatterbox service.
- The production image contains a snapshot of the source at build time. Development
  edits do not hot-reload into production.
- Production joins `rc_default` only so the existing Cloudflare Tunnel connector can
  reach `http://voice-channel-production:6891`.

## Required Cloudflare configuration

Do not create the public hostname until the Access application is active. The Gateway
API can read Rocket.Chat history and dispatch confirmed messages; its origin is
intentionally unreachable from a host or public port, but the tunnel hostname still
needs an identity gate.

1. Create a Cloudflare Access self-hosted application for
   `voice.peyvastegi.uk/*`.
2. Add an Allow policy restricted to Ed's identity. Do not add a Bypass policy.
3. In the existing Rocket.Chat tunnel, add the public hostname
   `voice.peyvastegi.uk` with service `http://voice-channel-production:6891`.
4. Keep WebSockets enabled and disable public caching for this hostname.
5. Run `scripts/verify-production-exposure.sh`. An unauthenticated request must get an
   Access redirect or rejection, never the Voice Channel page.
6. Sign in through a browser and verify the UI, microphone permission, room history,
   a draft, confirmation-gated send, and both voice modes.

## Deploy and update

Run:

```bash
./deploy-production.sh
```

The script builds and updates only the production Compose project, waits for its health
check, and verifies that the development container was not restarted or replaced.

Rollback is image-based: retag the previously known-good production image and run the
same Compose project again. Do not use the local `restart.sh` for production.

## Important operational notes

- Chatterbox remains on Mac loopback port `8765`; it is never a tunnel origin.
- Rocket.Chat credentials and model credentials remain in the protected `.env` file.
- Cloudflare Access protects the public edge. The application currently has no
  independent end-user login, so exposing the hostname without Access is prohibited.
- Rotate the Cloudflare tunnel token if it is ever printed, copied into a report, or
  otherwise disclosed outside the protected environment.
