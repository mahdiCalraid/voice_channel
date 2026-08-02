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

    # 5. Attention state checks
    attn_state = attention_summary.get("attention_state")
    if attn_state is None or attn_state == AttentionState.UNKNOWN:
        return "unknown", None, None
    if attn_state not in ACTIONABLE_ATTENTION_STATES:
        return "idle", None, None

    # 6. Eligible for ranking: calculate score
    # Base importance points (20 points per importance level 1..5)
    importance = entry.base_importance
    base_importance_points = float(importance) * 20.0

    # Urgency points
    if entry.urgency == UrgencyLevel.HIGH:
        urgency_points = 25.0
    elif entry.urgency == UrgencyLevel.NORMAL:
        urgency_points = 10.0
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
    blocking_points = 30.0 if entry.blocking else 0.0

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
        + boost_points,
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
) -> List[Dict[str, Any]]:
    """Builds sorted attention queue for all registered & configured channels.
    
    Scores are calculated dynamically at request time and are never persisted to disk.
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

        category, score, factors = calculate_channel_attention_score(
            entry=entry,
            status_info=status_info,
            attention_summary=attn_summary,
            now=now_ts,
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

        queue_items.append({
            "channel_name": cname,
            "room_id": room_id,
            "queue_category": category,
            "rank": None,
            "score": score,
            "attention_state": attn_state_val,
            "is_busy": bool(attn_summary.get("is_busy")),
            "working_since": working_since,
            "working_elapsed_seconds": working_elapsed,
            "ready_since": ready_since,
            "waiting_age_seconds": waiting_age,
            "last_activity_at": last_activity_at,
            "status": status_info.get("status"),
            "factors": factors,
        })

    # Sort rules:
    # 1. 'ranked' items first, ordered by score DESCENDING
    # 2. 'busy' items next, ordered by working_since ASCENDING (longest working first)
    # 3. 'unknown' / 'unconfigured' next
    # 4. 'idle' / 'snoozed' / 'inactive' last
    category_priority = {
        "ranked": 1,
        "busy": 2,
        "unknown": 3,
        "unconfigured": 4,
        "idle": 5,
        "snoozed": 6,
        "inactive": 7,
    }

    def sort_key(item: Dict[str, Any]):
        cat = item["queue_category"]
        prio = category_priority.get(cat, 99)
        if cat == "ranked":
            return (prio, -(item["score"] or 0.0), item["channel_name"])
        elif cat == "busy":
            return (prio, (item["working_since"] or 0.0), item["channel_name"])
        else:
            return (prio, 0.0, item["channel_name"])

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
