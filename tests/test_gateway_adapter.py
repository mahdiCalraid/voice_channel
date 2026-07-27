"""Integration tests for gateway interact/confirm endpoints and CLI client adapter."""

import time
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.contracts import (
    CURRENT_SCHEMA_VERSION,
    InputMode,
    PermissionTier,
    TaskState,
    InteractionRequest,
    ConfirmationSnapshot,
    GatewayResult,
)
from cli.gateway_client import run_gateway_client


class TestGatewayAdapterEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_gateway_interact_success(self):
        payload = {
            "schema_version": "1.0",
            "raw_input": "Send status update to team",
            "input_mode": "text",
            "requested_room_id": "test_room_123",
            "requested_agent": "codex"
        }
        resp = self.client.post("/api/gateway/interact", json=payload)
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        self.assertEqual(data["schema_version"], "1.0")
        self.assertEqual(data["status"], "awaiting_confirmation")
        self.assertEqual(data["selected_room_id"], "test_room_123")
        self.assertEqual(data["selected_agent"], "codex")

        metadata = data.get("provider_metadata", {})
        self.assertIn("interpretation", metadata)
        self.assertIn("confirmation_snapshot", metadata)

        conf = metadata["confirmation_snapshot"]
        self.assertEqual(conf["room_id"], "test_room_123")
        self.assertEqual(conf["agent"], "codex")
        self.assertEqual(conf["exact_message"], "Send status update to team")
        self.assertEqual(conf["permission_tier"], "commit")
        self.assertTrue(conf["nonce"].startswith("nonce_"))

    def test_gateway_interact_invalid_schema_version(self):
        payload = {
            "schema_version": "2.0",
            "raw_input": "Test prompt"
        }
        resp = self.client.post("/api/gateway/interact", json=payload)
        self.assertEqual(resp.status_code, 422)

    def test_gateway_confirm_expired_snapshot_rejection(self):
        past_expiry = time.time() - 10.0
        conf_payload = {
            "schema_version": "1.0",
            "immutable_interaction_id": "int_expired_001",
            "room_id": "test_room_123",
            "agent": "codex",
            "exact_message": "Expired test message",
            "permission_tier": "commit",
            "expires_at": past_expiry,
            "nonce": "nonce_expired_123"
        }
        resp = self.client.post("/api/gateway/confirm", json=conf_payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Confirmation snapshot has expired", resp.json()["detail"])

    @patch("app.main.httpx.AsyncClient")
    def test_gateway_confirm_and_post_mocked_success(self, mock_async_client_cls):
        # Mock httpx AsyncClient response for chat.postMessage
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "message": {"_id": "rc_mock_msg_999", "msg": "Mocked test message"}
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_async_client_cls.return_value = mock_client

        future_expiry = time.time() + 300.0
        conf_payload = {
            "schema_version": "1.0",
            "immutable_interaction_id": "int_mock_001",
            "room_id": "test_room_123",
            "agent": "codex",
            "exact_message": "Mocked test message",
            "permission_tier": "commit",
            "expires_at": future_expiry,
            "nonce": "nonce_mock_999"
        }
        resp = self.client.post("/api/gateway/confirm", json=conf_payload)
        self.assertEqual(resp.status_code, 200)

        data = resp.json()
        self.assertEqual(data["status"], "posted")
        self.assertEqual(data["rocket_chat_msg_ids"], ["rc_mock_msg_999"])
        self.assertIn("rc_mock_msg_999", data["full_response"])


class TestCLIGatewayAdapter(unittest.TestCase):
    @patch("httpx.Client")
    def test_cli_adapter_roundtrip(self, mock_client_cls):
        mock_interact_resp = MagicMock()
        mock_interact_resp.status_code = 200
        mock_interact_resp.json.return_value = {
            "schema_version": "1.0",
            "status": "awaiting_confirmation",
            "provider_metadata": {
                "confirmation_snapshot": {
                    "schema_version": "1.0",
                    "immutable_interaction_id": "int_cli_001",
                    "room_id": "cli_room",
                    "agent": "codex",
                    "exact_message": "CLI Test message",
                    "permission_tier": "commit",
                    "expires_at": time.time() + 300.0,
                    "nonce": "nonce_cli_001"
                }
            }
        }

        mock_confirm_resp = MagicMock()
        mock_confirm_resp.status_code = 200
        mock_confirm_resp.json.return_value = {
            "schema_version": "1.0",
            "status": "posted",
            "rocket_chat_msg_ids": ["cli_rc_msg_555"]
        }

        mock_httpx_client = MagicMock()
        mock_httpx_client.post.side_effect = [mock_interact_resp, mock_confirm_resp]
        mock_httpx_client.__enter__.return_value = mock_httpx_client
        mock_httpx_client.__exit__.return_value = None
        mock_client_cls.return_value = mock_httpx_client

        msg_id = run_gateway_client(
            gateway_url="http://localhost:6891",
            raw_input="CLI Test message",
            requested_room_id="cli_room",
            requested_agent="codex",
            auto_confirm=True
        )
        self.assertEqual(msg_id, "cli_rc_msg_555")


if __name__ == "__main__":
    unittest.main()
