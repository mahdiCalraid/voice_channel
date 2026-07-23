import unittest
import asyncio
import sys
import os
import json
from unittest.mock import patch, MagicMock

# Add parent directory to path so we can import app.main
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.main import (
    classify_message, 
    parse_routing, 
    parse_heartbeat, 
    parse_stopped, 
    parse_model_selected, 
    parse_attachment,
    parse_datetime,
    process_history_messages,
    generate_digest,
    DigestRequest
)

class TestMessageClassification(unittest.TestCase):
    
    def test_user_messages(self):
        msg = {"u": {"username": "ed"}, "msg": "ok please move with step 1"}
        res = classify_message(msg)
        self.assertEqual(res["lane"], "user")
        self.assertEqual(res["event"]["kind"], "user_message")

    def test_routing_system_messages(self):
        msg = {
            "u": {"username": "acli_bot"}, 
            "msg": "🔄 Routing to **agy** [model: `Gemini 3.5 Flash (High)`, effort: `low`]: alright, we need to move toward..."
        }
        res = classify_message(msg)
        self.assertEqual(res["lane"], "system")
        self.assertEqual(res["event"]["kind"], "routing")
        self.assertEqual(res["event"]["agent"], "agy")
        self.assertEqual(res["event"]["model"]["provider"], "google")
        self.assertEqual(res["event"]["model"]["name"], "Gemini 3.5 Flash (High)")
        self.assertEqual(res["event"]["model"]["effort"], "low")

    def test_heartbeat_system_messages(self):
        msg1 = {
            "u": {"username": "acli_bot"}, 
            "msg": "⏱️ **@agy** is still working (Elapsed: 21s)\n> *Last transcript update:* `unknown`"
        }
        res1 = classify_message(msg1)
        self.assertEqual(res1["lane"], "system")
        self.assertEqual(res1["event"]["kind"], "heartbeat")
        self.assertEqual(res1["event"]["agent"], "agy")
        self.assertEqual(res1["event"]["elapsed_seconds"], 21)

        msg2 = {
            "u": {"username": "acli_bot"}, 
            "msg": "⏱️ **@codex** is still working (Elapsed: 2m 57s)"
        }
        res2 = classify_message(msg2)
        self.assertEqual(res2["lane"], "system")
        self.assertEqual(res2["event"]["kind"], "heartbeat")
        self.assertEqual(res2["event"]["agent"], "codex")
        self.assertEqual(res2["event"]["elapsed_seconds"], 177)

    def test_agent_relay_with_keywords(self):
        # Test 1: Relayed agent message containing the word "stopped"
        msg1 = {
            "u": {"username": "acli_bot"}, 
            "msg": "**@grok**: I'll diagnose the infinite load... UI loads but Rocket.Chat stays on \"Connecting...\" and the transcript never finishes."
        }
        res1 = classify_message(msg1)
        self.assertEqual(res1["lane"], "agent")
        self.assertEqual(res1["event"]["kind"], "agent_response")
        self.assertEqual(res1["event"]["agent"], "grok")

        # Test 2: Relayed agent message containing the word "error"
        msg2 = {
            "u": {"username": "acli_bot"}, 
            "msg": "**@agy**: I have implemented and verified Step 1. No errors were detected."
        }
        res2 = classify_message(msg2)
        self.assertEqual(res2["lane"], "agent")
        self.assertEqual(res2["event"]["kind"], "agent_response")
        self.assertEqual(res2["event"]["agent"], "agy")

    def test_model_selected_notices(self):
        # Pattern 1: Current model notice
        msg1 = {
            "u": {"username": "acli_bot"}, 
            "msg": "ℹ️ **grok** current model is `grok-4.5` with effort `low`..."
        }
        res1 = classify_message(msg1)
        self.assertEqual(res1["lane"], "system")
        self.assertEqual(res1["event"]["kind"], "model_selected")
        self.assertEqual(res1["event"]["agent"], "grok")
        self.assertEqual(res1["event"]["model"]["provider"], "xai")
        self.assertEqual(res1["event"]["model"]["name"], "grok-4.5")
        self.assertEqual(res1["event"]["model"]["effort"], "low")

        # Pattern 2: Model changed notice
        msg2 = {
            "u": {"username": "acli_bot"}, 
            "msg": "✅ Set **grok** current model to `grok-4.5` with effort `high`."
        }
        res2 = classify_message(msg2)
        self.assertEqual(res2["lane"], "system")
        self.assertEqual(res2["event"]["kind"], "model_selected")
        self.assertEqual(res2["event"]["agent"], "grok")
        self.assertEqual(res2["event"]["model"]["provider"], "xai")
        self.assertEqual(res2["event"]["model"]["name"], "grok-4.5")
        self.assertEqual(res2["event"]["model"]["effort"], "high")

    def test_attachment_notices(self):
        # Pattern 1: Block attachment
        msg1 = {
            "u": {"username": "acli_bot"}, 
            "msg": "--- ATTACHED FILES ---\n  • /Users/ed/King/clawd_2/voice_channel/test.png"
        }
        res1 = classify_message(msg1)
        self.assertEqual(res1["lane"], "system")
        self.assertEqual(res1["event"]["kind"], "attachment")
        self.assertEqual(res1["event"]["count"], 1)

        # Pattern 2: Download notice attachment
        msg2 = {
            "u": {"username": "acli_bot"}, 
            "msg": "📎 Downloaded 2 attachment(s) to inbox: /Users/ed/..."
        }
        res2 = classify_message(msg2)
        self.assertEqual(res2["lane"], "system")
        self.assertEqual(res2["event"]["kind"], "attachment")
        self.assertEqual(res2["event"]["count"], 2)

    def test_native_membership_messages(self):
        msg = {
            "u": {"username": "some_user"}, 
            "msg": "User joined the channel", 
            "t": "uj"
        }
        res = classify_message(msg)
        self.assertEqual(res["lane"], "system")
        self.assertEqual(res["event"]["kind"], "membership")

    def test_fallback_acli_bot_messages(self):
        msg = {
            "u": {"username": "acli_bot"}, 
            "msg": "Some unparsed status or informational notice"
        }
        res = classify_message(msg)
        self.assertEqual(res["lane"], "system")
        self.assertEqual(res["event"]["kind"], "other")

    def test_failed_notice_classified_as_error(self):
        msg = {
            "u": {"username": "acli_bot"},
            "msg": "❌ **@agy** failed: > `run interrupted by ACLI restart`"
        }
        res = classify_message(msg)
        self.assertEqual(res["lane"], "system")
        self.assertEqual(res["event"]["kind"], "error")
        self.assertEqual(res["event"]["agent"], "agy")

    def test_invalid_model_notice(self):
        msg = {
            "u": {"username": "acli_bot"},
            "msg": "⚠️ Model 'high' is not valid for **grok**. Available: grok-4.5"
        }
        res = classify_message(msg)
        self.assertEqual(res["lane"], "system")
        self.assertEqual(res["event"]["kind"], "error")
        self.assertEqual(res["event"]["agent"], "grok")

    def test_history_processing_and_stats(self):
        # We can simulate the list of raw messages processed in the history endpoint
        raw_msgs = [
            {
                "_id": "1",
                "u": {"username": "acli_bot"},
                "msg": "🔄 Routing to **grok** [model: `grok-4.5`, effort: `low`]",
                "ts": "2026-07-16T12:00:00.000Z"
            },
            {
                "_id": "2",
                "u": {"username": "acli_bot"},
                "msg": "⏱️ **@grok** is still working (Elapsed: 15s)",
                "ts": "2026-07-16T12:00:15.000Z"
            },
            {
                "_id": "3",
                "u": {"username": "grok"},
                "msg": "**@grok**: Here is my answer.",
                "ts": "2026-07-16T12:00:30.000Z"
            }
        ]
        
        # Now run the production history logic
        cleaned_messages, rolling_stats = process_history_messages(raw_msgs)
            
        # Verify back-patching
        self.assertEqual(cleaned_messages[0]["event"]["status"], "completed")
        self.assertEqual(cleaned_messages[0]["event"]["response_time_seconds"], 30.0)
        self.assertEqual(cleaned_messages[2]["event"]["response_time_seconds"], 30.0)
        
        # Verify stats
        self.assertEqual(rolling_stats["grok"]["runs"], 1)
        self.assertEqual(rolling_stats["grok"]["avg_response_time"], 30.0)
        self.assertEqual(rolling_stats["grok"]["msg_count"], 1)
        self.assertEqual(rolling_stats["grok"]["status"], "idle")

    def test_superseded_routing(self):
        # Simulate two routings for same agent without a response in between
        raw_msgs = [
            {
                "_id": "1",
                "u": {"username": "acli_bot"},
                "msg": "🔄 Routing to **grok** [model: `grok-4.5`, effort: `low`]",
                "ts": "2026-07-16T12:00:00.000Z"
            },
            {
                "_id": "2",
                "u": {"username": "acli_bot"},
                "msg": "🔄 Routing to **grok** [model: `grok-4.5`, effort: `high`]",
                "ts": "2026-07-16T12:01:00.000Z"
            },
            {
                "_id": "3",
                "u": {"username": "grok"},
                "msg": "**@grok**: Here is my answer.",
                "ts": "2026-07-16T12:01:30.000Z"
            }
        ]
        
        cleaned_messages, rolling_stats = process_history_messages(raw_msgs)
        
        # First routing should be marked superseded
        self.assertEqual(cleaned_messages[0]["event"]["status"], "superseded")
        self.assertIsNone(cleaned_messages[0]["event"].get("response_time_seconds"))
        
        # Second routing should be marked completed
        self.assertEqual(cleaned_messages[1]["event"]["status"], "completed")
        self.assertEqual(cleaned_messages[1]["event"]["response_time_seconds"], 30.0)

    @patch("subprocess.run")
    def test_digest_filtering_and_saving(self, mock_run):
        import asyncio
        
        # Mock subprocess to write result.json in the job directory
        def mock_subprocess_run(cmd, **kwargs):
            job_path = cmd[-1]
            job_dir = os.path.dirname(job_path)
            res_data = {
                "ok": True,
                "task": "digest",
                "worker": "codex",
                "output": "Mocked Codex summary showing grok worked on 1 updates. The last update was from ed.",
                "included_message_ids": ["msg-agent-reply-1", "msg-user-1"],
                "error": None
            }
            with open(os.path.join(job_dir, "result.json"), "w", encoding="utf-8") as f:
                json.dump(res_data, f)
            
            m_res = MagicMock()
            m_res.returncode = 0
            m_res.stdout = ""
            m_res.stderr = ""
            return m_res
            
        mock_run.side_effect = mock_subprocess_run
        
        # Simulate messages with mixture of system, agent, user
        messages = [
            {
                "id": "msg-routing-1",
                "username": "acli_bot",
                "text": "🔄 Routing to **grok**...",
                "lane": "system",
                "event": {"kind": "routing", "agent": "grok"}
            },
            {
                "id": "msg-heartbeat-1",
                "username": "acli_bot",
                "text": "⏱️ **@grok** working...",
                "lane": "system",
                "event": {"kind": "heartbeat", "agent": "grok"}
            },
            {
                "id": "msg-agent-reply-1",
                "username": "grok",
                "text": "**@grok**: Task completed.",
                "lane": "agent",
                "event": {"kind": "agent_response", "agent": "grok"}
            },
            {
                "id": "msg-user-1",
                "username": "ed",
                "text": "Great job, what is next?",
                "lane": "user",
                "event": {"kind": "user_message"}
            }
        ]
        
        req = DigestRequest(messages=messages, roomId="test-room-123")
        
        # Run generate_digest asynchronously
        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(generate_digest(req))
        
        # Validate that system messages were excluded
        self.assertIn("grok worked on 1 updates", res["digest"])
        self.assertIn("The last update was from ed", res["digest"])
        
        # Validate that included_message_ids only contains msg-agent-reply-1 and msg-user-1
        self.assertEqual(res["included_message_ids"], ["msg-agent-reply-1", "msg-user-1"])

    def test_atomic_summary_save(self):
        from app.main import save_summary, read_prior_summaries
        test_room = "test-atomic-room"
        history_path = f"acli/summary_history/{test_room}.json"
        tmp_path = f"acli/summary_history/{test_room}.json.tmp"
        
        try:
            if os.path.exists(history_path):
                os.remove(history_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
            save_summary(test_room, "First digest")
            self.assertTrue(os.path.exists(history_path))
            self.assertFalse(os.path.exists(tmp_path))
            
            prior = read_prior_summaries(test_room)
            self.assertEqual(len(prior), 1)
            self.assertEqual(prior[0]["digest"], "First digest")
        finally:
            if os.path.exists(history_path):
                os.remove(history_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @patch("httpx.AsyncClient.get")
    def test_status_endpoint_component_health(self, mock_get):
        from app.main import get_status
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "username": "acli_bot"}
        mock_get.return_value = mock_resp
        
        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(get_status())
        
        self.assertEqual(res["status"], "online")
        self.assertIn("app", res)
        self.assertEqual(res["app"]["status"], "healthy")
        self.assertIn("worker", res)
        self.assertIn(res["worker"]["status"], ("ready", "degraded"))
        self.assertIn("configured", res["worker"])
        self.assertIn("rocket_chat", res)
        self.assertEqual(res["rocket_chat"]["status"], "connected")

    def test_degraded_ai_fallback(self):
        from app.main import generate_digest, DigestRequest, openai_client
        messages = [
            {"id": "m1", "lane": "agent", "text": "Report completed.", "event": {"agent": "codex"}},
            {"id": "m2", "lane": "user", "text": "Acknowledged.", "username": "ed"}
        ]
        req = DigestRequest(messages=messages, roomId="test-degraded-room")
        
        with patch("app.main.openai_client", None):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = Exception("Codex CLI unavailable")
                loop = asyncio.get_event_loop()
                res = loop.run_until_complete(generate_digest(req))
                
                self.assertIn("digest", res)
                self.assertTrue(len(res["digest"]) > 0)
                self.assertEqual(res["included_message_ids"], ["m1", "m2"])

    @patch("httpx.AsyncClient.get")
    def test_history_pagination_and_retries(self, mock_get):
        from app.main import get_history
        
        # Simulate initial failure then success
        fail_resp = MagicMock()
        fail_resp.status_code = 500
        fail_resp.text = "Transient error"
        
        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.json.return_value = {
            "success": True,
            "messages": [
                {"_id": "m10", "msg": "Older message", "u": {"username": "ed"}, "ts": "2026-07-20T20:00:00.000Z"},
                {"_id": "m11", "msg": "Newer message", "u": {"username": "codex"}, "ts": "2026-07-20T20:01:00.000Z"}
            ]
        }
        
        mock_get.side_effect = [fail_resp, success_resp]
        
        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(get_history(roomId="test-room-paged", count=10, offset=5))
        
        self.assertTrue(res["success"])
        self.assertEqual(res["room_id"], "test-room-paged")
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["offset"], 5)
        self.assertEqual(len(res["messages"]), 2)

    @patch("httpx.AsyncClient.get")
    def test_history_before_cursor_is_inclusive_and_returns_next_cursor(self, mock_get):
        from app.main import get_history

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "success": True,
            "messages": [
                {"_id": "m3", "msg": "Boundary", "u": {"username": "ed"}, "ts": "2026-07-20T20:03:00.000Z"},
                {"_id": "m2", "msg": "Older", "u": {"username": "ed"}, "ts": "2026-07-20T20:02:00.000Z"},
                {"_id": "m2", "msg": "Older duplicate", "u": {"username": "ed"}, "ts": "2026-07-20T20:02:00.000Z"},
            ],
        }
        mock_get.return_value = response

        loop = asyncio.get_event_loop()
        res = loop.run_until_complete(
            get_history(
                roomId="test-room-cursor",
                count=10,
                before="2026-07-20T20:03:00.000Z",
            )
        )

        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["latest"], "2026-07-20T20:03:00.000Z")
        self.assertEqual(params["inclusive"], "true")
        self.assertEqual([message["id"] for message in res["messages"]], ["m2", "m3"])
        self.assertEqual(res["next_before"], "2026-07-20T20:02:00.000Z")

if __name__ == '__main__':
    unittest.main()
