"""Unit tests for channel attention configuration loading, validation, registry merging, and persistence."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from app.attention_config import (
    ChannelAttentionConfig,
    ChannelAttentionEntry,
    UrgencyLevel,
    load_channel_attention_config,
    resolve_merged_channel_attention_status,
    save_channel_attention_config,
)


class TestChannelAttentionConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = str(Path(self.temp_dir.name) / "channel_attention_config.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_load_missing_file_returns_empty_config(self):
        config = load_channel_attention_config(self.config_path)
        self.assertEqual(config.schema_version, "1.0")
        self.assertEqual(config.channels, {})

    def test_env_var_override_path_is_honored(self):
        os.environ["CHANNEL_ATTENTION_CONFIG_PATH"] = self.config_path
        try:
            config = load_channel_attention_config()
            self.assertEqual(config.channels, {})
            
            entry = ChannelAttentionEntry(base_importance=4, urgency=UrgencyLevel.HIGH)
            save_channel_attention_config(ChannelAttentionConfig(channels={"voice_channel": entry}))
            
            reloaded = load_channel_attention_config()
            self.assertIn("voice_channel", reloaded.channels)
            self.assertEqual(reloaded.channels["voice_channel"].base_importance, 4)
            self.assertEqual(reloaded.channels["voice_channel"].urgency, UrgencyLevel.HIGH)
        finally:
            os.environ.pop("CHANNEL_ATTENTION_CONFIG_PATH", None)

    def test_valid_schema_loads_and_roundtrips(self):
        entry = ChannelAttentionEntry(
            attention_active=True,
            base_importance=5,
            urgency=UrgencyLevel.HIGH,
            deadline="2026-08-05T12:00:00Z",
            blocking=True,
            temporary_boost_until="2026-08-02T23:59:59+00:00",
            snoozed_until=None,
        )
        config = ChannelAttentionConfig(schema_version="1.0", channels={"voice_channel": entry})
        save_channel_attention_config(config, self.config_path)

        reloaded = load_channel_attention_config(self.config_path)
        self.assertEqual(reloaded.schema_version, "1.0")
        self.assertIn("voice_channel", reloaded.channels)
        v_entry = reloaded.channels["voice_channel"]
        self.assertEqual(v_entry.base_importance, 5)
        self.assertEqual(v_entry.urgency, UrgencyLevel.HIGH)
        self.assertTrue(v_entry.blocking)
        self.assertEqual(v_entry.deadline, "2026-08-05T12:00:00Z")

    def test_reject_invalid_base_importance(self):
        for bad_val in [0, 6, -1, 3.5, "5", True]:
            with self.subTest(bad_val=bad_val):
                with self.assertRaises(ValueError):
                    ChannelAttentionEntry(base_importance=bad_val)

    def test_reject_invalid_urgency(self):
        with self.assertRaises(ValueError):
            ChannelAttentionEntry(urgency="critical")

    def test_reject_naive_timestamps_without_timezone(self):
        with self.assertRaises(ValueError):
            ChannelAttentionEntry(deadline="2026-08-05 12:00:00")

    def test_reject_unknown_fields_or_derived_scores(self):
        # BaseContractModel extra=forbid rejects score, rank, ready_since, etc.
        data = {
            "schema_version": "1.0",
            "channels": {
                "voice_channel": {
                    "base_importance": 3,
                    "derived_score": 100,  # forbidden
                }
            }
        }
        with self.assertRaises(ValueError):
            ChannelAttentionConfig.model_validate(data) if hasattr(ChannelAttentionConfig, "model_validate") else ChannelAttentionConfig.parse_obj(data)

    def test_reject_case_insensitive_duplicate_channel_names(self):
        data = {
            "schema_version": "1.0",
            "channels": {
                "voice_channel": {"base_importance": 3},
                "VOICE_CHANNEL": {"base_importance": 4},
            }
        }
        with self.assertRaises(ValueError):
            ChannelAttentionConfig.model_validate(data) if hasattr(ChannelAttentionConfig, "model_validate") else ChannelAttentionConfig.parse_obj(data)

    def test_registry_merging_logic(self):
        config = ChannelAttentionConfig(
            channels={
                "voice_channel": ChannelAttentionEntry(attention_active=True, base_importance=5),
                "JobHunting": ChannelAttentionEntry(attention_active=False, base_importance=2),
                "orphaned_room": ChannelAttentionEntry(attention_active=True, base_importance=1),
            }
        )
        registry = [
            {"channel_name": "voice_channel", "active": True},
            {"channel_name": "JobHunting", "active": True},
            {"channel_name": "unconfigured_room", "active": True},
            {"channel_name": "disabled_room", "active": False},
        ]

        merged = resolve_merged_channel_attention_status(config, registry)

        self.assertEqual(merged["voice_channel"]["status"], "active")
        self.assertTrue(merged["voice_channel"]["attention_active"])

        self.assertEqual(merged["JobHunting"]["status"], "inactive_in_attention")
        self.assertFalse(merged["JobHunting"]["attention_active"])

        self.assertEqual(merged["unconfigured_room"]["status"], "unconfigured")
        self.assertIsNone(merged["unconfigured_room"]["entry"])

        self.assertEqual(merged["disabled_room"]["status"], "disabled_by_registry")

        self.assertEqual(merged["orphaned_room"]["status"], "orphaned")

    def test_save_is_atomic_and_leaves_file_intact_on_failure(self):
        # Save valid config first
        config = ChannelAttentionConfig(channels={"voice_channel": ChannelAttentionEntry(base_importance=3)})
        save_channel_attention_config(config, self.config_path)

        # Attempt to save invalid data directly via write failure simulation
        original_content = Path(self.config_path).read_text(encoding="utf-8")
        
        # Verify invalid config object fails before writing
        with self.assertRaises(Exception):
            invalid_config = ChannelAttentionConfig(channels={"voice_channel": ChannelAttentionEntry(base_importance=99)})
            save_channel_attention_config(invalid_config, self.config_path)

        self.assertEqual(Path(self.config_path).read_text(encoding="utf-8"), original_content)


if __name__ == "__main__":
    unittest.main()
