"""Unit and contract tests for app/tts_adapter.py and TTS endpoints."""

import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.tts_adapter import (
    TTSRequest,
    get_system_voices,
    stop_tts,
    is_active_playback,
    speak_text,
    synthesize_audio,
    synthesize_chatterbox,
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
        self.assertIn("chatterbox_available", data)

    @patch("app.main.synthesize_audio")
    def test_tts_speak_endpoint_returns_transient_audio(self, mock_synthesize):
        mock_synthesize.return_value = {
            "success": True,
            "engine": "chatterbox",
            "audio": b"RIFFtemporary-wav",
            "content_type": "audio/wav",
        }
        payload = {"text": "Endpoint test speech", "mode": "summary"}
        resp = self.client.post("/api/gateway/tts/speak", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "audio/wav")
        self.assertEqual(resp.content, b"RIFFtemporary-wav")

    @patch("app.tts_adapter.synthesize_chatterbox")
    def test_provider_selection_uses_chatterbox(self, mock_chatterbox):
        mock_chatterbox.return_value = {
            "success": True,
            "engine": "chatterbox",
            "audio": b"RIFFaudio",
            "content_type": "audio/wav",
        }
        import asyncio
        result = asyncio.run(synthesize_audio(TTSRequest(text="A sentence.", provider="chatterbox")))
        self.assertTrue(result["success"])
        mock_chatterbox.assert_called_once()

    def test_browser_provider_is_a_client_fallback(self):
        import asyncio
        result = asyncio.run(synthesize_audio(TTSRequest(text="Fallback.", provider="browser")))
        self.assertFalse(result["success"])
        self.assertEqual(result["fallback"], "browser")

    @patch("app.tts_adapter.TTS_FALLBACK_PROVIDER", "browser")
    @patch("app.tts_adapter.synthesize_chatterbox")
    def test_chatterbox_failure_marks_browser_fallback(self, mock_chatterbox):
        mock_chatterbox.return_value = {"success": False, "engine": "chatterbox", "error": "offline"}
        import asyncio
        result = asyncio.run(synthesize_audio(TTSRequest(text="Offline.")))
        self.assertFalse(result["success"])
        self.assertEqual(result["fallback"], "browser")

    @patch("app.tts_adapter.httpx.AsyncClient")
    def test_invalid_chatterbox_response_is_rejected(self, mock_client_type):
        response = MagicMock(status_code=200, content=b"not audio", headers={"content-type": "application/json"})
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.post = AsyncMock(return_value=response)
        mock_client_type.return_value = client
        import asyncio
        result = asyncio.run(synthesize_chatterbox(TTSRequest(text="Bad provider response.")))
        self.assertFalse(result["success"])
        self.assertIn("invalid audio", result["error"])

    def test_tts_stop_endpoint(self):
        resp = self.client.post("/api/gateway/tts/stop")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("stopped", data)


if __name__ == "__main__":
    unittest.main()
