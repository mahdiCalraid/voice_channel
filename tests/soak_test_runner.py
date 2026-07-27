#!/usr/bin/env python3
"""Rocket.Chat API multi-room hammer.

This is a live API regression check, not a browser endurance or memory-leak test. It
checks that the console stays online and returns correctly scoped, chronological
history while cycling through every discovered Rocket.Chat room.

Optional container RSS snapshots are observational only. They are never treated as
proof that the application is leak-free.

Usage:
    python3 tests/soak_test_runner.py --cycles 30
    python3 tests/soak_test_runner.py --duration 900 --interval 1
    python3 tests/soak_test_runner.py --container voice-channel-console
"""

import argparse
import json
import os
import random
import re
import subprocess
import time
import urllib.parse
import urllib.request


BASE_URL = os.environ.get("BASE_URL", "http://localhost:6891")


def log(message):
    print(f"[SOAK] {message}")


def fetch_json(url):
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


def container_rss_bytes(container):
    """Return the Docker container's current memory use when Docker is available."""
    if not container:
        return None
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        usage = result.stdout.strip().split(" / ", 1)[0]
        units = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3}
        match = re.fullmatch(r"([0-9.]+)\s*(B|KiB|MiB|GiB)", usage)
        if not match:
            return None
        number, unit = match.groups()
        return int(float(number) * units[unit.lower()])
    except (FileNotFoundError, IndexError, KeyError, subprocess.SubprocessError, ValueError):
        return None


def format_bytes(value):
    if value is None:
        return "unavailable"
    return f"{value / (1024 ** 2):.1f} MiB"


def history_is_chronological(messages):
    timestamps = [message.get("timestamp", "") for message in messages]
    return timestamps == sorted(timestamps)


def run_soak(cycles=30, duration=None, interval=1.0, container=None):
    """Run the API hammer and return True only when all checked API invariants hold."""
    log(f"Starting API multi-room hammer against {BASE_URL}...")
    try:
        rooms_data = fetch_json(f"{BASE_URL}/api/rooms")
        rooms = rooms_data.get("rooms", [])
        if not rooms_data.get("success") or not rooms:
            log("FAIL: Could not fetch rooms list")
            return False
    except Exception as error:
        log(f"FAIL: Initial room discovery failed: {error}")
        return False

    room_ids = [room["id"] for room in rooms]
    errors = 0
    cycle = 0
    start_time = time.monotonic()
    initial_container_rss = container_rss_bytes(container)
    if container:
        log(f"Container RSS at start: {format_bytes(initial_container_rss)} (observational only)")
    log(f"Loaded {len(room_ids)} rooms; this check exercises API responses, not browser UI state.")

    while True:
        cycle += 1
        target_room = room_ids[(cycle - 1) % len(room_ids)] if cycle <= len(room_ids) else random.choice(room_ids)
        try:
            status = fetch_json(f"{BASE_URL}/api/status")
            if status.get("status") != "online" or status.get("rocket_chat", {}).get("status") != "connected":
                log(f"Cycle {cycle}: unhealthy status response")
                errors += 1

            history_url = f"{BASE_URL}/api/history?roomId={urllib.parse.quote(target_room)}&count=20"
            history = fetch_json(history_url)
            messages = history.get("messages", [])
            if not history.get("success") or history.get("room_id") != target_room:
                log(f"Cycle {cycle}: history response did not match requested room {target_room}")
                errors += 1
            elif not history_is_chronological(messages):
                log(f"Cycle {cycle}: history response was not chronological for {target_room}")
                errors += 1

            if cycle % 10 == 0 or cycle == len(room_ids):
                elapsed = time.monotonic() - start_time
                log(f"Cycle {cycle} ({elapsed:.1f}s): {target_room}, {len(messages)} messages")
        except Exception as error:
            log(f"Cycle {cycle}: exception: {error}")
            errors += 1

        elapsed = time.monotonic() - start_time
        if duration is not None and elapsed >= duration:
            log(f"Reached requested duration of {duration:.0f}s after {cycle} cycles.")
            break
        if duration is None and cycle >= cycles:
            break
        time.sleep(interval)

    total_time = time.monotonic() - start_time
    final_container_rss = container_rss_bytes(container)
    if container:
        log(
            "Container RSS: "
            f"{format_bytes(initial_container_rss)} -> {format_bytes(final_container_rss)} "
            "(observational only; this runner does not make memory-leak claims)"
        )

    if errors:
        log(f"FAIL: API multi-room hammer found {errors} error(s)")
        return False
    log(f"SUCCESS: API multi-room hammer completed {cycle} cycles across {len(room_ids)} rooms in {total_time:.1f}s")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Voice Channel API multi-room hammer.")
    parser.add_argument("--cycles", type=int, default=30, help="cycles when --duration is omitted")
    parser.add_argument("--duration", type=float, help="run for this many seconds instead of a fixed cycle count")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between cycles (default: 1)")
    parser.add_argument(
        "--container",
        default=os.environ.get("SOAK_CONTAINER"),
        help="optional Docker container name for observational RSS snapshots",
    )
    arguments = parser.parse_args()
    raise SystemExit(
        0
        if run_soak(
            cycles=arguments.cycles,
            duration=arguments.duration,
            interval=arguments.interval,
            container=arguments.container,
        )
        else 1
    )
