"""Signed Rocket.Chat-only ingress envelopes for gateway-confirmed requests."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Optional

from app.contracts import ConfirmationSnapshot


INGRESS_VERSION = "1"
INGRESS_PREFIX = "[voice-gateway/v1"
_LIFECYCLE_TRAILER_RE = re.compile(
    r"(?:^|\n)\[gateway_interaction_id=(?P<interaction_id>[A-Za-z0-9_.-]+)\]\s*$"
)


class IngressConfigurationError(ValueError):
    """Raised when the gateway cannot safely create an ACLI ingress message."""


def _body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _canonical_fields(
    interaction_id: str,
    room_id: str,
    agent: str,
    nonce: str,
    issued_at: int,
    body_sha256: str,
) -> str:
    return "\n".join(
        (
            f"version={INGRESS_VERSION}",
            f"interaction_id={interaction_id}",
            f"room_id={room_id}",
            f"agent={agent}",
            f"nonce={nonce}",
            f"issued_at={issued_at}",
            f"body_sha256={body_sha256}",
        )
    )


def build_ingress_message(
    confirmation: ConfirmationSnapshot,
    secret: str,
    issued_at: Optional[int] = None,
) -> str:
    """Return a visible, signed Rocket.Chat message for ACLI to verify and route."""
    if len(secret) < 32:
        raise IngressConfigurationError("GATEWAY_RC_INGRESS_SECRET must contain at least 32 characters")
    if issued_at is None:
        issued_at = int(time.time())

    body_sha256 = _body_sha256(confirmation.exact_message)
    canonical = _canonical_fields(
        confirmation.immutable_interaction_id,
        confirmation.room_id,
        confirmation.agent,
        confirmation.nonce,
        issued_at,
        body_sha256,
    )
    signature = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    header = (
        f"{INGRESS_PREFIX}; interaction_id={confirmation.immutable_interaction_id};"
        f" room_id={confirmation.room_id}; agent={confirmation.agent}; nonce={confirmation.nonce};"
        f" issued_at={issued_at}; body_sha256={body_sha256}; signature={signature}]"
    )
    return f"{header}\n{confirmation.exact_message}"


def extract_interaction_id(text: str) -> Optional[str]:
    """Extract only ACLI's final lifecycle trailer, never a quoted request header."""
    match = _LIFECYCLE_TRAILER_RE.search(text or "")
    return match.group("interaction_id") if match else None
