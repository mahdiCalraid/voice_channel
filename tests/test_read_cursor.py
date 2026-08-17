import unittest
import time
from app.read_cursor import (
    get_last_real_conversation_timestamp,
    is_real_conversation_message,
    is_real_agent_reply,
    update_read_cursor,
    get_read_cursor,
    evaluate_room_unread_status,
)


class TestReadCursor(unittest.TestCase):

    def test_real_conversation_recency_includes_people_and_excludes_system_messages(self):
        messages = [
            {"lane": "user", "text": "Please review the latest draft.", "timestamp": 100.0, "event": {"kind": "user_message"}},
            {"lane": "system", "text": "Routing to codex", "timestamp": 200.0, "event": {"kind": "routing"}},
            {"lane": "system", "text": "Heartbeat", "timestamp": 300.0, "event": {"kind": "heartbeat"}},
            {"lane": "agent", "text": "Review is complete.", "timestamp": 400.0, "event": {"kind": "agent_response"}},
        ]
        self.assertTrue(is_real_conversation_message(messages[0]))
        self.assertFalse(is_real_conversation_message(messages[1]))
        self.assertFalse(is_real_conversation_message(messages[2]))
        self.assertEqual(get_last_real_conversation_timestamp(messages), 400.0)

    def test_is_real_agent_reply(self):
        # Real agent replies
        self.assertTrue(is_real_agent_reply({
            "lane": "agent",
            "name": "codex",
            "text": "Plan complete and ready for review.",
            "event": {"kind": "agent_response", "agent": "codex"}
        }))

        self.assertTrue(is_real_agent_reply({
            "lane": "user",
            "name": "claude",
            "text": "Approved. Safe to proceed.",
            "event": {"kind": "agent_response", "agent": "claude"}
        }))

        # System routing / heartbeat / model switch notices (NOT real replies)
        self.assertFalse(is_real_agent_reply({
            "lane": "system",
            "name": "system",
            "text": "Task routed to worker codex",
            "event": {"kind": "routing_notice"}
        }))

        self.assertFalse(is_real_agent_reply({
            "lane": "agent",
            "name": "voice_gateway",
            "text": "!model codex gpt-5.6-terra",
            "event": {"kind": "model_change"}
        }))

        self.assertFalse(is_real_agent_reply({
            "lane": "system",
            "name": "system",
            "text": "Heartbeat ok",
            "event": {"kind": "heartbeat"}
        }))

        self.assertFalse(is_real_agent_reply({
            "lane": "agent",
            "name": "codex",
            "text": "",
            "event": {"kind": "agent_response"}
        }))

    def test_update_and_get_read_cursor(self):
        room_id = "test_room_cursor_001"
        ts = time.time()
        cursor = update_read_cursor(room_id, msg_id="msg_999", ts=ts, actor="ed")
        self.assertEqual(cursor["room_id"], room_id)
        self.assertEqual(cursor["last_read_msg_id"], "msg_999")
        self.assertEqual(cursor["last_read_ts"], ts)
        self.assertEqual(cursor["updated_by_actor"], "ed")

        fetched = get_read_cursor(room_id)
        self.assertEqual(fetched["last_read_msg_id"], "msg_999")

    def test_evaluate_room_unread_status(self):
        room_id = f"test_room_unread_eval_{time.time()}"
        now_ts = time.time()
        old_ts = now_ts - 300.0
        newer_ts = now_ts - 50.0

        messages = [
            {
                "id": "msg_1",
                "lane": "agent",
                "name": "codex",
                "text": "First answer",
                "timestamp": old_ts,
            },
            {
                "id": "msg_2",
                "lane": "agent",
                "name": "grok",
                "text": "Second answer",
                "timestamp": newer_ts,
            }
        ]

        # Update cursor to msg_1
        update_read_cursor(room_id, msg_id="msg_1", ts=old_ts, actor="ed")
        eval_result = evaluate_room_unread_status(room_id, messages)

        self.assertTrue(eval_result["has_unread"])
        self.assertEqual(eval_result["unread_count"], 1)
        self.assertEqual(eval_result["last_agent_reply_id"], "msg_2")

        # Now read msg_2
        update_read_cursor(room_id, msg_id="msg_2", ts=newer_ts, actor="ed")
        eval_result_2 = evaluate_room_unread_status(room_id, messages)
        self.assertFalse(eval_result_2["has_unread"])
        self.assertEqual(eval_result_2["unread_count"], 0)

    def test_extract_msg_timestamp_iso_strings(self):
        from app.read_cursor import _extract_msg_timestamp

        # Standard ISO strings from Rocket.Chat history
        ts1 = _extract_msg_timestamp({"ts": "2026-07-20T20:01:00.000Z"})
        self.assertGreater(ts1, 1700000000.0)

        ts2 = _extract_msg_timestamp({"timestamp": "2026-08-06T09:50:00Z"})
        self.assertGreater(ts2, 1700000000.0)
        self.assertGreater(ts2, ts1)

        # Milliseconds numeric timestamp
        ts3 = _extract_msg_timestamp({"timestamp": 1754400000000})
        self.assertEqual(ts3, 1754400000.0)

    def test_evaluate_unread_status_with_production_iso_timestamps(self):
        room_id = f"test_room_iso_prod_{time.time()}"
        read_ts = 1784540000.0  # Ed read at 1784540000 (e.g. 2026-07-20T19:46:40Z)
        update_read_cursor(room_id, msg_id="msg_old", ts=read_ts, actor="ed")

        # Production Rocket.Chat messages with ISO timestamp strings
        messages = [
            {
                "_id": "msg_old",
                "lane": "agent",
                "name": "codex",
                "text": "Earlier reply",
                "ts": "2026-07-20T19:40:00.000Z",  # 1784539200
            },
            {
                "_id": "msg_new",
                "lane": "agent",
                "name": "grok",
                "text": "New reply after Ed left room",
                "ts": "2026-07-20T20:01:00.000Z",  # 1784540460 (> read_ts)
            }
        ]

        eval_res = evaluate_room_unread_status(room_id, messages)
        self.assertTrue(eval_res["has_unread"])
        self.assertEqual(eval_res["unread_count"], 1)
        self.assertEqual(eval_res["last_agent_reply_id"], "msg_new")

    def test_read_cursor_privacy_storage_contract(self):
        room_id = f"test_privacy_{time.time()}"
        cursor = update_read_cursor(room_id, msg_id="msg_secret_123", ts=time.time(), actor="ed")

        # Verify allowed keys only — NO message text or excerpts allowed
        allowed_keys = {"room_id", "last_read_msg_id", "last_read_ts", "updated_by_actor", "updated_at"}
        self.assertTrue(set(cursor.keys()).issubset(allowed_keys))
        self.assertNotIn("text", cursor)
        self.assertNotIn("content", cursor)
        self.assertNotIn("excerpt", cursor)


if __name__ == "__main__":
    unittest.main()
