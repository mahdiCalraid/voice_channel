"""Tests for Rocket.Chat outgoing-webhook wake-up authentication and handling."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.rc_webhook import (
    WebhookAuthError,
    WebhookReplayError,
    accept_message_id,
    authenticate_webhook_request,
    normalize_outgoing_webhook_payload,
    verify_shared_secret,
    webhook_configured,
)


SECRET = "x" * 32


class TestRcWebhookAuth(unittest.TestCase):
    def test_shared_secret_requires_length_and_matches(self):
        with self.assertRaises(WebhookAuthError):
            verify_shared_secret("short", expected="also-short")
        with self.assertRaises(WebhookAuthError):
            verify_shared_secret("wrong-secret-value-not-matching-xxx", expected=SECRET)
        verify_shared_secret(SECRET, expected=SECRET)

    def test_normalize_flat_and_nested_payloads(self):
        flat = normalize_outgoing_webhook_payload({
            "token": SECRET,
            "channel_id": "room-1",
            "channel_name": "voice_channel",
            "message_id": "msg-1",
            "user_name": "codex",
            "text": "Implemented the step.",
            "bot": False,
        })
        self.assertEqual(flat["room_id"], "room-1")
        self.assertEqual(flat["message_id"], "msg-1")
        self.assertEqual(flat["channel_name"], "voice_channel")

        nested = normalize_outgoing_webhook_payload({
            "token": SECRET,
            "message": {
                "_id": "msg-2",
                "rid": "room-2",
                "msg": "hello",
                "u": {"username": "ed"},
            },
        })
        self.assertEqual(nested["room_id"], "room-2")
        self.assertEqual(nested["message_id"], "msg-2")
        self.assertEqual(nested["user_name"], "ed")

    def test_replay_protection_rejects_duplicate_message_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonces.json"
            accept_message_id("msg-dup", room_id="room-a", path=path, now=1000.0)
            with self.assertRaises(WebhookReplayError):
                accept_message_id("msg-dup", room_id="room-a", path=path, now=1001.0)

    def test_authenticate_accepts_header_or_body_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonces.json"
            payload = {
                "channel_id": "room-h",
                "message_id": "msg-header",
                "channel_name": "voice_channel",
                "text": "hi",
            }
            with patch("app.rc_webhook.webhook_secret", return_value=SECRET):
                out = authenticate_webhook_request(
                    {"x-voice-gateway-webhook-secret": SECRET},
                    payload,
                    path=path,
                )
            self.assertEqual(out["message_id"], "msg-header")

            payload2 = {
                "token": SECRET,
                "channel_id": "room-b",
                "message_id": "msg-body",
                "channel_name": "voice_channel",
            }
            with patch("app.rc_webhook.webhook_secret", return_value=SECRET):
                out2 = authenticate_webhook_request({}, payload2, path=path)
            self.assertEqual(out2["message_id"], "msg-body")


class TestRcWebhookEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)
        main_module.ROOM_MESSAGES_CACHE.clear()
        main_module.ROOM_WEBHOOK_WAKE_LOCKS.clear()
        main_module.response_assistant_results.clear()
        main_module.response_assistant_inflight.clear()
        main_module.narration_prewarm_generation_starts.clear()

    def test_webhook_requires_configuration(self):
        with patch.dict(os.environ, {"GATEWAY_RC_WEBHOOK_SECRET": ""}, clear=False):
            # Force re-read via unconfigured secret
            with patch("app.main.webhook_configured", return_value=False):
                response = self.client.post(
                    "/api/rc/webhook/message",
                    json={"channel_id": "r1", "message_id": "m1", "token": SECRET},
                )
        self.assertEqual(response.status_code, 503)

    def test_webhook_rejects_bad_secret(self):
        with patch("app.main.webhook_configured", return_value=True), patch(
            "app.rc_webhook.webhook_secret", return_value=SECRET
        ):
            response = self.client.post(
                "/api/rc/webhook/message",
                json={
                    "token": "y" * 32,
                    "channel_id": "room-1",
                    "message_id": "msg-bad",
                    "channel_name": "voice_channel",
                    "text": "nope",
                },
            )
        self.assertEqual(response.status_code, 401)

    def test_webhook_wake_fetches_room_and_prewarms_real_agent_reply(self):
        async def fake_refresh(client, room_id):
            return [{
                "id": "msg-agent-1",
                "lane": "agent",
                "name": "codex",
                "text": "Implemented the bounded step.",
                "timestamp": time.time(),
                "event": {"kind": "agent_response", "agent": "codex"},
            }]

        async def fake_generate(req):
            return {
                "digest": "Codex finished the bounded step.",
                "suggested_message": "@grok Review the fix.",
                "trigger_message_id": req.trigger_message_id,
                "included_message_ids": [req.trigger_message_id],
            }

        with tempfile.TemporaryDirectory() as tmp:
            nonce_path = Path(tmp) / "webhook_nonces.json"
            with patch("app.main.webhook_configured", return_value=True), patch(
                "app.rc_webhook.webhook_secret", return_value=SECRET
            ), patch(
                "app.rc_webhook._nonce_path", return_value=nonce_path
            ), patch.object(
                main_module, "_refresh_active_task_room", new=AsyncMock(side_effect=fake_refresh)
            ), patch.object(
                main_module, "_generate_response_assistant_once", new=fake_generate
            ), patch.object(
                main_module, "load_channel_attention_config"
            ) as mock_cfg, patch.object(
                main_module.sse_broadcaster, "publish", new=AsyncMock()
            ) as mock_publish:
                from app.attention_config import ChannelAttentionConfig, ChannelAttentionEntry

                mock_cfg.return_value = ChannelAttentionConfig(
                    channels={
                        "voice_channel": ChannelAttentionEntry(
                            narration_active=True,
                            attention_active=True,
                        )
                    }
                )
                response = self.client.post(
                    "/api/rc/webhook/message",
                    json={
                        "token": SECRET,
                        "channel_id": "room-vc",
                        "channel_name": "voice_channel",
                        "message_id": "msg-agent-1",
                        "user_name": "codex",
                        "text": "Implemented the bounded step.",
                    },
                )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["replayed"])
        self.assertEqual(body["new_real_agent_replies"], 1)
        self.assertGreaterEqual(body["prewarm_scheduled"], 1)
        # SSE signals must not carry raw text
        for call in mock_publish.await_args_list:
            event = call.args[0]
            self.assertNotIn("text", event)
            self.assertNotIn("msg", event)

    def test_webhook_ignores_user_and_operational_messages_for_prewarm(self):
        async def fake_refresh_user(client, room_id):
            return []  # no new real agent replies after classify/filter

        with tempfile.TemporaryDirectory() as tmp:
            nonce_path = Path(tmp) / "webhook_nonces.json"
            with patch("app.main.webhook_configured", return_value=True), patch(
                "app.rc_webhook.webhook_secret", return_value=SECRET
            ), patch(
                "app.rc_webhook._nonce_path", return_value=nonce_path
            ), patch.object(
                main_module, "_refresh_active_task_room", new=AsyncMock(side_effect=fake_refresh_user)
            ), patch.object(
                main_module, "_schedule_background_narration_prewarm", new=AsyncMock(return_value=0)
            ) as mock_schedule, patch.object(
                main_module.sse_broadcaster, "publish", new=AsyncMock()
            ):
                # First baseline with user message only in cache after refresh
                main_module.ROOM_MESSAGES_CACHE["room-u"] = [{
                    "id": "msg-user-1",
                    "lane": "user",
                    "name": "ed",
                    "text": "please continue",
                    "timestamp": 1_786_000_000.0,
                    "event": {"kind": "user_message"},
                }]
                response = self.client.post(
                    "/api/rc/webhook/message",
                    json={
                        "token": SECRET,
                        "channel_id": "room-u",
                        "channel_name": "voice_channel",
                        "message_id": "msg-user-1",
                        "user_name": "ed",
                        "text": "please continue",
                    },
                )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["new_real_agent_replies"], 0)
        self.assertEqual(body["prewarm_scheduled"], 0)
        mock_schedule.assert_not_awaited()

    def test_webhook_prewarm_is_scoped_to_hinted_reply_not_wide_history_diff(self):
        hinted_reply = {
            "id": "msg-agent-hint",
            "lane": "agent",
            "name": "codex",
            "text": "Finished the requested work.",
            "timestamp": time.time(),
            "event": {"kind": "agent_response", "agent": "codex"},
        }
        older_replies = [
            {
                "id": f"msg-old-{index}",
                "lane": "agent",
                "name": "codex",
                "text": f"Older reply {index}",
                "timestamp": time.time() - 600 + index,
                "event": {"kind": "agent_response", "agent": "codex"},
            }
            for index in range(15)
        ]

        async def fake_refresh(client, room_id):
            messages = older_replies + [hinted_reply]
            main_module.ROOM_MESSAGES_CACHE[room_id] = messages
            return messages

        with patch.object(
            main_module, "_refresh_active_task_room", new=AsyncMock(side_effect=fake_refresh)
        ), patch.object(
            main_module, "_schedule_background_narration_prewarm", new=AsyncMock(return_value=1)
        ) as mock_schedule, patch.object(
            main_module.sse_broadcaster, "publish", new=AsyncMock()
        ):
            result = asyncio.run(main_module.process_rocket_chat_message_wake(
                "room-burst",
                channel_name="voice_channel",
                hint_message_id="msg-agent-hint",
                source="rc_webhook",
            ))

        self.assertEqual(result["new_real_agent_replies"], 1)
        self.assertEqual(result["prewarm_scheduled"], 1)
        scheduled = mock_schedule.await_args.kwargs["new_replies_by_room"]["room-burst"]
        self.assertEqual([message["id"] for message in scheduled], ["msg-agent-hint"])

    def test_webhook_replay_is_idempotent_success(self):
        async def fake_refresh(client, room_id):
            return []

        with tempfile.TemporaryDirectory() as tmp:
            nonce_path = Path(tmp) / "webhook_nonces.json"
            payload = {
                "token": SECRET,
                "channel_id": "room-r",
                "channel_name": "voice_channel",
                "message_id": "msg-replay-1",
                "user_name": "ed",
                "text": "hello",
            }
            with patch("app.main.webhook_configured", return_value=True), patch(
                "app.rc_webhook.webhook_secret", return_value=SECRET
            ), patch(
                "app.rc_webhook._nonce_path", return_value=nonce_path
            ), patch.object(
                main_module, "_refresh_active_task_room", new=AsyncMock(side_effect=fake_refresh)
            ), patch.object(
                main_module.sse_broadcaster, "publish", new=AsyncMock()
            ):
                first = self.client.post("/api/rc/webhook/message", json=payload)
                second = self.client.post("/api/rc/webhook/message", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json().get("replayed"))
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json().get("replayed"))

    def test_status_reports_webhook_configuration(self):
        with patch("app.main.webhook_configured", return_value=True):
            response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["rc_webhook"]["status"], "configured")
        self.assertEqual(data["rc_webhook"]["path"], "/api/rc/webhook/message")


if __name__ == "__main__":
    unittest.main()
