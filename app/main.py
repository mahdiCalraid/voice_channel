import os
import time
import json
import logging
import re
import asyncio
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from openai import OpenAI

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-channel")

app = FastAPI(title="Voice Channel Console API")

@app.on_event("startup")
async def startup_cleanup():
    # Clean up orphan temporary summary files
    summary_dir = "acli/summary_history"
    if os.path.exists(summary_dir):
        for fname in os.listdir(summary_dir):
            if fname.endswith(".tmp"):
                try:
                    os.remove(os.path.join(summary_dir, fname))
                    logger.info(f"Cleaned orphan temp file: {fname}")
                except Exception as e:
                    logger.warning(f"Could not remove temp file {fname}: {e}")

# Configuration from environment variables
RC_URL = os.environ.get("RC_URL", "http://host.docker.internal:3000")
RC_USER = os.environ.get("RC_USER", "acli_bot")
RC_USER_ID = os.environ.get("RC_USER_ID", "acli_bot")
RC_AUTH_TOKEN = os.environ.get("RC_AUTH_TOKEN", "")
RC_ROOM_ID = os.environ.get("RC_ROOM_ID", "6a32407ea294f44649684786")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Fallback helper for local execution outside Docker / network resolution inside Docker
def get_rc_base_url() -> str:
    url = RC_URL
    # Check if we are running in docker
    in_docker = os.path.exists('/.dockerenv')
    if in_docker:
        if "localhost" in url:
            url = url.replace("localhost", "host.docker.internal")
            logger.info(f"Docker mode: replacing localhost with host.docker.internal -> {url}")
        elif "127.0.0.1" in url:
            url = url.replace("127.0.0.1", "host.docker.internal")
            logger.info(f"Docker mode: replacing 127.0.0.1 with host.docker.internal -> {url}")
    else:
        if "host.docker.internal" in url:
            try:
                import socket
                socket.gethostbyname("host.docker.internal")
            except socket.gaierror:
                url = url.replace("host.docker.internal", "localhost")
                logger.info(f"Host mode: host.docker.internal not resolvable, using {url}")
    return url

# Helper functions for dynamic message classification
def parse_datetime(ts_str: str) -> datetime:
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)

def parse_routing(text: str):
    agent_match = re.search(r"🔄 Routing to \*\*([a-zA-Z0-9_]+)\*\*", text)
    if not agent_match:
        return None
    agent = agent_match.group(1)
    
    model_details_match = re.search(r"\[model:\s*\`?([^\`\]]+)\`?(?:,\s*effort:\s*\`?([^\`\]]+)\`?)?\]", text)
    model_name = None
    effort = "low"
    if model_details_match:
        model_name = model_details_match.group(1).strip("` ")
        if model_details_match.group(2):
            effort = model_details_match.group(2).strip("` ")
            
    provider = "unknown"
    if model_name:
        lower_name = model_name.lower()
        if "gemini" in lower_name:
            provider = "google"
        elif "gpt" in lower_name or "o1" in lower_name or "o3" in lower_name or "o4" in lower_name:
            provider = "openai"
        elif "claude" in lower_name or "sonnet" in lower_name or "opus" in lower_name:
            provider = "anthropic"
        elif "grok" in lower_name:
            provider = "xai"
            
    return {
        "kind": "routing",
        "agent": agent,
        "model": {
            "provider": provider,
            "name": model_name,
            "effort": effort
        },
        "stopped": False,
        "raw_text": text
    }

def parse_heartbeat(text: str):
    match = re.search(r"⏱️ \*\*@([a-zA-Z0-9_]+)\*\* is still working \(Elapsed: ([^\)]+)\)", text)
    if not match:
        match = re.search(r"⏱️ \*\*@([a-zA-Z0-9_]+)\*\* is still working", text)
        if not match:
            return None
        agent = match.group(1)
        elapsed_str = None
    else:
        agent = match.group(1)
        elapsed_str = match.group(2)
        
    elapsed_seconds = None
    if elapsed_str:
        elapsed_str = elapsed_str.strip()
        seconds = 0
        s_match = re.search(r"(\d+)s", elapsed_str)
        if s_match:
            seconds += int(s_match.group(1))
        m_match = re.search(r"(\d+)m", elapsed_str)
        if m_match:
            seconds += int(m_match.group(1)) * 60
        h_match = re.search(r"(\d+)h", elapsed_str)
        if h_match:
            seconds += int(h_match.group(1)) * 3600
        elapsed_seconds = seconds
        
    return {
        "kind": "heartbeat",
        "agent": agent,
        "elapsed_seconds": elapsed_seconds,
        "stopped": False,
        "raw_text": text
    }

def parse_stopped(text: str):
    stop_match = re.search(r"!stop\s+([a-zA-Z0-9_]+)", text)
    agent = None
    if stop_match:
        agent = stop_match.group(1)
        return {
            "kind": "stopped",
            "agent": agent,
            "stopped": True,
            "raw_text": text
        }
        
    # Check for direct notice from dispatcher
    if text.strip().startswith("🛑") or "job stopped" in text.lower() or "cancelled job" in text.lower():
        agent_match = re.search(r"(?:🛑|stopped|cancelled)\s+\*?\*?@?([a-zA-Z0-9_]+)", text, re.IGNORECASE)
        agent = agent_match.group(1) if agent_match else None
        return {
            "kind": "stopped",
            "agent": agent,
            "stopped": True,
            "raw_text": text
        }
        
    return None

def parse_model_selected(text: str):
    # Match standard dispatcher model strings:
    # ℹ️ **grok** current model is `grok-4.5` with effort `low`
    # ✅ Set **grok** current model to `grok-4.5` with effort `high`.
    match1 = re.search(r"\*\*([a-zA-Z0-9_]+)\*\*\s+current model is\s+\`?([^\`\s]+)\`?(?:\s+with effort\s+\`?([^\`\s\.]+)\`?)?", text, re.IGNORECASE)
    match2 = re.search(r"Set\s+\*\*([a-zA-Z0-9_]+)\*\*\s+current model to\s+\`?([^\`\s]+)\`?(?:\s+with effort\s+\`?([^\`\s\.]+)\`?)?", text, re.IGNORECASE)
    
    match = match1 or match2
    
    if not match:
        # Fallback to the original matching patterns
        if "Model set to" in text or "Model for" in text:
            agent_match = re.search(r"for \*\*([a-zA-Z0-9_]+)\*\*", text)
            agent = agent_match.group(1) if agent_match else None
            model_match = re.search(r"set to \`?([^\`\s]+)\`?", text)
            model_name = model_match.group(1) if model_match else None
            provider = "unknown"
            if model_name:
                lower_name = model_name.lower()
                if "gemini" in lower_name: provider = "google"
                elif "gpt" in lower_name or "o1" in lower_name or "o3" in lower_name or "o4" in lower_name: provider = "openai"
                elif "claude" in lower_name or "sonnet" in lower_name or "opus" in lower_name: provider = "anthropic"
                elif "grok" in lower_name: provider = "xai"
            return {
                "kind": "model_selected",
                "agent": agent,
                "model": {
                    "provider": provider,
                    "name": model_name,
                    "effort": None
                } if model_name else None,
                "stopped": False,
                "raw_text": text
            }
        return None
        
    agent = match.group(1)
    model_name = match.group(2).strip("` ")
    effort = match.group(3).strip("` ") if match.group(3) else "low"
    
    provider = "unknown"
    if model_name:
        lower_name = model_name.lower()
        if "gemini" in lower_name:
            provider = "google"
        elif "gpt" in lower_name or "o1" in lower_name or "o3" in lower_name or "o4" in lower_name:
            provider = "openai"
        elif "claude" in lower_name or "sonnet" in lower_name or "opus" in lower_name:
            provider = "anthropic"
        elif "grok" in lower_name:
            provider = "xai"
            
    return {
        "kind": "model_selected",
        "agent": agent,
        "model": {
            "provider": provider,
            "name": model_name,
            "effort": effort
        },
        "stopped": False,
        "raw_text": text
    }

def parse_attachment(text: str):
    if "--- ATTACHED FILES ---" in text or "ATTACHED FILES" in text or "📎" in text or "attachment(s)" in text:
        count_match = re.search(r"(\d+)\s+attachment\(s\)", text)
        count = int(count_match.group(1)) if count_match else 1
        return {
            "kind": "attachment",
            "agent": None,
            "model": None,
            "elapsed_seconds": None,
            "stopped": False,
            "count": count,
            "raw_text": text
        }
    return None

def classify_message(msg: dict) -> dict:
    text = msg.get("msg", "")
    username = msg.get("u", {}).get("username", "unknown")
    t = msg.get("t")
    
    # 1. Rocket.Chat Native System Messages
    if t and t != "thread-message":
        return {
            "lane": "system",
            "event": {
                "kind": "membership",
                "agent": None,
                "model": None,
                "elapsed_seconds": None,
                "stopped": False,
                "raw_text": text
            }
        }
        
    # 2. ACLI Dispatcher System Messages & Agent Relays (usually sent by acli_bot)
    if username == "acli_bot":
        # CRITICAL: Match agent relays FIRST before any system/stopped/error rules
        agent_relay_match = re.match(r"\*\*@([a-zA-Z0-9_]+)\*\*:\s*(.*)", text, re.DOTALL)
        if agent_relay_match:
            agent = agent_relay_match.group(1)
            return {
                "lane": "agent",
                "event": {
                    "kind": "agent_response",
                    "agent": agent,
                    "model": None,
                    "elapsed_seconds": None,
                    "stopped": False,
                    "raw_text": text
                }
            }
            
        routing = parse_routing(text)
        if routing:
            return {"lane": "system", "event": routing}
            
        heartbeat = parse_heartbeat(text)
        if heartbeat:
            return {"lane": "system", "event": heartbeat}
            
        model_selected = parse_model_selected(text)
        if model_selected:
            return {"lane": "system", "event": model_selected}
            
        attachment = parse_attachment(text)
        if attachment:
            return {"lane": "system", "event": attachment}
            
        stopped = parse_stopped(text)
        if stopped:
            return {"lane": "system", "event": stopped}

        # Explicit dispatcher failure notices, e.g. "❌ **@agy** failed: ..."
        failed_match = re.search(r"❌\s+\*\*@([a-zA-Z0-9_]+)\*\*\s+failed", text)
        if failed_match:
            return {
                "lane": "system",
                "event": {
                    "kind": "error",
                    "agent": failed_match.group(1),
                    "model": None,
                    "elapsed_seconds": None,
                    "stopped": False,
                    "raw_text": text
                }
            }
            
        # Narrow error notices: warning emoji or known invalid-model phrasing only.
        # Do not match bare substring "error" (false-positives already fixed for relays;
        # still avoid over-matching remaining dispatcher text).
        if "⚠️" in text or "is not valid" in text.lower() or re.search(r"\binvalid\b", text, re.IGNORECASE):
            agent_match = re.search(r"for \*\*([a-zA-Z0-9_]+)\*\*", text)
            if not agent_match:
                agent_match = re.search(r"\*\*([a-zA-Z0-9_]+)\*\*", text)
            agent = agent_match.group(1) if agent_match else None
            return {
                "lane": "system",
                "event": {
                    "kind": "error",
                    "agent": agent,
                    "model": None,
                    "elapsed_seconds": None,
                    "stopped": False,
                    "raw_text": text
                }
            }
            
        if "established" in text.lower() or "started" in text.lower():
            return {
                "lane": "system",
                "event": {
                    "kind": "system_startup",
                    "agent": None,
                    "model": None,
                    "elapsed_seconds": None,
                    "stopped": False,
                    "raw_text": text
                }
            }
            
        # Default fallback for any remaining acli_bot message is system/other
        return {
            "lane": "system",
            "event": {
                "kind": "other",
                "agent": None,
                "model": None,
                "elapsed_seconds": None,
                "stopped": False,
                "raw_text": text
            }
        }
        
    # 3. User Messages (Ed)
    if username == "ed":
        return {
            "lane": "user",
            "event": {
                "kind": "user_message",
                "agent": None,
                "model": None,
                "elapsed_seconds": None,
                "stopped": False,
                "raw_text": text
            }
        }
        
    # 4. Direct Agent Messages
    active_agents = {"gemini", "agy", "codex", "claude", "pplx", "cursor", "grok"}
    if username in active_agents:
        return {
            "lane": "agent",
            "event": {
                "kind": "agent_response",
                "agent": username,
                "model": None,
                "elapsed_seconds": None,
                "stopped": False,
                "raw_text": text
            }
        }
        
    default_lane = "user" if username == "ed" else "agent"
    return {
        "lane": default_lane,
        "event": {
            "kind": "other",
            "agent": None,
            "model": None,
            "elapsed_seconds": None,
            "stopped": False,
            "raw_text": text
        }
    }

def process_history_messages(raw_messages: List[dict]) -> tuple[List[dict], dict]:
    cleaned_messages = []
    last_routing_ts = {}
    
    # Agent statistics tracking
    agent_stats = {}
    
    for msg in raw_messages:
        classification = classify_message(msg)
        lane = classification["lane"]
        event = classification["event"]
        
        text = msg.get("msg", "")
        is_routing = text.startswith("🔄 Routing to") or "Routing to" in text
        
        user = msg.get("u", {})
        username = user.get("username", "unknown")
        
        # Track message count per agent
        if lane == "agent":
            agent = event.get("agent")
            if agent:
                if agent not in agent_stats:
                    agent_stats[agent] = { "run_times": [], "msg_count": 0, "status": "idle", "working_start": None }
                agent_stats[agent]["msg_count"] += 1
        
        # Dynamic response time calculation & back-patching
        if lane == "system" and event.get("kind") == "routing":
            agent = event.get("agent")
            if agent:
                try:
                    ts = parse_datetime(msg.get("ts"))
                    
                    # Mark prior open routing for the same agent as superseded
                    if agent in last_routing_ts:
                        for prev_msg in reversed(cleaned_messages):
                            if prev_msg["lane"] == "system" and prev_msg["event"].get("kind") == "routing" and prev_msg["event"].get("agent") == agent:
                                if prev_msg["event"].get("status") == "working":
                                    prev_msg["event"]["status"] = "superseded"
                                    break
                    
                    last_routing_ts[agent] = ts
                    
                    # Initialize stats entry
                    if agent not in agent_stats:
                        agent_stats[agent] = { "run_times": [], "msg_count": 0, "status": "idle", "working_start": None }
                    agent_stats[agent]["status"] = "working"
                    agent_stats[agent]["working_start"] = ts
                    event["status"] = "working"
                except Exception:
                    pass
                    
        elif lane == "agent":
            agent = event.get("agent")
            if agent and agent in last_routing_ts:
                try:
                    resp_ts = parse_datetime(msg.get("ts"))
                    rout_ts = last_routing_ts[agent]
                    diff = (resp_ts - rout_ts).total_seconds()
                    event["response_time_seconds"] = diff
                    
                    # Update stats
                    if agent not in agent_stats:
                        agent_stats[agent] = { "run_times": [], "msg_count": 0, "status": "idle", "working_start": None }
                    agent_stats[agent]["run_times"].append(diff)
                    agent_stats[agent]["status"] = "idle"
                    agent_stats[agent]["working_start"] = None
                    
                    # Back-patch the routing message (find the last routing msg for this agent in cleaned_messages)
                    for prev_msg in reversed(cleaned_messages):
                        if prev_msg["lane"] == "system" and prev_msg["event"].get("kind") == "routing" and prev_msg["event"].get("agent") == agent:
                            if prev_msg["event"].get("status") == "working":
                                prev_msg["event"]["response_time_seconds"] = diff
                                prev_msg["event"]["status"] = "completed"
                                break
                            
                    del last_routing_ts[agent]
                except Exception as e:
                    logger.error(f"Error matching agent response: {e}")
                    
        elif lane == "system" and event.get("kind") in ("stopped", "error"):
            agent = event.get("agent")
            if agent:
                if agent not in agent_stats:
                    agent_stats[agent] = { "run_times": [], "msg_count": 0, "status": "idle", "working_start": None }
                agent_stats[agent]["status"] = "idle"
                agent_stats[agent]["working_start"] = None
                
                # Back-patch routing message as failed/stopped
                for prev_msg in reversed(cleaned_messages):
                    if prev_msg["lane"] == "system" and prev_msg["event"].get("kind") == "routing" and prev_msg["event"].get("agent") == agent:
                        if prev_msg["event"].get("status") == "working":
                            prev_msg["event"]["status"] = "stopped" if event.get("kind") == "stopped" else "failed"
                            break
                        
                if agent in last_routing_ts:
                    del last_routing_ts[agent]
                    
        # Create message object
        cleaned_messages.append({
            "id": msg.get("_id"),
            "username": username,
            "name": user.get("name", "Unknown"),
            "text": text,
            "timestamp": msg.get("ts"),
            "is_routing": is_routing,
            "lane": lane,
            "event": event
        })
    
    # Compute rolling summary statistics
    rolling_stats = {}
    for agent, stats in agent_stats.items():
        times = stats["run_times"]
        avg_time = sum(times) / len(times) if times else 0
        runs = len(times)
        
        # Check if still working and calculate current elapsed
        current_elapsed = 0
        if stats["status"] == "working" and stats["working_start"]:
            if raw_messages:
                try:
                    last_msg_ts = parse_datetime(raw_messages[-1].get("ts"))
                    current_elapsed = (last_msg_ts - stats["working_start"]).total_seconds()
                except Exception:
                    current_elapsed = (datetime.now(timezone.utc) - stats["working_start"]).total_seconds()
            else:
                current_elapsed = (datetime.now(timezone.utc) - stats["working_start"]).total_seconds()
                
        rolling_stats[agent] = {
            "runs": runs,
            "avg_response_time": round(avg_time, 1),
            "msg_count": stats["msg_count"],
            "status": stats["status"],
            "current_elapsed": round(current_elapsed, 1) if current_elapsed > 0 else 0
        }
        
    return cleaned_messages, rolling_stats

# Initialize OpenAI client if key is available
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("OpenAI Client initialized successfully.")
else:
    logger.warning("OPENAI_API_KEY not found in environment. Digest generation will use rule-based fallback.")

# Request models
class MessageSendRequest(BaseModel):
    text: str
    roomId: str
    nonce: Optional[str] = None

class DigestRequest(BaseModel):
    messages: List[dict]
    style: Optional[str] = "narrator" # e.g. "narrator", "brief", "detailed"
    roomId: Optional[str] = None

@app.get("/api/status")
async def get_status():
    base_url = get_rc_base_url()
    headers = {
        "X-Auth-Token": RC_AUTH_TOKEN,
        "X-User-Id": RC_USER_ID,
    }
    
    rc_status = "unknown"
    rc_username = None
    rc_error = None
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/api/v1/me", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    rc_status = "connected"
                    rc_username = data.get("username")
                else:
                    rc_status = "auth_failed"
                    rc_error = "API returned success=false"
            else:
                rc_status = "error"
                rc_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as e:
        rc_status = "disconnected"
        rc_error = str(e)
        
    # Check if codex is available & executes successfully
    codex_available = False
    codex_exec_works = False
    try:
        import shutil
        from pathlib import Path
        import subprocess
        codex_bin = None
        for candidate in ["codex", "/usr/local/bin/codex"]:
            if "/" in candidate:
                if Path(candidate).exists():
                    codex_bin = candidate
                    break
            else:
                if shutil.which(candidate):
                    codex_bin = candidate
                    break
        if codex_bin:
            auth_paths = ["/root/.codex/auth.json", os.path.expanduser("~/.codex/auth.json")]
            for p in auth_paths:
                if os.path.exists(p):
                    try:
                        with open(p, "r", encoding="utf-8") as f:
                            auth_data = json.load(f)
                            if isinstance(auth_data, dict) and len(auth_data) > 0:
                                codex_available = True
                                break
                    except Exception:
                        pass
            if codex_available:
                try:
                    res = subprocess.run([codex_bin, "--help"], capture_output=True, text=True, timeout=2.0)
                    if res.returncode in (0, 1, 64): # CLI ran successfully and returned help output
                        codex_exec_works = True
                except Exception:
                    pass
    except Exception:
        pass
        
    active_worker = os.environ.get("VC_WORKER", "codex")
    has_ai_worker = (codex_available and codex_exec_works) or (openai_client is not None)
    worker_status = "ready" if has_ai_worker else "degraded"
    
    # Verify app storage path health
    app_status = "healthy"
    try:
        os.makedirs("acli/summary_history", exist_ok=True)
        test_path = "acli/summary_history/.health_probe"
        with open(test_path, "w") as f:
            f.write("probe")
        os.remove(test_path)
    except Exception:
        app_status = "degraded"
        
    return {
        "status": "online",
        "openai_available": openai_client is not None,
        "codex_available": codex_available,
        "active_worker": active_worker,
        "app": {
            "status": app_status
        },
        "worker": {
            "status": worker_status,
            "configured": codex_available or (openai_client is not None),
            "active_worker": active_worker,
            "codex_available": codex_available,
            "openai_available": openai_client is not None
        },
        "rocket_chat": {
            "url": base_url,
            "status": rc_status,
            "user": RC_USER,
            "username": rc_username,
            "room_id": RC_ROOM_ID,
            "error": rc_error
        }
    }

@app.get("/api/rooms")
async def get_rooms():
    base_url = get_rc_base_url()
    headers = {
        "X-Auth-Token": RC_AUTH_TOKEN,
        "X-User-Id": RC_USER_ID,
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{base_url}/api/v1/rooms.get?count=100"
            resp = await client.get(url, headers=headers)
            
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"Rocket.Chat rooms error: {resp.text}")
                
            data = resp.json()
            if not data.get("success"):
                raise HTTPException(status_code=400, detail="Rocket.Chat rooms request failed")
                
            raw_rooms = data.get("update", [])
            
            # Format and filter rooms
            rooms = []
            for room in raw_rooms:
                room_type = room.get("t")
                if room_type in ("c", "p"):
                    rooms.append({
                        "id": room.get("_id"),
                        "name": room.get("name") or room.get("fname", "Unnamed"),
                        "type": room_type
                    })
            
            # Sort by name
            rooms.sort(key=lambda r: r["name"].lower())
            
            return {
                "success": True,
                "rooms": rooms
            }
            
    except Exception as e:
        logger.exception("Error fetching rooms")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history")
async def get_history(
    roomId: Optional[str] = None, 
    count: int = 30, 
    offset: int = 0, 
    latest: Optional[str] = None,
    before: Optional[str] = None
):
    base_url = get_rc_base_url()
    headers = {
        "X-Auth-Token": RC_AUTH_TOKEN,
        "X-User-Id": RC_USER_ID,
    }
    
    room_id = roomId or RC_ROOM_ID
    clamped_count = min(max(count, 1), 100)
    
    params = {
        "roomId": room_id,
        "count": clamped_count,
        "offset": offset
    }

    effective_latest = before or latest
    if effective_latest:
        params["latest"] = effective_latest
        params["inclusive"] = "true"

    last_error = None
    data = None
    
    # Retry loop with exponential backoff for transient failures
    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(3):
            try:
                # Try channels history first
                resp = await client.get(f"{base_url}/api/v1/channels.history", headers=headers, params=params)
                
                # If error (e.g. is private group), fall back to groups history
                if resp.status_code != 200:
                    resp = await client.get(f"{base_url}/api/v1/groups.history", headers=headers, params=params)
                    
                if resp.status_code == 200:
                    parsed = resp.json()
                    if parsed.get("success"):
                        data = parsed
                        break
                    else:
                        last_error = f"API returned success=false: {parsed}"
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
            except Exception as e:
                last_error = str(e)
                
            if attempt < 2:
                await asyncio.sleep(0.15 * (2 ** attempt))
                
    if not data:
        logger.error(f"Failed to fetch history for room {room_id}: {last_error}")
        raise HTTPException(status_code=502, detail=f"Rocket.Chat history request failed: {last_error}")

    raw_messages = data.get("messages", [])
    has_more = len(raw_messages) >= clamped_count
    
    # Rocket.Chat returns messages in reverse chronological order (newest first).
    # We reverse them first to process and compute stats chronologically.
    raw_messages.reverse()
    
    # Deduplicate raw_messages by message _id to avoid duplicates across page boundaries
    seen_ids = set()
    deduped_raw = []
    for msg in raw_messages:
        m_id = msg.get("_id")
        if m_id and m_id not in seen_ids:
            seen_ids.add(m_id)
            deduped_raw.append(msg)
    raw_messages = deduped_raw

    cleaned_messages, rolling_stats = process_history_messages(raw_messages)
    
    return {
        "success": True,
        "room_id": room_id,
        "count": len(cleaned_messages),
        "offset": offset,
        "has_more": has_more,
        # The client uses this opaque cursor instead of offset math in a live room.
        "next_before": raw_messages[0].get("ts") if raw_messages else None,
        "messages": cleaned_messages,
        "stats": rolling_stats
    }

def read_matter_docs() -> str:
    docs = []
    for name in ("NORTH_STAR.md", "OBJECTIVES.md", "PROJECT_HANDOFF.md"):
        path = name
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    docs.append(f"=== {name} ===\n{f.read()}")
            except Exception as e:
                logger.error(f"Error reading {name}: {e}")
    return "\n\n".join(docs)

def read_prior_summaries(room_id: str) -> List[dict]:
    os.makedirs("acli/summary_history", exist_ok=True)
    path = f"acli/summary_history/{room_id}.json"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading summaries for {room_id}: {e}")
    return []

def save_summary(room_id: str, digest: str):
    os.makedirs("acli/summary_history", exist_ok=True)
    path = f"acli/summary_history/{room_id}.json"
    tmp_path = f"acli/summary_history/{room_id}.json.tmp"
    summaries = read_prior_summaries(room_id)
    summaries.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "digest": digest
    })
    # Cap to last 3 summaries
    summaries = summaries[-3:]
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(summaries, f, indent=2)
        os.replace(tmp_path, path)
    except Exception as e:
        logger.error(f"Error saving summary for {room_id}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

@app.post("/api/digest")
async def generate_digest(req: DigestRequest):
    room_id = req.roomId or RC_ROOM_ID
    
    # Filter messages to only include Lane B (agent) and Lane C (user)
    lane_b_c = [m for m in req.messages if m.get("lane") in ("agent", "user")]
    included_message_ids = [m.get("id") for m in lane_b_c if m.get("id")]
    
    if not lane_b_c:
        return {
            "digest": "No new agent updates or user instructions in the channel.",
            "included_message_ids": [],
            "prior_summary_used": False
        }
        
    # Read matter documents and prior summary history
    matter_docs = read_matter_docs()
    prior_summaries = read_prior_summaries(room_id)
    prior_summary_used = len(prior_summaries) > 0
    
    # Filter system messages for Stage 1 Operational Stats
    system_msgs = [m for m in req.messages if m.get("lane") == "system"]
    
    # --- STEP 2: Job Builder ---
    import subprocess
    import sys
    import time
    import uuid
    
    job_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    job_dir = os.path.join("tmp", "jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    # Write inputs
    messages_path = os.path.join(job_dir, "messages_for_llm.json")
    system_events_path = os.path.join(job_dir, "system_events.json")
    matter_context_path = os.path.join(job_dir, "matter_context.md")
    task_instructions_path = os.path.join(job_dir, "task_instructions.md")
    room_context_path = os.path.join(job_dir, "room_context.json")
    job_path = os.path.join(job_dir, "job.json")
    
    with open(messages_path, "w", encoding="utf-8") as f:
        json.dump(lane_b_c, f, indent=2)
        
    with open(system_events_path, "w", encoding="utf-8") as f:
        json.dump(system_msgs, f, indent=2)
        
    with open(matter_context_path, "w", encoding="utf-8") as f:
        f.write(matter_docs)
        
    rules = (
        "1. Speak directly to Ed. Refer to him as 'Ed' or 'you'.\n"
        "2. Summarize what changed, what was completed, and what is currently blocked, relative to the project objectives.\n"
        "3. Explain how the recent chat relates to the prior summaries and the overall project goals (e.g. 'You asked Codex to do X, and it is now done. Next step is Y.').\n"
        "4. Keep it extremely crisp and concise (under 200 words). Skip all greeting/intro boilerplate.\n"
        "5. Do NOT include Markdown formatting like asterisks or hashtags since they will be read literally by the browser's TTS engine."
    )
    with open(task_instructions_path, "w", encoding="utf-8") as f:
        f.write(rules)
        
    room_ctx = {
        "room_id": room_id,
        "prior_summaries": prior_summaries
    }
    with open(room_context_path, "w", encoding="utf-8") as f:
        json.dump(room_ctx, f, indent=2)
        
    # Create job.json referencing relative paths inside job directory
    job_cfg = {
        "room_id": room_id,
        "model": None,
        "effort": "low",
        "input_files": {
            "messages": "messages_for_llm.json",
            "system_events": "system_events.json",
            "matter_context": "matter_context.md",
            "task_instructions": "task_instructions.md",
            "room_context": "room_context.json"
        }
    }
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job_cfg, f, indent=2)
        
    # --- STEP 3: Wire to run_worker.py ---
    worker_name = os.environ.get("VC_WORKER", "codex")
    
    # Run the worker script
    cmd = [
        sys.executable,
        "workers/run_worker.py",
        "digest",
        "--worker", worker_name,
        "--job", job_path
    ]
    
    success = False
    digest_text = None
    
    try:
        # Run worker with 30s timeout
        env = os.environ.copy()
        env["PYTHONPATH"] = env.get("PYTHONPATH", "") + ":" + os.getcwd()
        
        result_proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30
        )
        
        if result_proc.returncode == 0:
            result_json_path = os.path.join(job_dir, "result.json")
            if os.path.exists(result_json_path):
                with open(result_json_path, "r", encoding="utf-8") as f:
                    job_result = json.load(f)
                if job_result.get("ok"):
                    digest_text = job_result.get("output")
                    success = True
                    logger.info(f"Worker '{worker_name}' successfully generated digest.")
        else:
            logger.error(f"Worker execution failed: {result_proc.stderr}")
            
    except Exception as e:
        logger.exception("Error executing worker process")
        
    if success and digest_text:
        # Persist summary
        save_summary(room_id, digest_text)
        return {
            "digest": digest_text,
            "included_message_ids": included_message_ids,
            "prior_summary_used": prior_summary_used
        }
        
    # Rule-based fallback summary with agent attribution
    logger.warning("Worker failed or returned error. Falling back to rule-based summary.")
    summary_parts = ["Here is a quick summary of the recent updates:"]
    user_counts = {}
    for m in lane_b_c:
        # Prefer event.agent for Lane B
        agent_name = m.get("event", {}).get("agent") if m.get("lane") == "agent" else None
        author = agent_name or m.get("name") or m.get("username") or "Unknown"
        user_counts[author] = user_counts.get(author, 0) + 1
        
    for user, count in user_counts.items():
        summary_parts.append(f"{user} worked on {count} updates.")
        
    if lane_b_c:
        last_msg = lane_b_c[-1]
        agent_name = last_msg.get("event", {}).get("agent") if last_msg.get("lane") == "agent" else None
        last_user = agent_name or last_msg.get("name") or last_msg.get("username") or "Unknown"
        last_text = last_msg.get("text", "")
        if len(last_text) > 100:
            last_text = last_text[:100] + "..."
        summary_parts.append(f"The last update was from {last_user}, saying: {last_text}")
    else:
        summary_parts.append("No active agent replies or user requests in the current window.")
        
    fallback_digest = " ".join(summary_parts)
    
    # Persist fallback summary
    try:
        save_summary(room_id, fallback_digest)
    except Exception:
        pass
        
    return {
        "digest": fallback_digest,
        "included_message_ids": included_message_ids,
        "prior_summary_used": prior_summary_used
    }

# A simple thread-safe in-memory cache for nonces.
processed_nonces = {}
processed_nonces_lock = asyncio.Lock()

async def deduplicate_nonce(nonce: str, execute_func):
    """
    Checks if a nonce exists. If yes, returns the cached result.
    Otherwise, executes the function, stores, and returns the result.
    """
    async with processed_nonces_lock:
        now = time.time()
        # inline expiration check (10 mins)
        expired = [k for k, v in list(processed_nonces.items()) if now - v[0] > 600]
        for k in expired:
            processed_nonces.pop(k, None)
            
        if nonce in processed_nonces:
            val = processed_nonces[nonce][1]
            if val == "pending":
                raise HTTPException(status_code=409, detail="Request is already being processed")
            logger.info(f"Duplicate request detected for nonce: {nonce}. Returning cached response.")
            return val
            
        # Mark as pending
        processed_nonces[nonce] = (now, "pending")
        
    try:
        res = await execute_func()
        async with processed_nonces_lock:
            processed_nonces[nonce] = (time.time(), res)
        return res
    except Exception as e:
        async with processed_nonces_lock:
            if processed_nonces.get(nonce) and processed_nonces[nonce][1] == "pending":
                processed_nonces.pop(nonce, None)
        raise e

@app.post("/api/send")
async def send_message(req: MessageSendRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Message text cannot be empty")
        
    async def _do_send():
        base_url = get_rc_base_url()
        headers = {
            "X-Auth-Token": RC_AUTH_TOKEN,
            "X-User-Id": RC_USER_ID,
            "Content-Type": "application/json"
        }
        
        payload = {
            "roomId": req.roomId,
            "text": req.text
        }
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base_url}/api/v1/chat.postMessage", headers=headers, json=payload)
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=f"Rocket.Chat post error: {resp.text}")
                    
                data = resp.json()
                if not data.get("success"):
                    raise HTTPException(status_code=400, detail="Rocket.Chat post message failed")
                    
                msg_data = data.get("message", {})
                msg_id = msg_data.get("_id")
                return {
                    "success": True, 
                    "msgId": msg_id,
                    "message": msg_data
                }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Error posting message")
            raise HTTPException(status_code=500, detail=str(e))

    if req.nonce:
        async with processed_nonces_lock:
            if req.nonce in processed_nonces:
                entry = processed_nonces[req.nonce]
                if entry[1] == "pending":
                    raise HTTPException(status_code=409, detail="Request is already being processed")
                return entry[1]
                
        return await deduplicate_nonce(req.nonce, _do_send)
    else:
        return await _do_send()

# Route to serve frontend assets
@app.get("/")
async def get_index():
    return FileResponse("frontend/index.html")

@app.get("/index.css")
async def get_css():
    return FileResponse("frontend/index.css")

@app.get("/index.js")
async def get_js():
    return FileResponse("frontend/index.js")

@app.get("/history_state.js")
async def get_history_state_js():
    return FileResponse("frontend/history_state.js")
