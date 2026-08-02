"""Unit tests for U-10b attention scoring, queue sorting, and API endpoint."""

import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from app.attention_config import ChannelAttentionConfig, ChannelAttentionEntry, UrgencyLevel
from app.attention_scoring import build_attention_queue, calculate_channel_attention_score
from app.contracts import AttentionState, TaskState
from app.main import app
from app.task_supervisor import TaskSupervisor


class TestAttentionScoring(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = str(Path(self.temp_dir.name) / "tasks.json")
        self.supervisor = TaskSupervisor(self.state_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_actionable_task_aggregation_remediation(self):
        """Claude's U-10a remediation: among non-busy tasks, prefer oldest actionable task over newer canceled task."""
        # Create older actionable task (needs_review at t=100)
        req1, conf1, evt1 = self._make_req_tuple("int_act_1", "room-remedy", "codex", 100)
        t1 = self.supervisor.create_interaction(req1, conf1, evt1)
        self.supervisor.mark_posted(t1.interaction_id, "msg-1")
        self.supervisor.ingest_event("room-remedy", "res-1", 110, {"kind": "agent_response", "agent": "codex"})

        # Create newer canceled task (state=CANCELLED, attention=NONE at t=190)
        req2, conf2, evt2 = self._make_req_tuple("int_act_2", "room-remedy", "grok", 180)
        t2 = self.supervisor.create_interaction(req2, conf2, evt2)
        self.supervisor.mark_posted(t2.interaction_id, "msg-2")
        self.supervisor.ingest_event("room-remedy", "stop-2", 190, {"kind": "stopped", "agent": "grok"})

        summary = self.supervisor.get_channel_attention_summary("room-remedy")
        self.assertFalse(summary["is_busy"])
        self.assertEqual(summary["attention_state"], AttentionState.NEEDS_REVIEW)
        self.assertEqual(summary["ready_since"], 110)

    def test_calculate_score_factor_breakdown(self):
        now = 1000000.0
        entry = ChannelAttentionEntry(
            attention_active=True,
            base_importance=5,  # 100 pts
            urgency=UrgencyLevel.HIGH,  # 25 pts
            blocking=True,  # 30 pts
            temporary_boost_until="2026-08-05T12:00:00Z",  # +50 pts (boosted)
            snoozed_until=None,
        )
        status_info = {"status": "active"}
        attention_summary = {
            "is_busy": False,
            "attention_state": AttentionState.NEEDS_REVIEW,
            "ready_since": now - 3600.0,  # 1 hour waiting -> +2 pts
        }

        category, score, factors = calculate_channel_attention_score(
            entry, status_info, attention_summary, now=now
        )

        self.assertEqual(category, "ranked")
        self.assertIsNotNone(score)
        self.assertEqual(factors["base_importance_points"], 100.0)
        self.assertEqual(factors["urgency_points"], 25.0)
        self.assertEqual(factors["blocking_points"], 30.0)
        self.assertEqual(factors["boost_points"], 50.0)
        self.assertEqual(factors["waiting_age_hours"], 1.0)
        self.assertEqual(factors["waiting_age_points"], 2.0)
        self.assertEqual(score, 207.0)

    def test_eligibility_gates(self):
        now = 1000000.0
        entry = ChannelAttentionEntry(base_importance=3, urgency=UrgencyLevel.NORMAL)

        # 1. Busy channel -> category 'busy', score None
        cat, score, factors = calculate_channel_attention_score(
            entry, {"status": "active"}, {"is_busy": True, "attention_state": AttentionState.NONE}, now=now
        )
        self.assertEqual(cat, "busy")
        self.assertIsNone(score)

        # 2. Snoozed channel -> category 'snoozed', score None
        snoozed_entry = ChannelAttentionEntry(base_importance=4, snoozed_until="2030-01-01T00:00:00Z")
        cat, score, factors = calculate_channel_attention_score(
            snoozed_entry, {"status": "active"}, {"is_busy": False, "attention_state": AttentionState.NEEDS_REVIEW}, now=now
        )
        self.assertEqual(cat, "snoozed")
        self.assertIsNone(score)

        # 3. Unconfigured channel (entry=None) -> category 'unconfigured', score None
        cat, score, factors = calculate_channel_attention_score(
            None, {"status": "unconfigured"}, {"is_busy": False, "attention_state": AttentionState.NEEDS_REVIEW}, now=now
        )
        self.assertEqual(cat, "unconfigured")
        self.assertIsNone(score)

        # 4. Unwatched / unknown room -> category 'unknown', score None
        cat, score, factors = calculate_channel_attention_score(
            entry, {"status": "active"}, {"is_busy": False, "attention_state": AttentionState.UNKNOWN}, now=now
        )
        self.assertEqual(cat, "unknown")
        self.assertIsNone(score)

        # 5. Idle channel (no actionable task) -> category 'idle', score None
        cat, score, factors = calculate_channel_attention_score(
            entry, {"status": "active"}, {"is_busy": False, "attention_state": AttentionState.NONE}, now=now
        )
        self.assertEqual(cat, "idle")
        self.assertIsNone(score)

    def test_build_attention_queue_sorting_and_ranking(self):
        now = 1000000.0
        config = ChannelAttentionConfig(
            channels={
                "voice_channel": ChannelAttentionEntry(base_importance=5, urgency=UrgencyLevel.HIGH),
                "JobHunting": ChannelAttentionEntry(base_importance=3, urgency=UrgencyLevel.NORMAL),
                "disabled_channel": ChannelAttentionEntry(base_importance=4, attention_active=False),
            }
        )
        registry = [
            {"channel_name": "voice_channel", "active": True},
            {"channel_name": "JobHunting", "active": True},
            {"channel_name": "disabled_channel", "active": True},
            {"channel_name": "unconfigured_room", "active": True},
        ]
        room_summaries = {
            "voice_channel": {"room_id": "r1", "is_busy": False, "attention_state": AttentionState.NEEDS_REVIEW, "ready_since": now - 1800.0},
            "JobHunting": {"room_id": "r2", "is_busy": True, "working_since": now - 300.0, "attention_state": AttentionState.NONE},
            "disabled_channel": {"room_id": "r3", "is_busy": False, "attention_state": AttentionState.NEEDS_REVIEW, "ready_since": now - 100.0},
            "unconfigured_room": {"room_id": "r4", "is_busy": False, "attention_state": AttentionState.UNKNOWN, "ready_since": None},
        }
        activity_map = {
            "voice_channel": now - 1800.0,
            "JobHunting": now - 300.0,
            "disabled_channel": now - 100.0,
            "unconfigured_room": None,
        }

        queue = build_attention_queue(config, registry, room_summaries, activity_map, now=now)

        self.assertTrue(len(queue) >= 4)
        item0 = queue[0]
        self.assertEqual(item0["channel_name"], "voice_channel")
        self.assertEqual(item0["queue_category"], "ranked")
        self.assertEqual(item0["rank"], 1)
        self.assertIsNotNone(item0["score"])

        item1 = queue[1]
        self.assertEqual(item1["channel_name"], "JobHunting")
        self.assertEqual(item1["queue_category"], "busy")
        self.assertIsNone(item1["rank"])
        self.assertIsNone(item1["score"])

    def test_get_attention_queue_endpoint(self):
        client = TestClient(app)
        resp = client.get("/api/attention/queue?now=1000000.0")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("timestamp"), 1000000.0)
        self.assertIn("queue", data)
        self.assertIsInstance(data["queue"], list)

    def _make_req_tuple(self, interaction_id, room_id, agent, created_at):
        from app.contracts import ConfirmationSnapshot, InteractionRequest, TaskEvent, TaskState
        req = InteractionRequest(
            interaction_id=interaction_id,
            actor_id="ed",
            client_id="test",
            raw_input=f"@{agent} test task",
            created_at=created_at,
            requested_room_id=room_id,
            requested_agent=agent,
        )
        conf = ConfirmationSnapshot(
            immutable_interaction_id=interaction_id,
            room_id=room_id,
            agent=agent,
            exact_message=f"@{agent} test task",
            expires_at=created_at + 300,
            nonce=f"nonce_{interaction_id}",
        )
        evt = TaskEvent(
            interaction_id=interaction_id,
            timestamp=created_at,
            state=TaskState.AWAITING_CONFIRMATION,
            details={},
        )
        return req, conf, evt


if __name__ == "__main__":
    unittest.main()
