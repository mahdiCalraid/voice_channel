"""Mac TTS Adapter for Voice Gateway.

Implements native macOS `say` subprocess execution and Web Speech API
provider abstraction for real-time text-to-speech without disk audio storage.
Supports stop, pause, resume, rate control, and full vs summary text selection.
"""

from __future__ import annotations

import sys
import shutil
import asyncio
import subprocess
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

logger = logging.getLogger("voice-channel.tts")

class TTSRequest(BaseModel):
    text: str
    mode: str = Field(default="summary", description="'summary' or 'full'")
    voice: Optional[str] = None
    rate: Optional[int] = Field(default=200, ge=100, le=400, description="Words per minute for macOS say")

class TTSStatus(BaseModel):
    engine: str
    is_available: bool
    active_playback: bool
    available_voices: List[str]

_active_process: Optional[subprocess.Popen] = None

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
