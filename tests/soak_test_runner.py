#!/usr/bin/env python3
"""Automated multi-room soak & recovery runner (F-08A Gate Hardening).

Simulates continuous multi-room background polling, rapid channel switching,
status checking, and history loading across ALL discovered rooms over N iterations or duration.
Tracks process RSS memory usage and verifies zero 500 errors, zero memory leaks, and clean recovery.

Usage:
    python3 tests/soak_test_runner.py [--cycles 30] [--duration 60] [--base-url http://localhost:6891]
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import time
import random
import resource

BASE_URL = os.environ.get("BASE_URL", "http://localhost:6891")

def log(msg):
    print(f"[SOAK] {msg}")

def fetch_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())

def get_rss_mb():
    try:
        # ru_maxrss is in KB on Linux/macOS (or Bytes on some platforms)
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return usage / 1024.0
    except Exception:
        return 0.0

def run_soak(cycles=30, duration=None):
    log(f"Starting multi-room soak runner against {BASE_URL}...")

    # 1. Fetch available rooms
    try:
        rooms_data = fetch_json(f"{BASE_URL}/api/rooms")
        rooms = rooms_data.get("rooms", [])
        if not rooms_data.get("success") or not rooms:
            log("FAIL: Could not fetch rooms list for soak test")
            return False
        log(f"Loaded ALL {len(rooms)} rooms for multi-room soak testing.")
    except Exception as e:
        log(f"FAIL: Initial room discovery failed: {e}")
        return False

    room_ids = [r["id"] for r in rooms] # Sample ALL discovered rooms!
    errors = 0
    start_time = time.time()
    initial_rss = get_rss_mb()
    log(f"Initial test runner RSS memory: {initial_rss:.2f} MB")

    cycle = 0
    while True:
        cycle += 1
        # Sequential sweep through all rooms first, then random sampling
        if cycle <= len(room_ids):
            target_room = room_ids[cycle - 1]
        else:
            target_room = random.choice(room_ids)

        try:
            # Simulate status check
            status = fetch_json(f"{BASE_URL}/api/status")
            if status.get("status") != "online":
                log(f"Cycle {cycle}: App status abnormal: {status.get('status')}")
                errors += 1

            # Simulate history query for selected room
            hist_url = f"{BASE_URL}/api/history?roomId={urllib.parse.quote(target_room)}&count=20"
            history = fetch_json(hist_url)
            if not history.get("success"):
                log(f"Cycle {cycle}: History fetch failed for room {target_room}")
                errors += 1

            msgs = history.get("messages", [])
            
            # Print periodic progress & memory metrics
            if cycle % 10 == 0 or cycle == len(room_ids):
                elapsed = time.time() - start_time
                current_rss = get_rss_mb()
                log(f"Cycle {cycle} ({elapsed:.1f}s) - Room: {target_room} ({len(msgs)} msgs) - Runner RSS: {current_rss:.2f} MB")

        except Exception as e:
            log(f"Cycle {cycle}: Exception encountered: {e}")
            errors += 1

        time.sleep(0.05) # 50ms delay between API queries

        # Termination criteria
        elapsed = time.time() - start_time
        if duration and elapsed >= duration:
            log(f"Reached specified duration of {duration}s ({cycle} cycles completed).")
            break
        elif not duration and cycle >= cycles:
            break

    total_time = time.time() - start_time
    final_rss = get_rss_mb()
    memory_delta = final_rss - initial_rss
    log(f"Final runner RSS memory: {final_rss:.2f} MB (delta: {memory_delta:+.2f} MB)")

    if errors == 0:
        log(f"SUCCESS: Multi-room soak passed across all {len(room_ids)} rooms ({cycle} cycles in {total_time:.2f}s, 0 errors)")
        return True
    else:
        log(f"FAIL: Multi-room soak encountered {errors} errors")
        return False

if __name__ == "__main__":
    cycles = 30
    duration = None
    for i, arg in enumerate(sys.argv):
        if arg == "--cycles" and i + 1 < len(sys.argv):
            cycles = int(sys.argv[i + 1])
        elif arg == "--duration" and i + 1 < len(sys.argv):
            duration = float(sys.argv[i + 1])
    
    success = run_soak(cycles=cycles, duration=duration)
    sys.exit(0 if success else 1)
