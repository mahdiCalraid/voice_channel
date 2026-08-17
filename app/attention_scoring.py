"""Deterministic Attention Scoring Engine for Voice Channel (U-10b).

Derives channel attention score dynamically at request time using configured operator fields,
task supervisor attention state, and current clock without persisting score or rank to disk.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.attention_config import (
    ChannelAttentionConfig,
    ChannelAttentionEntry,
    UrgencyLevel,
    resolve_merged_channel_attention_status,
)
from app.contracts import AttentionState

ACTIONABLE_ATTENTION_STATES = {
    AttentionState.NEEDS_REVIEW,
    AttentionState.NEEDS_DECISION,
    AttentionState.NEEDS_HELP,
    AttentionState.READY_FOR_INSTRUCTION,
}


def _parse_iso_to_utc(iso_str: Optional[str]) -> Optional[datetime]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def calculate_channel_attention_score(
    entry: Optional[ChannelAttentionEntry],
    status_info: Dict[str, Any],
    attention_summary: Dict[str, Any],
    now: Optional[float] = None,
    unread_info: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[float], Optional[Dict[str, Any]]]:
    """Calculates eligibility and dynamic attention score for a channel.
    
    Returns tuple: (queue_category, score, factor_breakdown)
    Categories: 'ranked', 'busy', 'snoozed', 'inactive', 'unconfigured', 'unknown', 'idle'
    """
    now_ts = now if now is not None else time.time()
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)

    # 1. Unconfigured check
    if entry is None or status_info.get("status") == "unconfigured":
        return "unconfigured", None, None

    # 2. Inactive / disabled check
    status = status_info.get("status")
    if status == "disabled_by_registry" or entry.attention_active is False:
        return "inactive", None, None

    # 3. Snoozed check
    snoozed_until_dt = _parse_iso_to_utc(entry.snoozed_until)
    if snoozed_until_dt and now_dt < snoozed_until_dt:
        return "snoozed", None, None

    # 4. Busy check
    if attention_summary.get("is_busy"):
        return "busy", None, None

    # 5. Real conversation recency.  Unreviewed posts (Ed or a worker) get a
    # large boost so the channel rises immediately.  After Ed opens the room
    # the boost collapses and ordinary importance / urgency take over.
    last_real_message_at = None
    real_activity_age_minutes = None
    real_activity_points = 0.0
    has_unseen_real_activity = bool(unread_info and unread_info.get("has_unseen_real_activity"))
    if unread_info and unread_info.get("last_real_message_at") is not None:
        try:
            candidate_ts = float(unread_info["last_real_message_at"])
            if candidate_ts <= now_ts:
                last_real_message_at = candidate_ts
                real_activity_age_minutes = max(0.0, now_ts - candidate_ts) / 60.0
                if has_unseen_real_activity or (unread_info and unread_info.get("has_unread")):
                    real_activity_points = 2500.0 / (1.0 + (real_activity_age_minutes / 20.0))
                else:
                    real_activity_points = 25.0 / (1.0 + (real_activity_age_minutes / 180.0))
        except (TypeError, ValueError):
            pass

    # 6. Unread real agent reply scoring
    has_unread = False
    unread_base_points = 0.0
    unread_freshness_points = 0.0
    unread_points = 0.0

    if unread_info and unread_info.get("has_unread"):
        has_unread = True
        unread_base_points = 20.0
        last_reply_ts = unread_info.get("last_agent_reply_ts")
        if last_reply_ts is not None and float(last_reply_ts) <= now_ts:
            age_seconds = max(0.0, now_ts - float(last_reply_ts))
            age_minutes = age_seconds / 60.0
            unread_freshness_points = 15.0 / (1.0 + (age_minutes / 15.0))
        else:
            unread_freshness_points = 15.0
        unread_points = unread_base_points + unread_freshness_points

    # 7. Attention state & ranking eligibility checks
    attn_state = attention_summary.get("attention_state")
    is_actionable_state = (attn_state in ACTIONABLE_ATTENTION_STATES)

    if not is_actionable_state and not has_unread and not has_unseen_real_activity:
        if attn_state is None or attn_state == AttentionState.UNKNOWN:
            return "unknown", None, None
        return "idle", None, None

    # 8. Eligible for ranking: calculate score
    # Importance remains meaningful, but is intentionally much smaller than a
    # fresh real conversation.  Critical work can use priority_override.
    importance = entry.base_importance
    base_importance_points = {1: 0.0, 2: 10.0, 3: 20.0, 4: 35.0, 5: 55.0}[importance]

    # Urgency points
    if entry.urgency == UrgencyLevel.HIGH:
        urgency_points = 20.0
    elif entry.urgency == UrgencyLevel.NORMAL:
        urgency_points = 8.0
    else:
        urgency_points = 0.0

    # Waiting age points (+2.0 points per hour since ready_since)
    ready_since = attention_summary.get("ready_since")
    if ready_since is not None and ready_since <= now_ts:
        waiting_age_seconds = max(0.0, now_ts - float(ready_since))
        waiting_age_hours = waiting_age_seconds / 3600.0
        waiting_age_points = waiting_age_hours * 2.0
    else:
        waiting_age_seconds = 0.0
        waiting_age_hours = 0.0
        waiting_age_points = 0.0

    # Deadline points
    deadline_dt = _parse_iso_to_utc(entry.deadline)
    if deadline_dt:
        rem_seconds = (deadline_dt - now_dt).total_seconds()
        if rem_seconds <= 0:
            deadline_points = 150.0  # Overdue bonus
        else:
            rem_hours = rem_seconds / 3600.0
            deadline_points = max(0.0, 100.0 - (rem_hours * 2.0))
    else:
        deadline_points = 0.0

    # Blocking points
    blocking_points = 15.0 if entry.blocking else 0.0

    # Temporary boost points (Focus Today)
    boost_dt = _parse_iso_to_utc(entry.temporary_boost_until)
    if boost_dt and now_dt <= boost_dt:
        boost_points = 50.0
    else:
        boost_points = 0.0

    total_score = round(
        base_importance_points
        + urgency_points
        + waiting_age_points
        + deadline_points
        + blocking_points
        + boost_points
        + real_activity_points
        + unread_points,
        2,
    )

    factors = {
        "base_importance_points": round(base_importance_points, 2),
        "urgency_points": round(urgency_points, 2),
        "waiting_age_hours": round(waiting_age_hours, 2),
        "waiting_age_points": round(waiting_age_points, 2),
        "deadline_points": round(deadline_points, 2),
        "blocking_points": round(blocking_points, 2),
        "boost_points": round(boost_points, 2),
        "last_real_message_at": last_real_message_at,
        "real_activity_age_minutes": round(real_activity_age_minutes, 2) if real_activity_age_minutes is not None else None,
        "real_activity_points": round(real_activity_points, 2),
        "priority_override": bool(entry.priority_override),
        "unread_base_points": round(unread_base_points, 2),
        "unread_freshness_points": round(unread_freshness_points, 2),
        "unread_points": round(unread_points, 2),
        "has_unread": has_unread,
        "has_unseen_real_activity": has_unseen_real_activity,
        "snoozed": False,
        "total_score": total_score,
    }

    return "ranked", total_score, factors


def build_attention_queue(
    config: ChannelAttentionConfig,
    channel_registry: List[Dict[str, Any]],
    room_summaries: Dict[str, Dict[str, Any]],
    room_activity_map: Dict[str, Optional[float]],
    now: Optional[float] = None,
    room_unread_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Builds sorted attention queue for all registered & configured channels.
    
    Scores are calculated dynamically at request time and are never persisted to disk.
    room_activity_map is the timestamp of the last real (non-system) message.
    """
    now_ts = now if now is not None else time.time()
    merged_statuses = resolve_merged_channel_attention_status(config, channel_registry)

    queue_items: List[Dict[str, Any]] = []

    for cname, status_info in merged_statuses.items():
        entry_data = status_info.get("entry")
        entry = ChannelAttentionEntry(**entry_data) if entry_data else None

        room_id = room_summaries.get(cname, {}).get("room_id")
        attn_summary = room_summaries.get(cname) or {
            "room_id": room_id,
            "is_busy": False,
            "working_since": None,
            "attention_state": AttentionState.UNKNOWN,
            "ready_since": None,
            "active_task_count": 0,
        }

        last_activity_at = room_activity_map.get(cname)
        unread_info_source = (
            (room_unread_map.get(cname) or room_unread_map.get(room_id or ""))
            if room_unread_map
            else None
        )
        unread_info = dict(unread_info_source or {})
        unread_info["last_real_message_at"] = last_activity_at

        category, score, factors = calculate_channel_attention_score(
            entry=entry,
            status_info=status_info,
            attention_summary=attn_summary,
            now=now_ts,
            unread_info=unread_info,
        )

        working_since = attn_summary.get("working_since")
        working_elapsed = (
            max(0.0, round(now_ts - float(working_since), 1))
            if (working_since is not None and working_since <= now_ts)
            else None
        )

        ready_since = attn_summary.get("ready_since")
        waiting_age = (
            max(0.0, round(now_ts - float(ready_since), 1))
            if (ready_since is not None and ready_since <= now_ts)
            else None
        )

        attn_state_val = (
            attn_summary.get("attention_state").value
            if hasattr(attn_summary.get("attention_state"), "value")
            else str(attn_summary.get("attention_state") or "unknown")
        )

        has_unread = bool(unread_info and unread_info.get("has_unread"))
        has_unseen_real_activity = bool(unread_info and unread_info.get("has_unseen_real_activity"))
        unread_count = int(unread_info.get("unread_count", 0)) if unread_info else 0

        queue_items.append({
            "channel_name": cname,
            "room_id": room_id,
            "configured": entry is not None,
            "queue_category": category,
            "rank": None,
            "score": score,
            "attention_state": attn_state_val,
            "is_busy": bool(attn_summary.get("is_busy")),
            "has_unread": has_unread,
            "has_unseen_real_activity": has_unseen_real_activity,
            "unread_count": unread_count,
            "working_since": working_since,
            "working_elapsed_seconds": working_elapsed,
            "ready_since": ready_since,
            "waiting_age_seconds": waiting_age,
            "last_activity_at": last_activity_at,
            "last_real_message_at": last_activity_at,
            "priority_override": bool(factors and factors.get("priority_override")),
            "status": status_info.get("status"),
            "factors": factors,
        })

    def sort_key(item: Dict[str, Any]):
        # Unreviewed real posts rise before Busy and reviewed work. After Ed
        # views a room the unseen flag drops and importance score wins.
        override_priority = 0 if item["priority_override"] else 1
        suppressed_priority = 1 if item["queue_category"] in {"inactive", "snoozed"} else 0
        has_new = bool(
            item.get("has_unseen_real_activity")
            or item.get("has_unread")
        )
        new_priority = 0 if has_new else 1
        busy_priority = 0 if item["queue_category"] == "busy" else 1
        activity_priority = -(item["last_real_message_at"] or 0.0)
        has_score_priority = 0 if item["score"] is not None else 1
        score_priority = -(item["score"] or 0.0)
        return (
            suppressed_priority,
            override_priority,
            new_priority,
            activity_priority if has_new else 0.0,
            busy_priority,
            activity_priority if item["queue_category"] == "busy" else 0.0,
            has_score_priority,
            score_priority,
            activity_priority,
            item["channel_name"].lower(),
        )

    queue_items.sort(key=sort_key)

    # Assign rank (1..N) to 'ranked' category items only
    current_rank = 1
    for item in queue_items:
        if item["queue_category"] == "ranked":
            item["rank"] = current_rank
            current_rank += 1
        else:
            item["rank"] = None

    return queue_items
