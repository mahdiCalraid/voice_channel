"""Unit tests for GET/PUT /api/attention/config and queue endpoint integration (U-10c)."""

import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from app.attention_config import ChannelAttentionConfig, ChannelAttentionEntry, UrgencyLevel
from app.main import app


class TestAttentionEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = str(Path(self.temp_dir.name) / "channel_attention_config.json")
        os.environ["CHANNEL_ATTENTION_CONFIG_PATH"] = self.config_path
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("CHANNEL_ATTENTION_CONFIG_PATH", None)
        self.temp_dir.cleanup()

    def test_get_attention_config_default(self):
        resp = self.client.get("/api/attention/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("config", data)
        self.assertEqual(data["config"]["schema_version"], "1.0")

    def test_put_attention_config_single_channel_valid(self):
        payload = {
            "channel_name": "voice_channel",
            "entry": {
                "attention_active": True,
                "base_importance": 4,
                "urgency": "high",
                "blocking": True,
            }
        }
        resp = self.client.put("/api/attention/config", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("voice_channel", data["config"]["channels"])
        v_entry = data["config"]["channels"]["voice_channel"]
        self.assertEqual(v_entry["base_importance"], 4)
        self.assertEqual(v_entry["urgency"], "high")
        self.assertTrue(v_entry["blocking"])

    def test_put_attention_config_rejects_invalid_values(self):
        # Invalid base importance 99
        payload = {
            "channel_name": "voice_channel",
            "entry": {
                "base_importance": 99,
            }
        }
        resp = self.client.put("/api/attention/config", json=payload)
        self.assertEqual(resp.status_code, 400)
        
        # Invalid string base_importance "5"
        payload_str = {
            "channel_name": "voice_channel",
            "entry": {
                "base_importance": "5",
            }
        }
        resp_str = self.client.put("/api/attention/config", json=payload_str)
        self.assertEqual(resp_str.status_code, 400)

    def test_queue_reflects_updated_config(self):
        # Set attention config for voice_channel
        payload = {
            "channel_name": "voice_channel",
            "entry": {
                "attention_active": True,
                "base_importance": 5,
                "urgency": "high",
                "blocking": True,
            }
        }
        self.client.put("/api/attention/config", json=payload)

        # GET queue
        q_resp = self.client.get("/api/attention/queue?now=1000000.0")
        self.assertEqual(q_resp.status_code, 200)
        q_data = q_resp.json()
        self.assertTrue(q_data.get("success"))
        
        # Find voice_channel item
        queue = q_data.get("queue", [])
        voice_item = next((item for item in queue if item["channel_name"] == "voice_channel"), None)
        self.assertIsNotNone(voice_item)
        self.assertEqual(voice_item["status"], "active")


if __name__ == "__main__":
    unittest.main()
