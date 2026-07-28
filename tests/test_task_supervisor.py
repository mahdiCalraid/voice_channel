"""Deterministic task-supervisor tests using interleaved ACLI event fixtures."""

import tempfile
import time
import unittest
from pathlib import Path

from app.contracts import ConfirmationSnapshot, InteractionRequest, TaskEvent, TaskState
from app.task_supervisor import TaskSupervisor


def make_task(supervisor, interaction_id, room_id, agent, created_at=0):
    request = InteractionRequest(
        interaction_id=interaction_id,
        actor_id="ed",
        client_id="test",
        raw_input=f"@{agent} test task",
        created_at=created_at,
        requested_room_id=room_id,
        requested_agent=agent,
    )
    confirmation = ConfirmationSnapshot(
        immutable_interaction_id=interaction_id,
        room_id=room_id,
        agent=agent,
        exact_message=request.raw_input,
        expires_at=time.time() + 300,
        nonce=f"nonce_{interaction_id}",
    )
    event = TaskEvent(interaction_id=interaction_id, state=TaskState.AWAITING_CONFIRMATION)
    supervisor.create_interaction(request, confirmation, event)
    return supervisor.mark_posted(interaction_id, f"rc_{interaction_id}")


class TestTaskSupervisor(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = str(Path(self.temp_dir.name) / "tasks.json")
        self.supervisor = TaskSupervisor(self.state_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_interleaved_agents_remain_isolated_and_terminal(self):
        codex = make_task(self.supervisor, "int_codex", "room-a", "codex")
        claude = make_task(self.supervisor, "int_claude", "room-a", "claude")

        self.supervisor.ingest_event("room-a", "route-codex", 10, {"kind": "routing", "agent": "codex"})
        self.supervisor.ingest_event("room-a", "route-claude", 11, {"kind": "routing", "agent": "claude"})
        self.supervisor.ingest_event("room-a", "beat-codex", 12, {"kind": "heartbeat", "agent": "codex"})
        self.supervisor.ingest_event("room-a", "result-claude", 13, {"kind": "agent_response", "agent": "claude"})
        self.supervisor.ingest_event("room-a", "error-codex", 14, {"kind": "error", "agent": "codex"})

        self.assertEqual(self.supervisor.get(codex.interaction_id).state, TaskState.FAILED)
        self.assertEqual(self.supervisor.get(claude.interaction_id).state, TaskState.COMPLETED)
        self.assertIn("error-codex", self.supervisor.get(codex.interaction_id).source_message_ids)
        self.assertIn("result-claude", self.supervisor.get(claude.interaction_id).source_message_ids)

    def test_historical_events_do_not_complete_new_interaction(self):
        task = make_task(
            self.supervisor,
            "int_historical",
            "room-history",
            "codex",
            created_at=100,
        )
        historical_timestamp = 99

        result = self.supervisor.ingest_event(
            "room-history",
            "old-codex-response",
            historical_timestamp,
            {"kind": "agent_response", "agent": "codex"},
        )

        self.assertIsNone(result)
        self.assertEqual(self.supervisor.get(task.interaction_id).state, TaskState.POSTED)
        self.assertNotIn("old-codex-response", self.supervisor.get(task.interaction_id).source_message_ids)

    def test_explicit_interaction_id_beats_same_agent_recency_guessing(self):
        first = make_task(self.supervisor, "int_explicit_first", "room-explicit", "codex")
        make_task(self.supervisor, "int_explicit_second", "room-explicit", "codex")

        result = self.supervisor.ingest_event(
            "room-explicit",
            "result-explicit-first",
            10,
            {
                "kind": "agent_response",
                "agent": "codex",
                "interaction_id": first.interaction_id,
            },
        )

        self.assertEqual(result.interaction_id, first.interaction_id)
        self.assertEqual(self.supervisor.get(first.interaction_id).state, TaskState.COMPLETED)
        self.assertEqual(self.supervisor.get("int_explicit_second").state, TaskState.POSTED)

    def test_source_messages_are_idempotent_and_state_survives_reload(self):
        task = make_task(self.supervisor, "int_reload", "room-b", "grok")
        self.supervisor.ingest_event("room-b", "route-grok", 10, {"kind": "routing", "agent": "grok"})
        self.supervisor.ingest_event("room-b", "route-grok", 10, {"kind": "routing", "agent": "grok"})

        reloaded = TaskSupervisor(self.state_path)
        record = reloaded.get(task.interaction_id)
        self.assertEqual(record.state, TaskState.ROUTED)
        self.assertEqual(record.source_message_ids.count("route-grok"), 1)
        self.assertTrue(reloaded.confirmation_matches(record.confirmation_snapshot))

    def test_confirmation_is_bound_to_the_interaction(self):
        task = make_task(self.supervisor, "int_bound", "room-c", "codex")
        if hasattr(task.confirmation_snapshot, "model_copy"):
            confirmation = task.confirmation_snapshot.model_copy(update={"exact_message": "forged"})
        else:
            confirmation = task.confirmation_snapshot.copy(update={"exact_message": "forged"})
        self.assertFalse(self.supervisor.confirmation_matches(confirmation))

    def test_cached_confirmation_never_rewinds_terminal_task(self):
        task = make_task(self.supervisor, "int_cached", "room-d", "codex")
        self.supervisor.ingest_event("room-d", "result-codex", 10, {"kind": "agent_response", "agent": "codex"})
        self.supervisor.mark_posted(task.interaction_id, "rc_int_cached", cached=True)
        self.assertEqual(self.supervisor.get(task.interaction_id).state, TaskState.COMPLETED)

    def test_newer_same_agent_routing_supersedes_prior_active_task(self):
        first = make_task(self.supervisor, "int_first", "room-e", "codex")
        self.supervisor.ingest_event("room-e", "route-first", 10, {"kind": "routing", "agent": "codex"})
        second = make_task(self.supervisor, "int_second", "room-e", "codex")
        self.supervisor.ingest_event("room-e", "route-second", 20, {"kind": "routing", "agent": "codex"})

        self.assertEqual(self.supervisor.get(first.interaction_id).state, TaskState.SUPERSEDED)
        self.assertEqual(self.supervisor.get(second.interaction_id).state, TaskState.ROUTED)


if __name__ == "__main__":
    unittest.main()
