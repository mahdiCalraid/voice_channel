"""Tests for the RC-only signed gateway ingress envelope."""

import hashlib
import unittest

from app.contracts import ConfirmationSnapshot
from app.rc_ingress import IngressConfigurationError, build_ingress_message, extract_interaction_id


class TestRocketChatIngress(unittest.TestCase):
    def setUp(self):
        self.confirmation = ConfirmationSnapshot(
            immutable_interaction_id="int_ingress_001",
            room_id="room_123",
            agent="codex",
            exact_message="@codex verify the signed ingress",
            expires_at=9999999999,
            nonce="nonce_ingress_001",
        )

    def test_signed_message_is_visible_and_carries_interaction_id(self):
        message = build_ingress_message(self.confirmation, "s" * 32, issued_at=1700000000)

        self.assertTrue(message.startswith("[voice-gateway/v1; interaction_id=int_ingress_001;"))
        self.assertIn("agent=codex", message)
        self.assertIn(hashlib.sha256(self.confirmation.exact_message.encode("utf-8")).hexdigest(), message)
        self.assertTrue(message.endswith(self.confirmation.exact_message))
        self.assertEqual(extract_interaction_id(message), "int_ingress_001")

    def test_short_secret_is_rejected(self):
        with self.assertRaises(IngressConfigurationError):
            build_ingress_message(self.confirmation, "too-short")


if __name__ == "__main__":
    unittest.main()
