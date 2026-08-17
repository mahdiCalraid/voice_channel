"""Per-actor read cursor tracking for Voice Channel.

Stores minimal cursor metadata per room: room_id, last_read_msg_id, last_read_ts, updated_by_actor.
Conforms strictly to privacy guidelines: zero raw transcript text or excerpts stored.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path("acli/gateway_state")
CURSOR_FILE = STATE_DIR / "read_cursors.json"


def _load_cursors_data() -> Dict[str, Any]:
    if not CURSOR_FILE.is_file():
        return {"rooms": {}}
    try:
        data = json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "rooms" in data and isinstance(data["rooms"], dict):
            return data
    except Exception:
        pass
    return {"rooms": {}}


def _save_cursors_data(data: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CURSOR_FILE.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(CURSOR_FILE)
    except Exception:
        pass


def is_real_agent_reply(msg: Dict[str, Any]) -> bool:
    """Classifies whether a message is an actual real agent reply.
    
    Excludes system routing notices, heartbeats, model switches, and status updates.
    """
    if not isinstance(msg, dict):
        return False

    # Exclude system lane & system event kinds
    if msg.get("lane") == "system":
        return False

    event = msg.get("event") or {}
    if not isinstance(event, dict):
        event = {}
    event_kind = event.get("kind")
    if event_kind in ("routing", "routing_notice", "heartbeat", "model_change", "model_selected", "status_update", "system_notice"):
        return False

    text = str(msg.get("text") or "").strip()
    if not text:
        return False

    lane = msg.get("lane")
    sender = str(msg.get("name") or msg.get("username") or "").lower()
    event_agent = str(event.get("agent") or "").lower()

    is_agent_lane = (lane == "agent")
    is_known_agent_sender = sender in ("codex", "claude", "grok", "agy", "voice_gateway") or event_agent in ("codex", "claude", "grok", "agy")
    is_agent_kind = (event_kind == "agent_response")

    return bool(is_agent_lane or is_known_agent_sender or is_agent_kind)


def is_real_conversation_message(msg: Dict[str, Any]) -> bool:
    """Return true for any substantive person/agent message, never system chatter.

    Channel ordering is intentionally broader than unread-reply detection: a recent
    instruction from Ed and a recent substantive agent response both mean the
    channel has an active real conversation. A signed Gateway dispatch is the
    transport form of Ed's confirmed instruction, despite using the system lane.
    Routing notices, heartbeats, status changes, and every other system-lane
    message must never refresh this timestamp.
    """
    if not isinstance(msg, dict):
        return False

    event = msg.get("event") or {}
    if not isinstance(event, dict):
        event = {}
    text = str(msg.get("text") or msg.get("msg") or "").strip()
    if event.get("kind") == "gateway_dispatch":
        return bool(text)
    if msg.get("lane") == "system":
        return False
    if event.get("kind") in {
        "routing", "routing_notice", "heartbeat", "model_change",
        "model_selected", "status_update", "system_notice",
    }:
        return False

    return bool(text)


def get_last_real_conversation_timestamp(messages: List[Dict[str, Any]]) -> Optional[float]:
    """Return the newest substantive non-system message timestamp in a room."""
    timestamps = [
        _extract_msg_timestamp(message)
        for message in messages
        if is_real_conversation_message(message)
    ]
    timestamps = [timestamp for timestamp in timestamps if timestamp > 0]
    return max(timestamps) if timestamps else None


def update_read_cursor(room_id: str, msg_id: Optional[str] = None, ts: Optional[float] = None, actor: str = "ed") -> Dict[str, Any]:
    """Updates the read cursor for a room."""
    data = _load_cursors_data()
    rooms = data.get("rooms", {})
    existing = rooms.get(room_id, {})

    target_ts = float(ts) if ts is not None else time.time()
    target_msg_id = str(msg_id) if msg_id else existing.get("last_read_msg_id", "")

    existing_ts = float(existing.get("last_read_ts") or 0.0)
    if target_ts >= existing_ts:
        rooms[room_id] = {
            "room_id": room_id,
            "last_read_msg_id": target_msg_id,
            "last_read_ts": target_ts,
            "updated_by_actor": str(actor),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        data["rooms"] = rooms
        _save_cursors_data(data)
        return rooms[room_id]
    return existing


def get_read_cursor(room_id: str) -> Optional[Dict[str, Any]]:
    """Gets the read cursor for a room."""
    data = _load_cursors_data()
    return data.get("rooms", {}).get(room_id)


def get_all_read_cursors() -> Dict[str, Dict[str, Any]]:
    """Gets all read cursors."""
    data = _load_cursors_data()
    return data.get("rooms", {})


def _extract_msg_timestamp(msg: Dict[str, Any]) -> float:
    if not isinstance(msg, dict):
        return 0.0
    raw_ts = (
        msg.get("timestamp")
        or (msg.get("event", {}) if isinstance(msg.get("event"), dict) else {}).get("ts")
        or msg.get("ts")
        or msg.get("_updatedAt")
    )
    if isinstance(raw_ts, (int, float)):
        val = float(raw_ts)
        return val / 1000.0 if val > 1e11 else val
    if isinstance(raw_ts, str) and raw_ts.strip():
        s = raw_ts.strip()
        try:
            val = float(s)
            return val / 1000.0 if val > 1e11 else val
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            pass
    return 0.0


def evaluate_room_unread_status(room_id: str, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Evaluates unread status for a room based on stored read cursor and room messages."""
    cursor = get_read_cursor(room_id)
    last_read_ts = float(cursor.get("last_read_ts") or 0.0) if cursor else 0.0
    last_read_id = cursor.get("last_read_msg_id") if cursor else None

    last_real_message_at = get_last_real_conversation_timestamp(messages)
    # A room with no cursor still rises for a *recent* real post so live
    # updates are not buried. Older backlog stays quiet until Ed opens it.
    unseen_horizon_seconds = 4 * 3600
    now_ts = time.time()
    if last_real_message_at:
        if cursor:
            has_unseen_real_activity = last_real_message_at > last_read_ts
        else:
            has_unseen_real_activity = (now_ts - last_real_message_at) <= unseen_horizon_seconds
    else:
        has_unseen_real_activity = False

    real_agent_replies = [m for m in messages if is_real_agent_reply(m)]
    if not real_agent_replies:
        return {
            "has_unread": False,
            "unread_count": 0,
            "last_agent_reply_ts": None,
            "last_agent_reply_id": None,
            "last_real_message_at": last_real_message_at,
            "has_unseen_real_activity": has_unseen_real_activity,
        }

    latest_reply = real_agent_replies[-1]
    latest_reply_ts = _extract_msg_timestamp(latest_reply)
    latest_reply_id = str(latest_reply.get("id") or latest_reply.get("_id") or "")

    if not cursor:
        # If cursor has not been initialized yet, treat room as read so historical backlog doesn't mark all channels unread on first launch
        has_unread = False
        unread_count = 0
    elif last_read_id and last_read_id == latest_reply_id:
        has_unread = False
        unread_count = 0
    elif last_read_ts > 0.0 and last_read_ts >= latest_reply_ts:
        has_unread = False
        unread_count = 0
    else:
        unread_replies = [
            m for m in real_agent_replies
            if str(m.get("id") or m.get("_id") or "") != last_read_id
            and _extract_msg_timestamp(m) > last_read_ts
        ]
        has_unread = True
        unread_count = max(1, len(unread_replies))

    return {
        "has_unread": has_unread,
        "unread_count": unread_count,
        "last_agent_reply_ts": latest_reply_ts if latest_reply_ts > 0 else None,
        "last_agent_reply_id": latest_reply_id,
        "last_real_message_at": last_real_message_at,
        "has_unseen_real_activity": has_unseen_real_activity,
    }
