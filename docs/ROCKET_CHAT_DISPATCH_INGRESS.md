# Rocket.Chat Dispatch Ingress Contract

## Boundary

The only permitted execution path is:

```text
client -> Voice Gateway -> Rocket.Chat -> ACLI -> worker
```

The gateway must not call an ACLI process, socket, worker API, or shell. Omi and other
clients connect only to the gateway.

## Rocket.Chat Identity

Create a dedicated Rocket.Chat service user for the gateway. Configure only the gateway
with its user ID and token:

```text
GATEWAY_RC_USER_ID=<dedicated RC user id>
GATEWAY_RC_AUTH_TOKEN=<dedicated RC token>
GATEWAY_RC_INGRESS_SECRET=<random 32+ character shared secret>
```

The identity must not be `acli_bot` and must not impersonate Ed. ACLI must treat this user
as a trusted ingress source only when the signed envelope below verifies. Without all three
values, `POST /api/gateway/confirm` fails closed with HTTP 503.

## Message Format

The gateway posts one visible Rocket.Chat message:

```text
[voice-gateway/v1; interaction_id=<id>; room_id=<room>; agent=<agent>; nonce=<nonce>; issued_at=<epoch>; body_sha256=<hex>; signature=<hex>]
@<agent> <confirmed message>
```

`signature` is HMAC-SHA256 with `GATEWAY_RC_INGRESS_SECRET` over these newline-separated
fields, in this exact order:

```text
version=1
interaction_id=<id>
room_id=<room>
agent=<agent>
nonce=<nonce>
issued_at=<epoch>
body_sha256=<SHA256 of the exact second line/body bytes>
```

The envelope remains in Rocket.Chat so the request is auditable. It is not a secret; only
the HMAC key is secret.

## ACLI Responsibilities

Before routing a gateway message, ACLI must:

1. Accept only the configured gateway Rocket.Chat user.
2. Parse the first-line envelope and reject malformed, expired, replayed, room-mismatched,
   agent-mismatched, or invalid-HMAC messages.
3. Verify `body_sha256` against the exact remaining message body.
4. Treat the remaining body as the requested work and preserve the original Rocket.Chat
   message ID in the route record.
5. Echo `interaction_id=<id>` in its routing, heartbeat, terminal, failure, and stop events.

The gateway supervisor uses that echoed ID for exact correlation. It deliberately does not
fall back to room-and-agent guessing when a malformed explicit ID is present.

## Verification

After the ACLI consumer is implemented and a dedicated RC identity is provisioned:

1. Confirm one low-impact gateway request.
2. Verify the visible signed RC message, one ACLI route with the same `interaction_id`, and
   terminal completion/failure/cancellation with the same ID.
3. Restart the gateway and verify the persisted task remains terminal.
4. Attempt a replay and a tampered envelope; both must be rejected without a second route.
