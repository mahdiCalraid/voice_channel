"""Unit and contract tests for app/contracts.py."""

import time
import unittest
from pydantic import ValidationError

from app.contracts import (
    CURRENT_SCHEMA_VERSION,
    InputMode,
    PermissionTier,
    TaskState,
    InteractionRequest,
    Interpretation,
    ConfirmationSnapshot,
    TaskEvent,
    GatewayResult,
    validate_schema_version,
)


class TestGatewayContracts(unittest.TestCase):
    def test_schema_version_validation(self):
        self.assertEqual(validate_schema_version("1.0"), "1.0")
        with self.assertRaises(ValueError) as ctx:
            validate_schema_version("2.0")
        self.assertIn("Unsupported schema version: '2.0'", str(ctx.exception))

    def test_interaction_request_valid_roundtrip(self):
        req = InteractionRequest(
            raw_input="Hello world",
            requested_room_id="room_123",
            requested_agent="grok",
            input_mode=InputMode.VOICE,
        )
        self.assertEqual(req.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertTrue(req.interaction_id.startswith("int_"))
        self.assertEqual(req.actor_id, "user")
        self.assertEqual(req.client_id, "browser_console")
        self.assertEqual(req.input_mode, InputMode.VOICE)
        self.assertEqual(req.raw_input, "Hello world")
        self.assertEqual(req.requested_room_id, "room_123")
        self.assertEqual(req.requested_agent, "grok")

        # Test dictionary conversion
        data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
        req2 = InteractionRequest.model_validate(data) if hasattr(InteractionRequest, "model_validate") else InteractionRequest.parse_obj(data)
        self.assertEqual(req, req2)

    def test_interaction_request_invalid_schema_and_empty_input(self):
        with self.assertRaises(ValidationError):
            InteractionRequest(raw_input="", schema_version="1.0")

        with self.assertRaises(ValidationError):
            InteractionRequest(raw_input="Valid input", schema_version="9.9")

    def test_interpretation_valid_and_bounds(self):
        interp = Interpretation(
            selected_action="post_message",
            selected_room_id="room_abc",
            selected_agent="codex",
            refined_draft="Send test update",
            confidence_score=0.95,
            source_context_ids=["msg_1", "msg_2"],
        )
        self.assertEqual(interp.selected_action, "post_message")
        self.assertEqual(interp.confidence_score, 0.95)

        # Invalid confidence score > 1.0 or < 0.0
        with self.assertRaises(ValidationError):
            Interpretation(selected_action="test", confidence_score=1.5)

        with self.assertRaises(ValidationError):
            Interpretation(selected_action="test", confidence_score=-0.1)

    def test_confirmation_snapshot_valid_and_validation(self):
        expiry = time.time() + 300.0
        conf = ConfirmationSnapshot(
            immutable_interaction_id="int_001",
            room_id="room_123",
            agent="codex",
            exact_message="Confirmed post message",
            permission_tier=PermissionTier.COMMIT,
            expires_at=expiry,
            nonce="nonce_test_123",
        )
        self.assertEqual(conf.permission_tier, PermissionTier.COMMIT)
        self.assertEqual(conf.nonce, "nonce_test_123")

        with self.assertRaises(ValidationError):
            ConfirmationSnapshot(
                immutable_interaction_id="int_001",
                room_id="room_123",
                agent="codex",
                exact_message="", # Empty message
                permission_tier=PermissionTier.COMMIT,
                expires_at=expiry,
                nonce="nonce_test",
            )

    def test_task_states(self):
        expected_states = [
            "captured", "interpreting", "needs_clarification", "awaiting_confirmation",
            "posted", "routed", "working", "completed", "failed", "cancel_requested",
            "cancelled", "superseded", "summarized", "spoken"
        ]
        actual_states = [s.value for s in TaskState]
        for s in expected_states:
            self.assertIn(s, actual_states)

    def test_gateway_result_serialization(self):
        evt = TaskEvent(
            interaction_id="int_001",
            state=TaskState.WORKING,
            details={"step": "processing"},
        )
        res = GatewayResult(
            status=TaskState.COMPLETED,
            selected_room_id="room_123",
            rocket_chat_msg_ids=["rc_msg_001"],
            task_events=[evt],
            full_response="Complete answer",
            concise_summary="Answer summary",
        )
        self.assertEqual(res.status, TaskState.COMPLETED)
        self.assertEqual(len(res.task_events), 1)
        self.assertEqual(res.task_events[0].state, TaskState.WORKING)

        data = res.model_dump() if hasattr(res, "model_dump") else res.dict()
        res2 = GatewayResult.model_validate(data) if hasattr(GatewayResult, "model_validate") else GatewayResult.parse_obj(data)
        self.assertEqual(res, res2)


if __name__ == "__main__":
    unittest.main()
