"""Unit and contract tests for U-01, U-02, and U-03 urgent implementation features."""

import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app, DigestRequest


class TestUrgentFeatures(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_digest_request_model_history_limit(self):
        req = DigestRequest(messages=[], history_limit=30)
        self.assertEqual(req.history_limit, 30)

        req_default = DigestRequest(messages=[])
        self.assertEqual(req_default.history_limit, 20)

    def test_frontend_shell_and_assets_disable_stale_browser_cache(self):
        shell = self.client.get("/")
        self.assertEqual(shell.status_code, 200)
        self.assertEqual(shell.headers.get("cache-control"), "no-store, max-age=0")
        self.assertIn("index.js?v=composer-layout-1", shell.text)
        self.assertIn("index.css?v=composer-layout-1", shell.text)
        self.assertIn("history_state.js?v=response-assistant-3", shell.text)
        self.assertIn('id="setting-system-font-size"', shell.text)
        self.assertIn('id="setting-chat-font-family"', shell.text)
        self.assertIn('id="setting-chat-font-size"', shell.text)
        self.assertIn('id="setting-chat-line-height"', shell.text)
        self.assertIn('id="composer-resize-handle"', shell.text)
        self.assertIn('id="digest-content"', shell.text)
        self.assertIn('contenteditable="plaintext-only"', shell.text)
        self.assertIn("Editable narration", shell.text)
        self.assertIn("<span>Play</span>", shell.text)

        script = self.client.get("/index.js")
        self.assertIn("const DISABLE_AUTO_NARRATION = true;", script.text)
        self.assertIn('if (!String(currentDigestText || "").trim()) return;', script.text)

        stylesheet = self.client.get("/index.css")
        self.assertIn(".digest-editor", stylesheet.text)
        self.assertIn("min-height: 36px;", stylesheet.text)
        self.assertIn(".composer-input-row .input-wrapper", stylesheet.text)
        self.assertIn("flex: 1 1 0;", stylesheet.text)
        self.assertIn("min-height: 250px;", stylesheet.text)

        for asset_path in ("/index.js", "/history_state.js", "/index.css"):
            with self.subTest(asset_path=asset_path):
                asset = self.client.get(asset_path)
                self.assertEqual(asset.status_code, 200)
                self.assertEqual(
                    asset.headers.get("cache-control"),
                    "no-store, max-age=0",
                )

    @patch("app.main.read_matter_docs")
    @patch("app.main.read_prior_summaries")
    @patch("app.main.save_summary")
    @patch("subprocess.run")
    def test_digest_generation_two_paragraphs(self, mock_subproc_run, mock_save, mock_summaries, mock_docs):
        mock_docs.return_value = "Matter docs"
        mock_summaries.return_value = []
        mock_save.return_value = None
        
        def mock_file_creator(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "--job" in cmd:
                job_file_path = cmd[cmd.index("--job") + 1]
                import json, os
                job_dir = os.path.dirname(job_file_path)
                out_path = os.path.join(job_dir, "result.json")
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "ok": True,
                        "output": "Paragraph 1: Codex completed the initial architecture setup and verified contract endpoints.\n\nParagraph 2: All 65 Python tests and 12 Node tests are passing cleanly."
                    }, f)
            res = MagicMock()
            res.returncode = 0
            res.stdout = "help text"
            return res

        mock_subproc_run.side_effect = mock_file_creator

        payload = {
            "messages": [
                {"id": "msg_001", "name": "Codex", "text": "Task finished", "lane": "agent", "ts": "2026-07-30T02:00:00.000Z"}
            ],
            "roomId": "test_room_123",
            "history_limit": 30
        }

        resp = self.client.post("/api/digest", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        digest = data.get("digest", "")
        self.assertIn("Paragraph 1:", digest)
        self.assertIn("Paragraph 2:", digest)

    @patch("app.main.read_matter_docs")
    @patch("app.main.read_prior_summaries")
    @patch("app.main.save_summary")
    @patch("subprocess.run")
    def test_fallback_digest_two_paragraphs(self, mock_subproc_run, mock_save, mock_summaries, mock_docs):
        mock_docs.return_value = "Matter docs"
        mock_summaries.return_value = []
        mock_save.return_value = None
        
        # Simulate worker failure
        res = MagicMock()
        res.returncode = 1
        res.stderr = "Worker error"
        mock_subproc_run.return_value = res

        payload = {
            "messages": [
                {"id": "msg_001", "name": "Codex", "text": "Task finished", "lane": "agent", "ts": "2026-07-30T02:00:00.000Z"}
            ],
            "roomId": "test_room_123",
            "history_limit": 10
        }

        resp = self.client.post("/api/digest", json=payload)
        self.assertEqual(resp.status_code, 200)
        digest = resp.json().get("digest", "")
        self.assertIn("\n\n", digest)
        paragraphs = digest.split("\n\n")
        self.assertEqual(len(paragraphs), 2)

    @patch("httpx.AsyncClient.get")
    def test_rooms_endpoint_returns_recency_and_sorts(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "update": [
                {"_id": "r1", "name": "alpha", "t": "c", "lm": "2026-07-30T01:00:00.000Z", "_updatedAt": "2026-07-30T01:00:00.000Z"},
                {"_id": "r2", "name": "zeta", "t": "c", "lm": "2026-07-30T05:00:00.000Z", "_updatedAt": "2026-07-30T05:00:00.000Z"}
            ]
        }
        mock_get.return_value = mock_resp

        resp = self.client.get("/api/rooms")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        rooms = data.get("rooms", [])
        self.assertEqual(len(rooms), 2)
        # Verify recency-first sorting (zeta timestamp > alpha timestamp)
        self.assertEqual(rooms[0]["name"], "zeta")
        self.assertEqual(rooms[1]["name"], "alpha")
        self.assertIn("lm", rooms[0])
        self.assertIn("_updatedAt", rooms[0])


if __name__ == "__main__":
    unittest.main()
