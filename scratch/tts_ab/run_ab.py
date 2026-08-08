"""U-08 quality/latency A/B for Ed's subjective listen.

Generates the same digest-length narration three ways and records
time-to-first-audio for each, which is the number that decides the U-09 design:

  1. chatterbox_whole  - one blocking synthesis of the full paragraph
  2. chatterbox_chunked - sentence-by-sentence, as U-09 would have to stream it
  3. macos_say_<voice> - the cheap fallback already wired in app/tts_adapter.py

Nothing here touches the product tree. Audio lands in ./out only.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
BASE = "http://127.0.0.1:8765"
MODEL = "mlx-community/chatterbox-turbo-8bit"


def post_speech(text: str, dest: Path) -> float:
    """POST one synthesis request; return wall-clock seconds."""
    # wav avoids the ffmpeg dependency mp3/flac encoding requires.
    body = json.dumps(
        {"model": MODEL, "input": text, "voice": "default", "response_format": "wav"}
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/audio/speech",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        audio = resp.read()
    elapsed = time.perf_counter() - start
    dest.write_bytes(audio)
    return elapsed


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


def concat_wavs(parts: list[Path], dest: Path) -> None:
    with wave.open(str(dest), "wb") as out:
        for i, part in enumerate(parts):
            with wave.open(str(part)) as w:
                if i == 0:
                    out.setparams(w.getparams())
                out.writeframes(w.readframes(w.getnframes()))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    text = (HERE / "digest_fixture.txt").read_text().strip()
    results: list[dict] = []

    # Warm the model so the cold-load cost is reported separately, not folded
    # into the whole-paragraph number.
    warm_secs = post_speech("Warm up.", OUT / "_warmup.wav")
    print(f"cold/warm-up request: {warm_secs:.2f}s")

    whole = OUT / "chatterbox_whole.wav"
    whole_secs = post_speech(text, whole)
    whole_dur = wav_duration(whole)
    results.append(
        {
            "variant": "chatterbox_whole",
            "time_to_first_audio_s": round(whole_secs, 2),
            "total_synth_s": round(whole_secs, 2),
            "audio_s": round(whole_dur, 2),
            "rtf": round(whole_secs / whole_dur, 3) if whole_dur else None,
        }
    )
    print(f"chatterbox whole: {whole_secs:.2f}s synth -> {whole_dur:.2f}s audio")

    parts: list[Path] = []
    first_chunk_secs = None
    chunk_total = 0.0
    for i, sent in enumerate(sentences(text)):
        part = OUT / f"_chunk_{i:02d}.wav"
        secs = post_speech(sent, part)
        chunk_total += secs
        if first_chunk_secs is None:
            first_chunk_secs = secs
        parts.append(part)
        print(f"  chunk {i}: {secs:.2f}s ({wav_duration(part):.2f}s audio)")
    chunked = OUT / "chatterbox_chunked.wav"
    concat_wavs(parts, chunked)
    results.append(
        {
            "variant": "chatterbox_chunked",
            "time_to_first_audio_s": round(first_chunk_secs or 0.0, 2),
            "total_synth_s": round(chunk_total, 2),
            "audio_s": round(wav_duration(chunked), 2),
            "chunks": len(parts),
        }
    )

    for voice in ("Samantha", "Ava (Premium)", "Zoe (Premium)"):
        dest = OUT / f"macos_say_{voice.split()[0].lower()}.aiff"
        start = time.perf_counter()
        proc = subprocess.run(
            ["say", "-v", voice, "-r", "200", "-o", str(dest), text],
            capture_output=True,
            text=True,
        )
        secs = time.perf_counter() - start
        if proc.returncode != 0:
            print(f"say {voice}: unavailable ({proc.stderr.strip()[:80]})")
            continue
        results.append(
            {
                "variant": f"macos_say_{voice.split()[0].lower()}",
                "file_render_s": round(secs, 2),
                "note": "live playback starts in <1s; file render is not the latency",
            }
        )
        print(f"say {voice}: rendered in {secs:.2f}s")

    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print("\nwrote", OUT / "results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
