"""Regression tests for newly registered ACLI matter discovery."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.supervision_strategy as strategy
import scripts.sync_gateway_matter_mounts as mount_sync


class TestChannelDiscovery(unittest.TestCase):
    def test_acli_registered_matter_is_available_without_strategy_snapshot_entry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            matter_root = root / "OB_revisit"
            (matter_root / "acli").mkdir(parents=True)
            (matter_root / "acli" / "matter.json").write_text(
                json.dumps({
                    "matter_id": "OB_revisit",
                    "default_agent": "codex",
                    "channel": {"name": "OB_revisit", "room_id": "room-ob-revisit"},
                }),
                encoding="utf-8",
            )
            registry = root / "acli_matters.json"
            registry.write_text(
                json.dumps({"matters": [str(matter_root)]}),
                encoding="utf-8",
            )

            with patch.object(
                strategy,
                "ACLI_MATTERS_REGISTRY_CANDIDATES",
                (registry,),
            ):
                profile = strategy.resolve_channel_profile("OB_revisit")

            self.assertTrue(profile["registered"])
            self.assertEqual(profile["channel_name"], "OB_revisit")
            self.assertEqual(profile["folder_path"], str(matter_root))
            self.assertEqual(profile["default_worker"], "codex")

    def test_restart_mount_sync_adds_new_matter_without_duplicate_static_mounts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            matter_root = root / "OB_revisit"
            (matter_root / "acli").mkdir(parents=True)
            (matter_root / "acli" / "matter.json").write_text(
                json.dumps({"channel": {"name": "OB_revisit"}}),
                encoding="utf-8",
            )
            registry = root / "acli_matters.json"
            registry.write_text(json.dumps({"matters": [str(matter_root)]}), encoding="utf-8")
            base_override = root / "docker-compose.override.yml"
            base_override.write_text("      - /already/registered:/already/registered:ro\n", encoding="utf-8")
            generated = root / "generated.yml"

            with patch.object(mount_sync, "REGISTRY_CANDIDATES", (registry,)), patch.object(
                mount_sync, "BASE_OVERRIDE", base_override
            ), patch.object(mount_sync, "GENERATED_OVERRIDE", generated):
                result = mount_sync.generate()

            text = result.read_text(encoding="utf-8")
            self.assertIn(f"{matter_root}:{matter_root}:ro", text)
            self.assertNotIn("/already/registered", text)


if __name__ == "__main__":
    unittest.main()
