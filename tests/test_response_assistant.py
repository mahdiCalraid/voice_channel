"""Tests for the combined narrator and strategic next-message assistant."""

import json
import os
import tempfile
import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import ResponseAssistantRequest, app
from app.supervision_strategy import (
    fallback_suggestion,
    normalize_two_paragraph_digest,
    parse_response_assistant_output,
    read_project_context,
    resolve_channel_profile,
)


CODING_PROFILE = {
    "channel_name": "voice_channel",
    "channel_type": "acli_coding",
    "default_worker": "codex",
    "roles": {
        "coder": "codex",
        "reviewer": "grok",
        "supervisor": "claude",
        "moderator": "agy",
    },
    "notes": "Voice gateway",
    "registered": True,
    "strategy_source": "test",
}

LATEST_AGY = {
    "id": "agent-2",
    "lane": "agent",
    "username": "acli_bot",
    "name": "ACLI Bot",
    "text": "**@agy**: Implementation complete with tests.",
    "event": {"kind": "agent_response", "agent": "agy"},
}


class TestSupervisionStrategy(unittest.TestCase):
    def test_bundled_registry_classifies_voice_channel(self):
        profile = resolve_channel_profile("voice_channel")
        self.assertTrue(profile["registered"])
        self.assertEqual(profile["channel_type"], "acli_coding")

    def test_unregistered_channel_is_conservative_noncoding(self):
        profile = resolve_channel_profile("not-in-registry")
        self.assertFalse(profile["registered"])
        self.assertEqual(profile["channel_type"], "acli_noncoding")
        self.assertEqual(profile["default_worker"], "codex")

    def test_project_context_reads_only_fixed_approved_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "NORTH_STAR.md"), "w", encoding="utf-8") as f:
                f.write("approved")
            with open(os.path.join(tmp, "secret.txt"), "w", encoding="utf-8") as f:
                f.write("must-not-load")
            profile = {**CODING_PROFILE, "channel_name": "other", "folder_path": tmp}
            context, used = read_project_context(profile)
            self.assertIn("approved", context)
            self.assertNotIn("must-not-load", context)
            self.assertEqual(len(used), 1)

    def test_output_parser_validates_worker_and_two_paragraph_contract(self):
        output = json.dumps(
            {
                "digest": "AGY completed the implementation.\n\nThe next step is independent review.",
                "phase": "review",
                "suggested_agent": "grok",
                "suggested_message": "@grok Independently review the implementation and tests.",
                "rationale": "Implementation should be reviewed.",
            }
        )
        parsed = parse_response_assistant_output(output, CODING_PROFILE, LATEST_AGY)
        self.assertEqual(parsed["suggested_agent"], "grok")
        self.assertEqual(len(parsed["digest"].split("\n\n")), 2)

        invalid = parse_response_assistant_output(
            json.dumps(
                {
                    "digest": "One sentence only.",
                    "suggested_agent": "unknown_worker",
                    "suggested_message": "@unknown_worker send this",
                }
            ),
            CODING_PROFILE,
            LATEST_AGY,
        )
        self.assertEqual(invalid["suggested_agent"], "grok")
        self.assertTrue(invalid["suggested_message"].startswith("@grok "))
        self.assertEqual(len(normalize_two_paragraph_digest("One sentence.").split("\n\n")), 2)
        self.assertTrue(invalid["_ai_digest_valid"])
        self.assertFalse(invalid["_ai_suggestion_valid"])

    def test_digest_normalizer_enforces_short_two_paragraph_boundary(self):
        long_paragraph = " ".join(["word"] * 140)
        digest = normalize_two_paragraph_digest(long_paragraph + "\n\n" + long_paragraph)
        paragraphs = digest.split("\n\n")
        self.assertEqual(len(paragraphs), 2)
        self.assertLessEqual(len(paragraphs[0].split()), 90)
        self.assertLessEqual(len(paragraphs[1].split()), 90)

    def test_safe_fallback_follows_coding_phase_evidence(self):
        claude_planning = {
            **LATEST_AGY,
            "event": {"kind": "agent_response", "agent": "claude"},
            "text": "The plan is promising, but it needs another technical pass.",
        }
        agent, _message, phase = fallback_suggestion(CODING_PROFILE, claude_planning)
        self.assertEqual((agent, phase), ("grok", "planning"))

        claude_green_light = {
            **claude_planning,
            "text": "This has a green light. Proceed with implementation.",
        }
        agent, _message, phase = fallback_suggestion(CODING_PROFILE, claude_green_light)
        self.assertEqual((agent, phase), ("agy", "implementation"))

        codex_fix = {
            **LATEST_AGY,
            "event": {"kind": "agent_response", "agent": "codex"},
            "text": "I fixed the bounded defect and the tests are passing.",
        }
        agent, _message, phase = fallback_suggestion(CODING_PROFILE, codex_fix)
        self.assertEqual((agent, phase), ("grok", "review"))


class TestResponseAssistantEndpoint(unittest.TestCase):
    def setUp(self):
        main_module.response_assistant_inflight.clear()
        main_module.response_assistant_results.clear()
        self.client = TestClient(app)
        self.payload = {
            "messages": [
                {
                    "id": "user-1",
                    "lane": "user",
                    "username": "ed",
                    "name": "Ed",
                    "text": "Please implement the next step.",
                    "event": {"kind": "user_message"},
                },
                LATEST_AGY,
                {
                    "id": "system-1",
                    "lane": "system",
                    "username": "acli_bot",
                    "text": "🔄 Routing to **agy**",
                    "event": {"kind": "routing", "agent": "agy"},
                },
            ],
            "roomId": "room-voice",
            "room_name": "voice_channel",
            "trigger_message_id": "agent-2",
            "history_limit": 20,
        }

    @patch("app.main.save_summary")
    @patch("app.main.read_prior_summaries", return_value=[])
    @patch("app.main.read_project_context", return_value=("PROJECT KNOWLEDGE", ["NORTH_STAR.md"]))
    @patch("app.main.resolve_channel_profile", return_value=CODING_PROFILE)
    @patch("subprocess.run")
    def test_combined_endpoint_returns_digest_and_editable_draft(
        self,
        mock_run,
        _mock_profile,
        _mock_context,
        _mock_summaries,
        mock_save,
    ):
        def create_result(command, **_kwargs):
            job_path = command[command.index("--job") + 1]
            job_dir = os.path.dirname(job_path)
            with open(os.path.join(job_dir, "result.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "ok": True,
                        "output": json.dumps(
                            {
                                "digest": "AGY completed the requested coding work.\n\nGrok should now verify the implementation and tests.",
                                "phase": "review",
                                "suggested_agent": "grok",
                                "suggested_message": "@grok Please independently review AGY's implementation and run the relevant tests.",
                                "rationale": "A separate reviewer should verify completed implementation.",
                            }
                        ),
                    },
                    f,
                )
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        mock_run.side_effect = create_result
        response = self.client.post("/api/response-assistant", json=self.payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["generation_mode"], "ai")
        self.assertEqual(data["worker"], "codex")
        self.assertEqual(data["model"], "gpt-5.6-luna")
        self.assertEqual(data["effort"], "low")
        self.assertEqual(data["phase"], "review")
        self.assertTrue(data["suggested_message"].startswith("@grok "))
        self.assertEqual(data["trigger_message_id"], "agent-2")
        self.assertEqual(data["project_context_files"], ["NORTH_STAR.md"])
        self.assertFalse(data["idempotency_replayed"])
        self.assertTrue(data["automatic_action_allowed"])

        replay = self.client.post("/api/response-assistant", json=self.payload)
        self.assertEqual(replay.status_code, 200)
        replay_data = replay.json()
        self.assertEqual(replay_data["digest"], data["digest"])
        self.assertEqual(replay_data["suggested_message"], data["suggested_message"])
        self.assertTrue(replay_data["idempotency_replayed"])
        self.assertFalse(replay_data["automatic_action_allowed"])
        mock_run.assert_called_once()
        mock_save.assert_called_once()

    @patch("app.main.save_summary")
    @patch("app.main.read_prior_summaries", return_value=[])
    @patch("app.main.read_project_context", return_value=("", []))
    @patch("app.main.resolve_channel_profile", return_value=CODING_PROFILE)
    @patch("subprocess.run")
    def test_concurrent_automatic_requests_share_one_generation_and_save(
        self,
        mock_run,
        _mock_profile,
        _mock_context,
        _mock_summaries,
        mock_save,
    ):
        def slow_failure(*_args, **_kwargs):
            time.sleep(0.05)
            result = MagicMock()
            result.returncode = 1
            result.stderr = "test fallback"
            return result

        mock_run.side_effect = slow_failure

        async def make_requests():
            request = ResponseAssistantRequest(**self.payload)
            return await asyncio.gather(
                main_module.generate_response_assistant(request),
                main_module.generate_response_assistant(request),
            )

        first, second = asyncio.run(make_requests())
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["suggested_message"], second["suggested_message"])
        self.assertEqual(
            sorted([first["automatic_action_allowed"], second["automatic_action_allowed"]]),
            [False, True],
        )
        mock_run.assert_called_once()
        mock_save.assert_called_once()

    def test_cancelled_creator_does_not_cancel_shared_result_for_waiting_tab(self):
        async def scenario():
            started = asyncio.Event()
            finish = asyncio.Event()
            generation_count = 0

            async def delayed_generation(_request):
                nonlocal generation_count
                generation_count += 1
                started.set()
                await finish.wait()
                return {
                    "digest": "The shared result completed.\n\nThe waiting tab can recover it.",
                    "suggested_message": "@grok Review the shared result.",
                    "trigger_message_id": "agent-2",
                }

            request = ResponseAssistantRequest(**self.payload)
            with patch.object(
                main_module,
                "_generate_response_assistant_once",
                new=delayed_generation,
            ):
                creator = asyncio.create_task(
                    main_module.generate_response_assistant(request)
                )
                await started.wait()
                waiting_tab = asyncio.create_task(
                    main_module.generate_response_assistant(request)
                )
                await asyncio.sleep(0)

                creator.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await creator

                finish.set()
                joined = await waiting_tab
                replay = await main_module.generate_response_assistant(request)

            return generation_count, joined, replay

        generation_count, joined, replay = asyncio.run(scenario())

        self.assertEqual(generation_count, 1)
        self.assertEqual(joined["digest"], replay["digest"])
        self.assertTrue(joined["idempotency_replayed"])
        self.assertFalse(joined["automatic_action_allowed"])
        self.assertTrue(replay["idempotency_replayed"])
        self.assertFalse(replay["automatic_action_allowed"])
        self.assertEqual(len(main_module.response_assistant_results), 1)

    @patch("app.main.save_summary")
    @patch("app.main.read_prior_summaries", return_value=[])
    @patch("app.main.read_project_context", return_value=("", []))
    @patch("app.main.resolve_channel_profile", return_value=CODING_PROFILE)
    @patch("subprocess.run")
    def test_manual_requests_bypass_automatic_idempotency(
        self,
        mock_run,
        _mock_profile,
        _mock_context,
        _mock_summaries,
        mock_save,
    ):
        result = MagicMock()
        result.returncode = 1
        result.stderr = "test fallback"
        mock_run.return_value = result
        payload = {**self.payload, "trigger_message_id": None}

        responses = [
            self.client.post("/api/response-assistant", json=payload),
            self.client.post("/api/response-assistant", json=payload),
        ]

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertTrue(all(response.json()["automatic_action_allowed"] for response in responses))
        self.assertTrue(all(not response.json()["idempotency_replayed"] for response in responses))
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(mock_save.call_count, 2)

    def test_trigger_must_be_real_agent_response(self):
        payload = {**self.payload, "trigger_message_id": "system-1"}
        response = self.client.post("/api/response-assistant", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertIn("real agent response", response.json()["detail"])

    @patch("app.main.save_summary")
    @patch("app.main.read_prior_summaries", return_value=[])
    @patch("app.main.read_project_context", return_value=("", []))
    @patch("app.main.resolve_channel_profile", return_value=CODING_PROFILE)
    @patch("subprocess.run")
    def test_worker_failure_uses_safe_review_fallback(
        self,
        mock_run,
        _mock_profile,
        _mock_context,
        _mock_summaries,
        _mock_save,
    ):
        result = MagicMock()
        result.returncode = 1
        result.stderr = "unavailable"
        mock_run.return_value = result
        response = self.client.post("/api/response-assistant", json=self.payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["generation_mode"], "fallback")
        self.assertEqual(data["suggested_agent"], "grok")
        self.assertTrue(data["suggested_message"].startswith("@grok "))
        self.assertEqual(len(data["digest"].split("\n\n")), 2)

    @patch("app.main.save_summary")
    @patch("app.main.read_prior_summaries", return_value=[])
    @patch("app.main.read_project_context", return_value=("", []))
    @patch("app.main.resolve_channel_profile", return_value=CODING_PROFILE)
    @patch("subprocess.run")
    def test_malformed_ai_output_is_reported_as_fallback(
        self,
        mock_run,
        _mock_profile,
        _mock_context,
        _mock_summaries,
        _mock_save,
    ):
        def create_result(command, **_kwargs):
            job_path = command[command.index("--job") + 1]
            job_dir = os.path.dirname(job_path)
            with open(os.path.join(job_dir, "result.json"), "w", encoding="utf-8") as f:
                json.dump({"ok": True, "output": "not valid JSON"}, f)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        mock_run.side_effect = create_result
        response = self.client.post("/api/response-assistant", json=self.payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["generation_mode"], "fallback")
        self.assertIn("agy has returned a substantive response", data["digest"].lower())
        self.assertTrue(data["suggested_message"].startswith("@grok "))

    @patch("app.main.save_summary")
    @patch("app.main.read_prior_summaries", return_value=[])
    @patch("app.main.read_project_context", return_value=("", []))
    @patch("app.main.resolve_channel_profile", return_value=CODING_PROFILE)
    @patch("subprocess.run")
    def test_context_bounds_long_chat_messages_and_uses_lightweight_model(
        self,
        mock_run,
        _mock_profile,
        _mock_context,
        _mock_summaries,
        _mock_save,
    ):
        captured = {}

        def create_result(command, **_kwargs):
            job_path = command[command.index("--job") + 1]
            job_dir = os.path.dirname(job_path)
            with open(job_path, "r", encoding="utf-8") as f:
                captured["job"] = json.load(f)
            with open(os.path.join(job_dir, "messages_for_llm.json"), "r", encoding="utf-8") as f:
                captured["messages"] = json.load(f)
            with open(os.path.join(job_dir, "result.json"), "w", encoding="utf-8") as f:
                json.dump({"ok": False, "error": "test fallback"}, f)
            result = MagicMock()
            result.returncode = 1
            result.stderr = "test fallback"
            return result

        mock_run.side_effect = create_result
        payload = dict(self.payload)
        payload["messages"] = [
            {**self.payload["messages"][0], "text": "x" * 25_000},
            {**LATEST_AGY, "text": "y" * 25_000},
        ]
        response = self.client.post("/api/response-assistant", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["job"]["model"], "gpt-5.6-luna")
        self.assertLessEqual(
            sum(len(message["text"]) for message in captured["messages"]),
            30_020,
        )
        self.assertTrue(all(len(message["text"]) <= 6_001 for message in captured["messages"]))

    def test_lease_timeout_budget_invariant(self):
        """Verify frontend lease timeout duration (90s) strictly exceeds max backend worker generation budget (60s)."""
        import re
        frontend_js_path = os.path.join(os.path.dirname(__file__), "../frontend/index.js")
        with open(frontend_js_path, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r"AUTOMATIC_ASSISTANCE_LEASE_MS\s*=\s*(.*?);", content)
        self.assertIsNotNone(match, "AUTOMATIC_ASSISTANCE_LEASE_MS constant missing in frontend/index.js")
        expr = match.group(1).replace(" ", "")
        # Evaluate simple math expressions like 90 * 1000
        lease_ms = eval(expr)
        max_backend_budget_ms = 60_000
        self.assertGreater(lease_ms, max_backend_budget_ms)


if __name__ == "__main__":
    unittest.main()
