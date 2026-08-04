"""Focused tests for the ACLI-backed graphical model selector."""

import json
import os
import asyncio
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

import app.main as main
from app.task_supervisor import TaskSupervisor


class TestAgentModelsEndpoints(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "acli").mkdir()
        self.development_root = self.root / "development_channel"
        (self.development_root / "acli").mkdir(parents=True)
        (self.development_root / "src" / "acli" / "core").mkdir(parents=True)
        self.room_id = "room-model-test"
        self.catalogs = {
            "codex": {
                "default_model": "gpt-5.4",
                "available_models": ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.4", "o3"],
                "aliases": {"terra": "gpt-5.6-terra"},
                "default_effort": "low",
                "available_efforts": ["low", "medium", "high", "xhigh", "max"],
            },
            "agy": {
                "default_model": "gemini-3.6-flash-low",
                "available_models": [
                    "gemini-3.6-flash-low",
                    "gemini-3.6-flash-medium",
                    "gemini-3.6-flash-high",
                    "gemini-3.5-flash-low",
                    "gemini-3.5-flash-medium",
                    "gemini-3.5-flash-high",
                    "gemini-3.1-pro-low",
                    "gemini-3.1-pro-high",
                    "claude-sonnet-4-6",
                    "claude-opus-4-6-thinking",
                    "gpt-oss-120b-medium",
                ],
                "aliases": {},
                "default_effort": "low",
                "available_efforts": ["low", "high"],
            },
            "claude": {
                "default_model": "claude-sonnet-5",
                "available_models": ["claude-sonnet-5", "claude-opus-5"],
                "aliases": {},
                "default_effort": "low",
                "available_efforts": ["low", "high"],
            },
            "grok": {
                "default_model": "grok-4.5",
                "available_models": ["grok-4.5", "grok-build"],
                "aliases": {},
                "default_effort": "high",
                "available_efforts": ["low", "high", "xhigh"],
            },
        }
        self.model_defaults = deepcopy(self.catalogs)
        self.sessions = {
                "agents": {
                    "codex": {"current_model": "gpt-5.6-sol", "current_effort": "high", "status": "idle"},
                "agy": {"current_model": "", "current_effort": "", "status": "idle"},
                "claude": {"current_model": "claude-opus-5", "current_effort": "high", "status": "idle"},
                "grok": {"current_model": "grok-4.5", "current_effort": "high", "status": "idle"},
            }
        }
        self.environment_patch = patch.dict(
            os.environ,
            {"ACLI_DEVELOPMENT_ROOT": str(self.development_root)},
        )
        self.environment_patch.start()
        self._write_state()
        profile = {
            "registered": True,
            "channel_name": "voice_channel",
            "folder_path": str(self.root),
        }
        self.profile_patch = patch.object(main, "resolve_channel_profile", return_value=profile)
        self.profile_patch.start()
        self.original_supervisor = main.task_supervisor
        main.task_supervisor = TaskSupervisor(str(self.root / "gateway_tasks.json"))
        self.client = TestClient(main.app)

    def tearDown(self):
        main.task_supervisor = self.original_supervisor
        self.profile_patch.stop()
        self.environment_patch.stop()
        self.tempdir.cleanup()

    def _write_state(self):
        (self.root / "acli" / "matter.json").write_text(
            json.dumps({"channel": {"name": "voice_channel", "room_id": self.room_id}}),
            encoding="utf-8",
        )
        (self.development_root / "acli" / "acli_settings.json").write_text(
            json.dumps({"agent_models": self.catalogs}),
            encoding="utf-8",
        )
        (self.development_root / "src" / "acli" / "core" / "models.py").write_text(
            "CANONICAL_EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')\n"
            f"DEFAULT_AGENT_MODEL_CONFIG = {self.model_defaults!r}\n",
            encoding="utf-8",
        )
        (self.root / "acli" / "sessions.json").write_text(
            json.dumps(self.sessions),
            encoding="utf-8",
        )

    def _prepare(self, *, agent="codex", model="gpt-5.6-terra", effort="high"):
        return self.client.post(
            "/api/agent-models/prepare",
            json={
                "room_id": self.room_id,
                "channel_name": "voice_channel",
                "agent": agent,
                "target_model": model,
                "target_effort": effort,
            },
        )

    def test_format_model_title(self):
        self.assertEqual(main.format_model_title("gpt-5.6-sol"), "GPT 5.6 Sol")
        self.assertEqual(main.format_model_title("gemini-3.6-flash"), "Gemini 3.6 Flash")
        self.assertEqual(main.format_model_title("custom-model"), "custom-model")
        self.assertEqual(main.format_agent_title("agy"), "AGY")

    def test_catalogs_are_provider_scoped_and_include_all_canonical_efforts(self):
        resp = self.client.get(
            "/api/agent-models",
            params={"roomId": self.room_id, "channelName": "voice_channel"},
        )
        self.assertEqual(resp.status_code, 200)
        agents = resp.json()["agents"]
        self.assertEqual(set(agents), {"codex", "agy", "claude", "grok"})
        for agent, config in self.catalogs.items():
            self.assertEqual(agents[agent]["available_models"], config["available_models"])
            self.assertEqual(
                set(agents[agent]["available_efforts"]),
                {"low", "medium", "high", "xhigh", "max"},
            )
        self.assertNotIn("grok-4.5", agents["codex"]["available_models"])
        self.assertNotIn("gpt-5.6-sol", agents["grok"]["available_models"])
        self.assertNotIn("gemini", agents)
        self.assertEqual(
            resp.json()["catalog_sources"],
            [
                str(self.development_root / "src" / "acli" / "core" / "models.py"),
                str(self.development_root / "acli" / "acli_settings.json"),
            ],
        )

    def test_models_py_defaults_fill_models_missing_from_configured_catalog(self):
        self.model_defaults["claude"]["available_models"].append("claude-model-from-code")
        self._write_state()

        data = self.client.get(
            "/api/agent-models",
            params={"roomId": self.room_id, "channelName": "voice_channel"},
        ).json()

        self.assertEqual(
            data["agents"]["claude"]["available_models"],
            self.catalogs["claude"]["available_models"] + ["claude-model-from-code"],
        )

    def test_stale_cross_provider_override_is_not_injected_into_catalog(self):
        self.sessions["agents"]["codex"]["current_model"] = "grok-4.5"
        self._write_state()
        data = self.client.get(
            "/api/agent-models",
            params={"roomId": self.room_id, "channelName": "voice_channel"},
        ).json()
        codex = data["agents"]["codex"]
        self.assertEqual(codex["current_model"], "gpt-5.4")
        self.assertEqual(codex["available_models"], self.catalogs["codex"]["available_models"])

    def test_prepare_returns_immutable_exact_gateway_snapshot(self):
        resp = self._prepare()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        snapshot = data["confirmation_snapshot"]
        change = data["change"]
        self.assertEqual(snapshot["agent"], "codex")
        self.assertEqual(snapshot["room_id"], self.room_id)
        self.assertEqual(snapshot["exact_message"], "!model codex gpt-5.6-terra high")
        self.assertTrue(snapshot["nonce"].startswith("nonce_int_"))
        self.assertEqual(change["target_model"], "gpt-5.6-terra")
        self.assertIn("Change Codex", data["confirmation_message"])

    def test_prepare_default_reset_is_exact(self):
        resp = self._prepare(model="default", effort="low")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["confirmation_snapshot"]["exact_message"], "!model codex default")
        self.assertEqual(data["change"]["formatted_command"], "!model codex default")
        self.assertIn("Reset Codex", data["confirmation_message"])

    def test_agy_catalog_groups_effort_slugs_for_the_shared_selector(self):
        data = self.client.get(
            "/api/agent-models",
            params={"roomId": self.room_id, "channelName": "voice_channel"},
        ).json()["agents"]["agy"]
        options = {option["id"]: option for option in data["model_options"]}

        self.assertEqual(data["selector_model"], "gemini-3.6-flash")
        self.assertEqual(data["selector_effort"], "low")
        self.assertEqual(options["gemini-3.6-flash"]["efforts"], ["low", "medium", "high"])
        self.assertEqual(
            options["gemini-3.6-flash"]["canonical_by_effort"]["high"],
            "gemini-3.6-flash-high",
        )
        self.assertIn("gpt-oss-120b-medium", options)

    def test_prepare_translates_shared_agy_selection_to_native_slug_state(self):
        resp = self._prepare(agent="agy", model="gemini-3.6-flash", effort="high")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["confirmation_snapshot"]["agent"], "agy")
        self.assertEqual(
            data["confirmation_snapshot"]["exact_message"],
            "!model agy gemini 3.6 flash high",
        )
        self.assertEqual(data["change"]["target_model"], "gemini-3.6-flash")
        self.assertEqual(data["change"]["effective_target_model"], "gemini-3.6-flash-high")
        self.assertIn("Change AGY", data["confirmation_message"])

    def test_prepare_rejects_effort_not_published_for_agy_family(self):
        resp = self._prepare(agent="agy", model="gemini-3.1-pro", effort="medium")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("unavailable for AGY model", resp.json()["detail"])

    def test_agy_confirmation_waits_for_effort_bearing_slug(self):
        prepared = self._prepare(
            agent="agy",
            model="gemini-3.6-flash",
            effort="high",
        ).json()
        rc_response = MagicMock()
        rc_response.status_code = 200
        rc_response.json.return_value = {"success": True, "message": {"_id": "rc-agy-model"}}
        rc_client = AsyncMock()
        rc_client.post.return_value = rc_response
        rc_client.__aenter__.return_value = rc_client
        rc_client.__aexit__.return_value = None

        with patch.object(main.httpx, "AsyncClient", return_value=rc_client), patch.object(
            main,
            "_wait_for_model_application",
            new=AsyncMock(return_value={
                "current_model": "gemini-3.6-flash-high",
                "current_effort": "high",
            }),
        ) as wait_for_ack, patch.multiple(
            main,
            GATEWAY_RC_USER_ID="voice-gateway-user-id",
            GATEWAY_RC_AUTH_TOKEN="gateway-token",
            RC_USER_ID="acli_bot",
        ):
            resp = self.client.post(
                "/api/agent-models/confirm",
                json={
                    "confirmation_snapshot": prepared["confirmation_snapshot"],
                    "channel_name": "voice_channel",
                },
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["posted_command"], "!model agy gemini 3.6 flash high")
        self.assertEqual(
            wait_for_ack.await_args.kwargs["target_model"],
            "gemini-3.6-flash-high",
        )

    def test_invalid_and_cross_provider_models_are_rejected(self):
        self.assertEqual(self._prepare(model="nonexistent-model-xyz").status_code, 400)
        self.assertEqual(self._prepare(model="grok-4.5").status_code, 400)
        self.assertEqual(self._prepare(agent="invalid_agent").status_code, 400)

    def test_confirm_posts_plain_native_command_and_reports_persisted_state(self):
        prepared = self._prepare().json()
        effective = {
            "current_model": "gpt-5.6-terra",
            "current_effort": "high",
            "display_model": "GPT 5.6 Terra",
        }
        rc_response = MagicMock()
        rc_response.status_code = 200
        rc_response.json.return_value = {
            "success": True,
            "message": {"_id": "rc-model-001"},
        }
        rc_client = AsyncMock()
        rc_client.post.return_value = rc_response
        rc_client.__aenter__.return_value = rc_client
        rc_client.__aexit__.return_value = None

        with patch.object(main.httpx, "AsyncClient", return_value=rc_client), patch.object(
            main,
            "_wait_for_model_application",
            new=AsyncMock(return_value=effective),
        ) as wait_for_ack, patch.multiple(
            main,
            GATEWAY_RC_USER_ID="voice-gateway-user-id",
            GATEWAY_RC_AUTH_TOKEN="gateway-token",
            RC_USER_ID="acli_bot",
        ):
            resp = self.client.post("/api/agent-models/confirm", json={
                "confirmation_snapshot": prepared["confirmation_snapshot"],
                "channel_name": "voice_channel",
            })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "applied")
        self.assertTrue(data["applied"])
        self.assertEqual(data["rocket_chat_msg_ids"], ["rc-model-001"])
        self.assertEqual(data["posted_command"], "!model codex gpt-5.6-terra high")
        self.assertEqual(data["effective"], effective)
        post_kwargs = rc_client.post.await_args.kwargs
        self.assertEqual(post_kwargs["headers"]["X-User-Id"], "voice-gateway-user-id")
        self.assertEqual(post_kwargs["json"], {
            "roomId": self.room_id,
            "text": "!model codex gpt-5.6-terra high",
        })
        self.assertNotIn("voice-gateway/v1", post_kwargs["json"]["text"])
        self.assertEqual(wait_for_ack.await_args.kwargs["target_model"], "gpt-5.6-terra")

    def test_model_state_poll_retries_transient_sessions_read(self):
        applied_state = {
            "agents": {
                "codex": {
                    "current_model": "gpt-5.6-terra",
                    "current_effort": "high",
                }
            }
        }
        with patch.object(
            main,
            "get_channel_agent_models",
            side_effect=[main.HTTPException(status_code=503, detail="sessions.json is being rewritten"), applied_state],
        ), patch.object(main, "MODEL_CONTROL_POLL_ATTEMPTS", 2), patch.object(
            main, "MODEL_CONTROL_POLL_INTERVAL_SECONDS", 0
        ):
            current_loop = asyncio.get_event_loop()
            test_loop = asyncio.new_event_loop()
            try:
                result = test_loop.run_until_complete(main._wait_for_model_application(
                    room_id=self.room_id,
                    channel_name="voice_channel",
                    agent="codex",
                    target_model="gpt-5.6-terra",
                    target_effort="high",
                    reset_default=False,
                ))
            finally:
                test_loop.close()
                asyncio.set_event_loop(current_loop)

        self.assertEqual(result, applied_state["agents"]["codex"])

    def test_confirm_revalidates_frozen_command_against_current_catalog(self):
        prepared = self._prepare().json()
        prepared["confirmation_snapshot"]["exact_message"] = "!model codex grok-4.5 high"
        resp = self.client.post(
            "/api/agent-models/confirm",
            json={
                "confirmation_snapshot": prepared["confirmation_snapshot"],
                "channel_name": "voice_channel",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_generic_gateway_rejects_control_command_bypass(self):
        resp = self.client.post(
            "/api/gateway/interact",
            json={
                "interaction_id": "int_test_model_001",
                "raw_input": "!model codex gpt-5.6-terra high",
                "requested_agent": "codex",
                "requested_room_id": self.room_id,
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("typed gateway endpoint", resp.json()["detail"])

    def test_registry_host_folder_is_resolved_through_the_container_mount(self):
        """channels.json stores host paths; the matter is mounted elsewhere.

        Without the mount translation every channel except voice_channel
        reported "ACLI matter files are unavailable" and the console showed a
        permanent model-control error.
        """
        host_folder = "/Users/ed/King/clawd_2/some_matter"
        mount_root = self.root / "approved_projects"
        mounted_matter = mount_root / "clawd_2" / "some_matter"
        (mounted_matter / "acli").mkdir(parents=True)
        (mounted_matter / "acli" / "matter.json").write_text(
            json.dumps({"channel": {"name": "some_matter", "room_id": "room-mounted"}}),
            encoding="utf-8",
        )
        (mounted_matter / "acli" / "sessions.json").write_text(
            json.dumps(self.sessions),
            encoding="utf-8",
        )
        profile = {
            "registered": True,
            "channel_name": "some_matter",
            "folder_path": host_folder,
        }
        with patch.object(main, "resolve_channel_profile", return_value=profile), patch.dict(
            os.environ, {"VOICE_GATEWAY_PROJECTS_ROOT": str(mount_root)}
        ):
            resp = self.client.get(
                "/api/agent-models",
                params={"roomId": "room-mounted", "channelName": "some_matter"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["state_source"], str(mounted_matter / "acli" / "sessions.json"))
        self.assertEqual(data["agents"]["codex"]["current_model"], "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()
