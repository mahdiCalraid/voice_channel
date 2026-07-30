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


if __name__ == "__main__":
    unittest.main()
