"""Authenticated Rocket.Chat outgoing-webhook wake-ups for the Voice Gateway.

The webhook is intentionally a wake-up signal, not a second transcript store.
Rocket.Chat remains the source of truth; the Gateway re-fetches the affected room
after a verified notification, then runs existing classify / prewarm paths.

Authentication reuses the same shared-secret discipline as gateway ingress:
a long secret plus replay protection keyed by Rocket.Chat message id.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


DEFAULT_NONCE_PATH = Path("acli/gateway_state/webhook_message_nonces.json")
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
MIN_SECRET_LENGTH = 32


class WebhookAuthError(ValueError):
    """Raised when a webhook cannot be trusted."""


class WebhookReplayError(ValueError):
    """Raised when a webhook message id was already accepted."""


def _nonce_path() -> Path:
    configured = os.environ.get("GATEWAY_RC_WEBHOOK_NONCE_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_NONCE_PATH


def webhook_secret() -> str:
    return (
        os.environ.get("GATEWAY_RC_WEBHOOK_SECRET", "").strip()
        or os.environ.get("RC_WEBHOOK_SECRET", "").strip()
    )


def webhook_configured() -> bool:
    secret = webhook_secret()
    return len(secret) >= MIN_SECRET_LENGTH


def _load_nonce_store(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"accepted": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("accepted"), dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"accepted": {}}


def _save_nonce_store(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


_lock = threading.Lock()


def _prune_accepted(accepted: Dict[str, Any], now: float, ttl_seconds: int) -> Dict[str, Any]:
    kept: Dict[str, Any] = {}
    for key, meta in accepted.items():
        try:
            accepted_at = float((meta or {}).get("accepted_at") or 0)
        except (TypeError, ValueError):
            continue
        if now - accepted_at <= ttl_seconds:
            kept[key] = meta
    return kept


def accept_message_id(
    message_id: str,
    *,
    room_id: str = "",
    path: Optional[Path] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> None:
    """Record a Rocket.Chat message id as consumed, or raise on replay."""
    mid = str(message_id or "").strip()
    if not mid:
        raise WebhookAuthError("message_id is required for replay protection")
    store_path = path or _nonce_path()
    current = now if now is not None else time.time()
    with _lock:
        data = _load_nonce_store(store_path)
        accepted = _prune_accepted(data.get("accepted") or {}, current, ttl_seconds)
        if mid in accepted:
            raise WebhookReplayError(f"Webhook message id already processed: {mid}")
        accepted[mid] = {
            "accepted_at": int(current),
            "room_id": str(room_id or ""),
        }
        data["accepted"] = accepted
        _save_nonce_store(store_path, data)


def verify_shared_secret(
    provided: Optional[str],
    *,
    expected: Optional[str] = None,
) -> None:
    """Constant-time compare of the configured shared secret."""
    secret = (expected if expected is not None else webhook_secret()).strip()
    if len(secret) < MIN_SECRET_LENGTH:
        raise WebhookAuthError(
            f"GATEWAY_RC_WEBHOOK_SECRET must be configured with at least {MIN_SECRET_LENGTH} characters"
        )
    candidate = str(provided or "").strip()
    if not candidate or not secrets.compare_digest(candidate, secret):
        raise WebhookAuthError("Invalid Rocket.Chat webhook secret")


def extract_webhook_token(headers: Dict[str, str], payload: Dict[str, Any]) -> str:
    """Accept RC's body token or an explicit gateway header."""
    header_token = (
        headers.get("x-voice-gateway-webhook-secret")
        or headers.get("x-rc-webhook-token")
        or headers.get("x-rocketchat-webhook-token")
        or ""
    )
    body_token = payload.get("token") or payload.get("secret") or ""
    return str(header_token or body_token or "").strip()


def normalize_outgoing_webhook_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    """Normalize Rocket.Chat outgoing integration payloads to room/message ids.

    Supports both the classic flat fields and nested ``message`` objects used by
    some Rocket.Chat versions. Never treats the webhook body as durable history.
    """
    if not isinstance(payload, dict):
        raise WebhookAuthError("Webhook payload must be a JSON object")

    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    room_id = str(
        payload.get("channel_id")
        or payload.get("room_id")
        or payload.get("rid")
        or message.get("rid")
        or message.get("room_id")
        or ""
    ).strip()
    message_id = str(
        payload.get("message_id")
        or payload.get("msgId")
        or payload.get("_id")
        or message.get("_id")
        or message.get("id")
        or ""
    ).strip()
    channel_name = str(
        payload.get("channel_name")
        or payload.get("room_name")
        or message.get("channel_name")
        or ""
    ).strip()
    user_name = str(
        payload.get("user_name")
        or payload.get("username")
        or (message.get("u") or {}).get("username")
        or ""
    ).strip()
    text = str(payload.get("text") or message.get("msg") or "").strip()
    bot_flag = payload.get("bot")
    if bot_flag is None and isinstance(message.get("bot"), dict):
        bot_flag = True

    if not room_id:
        raise WebhookAuthError("Webhook payload is missing channel_id/room_id")
    if not message_id:
        raise WebhookAuthError("Webhook payload is missing message_id")

    return {
        "room_id": room_id,
        "message_id": message_id,
        "channel_name": channel_name,
        "user_name": user_name,
        "text": text,
        "bot": "1" if bot_flag else "0",
    }


def authenticate_webhook_request(
    headers: Dict[str, str],
    payload: Dict[str, Any],
    *,
    path: Optional[Path] = None,
) -> Dict[str, str]:
    """Validate secret + replay, then return normalized wake-up fields."""
    verify_shared_secret(extract_webhook_token(headers, payload))
    normalized = normalize_outgoing_webhook_payload(payload)
    accept_message_id(
        normalized["message_id"],
        room_id=normalized["room_id"],
        path=path,
    )
    return normalized
