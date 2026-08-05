"""Server-side TTS providers for the Voice Gateway.

Implements native macOS `say` subprocess execution and Web Speech API
provider abstraction for real-time text-to-speech without disk audio storage.
Supports stop, pause, resume, rate control, and full vs summary text selection.
"""

from __future__ import annotations

import sys
import os
import shutil
import asyncio
import subprocess
import logging
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger("voice-channel.tts")

class TTSRequest(BaseModel):
    text: str
    mode: str = Field(default="summary", description="'summary' or 'full'")
    voice: Optional[str] = None
    provider: Optional[str] = Field(default=None, description="chatterbox or browser")
    voice_mode: Optional[str] = Field(default=None, description="fast or nice")
    rate: Optional[int] = Field(default=200, ge=100, le=400, description="Words per minute for macOS say")

class TTSStatus(BaseModel):
    engine: str
    is_available: bool
    active_playback: bool
    available_voices: List[str]
    provider: str = "chatterbox"
    chatterbox_available: bool = False
    fallback_provider: str = "browser"
    available_voice_modes: List[str] = ["fast", "nice"]
    default_voice_mode: str = "fast"

_active_process: Optional[subprocess.Popen] = None

CHATTERBOX_URL = os.environ.get("VC_CHATTERBOX_URL", "http://127.0.0.1:8765").rstrip("/")
CHATTERBOX_MODEL = os.environ.get("VC_CHATTERBOX_MODEL", "mlx-community/chatterbox-turbo-8bit")
CHATTERBOX_TIMEOUT_SECONDS = float(os.environ.get("VC_CHATTERBOX_TIMEOUT_SECONDS", "45"))
CHATTERBOX_HEALTH_TIMEOUT_SECONDS = float(os.environ.get("VC_CHATTERBOX_HEALTH_TIMEOUT_SECONDS", "2"))
TTS_PROVIDER = os.environ.get("VC_TTS_PROVIDER", "chatterbox").strip().lower()
TTS_FALLBACK_PROVIDER = os.environ.get(
    "VC_TTS_FALLBACK_PROVIDER", "macos_say" if sys.platform == "darwin" else "browser"
).strip().lower()
TTS_FALLBACK_VOICE = os.environ.get("VC_TTS_FALLBACK_VOICE", "Ava (Premium)")
MAX_TTS_TEXT_CHARS = int(os.environ.get("VC_MAX_TTS_TEXT_CHARS", "6000"))
MAX_TTS_AUDIO_BYTES = int(os.environ.get("VC_MAX_TTS_AUDIO_BYTES", str(12 * 1024 * 1024)))


def configured_provider() -> str:
    """Return the server-side primary provider, never a browser-bundled setting."""
    return TTS_PROVIDER if TTS_PROVIDER in {"chatterbox", "browser", "macos_say"} else "chatterbox"


def provider_for_request(req: TTSRequest) -> str:
    """Map the user-facing speed/quality choice to a server provider.

    The browser never needs to know where Chatterbox lives.  ``voice_mode`` is
    deliberately a small, stable contract while ``provider`` remains available
    for internal tests and explicit provider integrations.
    """
    requested_mode = (req.voice_mode or "").strip().lower()
    if requested_mode == "fast":
        return "browser"
    if requested_mode == "nice":
        return "chatterbox"
    requested_provider = (req.provider or configured_provider()).strip().lower()
    return requested_provider if requested_provider in {"chatterbox", "browser", "macos_say"} else "browser"


async def chatterbox_health() -> bool:
    """Check the local provider without retaining text or audio."""
    try:
        timeout = httpx.Timeout(CHATTERBOX_HEALTH_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{CHATTERBOX_URL}/")
        return response.status_code < 400
    except (httpx.HTTPError, OSError, asyncio.TimeoutError):
        return False


async def synthesize_chatterbox(req: TTSRequest) -> Dict[str, Any]:
    """Synthesize one bounded sentence and return transient WAV bytes."""
    clean_text = req.text.strip()
    if not clean_text:
        return {"success": False, "error": "Text cannot be empty"}
    if len(clean_text) > MAX_TTS_TEXT_CHARS:
        return {"success": False, "error": f"Text exceeds the {MAX_TTS_TEXT_CHARS}-character limit"}

    payload = {
        "model": CHATTERBOX_MODEL,
        "input": clean_text,
        "voice": "default",
        "response_format": "wav",
    }
    try:
        timeout = httpx.Timeout(CHATTERBOX_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{CHATTERBOX_URL}/v1/audio/speech",
                json=payload,
                headers={"Accept": "audio/wav"},
            )
        if response.status_code >= 400:
            return {"success": False, "engine": "chatterbox", "error": f"Provider returned HTTP {response.status_code}"}
        audio = response.content
        content_type = response.headers.get("content-type", "").lower()
        if len(audio) == 0 or len(audio) > MAX_TTS_AUDIO_BYTES or (
            "audio" not in content_type and not audio.startswith(b"RIFF")
        ):
            return {"success": False, "engine": "chatterbox", "error": "Provider returned invalid audio"}
        return {
            "success": True,
            "engine": "chatterbox",
            "audio": audio,
            "content_type": "audio/wav",
            "text_spoken_length": len(clean_text),
        }
    except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
        logger.warning("Chatterbox synthesis unavailable: %s", exc)
        return {"success": False, "engine": "chatterbox", "error": "Chatterbox unavailable"}

def get_system_voices() -> List[str]:
    if sys.platform != "darwin":
        return ["default"]
    say_bin = shutil.which("say")
    if not say_bin:
        return ["default"]
    try:
        res = subprocess.run([say_bin, "-v", "?"], capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0:
            voices = []
            for line in res.stdout.splitlines():
                if line.strip():
                    parts = line.split()
                    if parts:
                        voices.append(parts[0])
            return voices[:20] if voices else ["Samantha", "Alex", "Victoria", "Fred"]
    except Exception as e:
        logger.warning(f"Could not query macOS say voices: {e}")
    return ["Samantha", "Alex", "Victoria", "Fred"]

def stop_tts() -> bool:
    global _active_process
    if _active_process and _active_process.poll() is None:
        try:
            _active_process.terminate()
            _active_process.wait(timeout=1.0)
        except Exception:
            try:
                _active_process.kill()
            except Exception:
                pass
        _active_process = None
        return True
    return False

def is_active_playback() -> bool:
    global _active_process
    return bool(_active_process and _active_process.poll() is None)

async def speak_text(req: TTSRequest) -> Dict[str, Any]:
    global _active_process
    stop_tts()
    
    clean_text = req.text.strip()
    if not clean_text:
        return {"success": False, "error": "Text cannot be empty"}
        
    if sys.platform == "darwin":
        say_bin = shutil.which("say")
        if say_bin:
            cmd = [say_bin]
            if req.voice:
                cmd.extend(["-v", req.voice])
            if req.rate:
                cmd.extend(["-r", str(req.rate)])
            cmd.append(clean_text)
            
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _active_process = proc
                return {
                    "success": True,
                    "engine": "macos_say",
                    "pid": proc.pid,
                    "text_spoken_length": len(clean_text)
                }
            except Exception as e:
                logger.error(f"Error launching macOS say process: {e}")
                return {"success": False, "engine": "macos_say", "error": str(e)}

    return {
        "success": True,
        "engine": "web_speech_api",
        "client_instruction": "Use browser Web Speech API for playback",
        "text_spoken_length": len(clean_text)
    }


async def synthesize_macos_audio(req: TTSRequest) -> Dict[str, Any]:
    """Render one chunk with macOS say, then delete the transient AIFF."""
    if sys.platform != "darwin":
        return {"success": False, "engine": "macos_say", "error": "macOS say is unavailable"}
    say_bin = shutil.which("say")
    clean_text = req.text.strip()
    if not say_bin or not clean_text:
        return {"success": False, "engine": "macos_say", "error": "macOS say is unavailable"}

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="voice-gateway-", suffix=".aiff", delete=False) as temp:
            temp_path = Path(temp.name)
        voice = req.voice or TTS_FALLBACK_VOICE
        command = [say_bin, "-v", voice, "-r", str(req.rate or 200), "-o", str(temp_path), clean_text]
        result = await asyncio.to_thread(
            subprocess.run, command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=CHATTERBOX_TIMEOUT_SECONDS, check=False,
        )
        if result.returncode != 0 or not temp_path.exists():
            return {"success": False, "engine": "macos_say", "error": "macOS say failed"}
        audio = temp_path.read_bytes()
        return {
            "success": True,
            "engine": "macos_say",
            "audio": audio,
            "content_type": "audio/aiff",
            "text_spoken_length": len(clean_text),
        }
    except (OSError, subprocess.SubprocessError, asyncio.TimeoutError) as exc:
        logger.warning("macOS fallback synthesis unavailable: %s", exc)
        return {"success": False, "engine": "macos_say", "error": "macOS say failed"}
    finally:
        if temp_path:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


async def synthesize_audio(req: TTSRequest) -> Dict[str, Any]:
    """Use the configured server provider for one playback chunk.

    Browser speech is intentionally represented as a fallback instruction rather
    than synthesized here: on a phone, the browser must own that fallback voice.
    """
    provider = provider_for_request(req)
    if provider == "chatterbox":
        result = await synthesize_chatterbox(req)
        if result.get("success"):
            return result
        if TTS_FALLBACK_PROVIDER == "macos_say":
            fallback = await synthesize_macos_audio(req)
            if fallback.get("success"):
                return fallback
        result["fallback"] = "browser"
        return result
    if provider == "macos_say":
        return await synthesize_macos_audio(req)
    return {
        "success": False,
        "engine": "browser",
        "fallback": "browser",
        "error": "Browser speech is a client-side fallback",
    }
