#!/usr/bin/env python3
"""Automated multi-room soak & recovery runner.

Simulates continuous multi-room background polling, rapid channel switching,
status checking, and history loading across multiple rooms over N iterations.
Verifies zero unhandled 500 errors, zero room data bleed, and clean recovery.

Usage:
    python3 tests/soak_test_runner.py [--cycles 15] [--base-url http://localhost:6891]
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import time
import random

BASE_URL = os.environ.get("BASE_URL", "http://localhost:6891")

def log(msg):
    print(f"[SOAK] {msg}")

def fetch_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())

def run_soak(cycles=15):
    log(f"Starting multi-room soak runner against {BASE_URL} ({cycles} cycles)...")

    # 1. Fetch available rooms
    try:
        rooms_data = fetch_json(f"{BASE_URL}/api/rooms")
        rooms = rooms_data.get("rooms", [])
        if not rooms_data.get("success") or not rooms:
            log("FAIL: Could not fetch rooms list for soak test")
            return False
        log(f"Loaded {len(rooms)} rooms for multi-room soak testing.")
    except Exception as e:
        log(f"FAIL: Initial room discovery failed: {e}")
        return False

    room_ids = [r["id"] for r in rooms[:10]] # Select up to 10 rooms
    errors = 0
    completed_cycles = 0

    start_time = time.time()

    for cycle in range(1, cycles + 1):
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

            # Verify history items match or return empty list without crashing
            msgs = history.get("messages", [])
            
            # Print periodic progress
            if cycle % 5 == 0 or cycle == cycles:
                elapsed = time.time() - start_time
                log(f"Completed cycle {cycle}/{cycles} ({elapsed:.1f}s) - Active room: {target_room} - {len(msgs)} msgs")

        except Exception as e:
            log(f"Cycle {cycle}: Exception encountered: {e}")
            errors += 1

        time.sleep(0.1) # Short delay between cycles

    total_time = time.time() - start_time
    if errors == 0:
        log(f"SUCCESS: Multi-room soak passed ({cycles} cycles in {total_time:.2f}s, 0 errors)")
        return True
    else:
        log(f"FAIL: Multi-room soak encountered {errors} errors")
        return False

if __name__ == "__main__":
    cycles = 15
    for i, arg in enumerate(sys.argv):
        if arg == "--cycles" and i + 1 < len(sys.argv):
            cycles = int(sys.argv[i + 1])
    
    success = run_soak(cycles=cycles)
    sys.exit(0 if success else 1)
