import sys
import time
import subprocess
import urllib.request
import json

def query_endpoint(url):
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5.0) as response:
            return response.status, json.loads(response.read().decode())
    except Exception as e:
        return 500, {"error": str(e)}

def run_soak():
    print("==================================================")
    print("🚀 Starting F-02A Multi-Restart Verification Soak")
    print("==================================================")
    
    for cycle in range(1, 6):
        print(f"\n🔄 [Cycle {cycle}/5] Restarting container...")
        restart_res = subprocess.run(["./restart.sh"], capture_output=True, text=True)
        if restart_res.returncode != 0:
            print(f"❌ Cycle {cycle} failed: restart.sh exited with {restart_res.returncode}")
            sys.exit(1)
            
        print("Waiting for FastAPI server to boot up...")
        # Poll status until online or max 30 seconds
        status = 500
        data = {}
        for attempt in range(30):
            time.sleep(1.0)
            status, data = query_endpoint("http://localhost:6891/api/status")
            if status == 200 and data.get("status") == "online":
                break
                
        if status != 200 or data.get("status") != "online":
            print(f"❌ Cycle {cycle} failed: /api/status did not come online. Data: {data}")
            sys.exit(1)
            
        print("✅ Status is online. Querying rooms list...")
        status_rooms, rooms_data = query_endpoint("http://localhost:6891/api/rooms")
        if status_rooms != 200 or not rooms_data.get("success"):
            print(f"❌ Cycle {cycle} failed: /api/rooms failed. Data: {rooms_data}")
            sys.exit(1)
            
        print("✅ Rooms loaded successfully. Querying history for active room...")
        active_room_id = data["rocket_chat"]["room_id"]
        status_hist, hist_data = query_endpoint(f"http://localhost:6891/api/history?roomId={active_room_id}&count=5")
        if status_hist != 200 or not hist_data.get("success"):
            print(f"❌ Cycle {cycle} failed: /api/history failed. Data: {hist_data}")
            sys.exit(1)
            
        print(f"🎉 Cycle {cycle} completed successfully! Messages count: {len(hist_data.get('messages', []))}")
        
    print("\n==================================================")
    print("🏆 F-02A Multi-Restart Verification Soak PASSED!")
    print("==================================================")

if __name__ == "__main__":
    run_soak()
