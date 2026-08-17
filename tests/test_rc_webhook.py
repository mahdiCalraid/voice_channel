"""Tests for Rocket.Chat outgoing-webhook wake-up authentication and handling."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.rc_webhook import (
    WebhookAuthError,
    WebhookReplayError,
    accept_message_id,
    authenticate_webhook_request,
    load_webhook_state,
    nonce_store_count,
    normalize_outgoing_webhook_payload,
    update_webhook_state,
    verify_shared_secret,
    webhook_event_is_stale,
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

    def test_replay_store_enforces_ttl_and_count_cap_without_json_rewrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonces.sqlite3"
            for index in range(6):
                accept_message_id(
                    f"msg-{index}",
                    path=path,
                    now=1_000.0 + index,
                    ttl_seconds=60,
                    max_entries=3,
                )
            self.assertEqual(nonce_store_count(path), 3)
            self.assertEqual(path.read_bytes()[:16], b"SQLite format 3\x00")

            accept_message_id(
                "msg-after-ttl",
                path=path,
                now=1_100.0,
                ttl_seconds=60,
                max_entries=3,
            )
            self.assertEqual(nonce_store_count(path), 1)

    def test_stale_event_detection_uses_rocket_chat_timestamp(self):
        normalized = {"timestamp": "2026-08-16T20:00:00Z"}
        now = datetime.fromisoformat("2026-08-16T20:20:00+00:00").timestamp()
        self.assertTrue(webhook_event_is_stale(normalized, now=now))
        self.assertFalse(webhook_event_is_stale(normalized, now=now - 600))

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
        self.state_tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.state_tmp.name) / "webhook_status.json"
        self.state_patch = patch("app.rc_webhook._state_path", return_value=self.state_path)
        self.state_patch.start()
        self.original_debounce = main_module.WEBHOOK_DEBOUNCE_SECONDS
        main_module.WEBHOOK_DEBOUNCE_SECONDS = 0
        self.client = TestClient(main_module.app)
        main_module.ROOM_MESSAGES_CACHE.clear()
        main_module.ROOM_WEBHOOK_WAKE_LOCKS.clear()
        main_module.WEBHOOK_PENDING_BATCHES.clear()
        main_module.response_assistant_results.clear()
        main_module.response_assistant_inflight.clear()
        main_module.narration_prewarm_generation_starts.clear()

    def tearDown(self):
        main_module.WEBHOOK_DEBOUNCE_SECONDS = self.original_debounce
        main_module.WEBHOOK_PENDING_BATCHES.clear()
        self.state_patch.stop()
        self.state_tmp.cleanup()

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
        state = load_webhook_state()
        self.assertEqual(state["metrics"]["auth_failures"], 1)
        self.assertIn("Invalid Rocket.Chat webhook secret", state["last_error"])

    def test_webhook_rejects_stale_authenticated_event_without_room_refresh(self):
        stale_timestamp = datetime.fromtimestamp(
            time.time() - 1_000,
            tz=timezone.utc,
        ).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            nonce_path = Path(tmp) / "webhook_nonces.sqlite3"
            with patch("app.main.webhook_configured", return_value=True), patch(
                "app.rc_webhook.webhook_secret", return_value=SECRET
            ), patch(
                "app.rc_webhook._nonce_path", return_value=nonce_path
            ), patch.object(
                main_module, "_refresh_active_task_room", new=AsyncMock()
            ) as mock_refresh:
                response = self.client.post(
                    "/api/rc/webhook/message",
                    json={
                        "token": SECRET,
                        "channel_id": "room-stale",
                        "message_id": "msg-stale",
                        "timestamp": stale_timestamp,
                    },
                )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["stale"])
        mock_refresh.assert_not_awaited()
        self.assertEqual(load_webhook_state()["metrics"]["stale_rejections"], 1)

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
        state = load_webhook_state()
        self.assertEqual(state["metrics"]["real_agent_replies"], 1)
        self.assertGreaterEqual(state["metrics"]["prewarm_scheduled"], 1)
        self.assertEqual(state["last_event"]["message_id"], "msg-agent-1")
        self.assertEqual(state["last_event"]["room_id"], "room-vc")
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

    def test_same_room_burst_is_coalesced_into_one_refresh(self):
        async def exercise():
            main_module.WEBHOOK_DEBOUNCE_SECONDS = 0.01
            first = {
                "room_id": "room-coalesce",
                "message_id": "msg-1",
                "channel_name": "voice_channel",
            }
            second = {
                "room_id": "room-coalesce",
                "message_id": "msg-2",
                "channel_name": "voice_channel",
            }
            with patch.object(
                main_module,
                "process_rocket_chat_message_wake",
                new=AsyncMock(return_value={
                    "success": True,
                    "stale_rejections": 0,
                    "stale_message_ids": [],
                }),
            ) as mock_process:
                await asyncio.gather(
                    main_module._enqueue_webhook_wake(first),
                    main_module._enqueue_webhook_wake(second),
                )
            self.assertEqual(mock_process.await_count, 1)
            self.assertEqual(
                mock_process.await_args.kwargs["hint_message_ids"],
                ["msg-1", "msg-2"],
            )

        asyncio.run(exercise())
        self.assertEqual(load_webhook_state()["metrics"]["coalesced_count"], 1)

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

    def test_status_reports_webhook_liveness_watermarks(self):
        with patch("app.main.webhook_configured", return_value=True):
            # Test restart recovery (not received yet)
            response = self.client.get("/api/status")
            self.assertEqual(response.json()["rc_webhook"]["state"], "degraded_polling")
            self.assertEqual(response.json()["rc_webhook"]["covered_rooms"], {})

            # Test healthy delivery
            now_minus_10 = time.time() - 10
            update_webhook_state(
                received_at=now_minus_10,
                verified_at=now_minus_10,
                covered_room_id="covered-room-1",
            )
            response = self.client.get("/api/status")
            self.assertEqual(response.json()["rc_webhook"]["state"], "healthy")
            self.assertEqual(response.json()["rc_webhook"]["last_verified_at"], now_minus_10)
            self.assertEqual(response.json()["rc_webhook"]["covered_rooms"]["covered-room-1"], now_minus_10)
            self.assertNotIn("uncovered-room-2", response.json()["rc_webhook"]["covered_rooms"])

            # Test outage detection
            stale_time = time.time() - 150
            update_webhook_state(
                received_at=stale_time,
                verified_at=stale_time,
                covered_room_id="covered-room-1",
            )
            response = self.client.get("/api/status")
            self.assertEqual(response.json()["rc_webhook"]["state"], "degraded_polling")

    def test_verified_delivery_clears_resolved_error_but_keeps_failure_count(self):
        update_webhook_state(
            increments={"processing_failures": 1},
            error="temporary Rocket.Chat failure",
            now=1_000.0,
        )
        recovered = update_webhook_state(
            verified_at=1_010.0,
            covered_room_id="room-recovered",
            increments={"verified_count": 1},
            now=1_010.0,
        )
        self.assertIsNone(recovered["last_error"])
        self.assertIsNone(recovered["last_error_at"])
        self.assertEqual(recovered["metrics"]["processing_failures"], 1)

    def test_webhook_receipt_updates_liveness_watermark(self):
        async def fake_refresh(client, room_id):
            return []

        with tempfile.TemporaryDirectory() as tmp:
            nonce_path = Path(tmp) / "webhook_nonces.json"
            payload = {
                "token": SECRET,
                "channel_id": "room-r",
                "channel_name": "voice_channel",
                "message_id": "msg-live-1",
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
                # First delivery
                self.client.post("/api/rc/webhook/message", json=payload)
                first_state = load_webhook_state()
                self.assertIsNotNone(first_state["last_received_at"])
                self.assertIsNotNone(first_state["last_verified_at"])
                self.assertIn("room-r", first_state["covered_rooms"])
                first_ts = first_state["last_received_at"]
                first_covered_ts = first_state["covered_rooms"]["room-r"]

                time.sleep(0.01)

                # Replay delivery should still update the watermark
                self.client.post("/api/rc/webhook/message", json=payload)
                second_state = load_webhook_state()
                self.assertGreater(second_state["last_received_at"], first_ts)
                self.assertEqual(second_state["covered_rooms"]["room-r"], first_covered_ts)
                self.assertEqual(second_state["metrics"]["replayed_count"], 1)


    def test_webhook_prewarm_enforces_narration_eligibility(self):
        hinted_reply = {
            "id": "msg-agent-hint",
            "lane": "agent",
            "name": "codex",
            "text": "Finished the requested work.",
            "timestamp": time.time(),
            "event": {"kind": "agent_response", "agent": "codex"},
        }

        async def fake_refresh(client, room_id):
            messages = [hinted_reply]
            main_module.ROOM_MESSAGES_CACHE[room_id] = messages
            return messages

        from app.attention_config import ChannelAttentionConfig, ChannelAttentionEntry

        def fake_load_config():
            return ChannelAttentionConfig(
                channels={
                    "selected_channel": ChannelAttentionEntry(
                        narration_active=True,
                        attention_active=True,
                    ),
                    "unselected_channel": ChannelAttentionEntry(
                        narration_active=False,
                        attention_active=True,
                    )
                }
            )

        with patch.object(
            main_module, "_refresh_active_task_room", new=AsyncMock(side_effect=fake_refresh)
        ), patch.object(
            main_module, "_generate_response_assistant_once", new=AsyncMock(return_value={})
        ), patch.object(
            main_module.sse_broadcaster, "publish", new=AsyncMock()
        ), patch("app.main.load_channel_attention_config", side_effect=fake_load_config):

            # Selected channel should schedule prep
            result_selected = asyncio.run(main_module.process_rocket_chat_message_wake(
                "room-1",
                channel_name="selected_channel",
                hint_message_id="msg-agent-hint",
                source="rc_webhook",
            ))
            self.assertEqual(result_selected["prewarm_scheduled"], 1)

            # Unselected channel should NOT schedule prep
            result_unselected = asyncio.run(main_module.process_rocket_chat_message_wake(
                "room-2",
                channel_name="unselected_channel",
                hint_message_id="msg-agent-hint",
                source="rc_webhook",
            ))
            self.assertEqual(result_unselected["prewarm_scheduled"], 0)


    def test_webhook_retry_after_refresh_failure_succeeds_then_rejects_duplicate(self):
        call_count = 0
        async def fake_refresh(client, room_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Temporary RC outage")
            return []

        with tempfile.TemporaryDirectory() as tmp:
            nonce_path = Path(tmp) / "webhook_nonces.json"
            payload = {
                "token": SECRET,
                "channel_id": "room-retry",
                "message_id": "msg-retry-1",
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
                # 1. First delivery: refresh fails
                with self.assertRaises(RuntimeError):
                    self.client.post("/api/rc/webhook/message", json=payload)
                self.assertNotIn("room-retry", load_webhook_state()["covered_rooms"])
                self.assertEqual(call_count, 1)

                # 2. Rocket.Chat retry: same message, refresh succeeds
                response2 = self.client.post("/api/rc/webhook/message", json=payload)
                self.assertEqual(response2.status_code, 200)
                self.assertIn("room-retry", load_webhook_state()["covered_rooms"])
                self.assertEqual(call_count, 2)

                # 3. Later duplicate: safely rejected without refreshing
                response3 = self.client.post("/api/rc/webhook/message", json=payload)
                self.assertEqual(response3.status_code, 200)
                self.assertTrue(response3.json().get("replayed"))
                self.assertEqual(call_count, 2)


if __name__ == "__main__":
    unittest.main()
