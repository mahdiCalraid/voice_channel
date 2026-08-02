"""Durable, room-scoped correlation of gateway interactions and ACLI events."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.contracts import (
    CURRENT_SCHEMA_VERSION,
    AttentionState,
    ConfirmationSnapshot,
    InteractionRequest,
    TaskEvent,
    TaskRecord,
    TaskState,
)


ACTIVE_STATES = {TaskState.POSTED, TaskState.ROUTED, TaskState.WORKING}
TERMINAL_STATES = {
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELLED,
    TaskState.SUPERSEDED,
}


def _model_dump(model):
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _model_validate(model_class, value):
    return model_class.model_validate(value) if hasattr(model_class, "model_validate") else model_class.parse_obj(value)


class TaskSupervisor:
    """File-backed task state that survives gateway refreshes and container restarts.

    ACLI currently emits no interaction ID. Correlation is therefore deliberately
    conservative: room + selected agent + most-recent active interaction. A source
    message is never processed twice, and ambiguous same-agent concurrency remains
    visible instead of being guessed silently.
    """

    def __init__(self, state_path: str):
        self.state_path = Path(state_path)
        self._lock = threading.RLock()
        self._tasks: Dict[str, TaskRecord] = {}
        self._source_event_index: Dict[str, str] = {}
        self._load()

    def _load(self):
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._tasks = {
                interaction_id: _model_validate(TaskRecord, record)
                for interaction_id, record in data.get("tasks", {}).items()
            }
            self._source_event_index = dict(data.get("source_event_index", {}))
        except (OSError, ValueError, TypeError):
            # Keep the gateway usable if a damaged local state file is encountered.
            self._tasks = {}
            self._source_event_index = {}

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "tasks": {interaction_id: _model_dump(record) for interaction_id, record in self._tasks.items()},
            "source_event_index": self._source_event_index,
        }
        temporary_path = self.state_path.with_suffix(".tmp")
        temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary_path, self.state_path)

    def create_interaction(self, request: InteractionRequest, confirmation: ConfirmationSnapshot, event: TaskEvent) -> TaskRecord:
        with self._lock:
            record = TaskRecord(
                interaction_id=request.interaction_id,
                actor_id=request.actor_id,
                client_id=request.client_id,
                room_id=confirmation.room_id,
                agent=confirmation.agent,
                state=TaskState.AWAITING_CONFIRMATION,
                attention_state=AttentionState.NEEDS_DECISION,
                ready_since=request.created_at,
                working_since=None,
                created_at=request.created_at,
                updated_at=time.time(),
                confirmation_snapshot=confirmation,
                task_events=[event],
            )
            self._tasks[record.interaction_id] = record
            self._save()
            return record

    def get(self, interaction_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._tasks.get(interaction_id)

    def all(self) -> Iterable[TaskRecord]:
        with self._lock:
            return list(self._tasks.values())

    def get_channel_attention_summary(self, room_id: str) -> Dict[str, Any]:
        """Aggregate tasks for one channel: busy execution state takes precedence."""
        with self._lock:
            channel_tasks = [t for t in self._tasks.values() if t.room_id == room_id]
            if not channel_tasks:
                return {
                    "room_id": room_id,
                    "is_busy": False,
                    "working_since": None,
                    "attention_state": AttentionState.UNKNOWN,
                    "ready_since": None,
                    "active_task_count": 0,
                }
            active_tasks = [t for t in channel_tasks if t.state in ACTIVE_STATES]
            if active_tasks:
                working_times = [t.working_since for t in active_tasks if t.working_since is not None]
                working_since = min(working_times) if working_times else min(t.updated_at for t in active_tasks)
                return {
                    "room_id": room_id,
                    "is_busy": True,
                    "working_since": working_since,
                    "attention_state": AttentionState.NONE,
                    "ready_since": None,
                    "active_task_count": len(active_tasks),
                }
            
            # Among non-busy tasks, prefer the oldest task in an actionable state (NEEDS_REVIEW, NEEDS_DECISION, NEEDS_HELP)
            # so the waiting age reflects the earliest unmet obligation. Fall back to newest task if none is actionable.
            actionable_states = {AttentionState.NEEDS_REVIEW, AttentionState.NEEDS_DECISION, AttentionState.NEEDS_HELP}
            actionable_tasks = [t for t in channel_tasks if t.attention_state in actionable_states]
            if actionable_tasks:
                actionable_tasks.sort(key=lambda t: t.ready_since if t.ready_since is not None else t.updated_at)
                target = actionable_tasks[0]
            else:
                sorted_tasks = sorted(channel_tasks, key=lambda t: t.updated_at, reverse=True)
                target = sorted_tasks[0]

            return {
                "room_id": room_id,
                "is_busy": False,
                "working_since": None,
                "attention_state": target.attention_state,
                "ready_since": target.ready_since,
                "active_task_count": 0,
            }

    def confirmation_matches(self, confirmation: ConfirmationSnapshot) -> bool:
        with self._lock:
            record = self._tasks.get(confirmation.immutable_interaction_id)
            if not record or not record.confirmation_snapshot:
                return False
            return _model_dump(record.confirmation_snapshot) == _model_dump(confirmation)

    def mark_posted(self, interaction_id: str, message_id: str, cached: bool = False) -> TaskRecord:
        with self._lock:
            record = self._tasks[interaction_id]
            if message_id not in record.rocket_chat_msg_ids:
                record.rocket_chat_msg_ids.append(message_id)
            # A nonce replay must never rewind a routed, working, or terminal task.
            if cached and record.state != TaskState.AWAITING_CONFIRMATION:
                self._save()
                return record
            self._transition(
                record,
                TaskState.POSTED,
                details={"msg_id": message_id, "cached": cached},
            )
            self._save()
            return record

    def ingest_event(
        self,
        room_id: str,
        source_message_id: Optional[str],
        timestamp: float,
        event: Dict[str, Any],
    ) -> Optional[TaskRecord]:
        """Apply one classified ACLI event, returning its correlated task if any."""
        event_kind = event.get("kind")
        agent = event.get("agent")
        desired_state = {
            "routing": TaskState.ROUTED,
            "heartbeat": TaskState.WORKING,
            "agent_response": TaskState.COMPLETED,
            "error": TaskState.FAILED,
            "stopped": TaskState.CANCELLED,
        }.get(event_kind)
        if desired_state is None:
            return None

        with self._lock:
            if source_message_id and source_message_id in self._source_event_index:
                return self._tasks.get(self._source_event_index[source_message_id])

            explicit_interaction_id = event.get("interaction_id")
            if explicit_interaction_id:
                record = self._tasks.get(explicit_interaction_id)
                # An explicit ID is authoritative only if it still matches this room,
                # selected agent, and interaction lifetime. Do not fall back to guessing.
                if (
                    not record
                    or record.room_id != room_id
                    or record.state not in ACTIVE_STATES
                    or (agent and record.agent != agent)
                    or timestamp < record.created_at
                ):
                    return None
                candidates = [record]
            else:
                candidates = [
                    task
                    for task in self._tasks.values()
                    if task.room_id == room_id
                    and task.state in ACTIVE_STATES
                    and (not agent or task.agent == agent)
                    # History requests include messages from before this interaction.
                    # Never let those older events complete a newly created task.
                    and timestamp >= task.created_at
                ]
            if not candidates:
                return None

            candidates.sort(key=lambda task: task.updated_at, reverse=True)
            record = candidates[0]
            details = {"event_kind": event_kind, "agent": agent, "raw_text": event.get("raw_text", "")}
            elapsed_sec = event.get("elapsed_seconds") or (event.get("details") or {}).get("elapsed_seconds")
            if elapsed_sec is not None:
                details["elapsed_seconds"] = elapsed_sec
            if explicit_interaction_id:
                details["correlation"] = "explicit_interaction_id"
            if len(candidates) > 1:
                details["correlation_warning"] = "multiple active tasks match this room and agent"
            if event_kind == "routing":
                # ACLI emits routing per agent but no interaction ID. A newly posted task
                # routed to that agent supersedes an older routed/working task for it.
                for prior in candidates[1:]:
                    if record.state == TaskState.POSTED and prior.state in {TaskState.ROUTED, TaskState.WORKING}:
                        self._transition(
                            prior,
                            TaskState.SUPERSEDED,
                            timestamp,
                            details={"reason": "newer task routed to the same agent"},
                        )
            self._transition(record, desired_state, timestamp, source_message_id, details)
            if source_message_id:
                self._source_event_index[source_message_id] = record.interaction_id
            self._save()
            return record

    def _transition(
        self,
        record: TaskRecord,
        state: TaskState,
        timestamp: Optional[float] = None,
        source_message_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        timestamp = timestamp or time.time()
        if source_message_id and source_message_id not in record.source_message_ids:
            record.source_message_ids.append(source_message_id)
        record.state = state
        record.updated_at = timestamp
        
        # Derive attention_state and timestamps
        if state in ACTIVE_STATES:
            record.attention_state = AttentionState.NONE
            if state in {TaskState.ROUTED, TaskState.WORKING}:
                prior_state = record.task_events[-1].state if record.task_events else None
                if record.working_since is None or prior_state == TaskState.POSTED:
                    elapsed = (details or {}).get("elapsed_seconds")
                    if elapsed is not None:
                        try:
                            record.working_since = max(0.0, timestamp - float(elapsed))
                        except (ValueError, TypeError):
                            record.working_since = timestamp
                    else:
                        record.working_since = timestamp
            elif state == TaskState.POSTED:
                record.working_since = None
            record.ready_since = None
        elif state == TaskState.COMPLETED:
            record.attention_state = AttentionState.NEEDS_REVIEW
            if record.ready_since is None:
                record.ready_since = timestamp
            record.working_since = None
        elif state == TaskState.FAILED:
            record.attention_state = AttentionState.NEEDS_HELP
            if record.ready_since is None:
                record.ready_since = timestamp
            record.working_since = None
        elif state in {TaskState.AWAITING_CONFIRMATION, TaskState.NEEDS_CLARIFICATION}:
            record.attention_state = AttentionState.NEEDS_DECISION
            if record.ready_since is None:
                record.ready_since = timestamp
            record.working_since = None
        elif state in {TaskState.CANCELLED, TaskState.SUPERSEDED}:
            record.attention_state = AttentionState.NONE
            record.working_since = None
            record.ready_since = None

        record.task_events.append(
            TaskEvent(
                interaction_id=record.interaction_id,
                timestamp=timestamp,
                state=state,
                source_message_id=source_message_id,
                details=details or {},
            )
        )
        if state in TERMINAL_STATES:
            record.terminal_summary = (details or {}).get("event_kind", state.value)
