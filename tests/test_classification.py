import unittest
import sys
import os

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

    def test_digest_filtering_and_saving(self):
        import asyncio
        
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

if __name__ == '__main__':
    unittest.main()
