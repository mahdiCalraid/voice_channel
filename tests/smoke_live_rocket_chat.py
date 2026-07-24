#!/usr/bin/env python3
"""Live Rocket.Chat integration smoke test.

Opt-in test script that queries live endpoints, verifies Rocket.Chat connectivity,
fetches channel history, and tests nonce deduplication without requiring manual interpretation.

Usage:
    python3 tests/smoke_live_rocket_chat.py [--live-send]
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import time

BASE_URL = os.environ.get("BASE_URL", "http://localhost:6891")

def log(msg):
    print(f"[SMOKE] {msg}")

def run_smoke_test(allow_live_send=False):
    log(f"Starting live Rocket.Chat integration smoke test against {BASE_URL}...")
    
    # Step 1: Health / Status & Asset Route Check
    status_url = f"{BASE_URL}/api/status"
    try:
        req = urllib.request.Request(status_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") != "online":
                log(f"FAIL: App status is '{data.get('status')}', expected 'online'")
                return False
            rc_status = data.get("rocket_chat", {}).get("status")
            if rc_status != "connected":
                log(f"FAIL: Rocket.Chat status is '{rc_status}', expected 'connected'")
                return False
            log(f"PASS: App online & Rocket.Chat connected (user: {data.get('rocket_chat', {}).get('user')})")
    except Exception as e:
        log(f"FAIL: Health check request failed: {e}")
        return False

    # Step 1b: Verify Static Asset Route (/history_state.js)
    asset_url = f"{BASE_URL}/history_state.js"
    try:
        req = urllib.request.Request(asset_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                log(f"FAIL: /history_state.js status is {resp.status}, expected 200")
                return False
            content = resp.read().decode()
            if "createRoomState" not in content:
                log("FAIL: /history_state.js content does not contain VoiceChannelHistoryState definitions")
                return False
            log(f"PASS: /history_state.js served cleanly (200 OK, {len(content)} bytes)")
    except Exception as e:
        log(f"FAIL: /history_state.js asset check failed: {e}")
        return False

    # Step 2: Room Discovery Check
    rooms_url = f"{BASE_URL}/api/rooms"
    target_room_id = os.environ.get("SMOKE_ROOM_ID")
    try:
        with urllib.request.urlopen(urllib.request.Request(rooms_url), timeout=5) as resp:
            data = json.loads(resp.read().decode())
            rooms = data.get("rooms", [])
            if not data.get("success") or len(rooms) == 0:
                log("FAIL: No rooms returned from /api/rooms")
                return False
            if not target_room_id:
                target_room_id = rooms[0]["id"]
            matched = next((r for r in rooms if r["id"] == target_room_id), rooms[0])
            log(f"PASS: Discovered {len(rooms)} rooms. Target room: #{matched['name']} ({target_room_id})")
    except Exception as e:
        log(f"FAIL: Room discovery request failed: {e}")
        return False

    # Step 3: History & Cursor Fetch Check
    history_url = f"{BASE_URL}/api/history?roomId={urllib.parse.quote(target_room_id)}&count=10"
    try:
        with urllib.request.urlopen(urllib.request.Request(history_url), timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if not data.get("success"):
                log("FAIL: /api/history reported success=false")
                return False
            msgs = data.get("messages", [])
            log(f"PASS: Retrieved {len(msgs)} messages from history endpoint (has_more: {data.get('has_more')})")
    except Exception as e:
        log(f"FAIL: History fetch request failed: {e}")
        return False

    # Step 4: Nonce Deduplication / Outbound Path Check
    nonce = f"smoke_nonce_{int(time.time())}"
    send_payload = json.dumps({
        "roomId": target_room_id,
        "text": "[SMOKE_TEST] Automated health check message",
        "nonce": nonce
    }).encode("utf-8")

    send_url = f"{BASE_URL}/api/send"
    if allow_live_send:
        try:
            req = urllib.request.Request(send_url, data=send_payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                res1 = json.loads(resp.read().decode())
                if not res1.get("success"):
                    log("FAIL: Send endpoint reported success=false")
                    return False
                log(f"PASS: Live message sent successfully (msgId: {res1.get('msgId')})")
                
            # Verify immediate duplicate nonce returns cached success
            req2 = urllib.request.Request(send_url, data=send_payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req2, timeout=5) as resp2:
                res2 = json.loads(resp2.read().decode())
                if res2.get("msgId") != res1.get("msgId"):
                    log("FAIL: Duplicate nonce did not return cached result")
                    return False
                log("PASS: Nonce deduplication verified on live server.")
        except Exception as e:
            log(f"FAIL: Send test failed: {e}")
            return False
    else:
        log("SKIP: Live message transmission (run with --live-send to execute outbound post)")

    log("SUCCESS: All Rocket.Chat smoke test checks passed!")
    return True

if __name__ == "__main__":
    live_send = "--live-send" in sys.argv
    success = run_smoke_test(allow_live_send=live_send)
    sys.exit(0 if success else 1)
