from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.configure_rc_webhook import (
    ConfigurationError,
    desired_integration,
    integration_drift,
    read_env_file,
    redacted_plan,
)


SECRET = "s" * 48


class TestConfigureRocketChatWebhook(unittest.TestCase):
    def desired(self):
        return desired_integration(
            name="voice_test",
            scope="all_public_channels",
            webhook_url="http://host.docker.internal:6891/api/rc/webhook/message",
            webhook_secret=SECRET,
            username="acli_bot",
        )

    def test_desired_configuration_is_public_only_and_has_no_response_script(self):
        desired = self.desired()
        self.assertEqual(desired["channel"], "all_public_channels")
        self.assertEqual(desired["event"], "sendMessage")
        self.assertFalse(desired["scriptEnabled"])
        self.assertEqual(desired["script"], "")
        self.assertEqual(desired["responding"], "")

    def test_drift_normalizes_rocket_chat_channel_arrays(self):
        desired = self.desired()
        current = {**desired, "_id": "integration-1", "channel": ["all_public_channels"]}
        self.assertEqual(integration_drift(current, desired), [])
        current["channel"] = ["#TV_and_memory"]
        self.assertEqual(integration_drift(current, desired), ["channel"])

    def test_redacted_plan_never_prints_the_webhook_secret(self):
        rendered = str(redacted_plan(self.desired()))
        self.assertNotIn(SECRET, rendered)
        self.assertIn("<redacted>", rendered)

    def test_short_secret_fails_closed(self):
        with self.assertRaises(ConfigurationError):
            desired_integration(
                name="voice_test",
                scope="all_public_channels",
                webhook_url="http://gateway/hook",
                webhook_secret="short",
                username="acli_bot",
            )

    def test_disable_mode_changes_only_the_explicit_enabled_setting(self):
        disabled = desired_integration(
            name="voice_test",
            scope="all_public_channels",
            webhook_url="http://host.docker.internal:6891/api/rc/webhook/message",
            webhook_secret=SECRET,
            username="acli_bot",
            enabled=False,
        )
        self.assertFalse(disabled["enabled"])

    def test_legacy_colon_admin_values_are_read_without_special_casing_callers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "ROOT_URL=https://chat.example.test\nEmail: admin@example.test\nPassword: secret\n",
                encoding="utf-8",
            )
            values = read_env_file(path)
        self.assertEqual(values["ROOT_URL"], "https://chat.example.test")
        self.assertEqual(values["Email"], "admin@example.test")
        self.assertEqual(values["Password"], "secret")


if __name__ == "__main__":
    unittest.main()
