"""Unit tests for U-10b attention scoring, queue sorting, and API endpoint."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.attention_config import ChannelAttentionConfig, ChannelAttentionEntry, UrgencyLevel
from app.attention_scoring import build_attention_queue, calculate_channel_attention_score
from app.contracts import AttentionState, TaskState
from app.main import app
import app.main as main_module
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
            base_importance=5,  # 55 pts
            urgency=UrgencyLevel.HIGH,  # 20 pts
            blocking=True,  # 15 pts
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
        self.assertEqual(factors["base_importance_points"], 55.0)
        self.assertEqual(factors["urgency_points"], 20.0)
        self.assertEqual(factors["blocking_points"], 15.0)
        self.assertEqual(factors["boost_points"], 50.0)
        self.assertEqual(factors["waiting_age_hours"], 1.0)
        self.assertEqual(factors["waiting_age_points"], 2.0)
        self.assertEqual(score, 142.0)

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

    def test_unread_real_reply_scoring_and_eligibility(self):
        now = 1000000.0
        entry = ChannelAttentionEntry(base_importance=3, urgency=UrgencyLevel.NORMAL)  # 60 + 10 = 70 pts

        # Unread channel with idle attention state -> still rankable!
        unread_info = {
            "has_unread": True,
            "unread_count": 1,
            "last_agent_reply_ts": now - 900.0,  # 15 min ago -> freshness = 15 / (1 + 1) = 7.5
            "last_real_message_at": now - 900.0,
        }
        attention_summary = {"is_busy": False, "attention_state": AttentionState.NONE}

        cat, score, factors = calculate_channel_attention_score(
            entry, {"status": "active"}, attention_summary, now=now, unread_info=unread_info
        )

        self.assertEqual(cat, "ranked")
        self.assertTrue(factors["has_unread"])
        self.assertEqual(factors["unread_base_points"], 20.0)
        self.assertEqual(factors["unread_freshness_points"], 7.5)
        self.assertEqual(factors["unread_points"], 27.5)
        self.assertAlmostEqual(factors["real_activity_points"], 1428.57, places=2)
        self.assertAlmostEqual(score, 1484.07, places=2)

    def test_ranking_priority_hierarchy_ordering(self):
        now = 1000000.0

        # Overdue item
        entry_overdue = ChannelAttentionEntry(base_importance=3, deadline="1970-01-01T00:00:00Z")
        _, score_overdue, _ = calculate_channel_attention_score(
            entry_overdue, {"status": "active"}, {"attention_state": AttentionState.NEEDS_REVIEW}, now=now
        )

        # High importance (5) + High urgency
        entry_high_imp = ChannelAttentionEntry(base_importance=5, urgency=UrgencyLevel.HIGH)
        _, score_high_imp, _ = calculate_channel_attention_score(
            entry_high_imp, {"status": "active"}, {"attention_state": AttentionState.NEEDS_REVIEW}, now=now
        )

        # Fresh unread / real conversation on default importance 3 / normal urgency
        entry_unread = ChannelAttentionEntry(base_importance=3, urgency=UrgencyLevel.NORMAL)
        unread_info = {"has_unread": True, "unread_count": 1, "last_agent_reply_ts": now, "last_real_message_at": now}
        _, score_fresh_unread, _ = calculate_channel_attention_score(
            entry_unread, {"status": "active"}, {"attention_state": AttentionState.NONE}, now=now, unread_info=unread_info
        )

        # Idle normal item
        entry_idle = ChannelAttentionEntry(base_importance=3, urgency=UrgencyLevel.NORMAL)
        _, score_idle_ranked, _ = calculate_channel_attention_score(
            entry_idle, {"status": "active"}, {"attention_state": AttentionState.NEEDS_REVIEW}, now=now
        )

        # Fresh real conversation is deliberately dominant.  Truly exceptional
        # work can use priority_override, which is enforced by queue ordering.
        self.assertGreater(score_fresh_unread, score_high_imp)
        self.assertGreater(score_overdue, score_idle_ranked)
        self.assertGreater(score_fresh_unread, score_idle_ranked)

        viewed_info = {"has_unread": False, "has_unseen_real_activity": False, "last_real_message_at": now}
        _, score_viewed, viewed_factors = calculate_channel_attention_score(
            entry_high_imp, {"status": "active"}, {"attention_state": AttentionState.NEEDS_REVIEW}, now=now, unread_info=viewed_info
        )
        unseen_info = {"has_unread": False, "has_unseen_real_activity": True, "last_real_message_at": now}
        _, score_unseen, unseen_factors = calculate_channel_attention_score(
            entry_idle, {"status": "active"}, {"attention_state": AttentionState.NONE}, now=now, unread_info=unseen_info
        )
        self.assertGreater(score_unseen, score_viewed)
        self.assertGreater(unseen_factors["real_activity_points"], 2000)
        self.assertGreater(viewed_factors["real_activity_points"], 50)
        self.assertLess(viewed_factors["real_activity_points"], 100)

        # 4. Unwatched / unknown room -> category 'unknown', score None
        cat, score, factors = calculate_channel_attention_score(
            entry_unread, {"status": "active"}, {"is_busy": False, "attention_state": AttentionState.UNKNOWN}, now=now
        )
        self.assertEqual(cat, "unknown")
        self.assertIsNone(score)

        # 5. Idle channel (no actionable task) -> category 'idle', score None
        cat, score, factors = calculate_channel_attention_score(
            entry_unread, {"status": "active"}, {"is_busy": False, "attention_state": AttentionState.NONE}, now=now
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
        # The newest real conversation wins, even if that room is currently
        # Busy.  Inactive channels remain suppressed below active work.
        self.assertEqual(queue[0]["channel_name"], "JobHunting")
        self.assertEqual(queue[0]["queue_category"], "busy")
        self.assertIsNone(queue[0]["rank"])
        self.assertIsNone(queue[0]["score"])

        item1 = queue[1]
        self.assertEqual(item1["channel_name"], "voice_channel")
        self.assertEqual(item1["queue_category"], "ranked")
        self.assertEqual(item1["rank"], 1)
        self.assertIsNotNone(item1["score"])
        self.assertTrue(item1["configured"])

        self.assertEqual(queue[2]["channel_name"], "unconfigured_room")
        self.assertFalse(queue[2]["configured"])
        self.assertEqual(queue[3]["channel_name"], "disabled_channel")

    def test_queue_uses_recent_activity_to_break_equal_scores(self):
        now = 1000000.0
        config = ChannelAttentionConfig(channels={
            "older": ChannelAttentionEntry(base_importance=3, urgency=UrgencyLevel.NORMAL),
            "newer": ChannelAttentionEntry(base_importance=3, urgency=UrgencyLevel.NORMAL),
        })
        registry = [
            {"channel_name": "older", "active": True},
            {"channel_name": "newer", "active": True},
        ]
        summaries = {
            name: {
                "room_id": name,
                "is_busy": False,
                "attention_state": AttentionState.NEEDS_REVIEW,
                "ready_since": now - 60.0,
            }
            for name in ("older", "newer")
        }

        queue = build_attention_queue(
            config,
            registry,
            summaries,
            {"older": now - 500.0, "newer": now - 10.0},
            now=now,
        )

        self.assertEqual([item["channel_name"] for item in queue], ["newer", "older"])
        self.assertGreater(queue[0]["score"], queue[1]["score"])
        self.assertGreater(queue[0]["factors"]["real_activity_points"], queue[1]["factors"]["real_activity_points"])

    def test_recent_real_conversation_beats_ordinary_importance(self):
        now = 1_000_000.0
        config = ChannelAttentionConfig(channels={
            "important_but_older": ChannelAttentionEntry(base_importance=5, urgency=UrgencyLevel.HIGH),
            "recent_normal": ChannelAttentionEntry(base_importance=3, urgency=UrgencyLevel.NORMAL),
        })
        registry = [{"channel_name": name, "active": True} for name in config.channels]
        summaries = {
            name: {"room_id": name, "is_busy": False, "attention_state": AttentionState.NEEDS_REVIEW}
            for name in config.channels
        }
        viewed = build_attention_queue(
            config, registry, summaries,
            {"important_but_older": now - 7200, "recent_normal": now - 30}, now=now,
        )
        self.assertEqual(
            [item["channel_name"] for item in viewed],
            ["important_but_older", "recent_normal"],
        )

        unread_map = {
            "recent_normal": {
                "has_unread": False,
                "has_unseen_real_activity": True,
                "last_real_message_at": now - 30,
            },
            "important_but_older": {
                "has_unread": False,
                "has_unseen_real_activity": False,
                "last_real_message_at": now - 7200,
            },
        }
        unseen = build_attention_queue(
            config, registry, summaries,
            {"important_but_older": now - 7200, "recent_normal": now - 30},
            now=now,
            room_unread_map=unread_map,
        )
        self.assertEqual(
            [item["channel_name"] for item in unseen],
            ["recent_normal", "important_but_older"],
        )

    def test_old_waiting_age_cannot_bury_recent_more_important_channel(self):
        """Regression for the live Meeting_Confrences/discoveryTool mismatch."""
        now = 1_000_000.0
        config = ChannelAttentionConfig(channels={
            "Meeting_Confrences": ChannelAttentionEntry(
                base_importance=3,
                urgency=UrgencyLevel.NORMAL,
            ),
            "discoveryTool": ChannelAttentionEntry(
                base_importance=4,
                urgency=UrgencyLevel.HIGH,
            ),
        })
        registry = [{"channel_name": name, "active": True} for name in config.channels]
        summaries = {
            "Meeting_Confrences": {
                "room_id": "meeting",
                "is_busy": False,
                "attention_state": AttentionState.NEEDS_HELP,
                "ready_since": now - (356 * 3600),
            },
            "discoveryTool": {
                "room_id": "discovery",
                "is_busy": False,
                "attention_state": AttentionState.NEEDS_REVIEW,
                "ready_since": now - (256 * 3600),
            },
        }

        queue = build_attention_queue(
            config,
            registry,
            summaries,
            {
                "Meeting_Confrences": now - (11 * 24 * 3600),
                "discoveryTool": now - (4 * 3600),
            },
            now=now,
        )

        self.assertEqual(queue[0]["channel_name"], "discoveryTool")
        self.assertEqual(queue[0]["factors"]["waiting_age_points"], 24.0)
        meeting = next(item for item in queue if item["channel_name"] == "Meeting_Confrences")
        self.assertEqual(meeting["factors"]["waiting_age_points"], 24.0)
        self.assertTrue(meeting["factors"]["waiting_age_capped"])

    def test_priority_override_beats_real_conversation_recency(self):
        now = 1_000_000.0
        config = ChannelAttentionConfig(channels={
            "critical": ChannelAttentionEntry(base_importance=5, priority_override=True),
            "recent_normal": ChannelAttentionEntry(base_importance=3),
        })
        registry = [{"channel_name": name, "active": True} for name in config.channels]
        summaries = {
            name: {"room_id": name, "is_busy": False, "attention_state": AttentionState.NEEDS_REVIEW}
            for name in config.channels
        }
        queue = build_attention_queue(
            config, registry, summaries,
            {"critical": now - 7200, "recent_normal": now - 30}, now=now,
        )
        self.assertEqual([item["channel_name"] for item in queue], ["critical", "recent_normal"])

    def test_config_only_live_room_keeps_new_state_in_attention_queue(self):
        now = time.time()
        room_id = "room-config-only-new"
        marker = "2026-08-17T15:44:25.869Z"
        config = ChannelAttentionConfig(channels={
            "Client_zen": ChannelAttentionEntry(base_importance=3),
        })
        message = {
            "_id": "config-only-reply",
            "timestamp": now - 5,
            "lane": "agent",
            "name": "codex",
            "text": "Completed the requested update.",
        }

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "success": True,
                    "update": [{
                        "_id": room_id,
                        "name": "Client_zen",
                        "t": "c",
                        "lm": marker,
                        "_updatedAt": marker,
                    }],
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, *args, **kwargs):
                return FakeResponse()

        previous_messages = main_module.ROOM_MESSAGES_CACHE.get(room_id)
        previous_activity = main_module.ROOM_REAL_ACTIVITY_CACHE.get(room_id)
        main_module.ROOM_MESSAGES_CACHE[room_id] = [message]
        main_module.ROOM_REAL_ACTIVITY_CACHE[room_id] = {
            "marker": marker,
            "last_real_message_at": now - 5,
        }
        try:
            with patch.object(main_module, "load_channel_attention_config", return_value=config), \
                 patch.object(main_module, "load_channel_registry", return_value=([], "test")), \
                 patch.object(main_module.httpx, "AsyncClient", FakeClient):
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                result = loop.run_until_complete(main_module.get_attention_queue(now=now))
        finally:
            if previous_messages is None:
                main_module.ROOM_MESSAGES_CACHE.pop(room_id, None)
            else:
                main_module.ROOM_MESSAGES_CACHE[room_id] = previous_messages
            if previous_activity is None:
                main_module.ROOM_REAL_ACTIVITY_CACHE.pop(room_id, None)
            else:
                main_module.ROOM_REAL_ACTIVITY_CACHE[room_id] = previous_activity

        item = next(entry for entry in result["queue"] if entry["channel_name"] == "Client_zen")
        self.assertEqual(item["room_id"], room_id)
        self.assertEqual(item["status"], "active")
        self.assertTrue(item["has_unseen_real_activity"])
        self.assertEqual(item["queue_category"], "ranked")

    def test_get_attention_queue_endpoint(self):
        client = TestClient(app)
        resp = client.get("/api/attention/queue?now=1000000.0")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("timestamp"), 1000000.0)
        self.assertIn("queue", data)
        self.assertIsInstance(data["queue"], list)

    def test_background_room_refresh_clears_stale_busy_task(self):
        req, conf, evt = self._make_req_tuple("int-background", "room-background", "codex", 100)
        task = self.supervisor.create_interaction(req, conf, evt)
        self.supervisor.mark_posted(task.interaction_id, "dispatch-background")
        self.assertTrue(self.supervisor.get_channel_attention_summary("room-background")["is_busy"])

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "success": True,
                    "messages": [{
                        "_id": "background-response",
                        "ts": "1970-01-01T00:01:50.000Z",
                        "u": {"username": "acli_bot", "name": "ACLI Bot"},
                        "msg": "**@codex**: Work completed while another room was focused.",
                    }],
                }

        class FakeClient:
            async def get(self, *args, **kwargs):
                return FakeResponse()

        with patch.object(main_module, "task_supervisor", self.supervisor):
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(
                main_module._refresh_active_task_room(FakeClient(), "room-background")
            )

        summary = self.supervisor.get_channel_attention_summary("room-background")
        self.assertFalse(summary["is_busy"])
        self.assertEqual(summary["attention_state"], AttentionState.NEEDS_REVIEW)

    def test_background_room_refresh_turns_timeout_into_needs_help(self):
        req, conf, evt = self._make_req_tuple("int-background-timeout", "room-timeout", "grok", 100)
        task = self.supervisor.create_interaction(req, conf, evt)
        self.supervisor.mark_posted(task.interaction_id, "dispatch-timeout")
        self.supervisor.ingest_event("room-timeout", "route-timeout", 110, {"kind": "routing", "agent": "grok"})

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "success": True,
                    "messages": [{
                        "_id": "timeout-response",
                        "ts": "1970-01-01T00:03:20.000Z",
                        "u": {"username": "acli_bot", "name": "ACLI Bot"},
                        "msg": "**@grok** hit the 1800s timeout. The run was stopped and a timeout review packet was captured.",
                    }],
                }

        class FakeClient:
            async def get(self, *args, **kwargs):
                return FakeResponse()

        with patch.object(main_module, "task_supervisor", self.supervisor):
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            loop.run_until_complete(
                main_module._refresh_active_task_room(FakeClient(), "room-timeout")
            )

        summary = self.supervisor.get_channel_attention_summary("room-timeout")
        self.assertFalse(summary["is_busy"])
        self.assertEqual(summary["attention_state"], AttentionState.NEEDS_HELP)

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


    def test_channel_attention_entry_per_channel_automation_fields(self):
        # Default configuration: visible=True, narration_active=False, voice_active=False
        entry_default = ChannelAttentionEntry()
        self.assertTrue(entry_default.visible)
        self.assertFalse(entry_default.narration_active)
        self.assertFalse(entry_default.voice_active)

        # voice_active=True automatically normalizes narration_active=True
        entry_voice = ChannelAttentionEntry(voice_active=True)
        self.assertTrue(entry_voice.voice_active)
        self.assertTrue(entry_voice.narration_active)

        # narration_active=True alone leaves voice_active=False
        entry_narr = ChannelAttentionEntry(narration_active=True)
        self.assertTrue(entry_narr.narration_active)
        self.assertFalse(entry_narr.voice_active)


if __name__ == "__main__":
    unittest.main()
