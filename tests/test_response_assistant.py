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
from app.attention_config import ChannelAttentionConfig, ChannelAttentionEntry
from app.main import ResponseAssistantRequest, app
from app.supervision_strategy import (
    build_strategy_context,
    fallback_quick_suggestions,
    fallback_suggestion,
    normalize_attention_items,
    normalize_narrator_summary,
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

NONCODING_PROFILE = {
    "channel_name": "content_assistant",
    "channel_type": "acli_noncoding",
    "default_worker": "codex",
    "roles": None,
    "notes": "Content creation",
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

    def test_coding_strategy_encodes_forward_progress_and_effective_roles(self):
        strategy = build_strategy_context(CODING_PROFILE)

        self.assertIn('"coder": "agy"', strategy)
        self.assertIn('"one_pass_reviewer": "grok"', strategy)
        self.assertIn('"post_remediation_reviewer": "codex"', strategy)
        self.assertIn('"periodic_overall_reviewer": "claude"', strategy)
        self.assertIn("minor findings create a review-remediation-review loop", strategy)
        self.assertIn("After two or three successfully delivered small steps", strategy)

    def test_output_parser_validates_worker_and_attention_contract(self):
        output = json.dumps(
            {
                "digest": "AGY completed the requested work and it is ready for independent review.",
                "attention_items": [
                    {
                        "type": "decision",
                        "severity": None,
                        "text": "Ed needs to decide whether to begin the independent review now.",
                    },
                    {
                        "type": "issue",
                        "severity": "minor",
                        "text": "A tiny cleanup item remains.",
                    },
                ],
                "phase": "review",
                "suggested_agent": "grok",
                "suggested_message": "@grok Independently review the implementation and tests.",
                "quick_suggestions": [
                    {
                        "label": "Double-check",
                        "command": "@grok Independently double-check the implementation and tests.",
                    },
                    {
                        "label": "Your take",
                        "command": "@codex What is your opinion before we advance?",
                    },
                ],
                "rationale": "Implementation should be reviewed.",
            }
        )
        parsed = parse_response_assistant_output(output, CODING_PROFILE, LATEST_AGY)
        self.assertEqual(parsed["suggested_agent"], "grok")
        self.assertEqual(len(parsed["attention_items"]), 1)
        self.assertEqual(parsed["attention_items"][0]["type"], "decision")
        self.assertEqual(
            parsed["digest"],
            "AGY completed the requested work and it is ready for independent review.",
        )
        self.assertNotIn("Decision needed", parsed["digest"])
        self.assertNotIn("independent review now", parsed["digest"])
        self.assertNotIn("cleanup", parsed["digest"])
        self.assertEqual(len(parsed["quick_suggestions"]), 2)
        self.assertEqual(parsed["quick_suggestions"][0]["label"], "Double-check")
        self.assertTrue(parsed["quick_suggestions"][0]["command"].startswith("@grok "))
        self.assertTrue(parsed["_ai_quick_valid"])

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
        self.assertEqual(normalize_narrator_summary("One sentence."), "One sentence.")
        self.assertTrue(invalid["_ai_digest_valid"])
        self.assertFalse(invalid["_ai_suggestion_valid"])
        self.assertEqual(len(invalid["quick_suggestions"]), 2)
        self.assertFalse(invalid["_ai_quick_valid"])
        self.assertEqual(invalid["quick_suggestions"][0]["label"], "Double-check")

    def test_summary_normalizer_enforces_one_short_paragraph(self):
        long_summary = " ".join(["word"] * 140) + "\n\nSecond paragraph."
        summary = normalize_narrator_summary(long_summary)
        self.assertNotIn("\n", summary)
        self.assertLessEqual(len(summary.split()), 100)

    def test_attention_normalizer_requires_material_issue_severity(self):
        items = normalize_attention_items([
            {"type": "issue", "severity": "major", "text": "Progress is blocked."},
            {"type": "issue", "severity": "moderate", "text": "Duplicate issue."},
            {"type": "clarification", "severity": "major", "text": "The desired outcome is unclear."},
            {"type": "unknown", "text": "Ignore this."},
        ])
        self.assertEqual([item["type"] for item in items], ["issue", "clarification"])
        self.assertEqual(items[0]["severity"], "major")
        self.assertIsNone(items[1]["severity"])

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

        agy_implementation = {
            **LATEST_AGY,
            "event": {"kind": "agent_response", "agent": "agy"},
            "text": "The next bounded implementation step is complete and tests pass.",
        }
        agent, _message, phase = fallback_suggestion(CODING_PROFILE, agy_implementation)
        self.assertEqual((agent, phase), ("grok", "review"))

    def test_coding_fallback_records_minor_findings_and_moves_forward(self):
        grok_minor_findings = {
            **LATEST_AGY,
            "event": {"kind": "agent_response", "agent": "grok"},
            "text": "The implementation works. I found two minor naming issues, but there are no blocking issues.",
        }

        agent, message, phase = fallback_suggestion(CODING_PROFILE, grok_minor_findings)

        self.assertEqual((agent, phase), ("agy", "implementation"))
        self.assertIn("record", message.lower())
        self.assertIn("next bounded step", message.lower())
        self.assertTrue(message.startswith("@agy "))
        self.assertNotIn("@grok", message.lower())
        self.assertNotIn("@codex", message.lower())

        quick = fallback_quick_suggestions(CODING_PROFILE, grok_minor_findings, phase)
        self.assertEqual([item["label"] for item in quick], ["Next step", "Keep moving"])
        self.assertTrue(all(item["command"].startswith("@agy ") for item in quick))

    def test_coding_fallback_routes_serious_remediation_to_agy_then_codex(self):
        grok_blocker = {
            **LATEST_AGY,
            "event": {"kind": "agent_response", "agent": "grok"},
            "text": "A material blocker breaks core behavior and must be fixed before we proceed.",
        }
        agent, message, phase = fallback_suggestion(CODING_PROFILE, grok_blocker)
        self.assertEqual((agent, phase), ("agy", "remediation"))
        self.assertIn("blocking", message.lower())

        agy_remediation = {
            **LATEST_AGY,
            "event": {"kind": "agent_response", "agent": "agy"},
            "text": "The remediation is completed and the blocking issue is resolved.",
        }
        agent, message, phase = fallback_suggestion(CODING_PROFILE, agy_remediation)
        self.assertEqual((agent, phase), ("codex", "closure"))
        self.assertIn("closure review", message.lower())

        closure_quick = fallback_quick_suggestions(CODING_PROFILE, agy_remediation, phase)
        self.assertEqual(closure_quick[0]["label"], "Closure check")
        self.assertTrue(closure_quick[0]["command"].startswith("@codex "))

        codex_closure = {
            **LATEST_AGY,
            "event": {"kind": "agent_response", "agent": "codex"},
            "text": "Closure review complete: the blocker is resolved and the plan is ready to resume.",
        }
        agent, message, phase = fallback_suggestion(CODING_PROFILE, codex_closure)
        self.assertEqual((agent, phase), ("agy", "implementation"))
        self.assertIn("next bounded step", message.lower())

        overall_quick = fallback_quick_suggestions(
            CODING_PROFILE,
            codex_closure,
            "overall_review",
        )
        self.assertEqual(overall_quick[0]["label"], "Overall view")
        self.assertTrue(overall_quick[0]["command"].startswith("@claude "))

    def test_noncoding_fallback_requests_viewpoint_not_formal_review(self):
        latest_codex = {
            **LATEST_AGY,
            "event": {"kind": "agent_response", "agent": "codex"},
            "text": "Here is a possible content direction.",
        }
        agent, message, phase = fallback_suggestion(NONCODING_PROFILE, latest_codex)
        self.assertEqual((agent, phase), ("claude", "noncoding"))
        self.assertIn("perspective", message.lower())
        self.assertNotIn("review", message.lower())
        self.assertNotIn("verify", message.lower())

        quick = fallback_quick_suggestions(
            NONCODING_PROFILE,
            latest_codex,
            phase,
        )
        self.assertEqual([item["label"] for item in quick], ["Go deeper", "Another view"])
        self.assertTrue(quick[0]["command"].startswith("@codex "))
        self.assertTrue(quick[1]["command"].startswith("@claude "))
        self.assertTrue(all("review" not in item["command"].lower() for item in quick))


class TestResponseAssistantEndpoint(unittest.TestCase):
    def setUp(self):
        main_module.response_assistant_inflight.clear()
        main_module.response_assistant_results.clear()
        main_module.narration_prewarm_generation_starts.clear()
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
                                "digest": "AGY completed the requested coding work, which is now ready for independent review.",
                                "attention_items": [],
                                "phase": "review",
                                "suggested_agent": "grok",
                                "suggested_message": "@grok Please independently review AGY's implementation and run the relevant tests.",
                                "quick_suggestions": [
                                    {
                                        "label": "Double-check",
                                        "command": "@grok Independently double-check the implementation and tests.",
                                    },
                                    {
                                        "label": "Your take",
                                        "command": "@codex What is your opinion before we advance?",
                                    },
                                ],
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
        self.assertEqual(data["attention_items"], [])
        self.assertNotIn("\n\n", data["digest"])

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

    def test_background_prewarm_uses_top_attention_and_existing_cache_contract(self):
        async def generated(request):
            return {
                "digest": "The background reply is ready for Ed to review.",
                "suggested_message": "@grok Review the reply.",
                "trigger_message_id": request.trigger_message_id,
                "included_message_ids": [request.trigger_message_id],
            }

        queue = [
            {
                "room_id": "room-ready",
                "channel_name": "voice_channel",
                "queue_category": "ranked",
                "rank": 1,
            },
            {
                "room_id": "room-later",
                "channel_name": "other_channel",
                "queue_category": "ranked",
                "rank": 6,
            },
        ]
        main_module.ROOM_MESSAGES_CACHE["room-ready"] = [LATEST_AGY]
        main_module.ROOM_MESSAGES_CACHE["room-later"] = [{**LATEST_AGY, "id": "agent-later"}]
        config = {
            "enabled": True,
            "scope": "top_attention",
            "top_n": 5,
            "history_limit": 20,
            "hourly_generation_cap": 12,
        }

        async def scenario():
            with patch.object(main_module, "load_narration_prewarm_config", return_value=config), patch.object(
                main_module, "_generate_response_assistant_once", new=generated
            ):
                scheduled = await main_module._schedule_background_narration_prewarm(
                    queue,
                    {
                        "room-ready": [LATEST_AGY],
                        "room-later": [{**LATEST_AGY, "id": "agent-later"}],
                    },
                    set(),
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                replay = await main_module.get_cached_response_assistant("room-ready", "agent-2")
            return scheduled, replay

        scheduled, replay = asyncio.run(scenario())
        self.assertEqual(scheduled, 1)
        self.assertEqual(replay["result"]["digest"], "The background reply is ready for Ed to review.")
        self.assertNotIn(("room-later", "agent-later"), main_module.response_assistant_results)

    def test_background_prewarm_spends_hourly_cap_on_highest_ranked_rooms(self):
        async def generated(request):
            return {
                "digest": "Ready.",
                "suggested_message": "@grok Review the reply.",
                "trigger_message_id": request.trigger_message_id,
                "included_message_ids": [request.trigger_message_id],
            }

        # Deliberately list the worst-ranked room first so dict/iteration order
        # cannot be what makes the assertion pass.
        ranks = [("room-low", 5), ("room-high", 1), ("room-mid", 3)]
        queue = [
            {
                "room_id": room_id,
                "channel_name": room_id,
                "queue_category": "ranked",
                "rank": rank,
            }
            for room_id, rank in ranks
        ]
        new_replies = {}
        for room_id, _rank in ranks:
            reply = {**LATEST_AGY, "id": f"agent-{room_id}"}
            main_module.ROOM_MESSAGES_CACHE[room_id] = [reply]
            new_replies[room_id] = [reply]

        config = {
            "enabled": True,
            "scope": "top_attention",
            "top_n": 5,
            "history_limit": 20,
            "hourly_generation_cap": 2,
        }

        async def scenario():
            with patch.object(main_module, "load_narration_prewarm_config", return_value=config), patch.object(
                main_module, "_generate_response_assistant_once", new=generated
            ):
                scheduled = await main_module._schedule_background_narration_prewarm(
                    queue,
                    new_replies,
                    set(),
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            return scheduled

        scheduled = asyncio.run(scenario())
        self.assertEqual(scheduled, 2)
        prewarmed = {room_id for room_id, _trigger in main_module.response_assistant_results}
        self.assertEqual(prewarmed, {"room-high", "room-mid"})
        self.assertNotIn("room-low", prewarmed)

    def test_prewarm_settings_store_control_metadata_without_message_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "narration_prewarm_settings.json")
            with patch.object(main_module, "NARRATION_PREWARM_CONFIG_PATH", config_path):
                saved = main_module.save_narration_prewarm_config({
                    "enabled": True,
                    "scope": "supervised",
                    "top_n": 99,
                    "history_limit": 20,
                    "hourly_generation_cap": 12,
                    "message": "This must never be persisted.",
                })
                with open(config_path, "r", encoding="utf-8") as f:
                    on_disk = json.load(f)

        self.assertEqual(saved["scope"], "supervised")
        self.assertEqual(saved["top_n"], 20)
        self.assertEqual(
            set(on_disk),
            {"enabled", "scope", "top_n", "history_limit", "hourly_generation_cap"},
        )
        self.assertNotIn("message", on_disk)

    def test_explicit_channel_narration_active_prewarm_eligibility(self):
        async def generated(request):
            return {
                "digest": "Explicit channel narration active.",
                "suggested_message": "@codex proceed.",
                "trigger_message_id": request.trigger_message_id,
                "included_message_ids": [request.trigger_message_id],
            }

        queue = [
            {
                "room_id": "room-explicit-active",
                "channel_name": "custom_active_channel",
                "queue_category": "idle",
                "rank": 99,
            },
            {
                "room_id": "room-unconfigured",
                "channel_name": "unconfigured_channel",
                "queue_category": "idle",
                "rank": 100,
            }
        ]
        main_module.ROOM_MESSAGES_CACHE["room-explicit-active"] = [LATEST_AGY]
        main_module.ROOM_MESSAGES_CACHE["room-unconfigured"] = [{**LATEST_AGY, "id": "agent-unconf"}]

        attn_cfg = ChannelAttentionConfig(
            channels={
                "custom_active_channel": ChannelAttentionEntry(narration_active=True)
            }
        )

        async def scenario():
            with patch.object(main_module, "load_channel_attention_config", return_value=attn_cfg), patch.object(
                main_module, "_generate_response_assistant_once", new=generated
            ):
                scheduled = await main_module._schedule_background_narration_prewarm(
                    queue,
                    {
                        "room-explicit-active": [LATEST_AGY],
                        "room-unconfigured": [{**LATEST_AGY, "id": "agent-unconf"}],
                    },
                    set(),
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                replay = await main_module.get_cached_response_assistant("room-explicit-active", "agent-2")
            return scheduled, replay

        scheduled, replay = asyncio.run(scenario())
        self.assertEqual(scheduled, 1)
        self.assertIsNotNone(replay)
        self.assertEqual(replay["result"]["digest"], "Explicit channel narration active.")
        self.assertNotIn(("room-unconfigured", "agent-unconf"), main_module.response_assistant_results)

    def test_gateway_monitor_refreshes_and_prewarms_idle_narration_active_channel(self):
        """End-to-end test for Grok's remediation: get_attention_queue refreshes history for narration_active channels even without active tasks."""
        async def generated(request):
            return {
                "digest": "Auto narration generated via monitor refresh.",
                "suggested_message": "@codex checked.",
                "trigger_message_id": request.trigger_message_id,
                "included_message_ids": [request.trigger_message_id],
            }

        attn_cfg = ChannelAttentionConfig(
            channels={
                "auto_channel": ChannelAttentionEntry(narration_active=True),
                "unconfig_channel": ChannelAttentionEntry(narration_active=False),
            }
        )

        mock_rc_rooms = [
            {"_id": "room-auto", "name": "auto_channel", "lm": "2026-08-06T23:00:00Z"},
            {"_id": "room-unconfig", "name": "unconfig_channel", "lm": "2026-08-06T23:00:00Z"},
        ]

        refreshed_rooms = set()

        async def fake_refresh_active_task_room(client, room_id):
            refreshed_rooms.add(room_id)
            if room_id == "room-auto":
                return [LATEST_AGY]
            return []

        async def scenario():
            with patch.object(main_module, "load_channel_attention_config", return_value=attn_cfg), patch.object(
                main_module, "load_channel_registry", return_value=([
                    {"channel_name": "auto_channel"},
                    {"channel_name": "unconfig_channel"},
                ], None)
            ), patch.object(
                main_module, "_generate_response_assistant_once", new=generated
            ), patch.object(
                main_module, "_refresh_active_task_room", side_effect=fake_refresh_active_task_room
            ), patch("httpx.AsyncClient.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"success": True, "update": mock_rc_rooms}
                mock_get.return_value = mock_resp

                res = await main_module.get_attention_queue(now=1000000.0)
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                replay = await main_module.get_cached_response_assistant("room-auto", "agent-2")
                return res, replay

        res, replay = asyncio.run(scenario())
        # Assert room-auto WAS refreshed because narration_active=True
        self.assertIn("room-auto", refreshed_rooms)
        # Assert room-unconfig WAS NOT refreshed because narration_active=False and no active task
        self.assertNotIn("room-unconfig", refreshed_rooms)
        # Assert prewarm ran and generated digest for room-auto
        self.assertIsNotNone(replay)
        self.assertEqual(replay["result"]["digest"], "Auto narration generated via monitor refresh.")

    def test_ddp_adapter_status_and_disabled_state(self):
        """U-11E-a test: RocketChatDDPAdapter status reporting and fallback degraded state when GATEWAY_RC_EVENTS!=1."""
        adapter = main_module.RocketChatDDPAdapter()
        status = adapter.get_status()
        self.assertEqual(status["state"], "disconnected")
        self.assertEqual(status["subscribed_rooms_count"], 0)

        async def run_start():
            with patch.dict("os.environ", {"GATEWAY_RC_EVENTS": "0"}):
                await adapter.start()

        asyncio.run(run_start())
        self.assertEqual(adapter.get_status()["state"], "degraded_polling")

    def test_sse_events_endpoint(self):
        """U-11E-b test: SSE broadcaster subscriber and /api/events endpoint test."""
        async def run_sse():
            q = await main_module.sse_broadcaster.subscribe()
            await main_module.sse_broadcaster.publish({"type": "room_changed", "room_id": "test-room-123"})
            evt = await q.get()
            self.assertEqual(evt["type"], "room_changed")
            self.assertEqual(evt["room_id"], "test-room-123")
            main_module.sse_broadcaster.unsubscribe(q)

        asyncio.run(run_sse())

    def test_ddp_message_triggers_prewarm_without_cursor(self):
        """U-11E-b remediation: DDP real agent replies should trigger prewarm even without a read cursor."""
        adapter = main_module.RocketChatDDPAdapter()

        async def scenario():
            main_module.ROOM_MESSAGES_CACHE.clear()

            with patch("app.main._schedule_background_narration_prewarm") as mock_prewarm:
                msg_obj = {
                    "_id": "agent-msg-1",
                    "rid": "room-test",
                    "msg": "Here is the plan.",
                    "ts": {"$date": int(time.time() * 1000)},
                    "u": {"username": "codex"},
                }
                data = {"fields": {"args": [msg_obj]}}

                await adapter._handle_room_message_event(data)

                self.assertEqual(len(main_module.ROOM_MESSAGES_CACHE["room-test"]), 1)
                cached_msg = main_module.ROOM_MESSAGES_CACHE["room-test"][0]
                self.assertEqual(cached_msg["lane"], "agent")
                self.assertEqual(cached_msg["name"], "codex")

                mock_prewarm.assert_called_once()

                # Assert system message doesn't trigger prewarm
                mock_prewarm.reset_mock()
                sys_msg_obj = {
                    "_id": "sys-msg-2",
                    "rid": "room-test",
                    "msg": "🔄 Routing to **codex**",
                    "ts": {"$date": int(time.time() * 1000)},
                    "u": {"username": "acli_bot"},
                }
                data_sys = {"fields": {"args": [sys_msg_obj]}}
                await adapter._handle_room_message_event(data_sys)
                mock_prewarm.assert_not_called()

        asyncio.run(scenario())

if __name__ == "__main__":
    unittest.main()
