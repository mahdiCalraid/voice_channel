"""Authenticated Rocket.Chat outgoing-webhook wake-ups for the Voice Gateway.

The webhook is intentionally a wake-up signal, not a second transcript store.
Rocket.Chat remains the source of truth; the Gateway re-fetches the affected room
after a verified notification, then runs existing classify / prewarm paths.

Authentication uses a long shared secret plus bounded, durable replay protection
keyed by Rocket.Chat message id.  Only delivery metadata is persisted here; raw
message text never enters the Gateway webhook state.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_NONCE_PATH = Path("acli/gateway_state/webhook_message_nonces.sqlite3")
DEFAULT_STATE_PATH = Path("acli/gateway_state/webhook_status.json")
DEFAULT_TTL_SECONDS = 6 * 3600
DEFAULT_MAX_ENTRIES = 5_000
DEFAULT_MAX_COVERED_ROOMS = 1_000
MIN_SECRET_LENGTH = 32
WEBHOOK_STALE_AFTER_SECONDS = 15 * 60

_METRIC_NAMES = (
    "received_count",
    "verified_count",
    "replayed_count",
    "auth_failures",
    "stale_rejections",
    "processing_failures",
    "coalesced_count",
    "real_agent_replies",
    "prewarm_scheduled",
)


class WebhookAuthError(ValueError):
    """Raised when a webhook cannot be trusted."""


class WebhookReplayError(ValueError):
    """Raised when a webhook message id was already accepted."""


def _nonce_path() -> Path:
    configured = os.environ.get("GATEWAY_RC_WEBHOOK_NONCE_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_NONCE_PATH


def _state_path() -> Path:
    configured = os.environ.get("GATEWAY_RC_WEBHOOK_STATE_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_STATE_PATH


def webhook_secret() -> str:
    return (
        os.environ.get("GATEWAY_RC_WEBHOOK_SECRET", "").strip()
        or os.environ.get("RC_WEBHOOK_SECRET", "").strip()
    )


def webhook_configured() -> bool:
    return len(webhook_secret()) >= MIN_SECRET_LENGTH


_nonce_lock = threading.Lock()
_state_lock = threading.Lock()


def _legacy_nonce_rows(path: Path) -> list[tuple[str, float, str]]:
    """Read the old JSON replay store during a one-time in-place migration."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    accepted = data.get("accepted") if isinstance(data, dict) else None
    if not isinstance(accepted, dict):
        return []
    rows: list[tuple[str, float, str]] = []
    for message_id, meta in accepted.items():
        try:
            accepted_at = float((meta or {}).get("accepted_at") or 0)
        except (TypeError, ValueError):
            continue
        if message_id and accepted_at > 0:
            rows.append((str(message_id), accepted_at, str((meta or {}).get("room_id") or "")))
    return rows


def _initialize_nonce_db(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS accepted (
            message_id TEXT PRIMARY KEY,
            accepted_at REAL NOT NULL,
            room_id TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS accepted_by_time ON accepted(accepted_at)"
    )


def _connect_nonce_db(path: Path) -> sqlite3.Connection:
    """Open the replay DB, migrating the former JSON format if encountered."""
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_rows: list[tuple[str, float, str]] = []
    if path.is_file():
        try:
            is_sqlite = path.read_bytes()[:16] == b"SQLite format 3\x00"
        except OSError:
            is_sqlite = False
        if not is_sqlite:
            legacy_rows = _legacy_nonce_rows(path)
            migration_path = path.with_suffix(path.suffix + ".migrating")
            try:
                migration_path.unlink(missing_ok=True)
                migrated = sqlite3.connect(str(migration_path), timeout=5)
                _initialize_nonce_db(migrated)
                migrated.executemany(
                    "INSERT OR IGNORE INTO accepted(message_id, accepted_at, room_id) VALUES (?, ?, ?)",
                    legacy_rows,
                )
                migrated.commit()
                migrated.close()
                os.replace(migration_path, path)
            finally:
                migration_path.unlink(missing_ok=True)

    connection = sqlite3.connect(str(path), timeout=5)
    connection.execute("PRAGMA synchronous=NORMAL")
    _initialize_nonce_db(connection)
    return connection


def accept_message_id(
    message_id: str,
    *,
    room_id: str = "",
    path: Optional[Path] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    now: Optional[float] = None,
) -> None:
    """Record a Rocket.Chat message id as consumed, or raise on replay.

    SQLite gives us an indexed insert/delete instead of rewriting the full JSON
    collection on every public-channel event.  Both age and row-count bounds are
    enforced in the same transaction as the insert.
    """
    mid = str(message_id or "").strip()
    if not mid:
        raise WebhookAuthError("message_id is required for replay protection")
    store_path = path or _nonce_path()
    current = float(now if now is not None else time.time())
    bounded_ttl = max(60, int(ttl_seconds))
    bounded_count = max(1, int(max_entries))
    with _nonce_lock:
        connection = _connect_nonce_db(store_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM accepted WHERE accepted_at < ?",
                (current - bounded_ttl,),
            )
            try:
                connection.execute(
                    "INSERT INTO accepted(message_id, accepted_at, room_id) VALUES (?, ?, ?)",
                    (mid, current, str(room_id or "")),
                )
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise WebhookReplayError(
                    f"Webhook message id already processed: {mid}"
                ) from error
            row_count = int(connection.execute("SELECT COUNT(*) FROM accepted").fetchone()[0])
            excess = row_count - bounded_count
            if excess > 0:
                connection.execute(
                    """
                    DELETE FROM accepted
                    WHERE message_id IN (
                        SELECT message_id FROM accepted
                        ORDER BY accepted_at ASC, message_id ASC
                        LIMIT ?
                    )
                    """,
                    (excess,),
                )
            connection.commit()
        finally:
            connection.close()


def rollback_message_id(message_id: str, *, path: Optional[Path] = None) -> None:
    """Remove a previously accepted message id to allow retry after failure."""
    mid = str(message_id or "").strip()
    if not mid:
        return
    with _nonce_lock:
        connection = _connect_nonce_db(path or _nonce_path())
        try:
            connection.execute("DELETE FROM accepted WHERE message_id = ?", (mid,))
            connection.commit()
        finally:
            connection.close()


def nonce_store_count(path: Optional[Path] = None) -> int:
    """Return the bounded replay-store row count for diagnostics and tests."""
    with _nonce_lock:
        connection = _connect_nonce_db(path or _nonce_path())
        try:
            return int(connection.execute("SELECT COUNT(*) FROM accepted").fetchone()[0])
        finally:
            connection.close()


def _default_webhook_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "last_received_at": None,
        "last_verified_at": None,
        "covered_rooms": {},
        "metrics": {name: 0 for name in _METRIC_NAMES},
        "last_error": None,
        "last_error_at": None,
        "last_event": None,
    }


def _normalize_webhook_state(value: Any) -> Dict[str, Any]:
    state = _default_webhook_state()
    if not isinstance(value, dict):
        return state
    for name in ("last_received_at", "last_verified_at", "last_error_at"):
        try:
            raw = value.get(name)
            state[name] = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            state[name] = None
    covered = value.get("covered_rooms")
    if isinstance(covered, dict):
        for room_id, timestamp in covered.items():
            try:
                room_key = str(room_id or "").strip()
                if room_key:
                    state["covered_rooms"][room_key] = float(timestamp)
            except (TypeError, ValueError):
                continue
    supplied_metrics = value.get("metrics")
    if isinstance(supplied_metrics, dict):
        for name in _METRIC_NAMES:
            try:
                state["metrics"][name] = max(0, int(supplied_metrics.get(name) or 0))
            except (TypeError, ValueError):
                state["metrics"][name] = 0
    raw_error = value.get("last_error")
    state["last_error"] = str(raw_error)[:500] if raw_error else None
    raw_event = value.get("last_event")
    if isinstance(raw_event, dict):
        state["last_event"] = {
            "message_id": str(raw_event.get("message_id") or "")[:200],
            "room_id": str(raw_event.get("room_id") or "")[:200],
            "verified_at": raw_event.get("verified_at"),
            "new_real_agent_replies": int(raw_event.get("new_real_agent_replies") or 0),
            "prewarm_scheduled": int(raw_event.get("prewarm_scheduled") or 0),
            "stale": bool(raw_event.get("stale")),
        }
    return state


def load_webhook_state(path: Optional[Path] = None) -> Dict[str, Any]:
    state_path = path or _state_path()
    try:
        return _normalize_webhook_state(json.loads(state_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return _default_webhook_state()


def update_webhook_state(
    *,
    path: Optional[Path] = None,
    received_at: Optional[float] = None,
    verified_at: Optional[float] = None,
    covered_room_id: str = "",
    increments: Optional[Dict[str, int]] = None,
    last_event: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Atomically persist liveness watermarks and bounded operational metrics."""
    state_path = path or _state_path()
    current = float(now if now is not None else time.time())
    with _state_lock:
        state = load_webhook_state(state_path)
        if received_at is not None:
            state["last_received_at"] = float(received_at)
        if verified_at is not None:
            state["last_verified_at"] = float(verified_at)
        room_key = str(covered_room_id or "").strip()
        if room_key and verified_at is not None:
            state["covered_rooms"][room_key] = float(verified_at)
        for metric_name, amount in (increments or {}).items():
            if metric_name in state["metrics"]:
                state["metrics"][metric_name] += max(0, int(amount))
        if isinstance(last_event, dict):
            state["last_event"] = {
                "message_id": str(last_event.get("message_id") or "")[:200],
                "room_id": str(last_event.get("room_id") or "")[:200],
                "verified_at": last_event.get("verified_at"),
                "new_real_agent_replies": int(last_event.get("new_real_agent_replies") or 0),
                "prewarm_scheduled": int(last_event.get("prewarm_scheduled") or 0),
                "stale": bool(last_event.get("stale")),
            }
        if error:
            state["last_error"] = str(error)[:500]
            state["last_error_at"] = current
        elif verified_at is not None:
            # A later Rocket.Chat source-of-truth refetch proves recovery. Keep
            # cumulative failure counters, but do not present a resolved fault as
            # the current webhook error.
            state["last_error"] = None
            state["last_error_at"] = None

        newest_rooms = sorted(
            state["covered_rooms"].items(),
            key=lambda item: item[1],
            reverse=True,
        )[:DEFAULT_MAX_COVERED_ROOMS]
        state["covered_rooms"] = dict(newest_rooms)

        state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = state_path.with_suffix(state_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, state_path)
        return state


def verify_shared_secret(provided: Optional[str], *, expected: Optional[str] = None) -> None:
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
    """Normalize Rocket.Chat outgoing integration payloads to trusted hints."""
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
    event_timestamp = str(
        payload.get("timestamp")
        or payload.get("ts")
        or message.get("ts")
        or message.get("timestamp")
        or ""
    ).strip()
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
        "timestamp": event_timestamp,
        "bot": "1" if bot_flag else "0",
    }


def webhook_event_timestamp(normalized: Dict[str, str]) -> Optional[float]:
    raw = str((normalized or {}).get("timestamp") or "").strip()
    if not raw:
        return None
    try:
        numeric = float(raw)
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def webhook_event_is_stale(
    normalized: Dict[str, str],
    *,
    now: Optional[float] = None,
    max_age_seconds: int = WEBHOOK_STALE_AFTER_SECONDS,
) -> bool:
    event_time = webhook_event_timestamp(normalized)
    if event_time is None:
        return False
    current = float(now if now is not None else time.time())
    return current - event_time > max(60, int(max_age_seconds))


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
