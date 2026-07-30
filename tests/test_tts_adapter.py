"""Unit and contract tests for app/tts_adapter.py and TTS endpoints."""

import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.tts_adapter import (
    TTSRequest,
    get_system_voices,
    stop_tts,
    is_active_playback,
    speak_text,
)


class TestTTSAdapter(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_get_system_voices(self):
        voices = get_system_voices()
        self.assertIsInstance(voices, list)
        self.assertGreater(len(voices), 0)

    def test_stop_tts_when_idle(self):
        result = stop_tts()
        self.assertFalse(result)
        self.assertFalse(is_active_playback())

    @patch("subprocess.Popen")
    @patch("shutil.which")
    def test_speak_text_macos_say(self, mock_which, mock_popen):
        mock_which.return_value = "/usr/bin/say"
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        req = TTSRequest(text="Test speech output", voice="Samantha", rate=220)
        loop_res = MagicMock()

        import asyncio
        res = asyncio.run(speak_text(req))

        self.assertTrue(res["success"])
        self.assertEqual(res["engine"], "macos_say")
        self.assertEqual(res["pid"], 12345)
        self.assertEqual(res["text_spoken_length"], len("Test speech output"))

        # Test stop_tts when process is active
        stopped = stop_tts()
        self.assertTrue(stopped)
        mock_proc.terminate.assert_called_once()

    def test_tts_status_endpoint(self):
        resp = self.client.get("/api/gateway/tts/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("engine", data)
        self.assertTrue(data["is_available"])
        self.assertIsInstance(data["available_voices"], list)

    @patch("app.main.speak_text")
    def test_tts_speak_endpoint(self, mock_speak_text):
        mock_speak_text.return_value = {"success": True, "engine": "macos_say", "pid": 999}
        payload = {"text": "Endpoint test speech", "mode": "summary"}
        resp = self.client.post("/api/gateway/tts/speak", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])

    def test_tts_stop_endpoint(self):
        resp = self.client.post("/api/gateway/tts/stop")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("stopped", data)


if __name__ == "__main__":
    unittest.main()
