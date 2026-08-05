import os
import sys
import time
import json
import ast
import logging
import re
import asyncio
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx
from openai import OpenAI
from app.contracts import (
    AttentionState,
    CURRENT_SCHEMA_VERSION,
    InputMode,
    PermissionTier,
    TaskState,
    InteractionRequest,
    Interpretation,
    ConfirmationSnapshot,
    TaskEvent,
    TaskRecord,
    GatewayResult,
)
from app.tts_adapter import (
    TTSRequest,
    TTSStatus,
    get_system_voices,
    stop_tts,
    is_active_playback,
    speak_text,
    synthesize_audio,
    chatterbox_health,
    configured_provider,
    TTS_FALLBACK_PROVIDER,
)
from app.task_supervisor import TaskSupervisor
from app.rc_ingress import IngressConfigurationError, build_ingress_message, extract_interaction_id
from app.supervision_strategy import (
    build_strategy_context,
    load_channel_registry,
    normalize_narrator_summary,
    parse_response_assistant_output,
    read_project_context,
    resolve_channel_profile,
)
from app.attention_config import (
    ChannelAttentionConfig,
    ChannelAttentionEntry,
    load_channel_attention_config,
    save_channel_attention_config,
)
from app.attention_scoring import build_attention_queue

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

    # Clean up orphan temporary job directories
    jobs_dir = os.path.join("tmp", "jobs")
    if os.path.exists(jobs_dir):
        try:
            shutil.rmtree(jobs_dir, ignore_errors=True)
            logger.info("Cleaned orphan temporary job directories.")
        except Exception as e:
            logger.warning(f"Could not remove temp job dir: {e}")

# Configuration from environment variables
RC_URL = os.environ.get("RC_URL", "http://host.docker.internal:3000")
RC_USER = os.environ.get("RC_USER", "acli_bot")
RC_USER_ID = os.environ.get("RC_USER_ID", "acli_bot")
RC_AUTH_TOKEN = os.environ.get("RC_AUTH_TOKEN", "")
RC_ROOM_ID = os.environ.get("RC_ROOM_ID", "6a32407ea294f44649684786")
GATEWAY_RC_USER_ID = os.environ.get("GATEWAY_RC_USER_ID", "")
GATEWAY_RC_AUTH_TOKEN = os.environ.get("GATEWAY_RC_AUTH_TOKEN", "")
GATEWAY_RC_INGRESS_SECRET = os.environ.get("GATEWAY_RC_INGRESS_SECRET", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GATEWAY_TASK_STATE_PATH = os.environ.get("GATEWAY_TASK_STATE_PATH", "acli/gateway_state/tasks.json")
task_supervisor = TaskSupervisor(GATEWAY_TASK_STATE_PATH)

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


def timestamp_to_epoch(timestamp: Optional[str]) -> float:
    if not timestamp:
        return time.time()
    try:
        return parse_datetime(timestamp).timestamp()
    except (TypeError, ValueError):
        return time.time()

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
        "raw_text": text,
        "interaction_id": extract_interaction_id(text),
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

    # A gateway envelope is an auditable dispatch request, not an agent reply.
    # This classification is display-only; ACLI performs the actual signature check.
    if text.startswith("[voice-gateway/v1;"):
        return {
            "lane": "system",
            "event": {
                "kind": "gateway_dispatch",
                "agent": None,
                "model": None,
                "elapsed_seconds": None,
                "stopped": False,
                "raw_text": text,
            },
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

        # Explicit dispatcher failure notices, including the timeout form ACLI
        # emits without the usual ❌ prefix.  Timeout notices are terminal: if
        # they remain ordinary text, the attention rail can show Busy forever.
        failed_match = re.search(
            r"❌\s+\*\*@([a-zA-Z0-9_]+)\*\*\s+failed"
            r"|\*\*@([a-zA-Z0-9_]+)\*\*\s+hit\s+the\s+\d+s\s+timeout",
            text,
            re.IGNORECASE,
        )
        if failed_match:
            agent = failed_match.group(1) or failed_match.group(2)
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
        interaction_id = extract_interaction_id(msg.get("msg", ""))
        if interaction_id:
            event["interaction_id"] = interaction_id
        
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
    history_limit: Optional[int] = 20


class ResponseAssistantRequest(DigestRequest):
    room_name: Optional[str] = None
    trigger_message_id: Optional[str] = None


# Automatic response assistance is idempotent for a bounded window.
# DEPLOYMENT CONSTRAINT: The idempotency cache is deliberately process-local:
# the console runs in a single API container process (single worker process).
# This prevents duplicate model execution and saves when multiple browser tabs
# race or a client retries. Any multi-replica or multi-worker deployment scaling
# must replace this with a shared backing store (e.g. Redis).
#
# TIMEOUT BUDGET INVARIANT: The frontend automatic assistance lease duration
# (AUTOMATIC_ASSISTANCE_LEASE_MS = 90s, refreshed every 15s in-flight) strictly
# exceeds the maximum backend generation budget (45s worker timeout + 10s history fetch).
# Manual requests omit trigger_message_id and intentionally bypass this cache.
RESPONSE_ASSISTANT_IDEMPOTENCY_TTL_SECONDS = int(
    os.environ.get("VC_RESPONSE_ASSISTANT_IDEMPOTENCY_TTL_SECONDS", "3600")
)
RESPONSE_ASSISTANT_IDEMPOTENCY_MAX_ENTRIES = int(
    os.environ.get("VC_RESPONSE_ASSISTANT_IDEMPOTENCY_MAX_ENTRIES", "1024")
)
response_assistant_idempotency_lock = asyncio.Lock()
response_assistant_inflight: Dict[tuple, asyncio.Task] = {}
response_assistant_results: Dict[tuple, tuple] = {}

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
        "schema_version": CURRENT_SCHEMA_VERSION,
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
        },
        "gateway_ingress": {
            "status": "configured" if all(
                (GATEWAY_RC_USER_ID, GATEWAY_RC_AUTH_TOKEN, GATEWAY_RC_INGRESS_SECRET)
            ) and GATEWAY_RC_USER_ID != RC_USER_ID else "not_configured"
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
            
            # Format and filter rooms with recency timestamps (U-01)
            rooms = []
            for room in raw_rooms:
                room_type = room.get("t")
                if room_type in ("c", "p"):
                    rooms.append({
                        "id": room.get("_id"),
                        "name": room.get("name") or room.get("fname", "Unnamed"),
                        "type": room_type,
                        "lm": room.get("lm"),
                        "_updatedAt": room.get("_updatedAt") or room.get("lm")
                    })
            
            # Sort rooms recency-first by timestamp descending (fallback to name)
            rooms.sort(
                key=lambda r: (
                    r.get("_updatedAt") or r.get("lm") or "",
                    r["name"].lower()
                ),
                reverse=True
            )
            
            return {
                "success": True,
                "rooms": rooms
            }
            
    except Exception as e:
        logger.exception("Error fetching rooms")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/attention/queue")
async def get_attention_queue(now: Optional[float] = None):
    """GET /api/attention/queue: Return ranked channel attention queue with factor breakdown (U-10b).
    
    Scores are computed dynamically at request time and are never persisted to disk.
    """
    config = load_channel_attention_config()
    channel_registry, _ = load_channel_registry()

    # Fetch live Rocket.Chat rooms for last_activity_at and room mapping
    rc_rooms = []
    base_url = get_rc_base_url()
    headers = {"X-Auth-Token": RC_AUTH_TOKEN, "X-User-Id": RC_USER_ID}
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/api/v1/rooms.get?count=100", headers=headers)
            if resp.status_code == 200 and resp.json().get("success"):
                rc_rooms = resp.json().get("update", [])
    except Exception as err:
        logger.debug(f"Could not fetch Rocket.Chat rooms for attention queue: {err}")

    # Build room lookup & last activity map by channel name
    room_by_cname: Dict[str, Dict[str, Any]] = {}
    activity_map: Dict[str, Optional[float]] = {}
    for r in rc_rooms:
        rname = r.get("name") or r.get("fname")
        if rname:
            cname_key = rname.lower()
            room_by_cname[cname_key] = r
            lm = r.get("lm") or r.get("_updatedAt")
            if lm:
                if isinstance(lm, (int, float)):
                    activity_map[cname_key] = float(lm)
                elif isinstance(lm, str):
                    try:
                        dt = datetime.fromisoformat(lm.replace("Z", "+00:00"))
                        activity_map[cname_key] = dt.timestamp()
                    except Exception:
                        activity_map[cname_key] = None

    # /api/history normally hydrates the supervisor, but the browser polls that
    # endpoint only for the focused room. Refresh active tasks here as part of
    # the all-channel attention poll so a background response can clear Busy
    # before the queue is scored.
    active_supervised_room_ids = {
        task.room_id
        for task in task_supervisor.all()
        if task.state in {TaskState.POSTED, TaskState.ROUTED, TaskState.WORKING}
    }
    background_room_ids = {
        room.get("_id")
        for room in room_by_cname.values()
        if room.get("_id") in active_supervised_room_ids
    }
    if background_room_ids:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                refreshes = [
                    _refresh_active_task_room(client, room_id)
                    for room_id in background_room_ids
                ]
                results = await asyncio.gather(*refreshes, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        logger.debug("Could not refresh a background task room: %s", result)
        except Exception as err:
            logger.debug("Could not refresh background task rooms for attention: %s", err)

    # Collect task supervisor summaries per channel
    room_summaries: Dict[str, Dict[str, Any]] = {}
    for citem in channel_registry:
        cname = citem.get("channel_name")
        if not cname:
            continue
        cname_lower = cname.lower()
        matched_rc_room = room_by_cname.get(cname_lower)
        room_id = matched_rc_room.get("_id") if matched_rc_room else None

        if room_id:
            summary = task_supervisor.get_channel_attention_summary(room_id)
        else:
            summary = {
                "room_id": None,
                "is_busy": False,
                "working_since": None,
                "attention_state": AttentionState.UNKNOWN,
                "ready_since": None,
                "active_task_count": 0,
            }
        room_summaries[cname] = summary

    # Build attention queue items
    queue = build_attention_queue(
        config=config,
        channel_registry=channel_registry,
        room_summaries=room_summaries,
        room_activity_map={cname: activity_map.get(cname.lower()) for cname in room_summaries},
        now=now,
    )

    now_ts = now if now is not None else time.time()
    return {
        "success": True,
        "timestamp": now_ts,
        "queue": queue,
    }

@app.get("/api/attention/config")
async def get_attention_config():
    """GET /api/attention/config: Return current operator channel attention configuration."""
    config = load_channel_attention_config()
    return {
        "success": True,
        "config": config.model_dump() if hasattr(config, "model_dump") else config.dict(),
    }

@app.put("/api/attention/config")
async def update_attention_config(payload: Dict[str, Any] = Body(...)):
    """PUT /api/attention/config: Atomically update channel attention configuration for one or all channels."""
    current_config = load_channel_attention_config()
    
    try:
        if "channels" in payload and isinstance(payload["channels"], dict):
            new_config = ChannelAttentionConfig.model_validate(payload) if hasattr(ChannelAttentionConfig, "model_validate") else ChannelAttentionConfig.parse_obj(payload)
        elif "channel_name" in payload and "entry" in payload:
            cname = payload["channel_name"]
            entry_data = payload["entry"]
            new_entry = ChannelAttentionEntry.model_validate(entry_data) if hasattr(ChannelAttentionEntry, "model_validate") else ChannelAttentionEntry.parse_obj(entry_data)
            updated_channels = dict(current_config.channels)
            updated_channels[cname] = new_entry
            new_config = ChannelAttentionConfig(schema_version=current_config.schema_version, channels=updated_channels)
        else:
            raise ValueError("Payload must specify either 'channels' object or 'channel_name' and 'entry'.")
            
        save_channel_attention_config(new_config)
        return {
            "success": True,
            "message": "Attention configuration saved cleanly.",
            "config": new_config.model_dump() if hasattr(new_config, "model_dump") else new_config.dict(),
        }
    except Exception as err:
        logger.warning(f"Failed to update channel attention config: {err}")
        raise HTTPException(status_code=400, detail=str(err))

def _ingest_supervised_history(room_id: str, raw_messages: List[dict], cleaned_messages: List[dict]) -> List[str]:
    """Apply classified room events to the durable supervisor exactly once."""
    supervised_interaction_ids = []
    for raw_message, cleaned_message in zip(raw_messages, cleaned_messages):
        record = task_supervisor.ingest_event(
            room_id,
            raw_message.get("_id"),
            timestamp_to_epoch(raw_message.get("ts")),
            cleaned_message.get("event", {}),
        )
        if record and record.interaction_id not in supervised_interaction_ids:
            supervised_interaction_ids.append(record.interaction_id)
    return supervised_interaction_ids


async def _refresh_active_task_room(client: httpx.AsyncClient, room_id: str) -> None:
    """Hydrate supervision for a background room before its attention is scored.

    The browser normally hydrates a room through /api/history, but that endpoint
    is intentionally scoped to the visible room. Attention polling is the one
    place that already surveys all channels, so it also refreshes only rooms
    that still have an active supervised task. This clears stale Busy markers
    when a response arrives while Ed is viewing another channel.
    """
    params = {"roomId": room_id, "count": 100, "offset": 0}
    response = await client.get(
        f"{get_rc_base_url()}/api/v1/channels.history",
        headers={"X-Auth-Token": RC_AUTH_TOKEN, "X-User-Id": RC_USER_ID},
        params=params,
    )
    if response.status_code != 200:
        response = await client.get(
            f"{get_rc_base_url()}/api/v1/groups.history",
            headers={"X-Auth-Token": RC_AUTH_TOKEN, "X-User-Id": RC_USER_ID},
            params=params,
        )
    if response.status_code != 200:
        return
    payload = response.json()
    if not payload.get("success"):
        return

    raw_messages = list(payload.get("messages", []))
    raw_messages.reverse()
    seen_ids = set()
    deduped_raw = []
    for message in raw_messages:
        message_id = message.get("_id")
        if message_id and message_id not in seen_ids:
            seen_ids.add(message_id)
            deduped_raw.append(message)
    cleaned_messages, _ = process_history_messages(deduped_raw)
    _ingest_supervised_history(room_id, deduped_raw, cleaned_messages)


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
    supervised_interaction_ids = _ingest_supervised_history(room_id, raw_messages, cleaned_messages)
    
    return {
        "success": True,
        "room_id": room_id,
        "count": len(cleaned_messages),
        "offset": offset,
        "has_more": has_more,
        # The client uses this opaque cursor instead of offset math in a live room.
        "next_before": raw_messages[0].get("ts") if raw_messages else None,
        "messages": cleaned_messages,
        "stats": rolling_stats,
        "supervised_interaction_ids": supervised_interaction_ids,
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
    
    # Enforce history_limit context depth N on backend (U-02)
    limit = req.history_limit if req.history_limit and req.history_limit > 0 else 20
    raw_lane_b_c = [m for m in req.messages if m.get("lane") in ("agent", "user")]
    lane_b_c = raw_lane_b_c[-limit:] if len(raw_lane_b_c) > limit else raw_lane_b_c
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
        "2. Give exactly one short paragraph summarizing what is happening and the meaningful outcome.\n"
        "3. Stay high-level: omit technical details, filenames, paths, tests, logs, commands, message IDs, and minor points.\n"
        "4. Mention only material decisions, blocking issues, or clarifications Ed actually needs to address; omit categories that do not apply.\n"
        "5. Do not mention small issues or routine cleanup. Keep it crisp, conversational, and under 180 words.\n"
        "6. Skip greetings, sign-offs, Markdown headings, bullets, and numbered lists."
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
                
        except subprocess.TimeoutExpired:
            logger.warning(f"Worker '{worker_name}' execution timed out after 30s.")
        except Exception as e:
            logger.exception("Error executing worker process")
    finally:
        if 'job_dir' in locals() and os.path.exists(job_dir):
            try:
                shutil.rmtree(job_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Could not remove temp job_dir '{job_dir}': {e}")
        
    if success and digest_text:
        digest_text = normalize_narrator_summary(digest_text)
        # Persist summary
        save_summary(room_id, digest_text)
        return {
            "digest": digest_text,
            "included_message_ids": included_message_ids,
            "prior_summary_used": prior_summary_used
        }
        
    # Rule-based fallback summary: one high-level paragraph.
    logger.warning("Worker failed or returned error. Falling back to rule-based summary.")
    p1_parts = ["Ed, here is the high-level context from the recent channel updates."]
    user_counts = {}
    for m in lane_b_c:
        agent_name = m.get("event", {}).get("agent") if m.get("lane") == "agent" else None
        author = agent_name or m.get("name") or m.get("username") or "Unknown"
        user_counts[author] = user_counts.get(author, 0) + 1
        
    for user, count in user_counts.items():
        p1_parts.append(f"{user} contributed {count} updates.")
        
    if lane_b_c:
        last_msg = lane_b_c[-1]
        agent_name = last_msg.get("event", {}).get("agent") if last_msg.get("lane") == "agent" else None
        last_user = agent_name or last_msg.get("name") or last_msg.get("username") or "Unknown"
        p1_parts.append(
            f"The latest activity was from {last_user}. The AI narrator was unavailable, "
            "so review the response directly for its substantive outcome."
        )
    else:
        p1_parts.append("There are no active agent replies or user requests in the current window.")
        
    fallback_digest = " ".join(p1_parts)
    
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


async def _generate_response_assistant_once(req: ResponseAssistantRequest):
    """Generate one grounded narrator digest and one editable next-message draft."""
    import uuid

    room_id = req.roomId or RC_ROOM_ID
    limit = min(max(req.history_limit or 20, 5), 100)
    real_messages = [m for m in req.messages if m.get("lane") in ("agent", "user")]
    real_messages = real_messages[-limit:]
    # One pasted handoff can be enormous. Keep the newest N real messages, while
    # bounding individual and aggregate text so the automatic loop stays quick.
    bounded_messages = []
    per_message_limit = max(
        800,
        min(6_000, 30_000 // max(len(real_messages), 1)),
    )
    for message in real_messages:
        bounded = dict(message)
        text = str(bounded.get("text") or "")
        bounded["text"] = text[:per_message_limit] + (
            "…" if len(text) > per_message_limit else ""
        )
        bounded_messages.append(bounded)
    real_messages = bounded_messages
    included_message_ids = [m.get("id") for m in real_messages if m.get("id")]
    agent_responses = [
        m for m in real_messages
        if m.get("lane") == "agent"
        and (m.get("event") or {}).get("kind") == "agent_response"
    ]

    latest_response = None
    if req.trigger_message_id:
        latest_response = next(
            (m for m in agent_responses if m.get("id") == req.trigger_message_id),
            None,
        )
        if latest_response is None:
            raise HTTPException(
                status_code=422,
                detail="trigger_message_id must identify a real agent response in the supplied context",
            )
    elif agent_responses:
        latest_response = agent_responses[-1]

    if latest_response is None:
        raise HTTPException(
            status_code=422,
            detail="Response assistant requires at least one real agent response",
        )

    profile = resolve_channel_profile(req.room_name)
    project_context, context_files = read_project_context(profile)
    prior_summaries = read_prior_summaries(room_id)
    system_messages = [m for m in req.messages if m.get("lane") == "system"][-limit:]
    instructions = (
        build_strategy_context(profile)
        + "\n\n=== NARRATOR CONTRACT ===\n"
        + "Speak directly to Ed. The digest field must be exactly one short paragraph "
          "summarizing what is happening and the meaningful outcome. Keep it high-level "
          "and conversational. Do not include technical details, implementation mechanics, "
          "filenames, file paths, test names, logs, commands, message IDs, or minor points.\n"
        + "Put only material items requiring Ed's attention in attention_items. A decision "
          "is a meaningful choice, tradeoff, direction, or approval. An issue must materially "
          "block progress, threaten the result, or require meaningful rework; classify it as "
          "major or moderate and never include small defects or routine cleanup. A clarification "
          "is missing or confused intent, scope, priority, outcome, or constraints that affects "
          "the next step. An approval is a consequential action requiring Ed's authorization; "
          "omit it when a decision item already says the same thing. Omit every category that "
          "does not apply, and return an empty list when nothing needs Ed's attention.\n"
        + "For coding work, judge decisions around scope, architecture, behavior, tradeoffs, "
          "and important limitations; issues around real blockers, failed core behavior, "
          "substantial review findings, security or data risk, and meaningful rework; and "
          "clarifications around requirements, expected behavior, boundaries, acceptance "
          "criteria, or implementation direction. Never surface code-level detail by itself.\n"
        + "For non-coding work, judge decisions around priority, strategy, commitments, and "
          "direction; issues around material constraints, dependencies, conflicts, risks, or "
          "missing resources; clarifications around goals, audience, timing, preferences, "
          "responsibility, or desired outcome; and approvals for contacting, publishing, "
          "purchasing, submitting, scheduling, spending resources, or irreversible commitments.\n"
        + "Do not invent uncertainty, inflate a small issue, or ask Ed to decide something the "
          "responding agent can reasonably resolve independently. Do not put the suggested "
          "workflow or next-message draft in the digest.\n"
        + "Base both outputs on the same evidence. Treat the latest real agent response "
          "as the trigger, but use the bounded history and approved matter documents to "
          "avoid a shallow or repetitive next step.\n"
        + "Also produce exactly two quick_suggestions for the composer chip row. Each "
          "item needs a 1–2 word label (very short; e.g. Double-check, Next action, Your "
          "take, Overall plan, Green light?, Fix gaps, Pressure-test) and a full command "
          "string Ed can click to drop into the input. Prefer commands that start with "
          "@worker when another agent should act next. These chips are not the long "
          "suggested_message draft; they are alternate one-click asks. Match the situation: "
          "coding phases (verify, next action, plan location, checkpoint, remediation) vs "
          "non-coding (priority, opinion, clarify, next step). After one agent replies, often "
          "suggest getting another worker's opinion (e.g. after Grok, ask Codex 'Your take')."
    )
    worker_name = os.environ.get("VC_RESPONSE_ASSISTANT_WORKER", "codex")
    assistant_model = os.environ.get("VC_RESPONSE_ASSISTANT_MODEL")
    if not assistant_model and worker_name == "codex":
        assistant_model = "gpt-5.6-luna"

    job_id = f"assist_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    job_dir = os.path.join("tmp", "jobs", job_id)
    os.makedirs(job_dir, exist_ok=True)
    input_files = {
        "messages": "messages_for_llm.json",
        "system_events": "system_events.json",
        "matter_context": "matter_context.md",
        "task_instructions": "task_instructions.md",
        "room_context": "room_context.json",
    }
    job_path = os.path.join(job_dir, "job.json")

    try:
        with open(os.path.join(job_dir, input_files["messages"]), "w", encoding="utf-8") as f:
            json.dump(real_messages, f, indent=2)
        with open(os.path.join(job_dir, input_files["system_events"]), "w", encoding="utf-8") as f:
            json.dump(system_messages, f, indent=2)
        with open(os.path.join(job_dir, input_files["matter_context"]), "w", encoding="utf-8") as f:
            f.write(project_context)
        with open(os.path.join(job_dir, input_files["task_instructions"]), "w", encoding="utf-8") as f:
            f.write(instructions)
        with open(os.path.join(job_dir, input_files["room_context"]), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "room_id": room_id,
                    "room_name": req.room_name,
                    "trigger_message_id": latest_response.get("id"),
                    "channel_profile": profile,
                    "prior_summaries": prior_summaries,
                },
                f,
                indent=2,
            )
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "room_id": room_id,
                    "model": assistant_model,
                    "effort": "low",
                    "input_files": input_files,
                },
                f,
                indent=2,
            )

        env = os.environ.copy()
        env["PYTHONPATH"] = env.get("PYTHONPATH", "") + ":" + os.getcwd()
        command = [
            sys.executable,
            "workers/run_worker.py",
            "response_assistant",
            "--worker",
            worker_name,
            "--job",
            job_path,
        ]
        ai_output = ""
        try:
            result_proc = await asyncio.to_thread(
                subprocess.run,
                command,
                env=env,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=45,
            )
            result_path = os.path.join(job_dir, "result.json")
            if result_proc.returncode == 0 and os.path.exists(result_path):
                with open(result_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
                if result.get("ok"):
                    ai_output = str(result.get("output") or "")
            elif result_proc.returncode != 0:
                logger.error("Response assistant worker failed: %s", result_proc.stderr)
        except subprocess.TimeoutExpired:
            logger.warning("Response assistant worker '%s' timed out after 45s.", worker_name)
        except Exception:
            logger.exception("Response assistant worker execution failed")

        parsed = parse_response_assistant_output(ai_output, profile, latest_response)
        ai_digest_valid = parsed.pop("_ai_digest_valid", False)
        ai_suggestion_valid = parsed.pop("_ai_suggestion_valid", False)
        ai_quick_valid = parsed.pop("_ai_quick_valid", False)
        if ai_digest_valid and ai_suggestion_valid and ai_quick_valid:
            generation_mode = "ai"
        elif ai_digest_valid or ai_suggestion_valid or ai_quick_valid:
            generation_mode = "hybrid"
        else:
            generation_mode = "fallback"

        if not ai_digest_valid:
            latest_author = (
                (latest_response.get("event") or {}).get("agent")
                or latest_response.get("name")
                or latest_response.get("username")
                or "the agent"
            )
            parsed["digest"] = (
                f"Ed, {latest_author} has returned a substantive response in {req.room_name or 'this channel'}. "
                "The AI narrator was unavailable, so review the response directly for its meaningful outcome."
            )
            parsed["attention_items"] = []

        save_summary(room_id, parsed["digest"])
        return {
            **parsed,
            "trigger_message_id": latest_response.get("id"),
            "included_message_ids": included_message_ids,
            "prior_summary_used": bool(prior_summaries),
            "channel_type": profile.get("channel_type"),
            "channel_registered": profile.get("registered"),
            "strategy_source": profile.get("strategy_source"),
            "project_context_files": context_files,
            "generation_mode": generation_mode,
            "worker": worker_name,
            "model": assistant_model,
            "effort": "low",
        }
    finally:
        if os.path.exists(job_dir):
            shutil.rmtree(job_dir, ignore_errors=True)


def _prune_response_assistant_results(now: float) -> None:
    expired = [
        key
        for key, (created_at, _result) in response_assistant_results.items()
        if now - created_at > RESPONSE_ASSISTANT_IDEMPOTENCY_TTL_SECONDS
    ]
    for key in expired:
        response_assistant_results.pop(key, None)

    overflow = len(response_assistant_results) - RESPONSE_ASSISTANT_IDEMPOTENCY_MAX_ENTRIES
    if overflow > 0:
        oldest = sorted(
            response_assistant_results,
            key=lambda key: response_assistant_results[key][0],
        )[:overflow]
        for key in oldest:
            response_assistant_results.pop(key, None)


async def _run_and_cache_response_assistant(key: tuple, req: ResponseAssistantRequest) -> dict:
    try:
        result = await _generate_response_assistant_once(req)
        async with response_assistant_idempotency_lock:
            response_assistant_results[key] = (time.monotonic(), dict(result))
            _prune_response_assistant_results(time.monotonic())
        return result
    finally:
        async with response_assistant_idempotency_lock:
            current = response_assistant_inflight.get(key)
            if current is asyncio.current_task():
                response_assistant_inflight.pop(key, None)


@app.post("/api/response-assistant")
async def generate_response_assistant(req: ResponseAssistantRequest):
    """Generate grounded assistance, once per automatic response trigger.

    A manual Generate Digest request omits trigger_message_id, so every manual
    request still generates, saves, drafts, and narrates normally. Automatic
    requests share one in-flight task/result for (room_id, trigger_message_id).
    Only the first caller is authorized to apply automatic UI actions when the
    client has no cross-tab coordination. A coordinated browser may use a
    replayed cached result to restore a digest/draft after an abandoned tab
    lease; that recovery does not regenerate or save here.
    """
    trigger_message_id = (req.trigger_message_id or "").strip()
    if not trigger_message_id:
        result = await _generate_response_assistant_once(req)
        return {
            **result,
            "idempotency_replayed": False,
            "automatic_action_allowed": True,
        }

    room_id = req.roomId or RC_ROOM_ID
    key = (room_id, trigger_message_id)
    created = False
    cached_result = None
    async with response_assistant_idempotency_lock:
        now = time.monotonic()
        _prune_response_assistant_results(now)
        cached = response_assistant_results.get(key)
        if cached is not None:
            cached_result = dict(cached[1])
            task = None
        else:
            task = response_assistant_inflight.get(key)
            if task is None:
                task = asyncio.create_task(_run_and_cache_response_assistant(key, req))
                response_assistant_inflight[key] = task
                created = True

    if cached_result is not None:
        result = cached_result
    else:
        # A disconnected first browser must not cancel the shared generation and
        # cause another tab to save a second digest.
        result = await asyncio.shield(task)

    return {
        **result,
        "idempotency_replayed": not created,
        "automatic_action_allowed": created,
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

def _prepare_gateway_interaction(
    req: InteractionRequest,
    *,
    allow_model_control: bool = False,
) -> GatewayResult:
    """Create a frozen gateway snapshot; typed callers may opt into !model only."""
    target_room = req.requested_room_id or RC_ROOM_ID
    target_agent = req.requested_agent or "codex"

    raw_text = req.raw_input.strip()
    if raw_text.startswith("!"):
        if not allow_model_control or not re.fullmatch(
            rf"!model\s+{re.escape(target_agent)}\s+.+",
            raw_text,
            flags=re.IGNORECASE,
        ):
            raise HTTPException(
                status_code=400,
                detail="Control commands require a typed gateway endpoint",
            )
        refined_draft = raw_text
    elif target_agent and not raw_text.startswith(f"@{target_agent}"):
        refined_draft = f"@{target_agent} {raw_text}"
    else:
        refined_draft = raw_text
    
    interp = Interpretation(
        schema_version=CURRENT_SCHEMA_VERSION,
        selected_action="post_message",
        selected_room_id=target_room,
        selected_agent=target_agent,
        refined_draft=refined_draft,
        confidence_score=1.0,
        requires_clarification=False,
        audit_explanation="Interaction converted to message post draft.",
        source_context_ids=[]
    )
    
    expiry = time.time() + 300.0
    nonce = f"nonce_{req.interaction_id}"
    
    conf = ConfirmationSnapshot(
        schema_version=CURRENT_SCHEMA_VERSION,
        immutable_interaction_id=req.interaction_id,
        room_id=target_room,
        agent=target_agent,
        exact_message=interp.refined_draft,
        permission_tier=PermissionTier.COMMIT,
        expires_at=expiry,
        nonce=nonce
    )
    
    event = TaskEvent(
        interaction_id=req.interaction_id,
        timestamp=time.time(),
        state=TaskState.AWAITING_CONFIRMATION,
        details={"room_id": target_room, "agent": target_agent}
    )
    task_supervisor.create_interaction(req, conf, event)
    
    interp_dict = interp.model_dump() if hasattr(interp, "model_dump") else interp.dict()
    conf_dict = conf.model_dump() if hasattr(conf, "model_dump") else conf.dict()
    
    return GatewayResult(
        schema_version=CURRENT_SCHEMA_VERSION,
        status=TaskState.AWAITING_CONFIRMATION,
        selected_room_id=target_room,
        selected_agent=target_agent,
        task_events=[event],
        confirmation_snapshot=conf,
        full_response=f"Draft prepared for room {target_room}. Awaiting confirmation.",
        concise_summary=f"Draft: '{interp.refined_draft}'",
        provider_metadata={
            "interpretation": interp_dict,
            "confirmation_snapshot": conf_dict
        }
    )


@app.post("/api/gateway/interact", response_model=GatewayResult)
async def gateway_interact(req: InteractionRequest):
    """Prepare an ordinary message; control operations use their typed endpoints."""
    return _prepare_gateway_interaction(req)

@app.post("/api/gateway/confirm", response_model=GatewayResult)
async def gateway_confirm(conf: ConfirmationSnapshot):
    """Client-neutral confirmation execution entrypoint.
    Validates confirmation expiration, posts the message to Rocket.Chat,
    and returns GatewayResult with resulting message ID.
    """
    if conf.is_expired():
        raise HTTPException(status_code=400, detail="Confirmation snapshot has expired")
    if not task_supervisor.confirmation_matches(conf):
        raise HTTPException(status_code=400, detail="Confirmation snapshot is unknown or does not match its interaction")
    existing_record = task_supervisor.get(conf.immutable_interaction_id)
    if existing_record and existing_record.state != TaskState.AWAITING_CONFIRMATION:
        # Task state is persisted, unlike the in-process nonce cache. A retry after a
        # restart must return the original result rather than create another RC request.
        return GatewayResult(
            schema_version=CURRENT_SCHEMA_VERSION,
            status=existing_record.state,
            selected_room_id=existing_record.room_id,
            selected_agent=existing_record.agent,
            rocket_chat_msg_ids=existing_record.rocket_chat_msg_ids,
            task_events=[existing_record.task_events[-1]] if existing_record.task_events else [],
            full_response="Confirmation was already dispatched.",
            concise_summary="Reused the persisted dispatch result.",
        )
    if not GATEWAY_RC_USER_ID or not GATEWAY_RC_AUTH_TOKEN or not GATEWAY_RC_INGRESS_SECRET:
        raise HTTPException(status_code=503, detail="Rocket.Chat gateway ingress is not configured")
    if GATEWAY_RC_USER_ID == RC_USER_ID:
        raise HTTPException(
            status_code=503,
            detail="Rocket.Chat gateway ingress must use a dedicated non-acli_bot identity",
        )
        
    async def _do_send():
        base_url = get_rc_base_url()
        headers = {
            "X-Auth-Token": GATEWAY_RC_AUTH_TOKEN,
            "X-User-Id": GATEWAY_RC_USER_ID,
            "Content-Type": "application/json"
        }
        try:
            ingress_message = build_ingress_message(conf, GATEWAY_RC_INGRESS_SECRET)
        except IngressConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        payload = {
            "roomId": conf.room_id,
            "text": ingress_message,
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
                return msg_data.get("_id", "unknown")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("Error posting confirmed message")
            raise HTTPException(status_code=500, detail=str(e))
            
    if conf.nonce:
        async with processed_nonces_lock:
            if conf.nonce in processed_nonces:
                entry = processed_nonces[conf.nonce]
                if entry[1] == "pending":
                    raise HTTPException(status_code=409, detail="Request is already being processed")
                res_val = entry[1]
                msg_id = res_val if isinstance(res_val, str) else (res_val.get("msgId") if isinstance(res_val, dict) else "unknown")
                task_supervisor.mark_posted(conf.immutable_interaction_id, msg_id, cached=True)
                event = TaskEvent(
                    interaction_id=conf.immutable_interaction_id,
                    state=TaskState.POSTED,
                    details={"msg_id": msg_id, "cached": True},
                )
                return GatewayResult(
                    schema_version=CURRENT_SCHEMA_VERSION,
                    status=TaskState.POSTED,
                    selected_room_id=conf.room_id,
                    selected_agent=conf.agent,
                    rocket_chat_msg_ids=[msg_id],
                    task_events=[event],
                    full_response=f"Message posted successfully (ID: {msg_id})",
                    concise_summary=f"Posted to {conf.room_id}: {msg_id}"
                )
        msg_id = await deduplicate_nonce(conf.nonce, _do_send)
    else:
        msg_id = await _do_send()

    if isinstance(msg_id, dict):
        msg_id = msg_id.get("msgId", "unknown")

    record = task_supervisor.mark_posted(conf.immutable_interaction_id, msg_id)
    event = record.task_events[-1]

    return GatewayResult(
        schema_version=CURRENT_SCHEMA_VERSION,
        status=TaskState.POSTED,
        selected_room_id=conf.room_id,
        selected_agent=conf.agent,
        rocket_chat_msg_ids=[msg_id],
        task_events=[event],
        full_response=f"Message posted successfully (ID: {msg_id})",
        concise_summary=f"Posted to {conf.room_id}: {msg_id}"
    )


@app.get("/api/gateway/tasks/{interaction_id}", response_model=TaskRecord)
async def gateway_task_status(interaction_id: str):
    """Return durable, source-linked supervision state for one interaction."""
    record = task_supervisor.get(interaction_id)
    if not record:
        raise HTTPException(status_code=404, detail="Gateway interaction not found")
    return record


@app.get("/api/gateway/tts/status", response_model=TTSStatus)
async def gateway_tts_status():
    voices = get_system_voices()
    provider = configured_provider()
    chatterbox_available = await chatterbox_health() if provider == "chatterbox" else False
    return TTSStatus(
        engine="chatterbox" if chatterbox_available else ("macos_say" if sys.platform == "darwin" else "web_speech_api"),
        is_available=True,
        active_playback=is_active_playback(),
        available_voices=voices,
        provider=provider,
        chatterbox_available=chatterbox_available,
        fallback_provider=TTS_FALLBACK_PROVIDER,
    )

@app.post("/api/gateway/tts/speak")
async def gateway_tts_speak(req: TTSRequest):
    res = await synthesize_audio(req)
    if not res.get("success"):
        # The browser uses this status to switch to its local speech engine.
        raise HTTPException(status_code=503, detail=res.get("error", "TTS execution failed"))
    if res.get("audio"):
        return Response(
            content=res["audio"],
            media_type=res.get("content_type", "audio/wav"),
            headers={
                "Cache-Control": "no-store",
                "X-TTS-Engine": str(res.get("engine", "unknown")),
            },
        )
    return res

@app.post("/api/gateway/tts/stop")
async def gateway_tts_stop():
    stopped = stop_tts()
    return {"stopped": stopped}

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


# Agent Model State & Control Endpoints
MODEL_CONTROL_AGENTS = ("codex", "agy", "claude", "grok")
MODEL_CONTROL_POLL_ATTEMPTS = int(os.environ.get("VC_MODEL_CONTROL_POLL_ATTEMPTS", "20"))
MODEL_CONTROL_POLL_INTERVAL_SECONDS = float(
    os.environ.get("VC_MODEL_CONTROL_POLL_INTERVAL_SECONDS", "0.5")
)
ACLI_CANONICAL_EFFORTS = ("low", "medium", "high", "xhigh", "max")
ACLI_DEVELOPMENT_ROOT = "/Users/ed/King/clawd_2/development_channel"


def format_model_title(model_id: str) -> str:
    if not model_id:
        return "Default"
    pretty_map = {
        "gpt-5.6-sol": "GPT 5.6 Sol",
        "gpt-5.6-terra": "GPT 5.6 Terra",
        "gpt-5.6-luna": "GPT 5.6 Luna",
        "gpt-5.4": "GPT 5.4",
        "gpt-5.5": "GPT 5.5",
        "gpt-5.4-mini": "GPT 5.4 Mini",
        "gpt-5.3": "GPT 5.3",
        "gpt-5.2": "GPT 5.2",
        "gemini-3.6-flash": "Gemini 3.6 Flash",
        "gemini-3.5-flash": "Gemini 3.5 Flash",
        "gemini-3.1-pro": "Gemini 3.1 Pro",
        "gemini-3-flash-preview": "Gemini 3 Flash",
        "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
        "gemini-3-pro-preview": "Gemini 3 Pro",
        "gemini-2.5-pro": "Gemini 2.5 Pro",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "claude-sonnet-5": "Claude Sonnet 5",
        "claude-sonnet-4-6": "Claude Sonnet 4.6",
        "claude-opus-5": "Claude Opus 5",
        "claude-opus-4-8": "Claude Opus 4.8",
        "claude-opus-4-7": "Claude Opus 4.7",
        "claude-haiku-3-5": "Claude Haiku 3.5",
        "grok-4.5": "Grok 4.5",
        "grok-4": "Grok 4",
    }
    return pretty_map.get(model_id.lower(), model_id)


def format_agent_title(agent_id: str) -> str:
    pretty_map = {
        "agy": "AGY",
    }
    normalized = str(agent_id or "").strip().lower()
    return pretty_map.get(normalized, normalized.capitalize())


def _load_json_object(path: Path, description: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"{description} is unavailable for this channel") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail=f"{description} could not be read for this channel") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=503, detail=f"{description} is not a JSON object")
    return value


def _resolve_acli_model_catalog_files() -> tuple[Path, Path]:
    """Resolve ACLI's shared model definitions and configured catalog."""
    explicit_root = os.environ.get("ACLI_DEVELOPMENT_ROOT", "").strip()
    project_mount_root = os.environ.get("VOICE_GATEWAY_PROJECTS_ROOT", "").strip()
    candidates = []
    if explicit_root:
        candidates.append(Path(explicit_root))
    candidates.append(Path(ACLI_DEVELOPMENT_ROOT))
    if project_mount_root:
        candidates.append(Path(project_mount_root) / "clawd_2" / "development_channel")

    unique_candidates = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)

    for root in unique_candidates:
        models_file = root / "src" / "acli" / "core" / "models.py"
        settings_file = root / "acli" / "acli_settings.json"
        if models_file.is_file() and settings_file.is_file():
            return models_file, settings_file

    root = unique_candidates[0]
    return (
        root / "src" / "acli" / "core" / "models.py",
        root / "acli" / "acli_settings.json",
    )


def _load_acli_model_definitions(path: Path) -> tuple[Dict[str, Any], List[str]]:
    """Read ACLI's literal model defaults and canonical efforts without executing it."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail="ACLI model definitions are unavailable") from exc
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail="ACLI model definitions could not be read") from exc

    values: Dict[str, Any] = {}
    requested_names = {"DEFAULT_AGENT_MODEL_CONFIG", "CANONICAL_EFFORTS"}
    for node in tree.body:
        name = None
        value_node = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            value_node = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value_node = node.value
        if name not in requested_names or value_node is None:
            continue
        try:
            values[name] = ast.literal_eval(value_node)
        except (ValueError, TypeError, SyntaxError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"ACLI model definition '{name}' is not a literal value",
            ) from exc

    defaults = values.get("DEFAULT_AGENT_MODEL_CONFIG")
    efforts = values.get("CANONICAL_EFFORTS")
    if not isinstance(defaults, dict) or not isinstance(efforts, (list, tuple)):
        raise HTTPException(status_code=503, detail="ACLI model definitions are incomplete")

    canonical_efforts = [
        str(effort).strip().lower()
        for effort in efforts
        if str(effort).strip().lower() in ACLI_CANONICAL_EFFORTS
    ]
    if not canonical_efforts:
        raise HTTPException(status_code=503, detail="ACLI canonical effort definitions are empty")
    return defaults, canonical_efforts


def _merge_unique_strings(*collections: Any) -> List[str]:
    merged: List[str] = []
    seen = set()
    for collection in collections:
        if not isinstance(collection, (list, tuple)):
            continue
        for value in collection:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def _agy_model_options(available_models: List[str], canonical_efforts: List[str]) -> List[Dict[str, Any]]:
    """Translate AGY's effort-bearing CLI slugs into the shared model/effort UI contract."""
    suffix_pattern = re.compile(r"^(.+)-(low|medium|high|xhigh|max)$", re.IGNORECASE)
    variants: Dict[str, Dict[str, str]] = {}
    for model in available_models:
        match = suffix_pattern.fullmatch(model)
        if match:
            variants.setdefault(match.group(1), {})[match.group(2).lower()] = model

    # A suffix is part of a selectable effort family only when ACLI publishes
    # more than one variant. This keeps fixed slugs such as gpt-oss-120b-medium
    # intact as ordinary models.
    family_bases = {base for base, choices in variants.items() if len(choices) > 1}
    options: List[Dict[str, Any]] = []
    emitted_families = set()
    for model in available_models:
        match = suffix_pattern.fullmatch(model)
        base = match.group(1) if match else ""
        if base in family_bases:
            if base in emitted_families:
                continue
            emitted_families.add(base)
            choices = variants[base]
            efforts = [
                effort
                for effort in ACLI_CANONICAL_EFFORTS
                if effort in canonical_efforts and effort in choices
            ]
            options.append({
                "id": base,
                "label": format_model_title(base),
                "command_model": base.replace("-", " "),
                "efforts": efforts,
                "canonical_by_effort": {effort: choices[effort] for effort in efforts},
            })
            continue
        options.append({
            "id": model,
            "label": format_model_title(model),
            "command_model": model,
            "efforts": list(canonical_efforts),
            "canonical_by_effort": {effort: model for effort in canonical_efforts},
        })
    return options


def _load_shared_acli_model_catalog() -> tuple[Dict[str, Any], List[str]]:
    """Mirror ACLI's configured-catalog-plus-code-defaults merge for the UI."""
    models_file, settings_file = _resolve_acli_model_catalog_files()
    defaults_map, canonical_efforts = _load_acli_model_definitions(models_file)
    settings_data = _load_json_object(settings_file, "ACLI configured model catalog")
    configured_map = settings_data.get("agent_models")
    if not isinstance(configured_map, dict):
        raise HTTPException(status_code=503, detail="ACLI configured model catalog is incomplete")

    merged_map: Dict[str, Any] = {}
    for agent in MODEL_CONTROL_AGENTS:
        configured = configured_map.get(agent)
        defaults = defaults_map.get(agent)
        configured = configured if isinstance(configured, dict) else {}
        defaults = defaults if isinstance(defaults, dict) else {}
        if not configured and not defaults:
            continue

        aliases = dict(defaults.get("aliases", {})) if isinstance(defaults.get("aliases"), dict) else {}
        if isinstance(configured.get("aliases"), dict):
            aliases.update(configured["aliases"])

        merged_efforts = _merge_unique_strings(
            configured.get("available_efforts"),
            defaults.get("available_efforts"),
            canonical_efforts,
        )
        merged_efforts = [effort.lower() for effort in merged_efforts if effort.lower() in canonical_efforts]
        merged_map[agent] = {
            "default_model": configured.get("default_model") or defaults.get("default_model") or "",
            "default_effort": configured.get("default_effort") or defaults.get("default_effort") or "low",
            "available_models": _merge_unique_strings(
                configured.get("available_models"),
                defaults.get("available_models"),
            ),
            "available_efforts": merged_efforts,
            "aliases": aliases,
        }

    return merged_map, [str(models_file), str(settings_file)]


def _matter_root_candidates(folder_path: Optional[str], channel_name: str) -> List[Path]:
    """Map a registry host folder to every readable location of that matter.

    ``channels.json`` records host paths such as
    ``/Users/ed/King/clawd_2/production_repo``.  Inside the container those
    projects are mounted read-only under ``VOICE_GATEWAY_PROJECTS_ROOT``.
    Without this translation every channel except voice_channel reported
    "ACLI matter files are unavailable", which is what surfaced in the UI as a
    permanent model-control error.
    """
    candidates: List[Path] = []

    def _add(path: Path) -> None:
        if path not in candidates:
            candidates.append(path)

    if folder_path:
        configured = Path(str(folder_path))
        _add(configured)
        mount_root = os.environ.get("VOICE_GATEWAY_PROJECTS_ROOT", "").strip()
        if mount_root:
            try:
                _add(Path(mount_root) / configured.relative_to("/Users/ed/King"))
            except ValueError:
                pass

    # This matter's own acli/ directory is mounted directly at /app/acli even
    # when the project-root mounts are absent.
    if channel_name.casefold() == "voice_channel":
        _add(Path.cwd())
    return candidates


def _resolve_model_state_root(room_id: Optional[str], channel_name: Optional[str]) -> tuple[dict, Path]:
    """Resolve one registered ACLI matter for room-specific active model state."""
    cname = (channel_name or "").strip()
    if not cname:
        # Room IDs are mapped through each registered matter's authoritative
        # matter.json.  This keeps room-only gateway clients honest without a
        # Rocket.Chat prose scrape or a voice_channel fallback.
        channels, _ = load_channel_registry()
        for channel in channels:
            candidate_name = str(channel.get("channel_name") or "").strip()
            candidate_folder = channel.get("folder_path")
            if not candidate_name or not candidate_folder:
                continue
            matched = False
            for candidate_root in _matter_root_candidates(str(candidate_folder), candidate_name):
                matter_path = candidate_root / "acli" / "matter.json"
                if not matter_path.is_file():
                    continue
                try:
                    matter = json.loads(matter_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    continue
                configured_room = str(((matter.get("channel") or {}).get("room_id")) or "")
                if room_id and configured_room == room_id:
                    cname = candidate_name
                    matched = True
                    break
            if matched:
                break

    if not cname:
        raise HTTPException(
            status_code=404,
            detail="No registered ACLI matter matches this Rocket.Chat room",
        )

    profile = resolve_channel_profile(cname)
    if not profile.get("registered"):
        raise HTTPException(status_code=404, detail=f"Channel '{cname}' is not registered with ACLI")

    folder_path = profile.get("folder_path")
    root = None
    for candidate_root in _matter_root_candidates(folder_path, cname):
        if (candidate_root / "acli" / "sessions.json").is_file():
            root = candidate_root
            break
        if root is None and candidate_root.is_dir():
            root = candidate_root
    if root is None:
        raise HTTPException(status_code=503, detail=f"ACLI matter files are unavailable for #{cname}")

    matter_path = root / "acli" / "matter.json"
    if room_id and matter_path.is_file():
        matter = _load_json_object(matter_path, "ACLI matter metadata")
        configured_room = str(((matter.get("channel") or {}).get("room_id")) or "")
        if configured_room and configured_room != room_id:
            raise HTTPException(
                status_code=409,
                detail=f"Rocket.Chat room does not match the registered ACLI matter for #{cname}",
            )
    return profile, root


def get_channel_agent_models(room_id: Optional[str] = None, channel_name: Optional[str] = None) -> Dict[str, Any]:
    profile, root = _resolve_model_state_root(room_id, channel_name)
    folder_path = profile.get("folder_path") or str(root)
    sessions_file = root / "acli" / "sessions.json"
    sessions_data = _load_json_object(sessions_file, "ACLI model state")

    agent_settings_map, catalog_sources = _load_shared_acli_model_catalog()
    sessions_agents_map = sessions_data.get("agents", {})

    result_agents: Dict[str, Any] = {}

    for ag in MODEL_CONTROL_AGENTS:
        st = agent_settings_map.get(ag, {})
        sess = sessions_agents_map.get(ag, {})

        if not isinstance(st, dict) or not st:
            result_agents[ag] = {
                "agent": ag,
                "available": False,
                "error": "Agent has no catalog in ACLI's shared model configuration",
                "available_models": [],
                "available_efforts": [],
            }
            continue

        def_model = st.get("default_model", "")
        def_effort = st.get("default_effort", "low")

        # ACLI's merged configured/default catalog is the provider boundary.
        # Never inject a stale session value or a cross-provider UI fallback.
        available_models = [str(model) for model in st.get("available_models", []) if str(model).strip()]
        available_efforts = [
            str(effort).strip().lower()
            for effort in st.get("available_efforts", [])
            if str(effort).strip().lower() in ACLI_CANONICAL_EFFORTS
        ]
        if not available_efforts:
            available_efforts = list(ACLI_CANONICAL_EFFORTS)

        curr_model = sess.get("current_model", "")
        curr_effort = sess.get("current_effort", "")

        # Match ACLI get_current_model/get_current_effort: an invalid stale
        # override is ignored rather than exposed as another provider's model.
        eff_model = curr_model if curr_model and curr_model in available_models else def_model
        eff_effort = str(curr_effort or "").strip().lower()
        if eff_effort not in available_efforts:
            eff_effort = str(def_effort or "low").strip().lower()
        if eff_effort not in available_efforts:
            eff_effort = available_efforts[0]

        model_options = [
            {
                "id": model,
                "label": format_model_title(model),
                "command_model": model,
                "efforts": list(available_efforts),
                "canonical_by_effort": {effort: model for effort in available_efforts},
            }
            for model in available_models
        ]
        selector_model = eff_model
        selector_effort = eff_effort
        selected_model_option = None
        if ag == "agy":
            model_options = _agy_model_options(available_models, available_efforts)
            for option in model_options:
                canonical_by_effort = option["canonical_by_effort"]
                matching_effort = next(
                    (effort for effort, canonical in canonical_by_effort.items() if canonical == eff_model),
                    None,
                )
                if matching_effort:
                    selected_model_option = option
                    selector_model = option["id"]
                    selector_effort = matching_effort
                    # The effort encoded in AGY's model slug is authoritative.
                    eff_effort = matching_effort
                    break

        disp_model = (
            selected_model_option["label"]
            if selected_model_option
            else format_model_title(eff_model)
        )
        disp_effort = eff_effort or "low"

        result_agents[ag] = {
            "agent": ag,
            "available": bool(available_models and eff_model),
            "current_model": eff_model,
            "current_effort": eff_effort,
            "display_model": disp_model,
            "display_effort": disp_effort,
            "display_label": f"{format_agent_title(ag)} · {disp_model} · {disp_effort}",
            "inherited_model": not bool(curr_model),
            "inherited_effort": not bool(curr_effort),
            "default_model": def_model,
            "default_effort": def_effort,
            "available_models": available_models,
            "available_efforts": available_efforts,
            "model_options": model_options,
            "selector_model": selector_model,
            "selector_effort": selector_effort,
            "aliases": dict(st.get("aliases", {})),
            "status": sess.get("status", "idle"),
        }

    return {
        "success": True,
        "room_id": room_id,
        "channel_name": profile.get("channel_name", "voice_channel"),
        "folder_path": str(folder_path) if folder_path else None,
        "catalog_source": catalog_sources[-1],
        "catalog_sources": catalog_sources,
        "state_source": str(sessions_file),
        "agents": result_agents,
    }


class AgentModelPrepareRequest(BaseModel):
    room_id: str
    channel_name: Optional[str] = None
    agent: str
    target_model: str
    target_effort: Optional[str] = "high"


class AgentModelConfirmRequest(BaseModel):
    confirmation_snapshot: ConfirmationSnapshot
    channel_name: Optional[str] = None


@app.get("/api/agent-models")
async def get_agent_models(roomId: Optional[str] = None, channelName: Optional[str] = None):
    """GET /api/agent-models: Return effective and available model/effort configuration for target agents."""
    return get_channel_agent_models(room_id=roomId, channel_name=channelName)


@app.post("/api/agent-models/prepare")
async def prepare_agent_model_switch(req: AgentModelPrepareRequest):
    """POST /api/agent-models/prepare: Format and validate a proposed agent model control command with confirmation."""
    agent_key = req.agent.lower().strip()
    if agent_key not in MODEL_CONTROL_AGENTS:
        raise HTTPException(status_code=400, detail=f"Invalid agent '{req.agent}'.")

    models_info = get_channel_agent_models(room_id=req.room_id, channel_name=req.channel_name)
    agent_info = models_info["agents"].get(agent_key)
    if not agent_info or not agent_info.get("available"):
        raise HTTPException(status_code=404, detail=f"Agent '{agent_key}' state not found.")

    target_m = req.target_model.strip()
    target_e = (req.target_effort or "low").strip().lower()

    if target_e not in agent_info.get("available_efforts", []):
        raise HTTPException(status_code=400, detail=f"Invalid effort level '{req.target_effort}'.")

    selected_option = None
    if target_m.lower() != "default":
        if agent_key == "agy":
            selected_option = next(
                (
                    option
                    for option in agent_info.get("model_options", [])
                    if str(option.get("id", "")).casefold() == target_m.casefold()
                ),
                None,
            )
            if not selected_option:
                raise HTTPException(
                    status_code=400,
                    detail=f"Target model '{target_m}' is not in available models for agent '{agent_key}'.",
                )
            if target_e not in selected_option.get("efforts", []):
                raise HTTPException(
                    status_code=400,
                    detail=f"Effort '{target_e}' is unavailable for AGY model '{target_m}'.",
                )
            target_m = str(selected_option["id"])
        else:
            available_by_casefold = {
                str(model).casefold(): str(model) for model in agent_info.get("available_models", [])
            }
            canonical_target = available_by_casefold.get(target_m.casefold())
            if not canonical_target:
                raise HTTPException(
                    status_code=400,
                    detail=f"Target model '{target_m}' is not in available models for agent '{agent_key}'.",
                )
            target_m = canonical_target

    curr_m_disp = agent_info["display_model"]
    curr_e = agent_info["display_effort"]
    target_m_disp = format_model_title(target_m)

    if target_m.lower() == "default":
        formatted_cmd = f"!model {agent_key} default"
        conf_msg = f"Reset {format_agent_title(agent_key)} in #{models_info['channel_name']} to default model and effort for future tasks?"
    else:
        command_model = selected_option["command_model"] if selected_option else target_m
        formatted_cmd = f"!model {agent_key} {command_model} {target_e}"
        conf_msg = f"Change {format_agent_title(agent_key)} in #{models_info['channel_name']} from {curr_m_disp} ({curr_e}) to {target_m_disp} ({target_e}) for future tasks?"

    gateway_request = InteractionRequest(
        raw_input=formatted_cmd,
        requested_room_id=req.room_id,
        requested_agent=agent_key,
        client_id="browser_model_selector",
    )
    gateway_result = _prepare_gateway_interaction(gateway_request, allow_model_control=True)
    gateway_snapshot = gateway_result.confirmation_snapshot
    if gateway_snapshot is None:
        raise HTTPException(status_code=500, detail="Gateway did not create a confirmation snapshot")

    return {
        "success": True,
        "confirmation_message": conf_msg,
        "confirmation_snapshot": gateway_snapshot.model_dump() if hasattr(gateway_snapshot, "model_dump") else gateway_snapshot.dict(),
        "change": {
            "room_id": req.room_id,
            "channel_name": models_info["channel_name"],
            "agent": agent_key,
            "current_model": agent_info["current_model"],
            "current_effort": curr_e,
            "target_model": target_m,
            "effective_target_model": (
                selected_option["canonical_by_effort"][target_e]
                if selected_option
                else target_m
            ),
            "target_effort": target_e,
            "formatted_command": formatted_cmd,
        },
    }


def _validate_model_snapshot(
    snapshot: ConfirmationSnapshot,
    channel_name: Optional[str],
) -> tuple[Dict[str, Any], str, str, bool]:
    command = snapshot.exact_message.strip()
    match = re.fullmatch(r"!model\s+([a-z0-9_]+)\s+(.+)", command, flags=re.IGNORECASE)
    if not match:
        raise HTTPException(status_code=400, detail="Confirmation is not an exact !model command")
    agent_key = match.group(1).lower()
    if agent_key not in MODEL_CONTROL_AGENTS or snapshot.agent != agent_key:
        raise HTTPException(status_code=400, detail="Model command agent does not match confirmation")

    models_info = get_channel_agent_models(snapshot.room_id, channel_name)
    agent_info = models_info["agents"].get(agent_key) or {}
    value = match.group(2)
    if value.casefold() == "default":
        if command != f"!model {agent_key} default":
            raise HTTPException(status_code=400, detail="Default model command is not canonical")
        return models_info, agent_key, str(agent_info.get("default_model") or ""), True

    for option in agent_info.get("model_options", []):
        for effort in option.get("efforts", []):
            expected = f"!model {agent_key} {option.get('command_model')} {effort}"
            if command == expected:
                target = option.get("canonical_by_effort", {}).get(effort, option.get("id"))
                return models_info, agent_key, str(target), False
    raise HTTPException(status_code=400, detail="Model command is not in the current ACLI catalog")


async def _wait_for_model_application(
    *,
    room_id: str,
    channel_name: str,
    agent: str,
    target_model: str,
    target_effort: Optional[str],
    reset_default: bool,
) -> Optional[Dict[str, Any]]:
    for attempt in range(max(1, MODEL_CONTROL_POLL_ATTEMPTS)):
        try:
            state = get_channel_agent_models(room_id, channel_name)
        except HTTPException as exc:
            # ACLI currently rewrites sessions.json in place.  A poll that
            # lands during that tiny write window can see a transient 503
            # (including a partially-written JSON file) even though ACLI is
            # applying the command successfully.  Keep polling instead of
            # turning that recoverable race into a failed model switch.
            if exc.status_code != 503:
                raise
            logger.info(
                "Model state temporarily unavailable while waiting for ACLI "
                "to apply %s in #%s; retrying (%s/%s)",
                agent,
                channel_name,
                attempt + 1,
                max(1, MODEL_CONTROL_POLL_ATTEMPTS),
            )
            state = None
        info = state.get("agents", {}).get(agent) if state else {}
        info = info or {}
        model_matches = info.get("current_model") == target_model
        effort_matches = reset_default or info.get("current_effort") == target_effort
        inheritance_matches = not reset_default or (
            info.get("inherited_model") and info.get("inherited_effort")
        )
        if model_matches and effort_matches and inheritance_matches:
            return info
        if attempt + 1 < max(1, MODEL_CONTROL_POLL_ATTEMPTS):
            await asyncio.sleep(max(0.0, MODEL_CONTROL_POLL_INTERVAL_SECONDS))
    return None


@app.post("/api/agent-models/confirm")
async def confirm_agent_model_switch(req: AgentModelConfirmRequest):
    """Post the frozen native ``!model`` command verbatim and wait for ACLI state."""
    snapshot = req.confirmation_snapshot
    models_info, agent_key, target_model, reset_default = _validate_model_snapshot(
        snapshot,
        req.channel_name,
    )
    target_effort = None
    if not reset_default:
        target_effort = snapshot.exact_message.rsplit(" ", 1)[-1]

    if snapshot.is_expired():
        raise HTTPException(status_code=400, detail="Confirmation snapshot has expired")
    if not task_supervisor.confirmation_matches(snapshot):
        raise HTTPException(
            status_code=400,
            detail="Confirmation snapshot is unknown or does not match its interaction",
        )
    if not GATEWAY_RC_USER_ID or not GATEWAY_RC_AUTH_TOKEN:
        raise HTTPException(status_code=503, detail="Rocket.Chat model-control sender is not configured")
    if GATEWAY_RC_USER_ID == RC_USER_ID:
        raise HTTPException(
            status_code=503,
            detail="Rocket.Chat model control must use the dedicated gateway identity",
        )

    existing_record = task_supervisor.get(snapshot.immutable_interaction_id)
    if (
        existing_record
        and existing_record.state != TaskState.AWAITING_CONFIRMATION
        and existing_record.rocket_chat_msg_ids
    ):
        message_id = existing_record.rocket_chat_msg_ids[-1]
    else:
        async def _post_native_model_command():
            headers = {
                "X-Auth-Token": GATEWAY_RC_AUTH_TOKEN,
                "X-User-Id": GATEWAY_RC_USER_ID,
                "Content-Type": "application/json",
            }
            payload = {
                "roomId": snapshot.room_id,
                # This must remain a plain, visible Rocket.Chat command. ACLI's
                # native !model resolver consumes it exactly as written here.
                "text": snapshot.exact_message,
            }
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        f"{get_rc_base_url()}/api/v1/chat.postMessage",
                        headers=headers,
                        json=payload,
                    )
            except Exception as exc:
                logger.exception("Error posting native model command")
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Rocket.Chat post error: {response.text}",
                )
            response_data = response.json()
            if not response_data.get("success"):
                raise HTTPException(status_code=400, detail="Rocket.Chat model command post failed")
            return str((response_data.get("message") or {}).get("_id") or "unknown")

        message_id = await deduplicate_nonce(snapshot.nonce, _post_native_model_command)
        if isinstance(message_id, dict):
            message_id = str(message_id.get("msgId") or "unknown")
        task_supervisor.mark_posted(snapshot.immutable_interaction_id, str(message_id))

    applied = await _wait_for_model_application(
        room_id=snapshot.room_id,
        channel_name=models_info["channel_name"],
        agent=agent_key,
        target_model=target_model,
        target_effort=target_effort,
        reset_default=reset_default,
    )
    return {
        "success": True,
        "status": "applied" if applied else "posted",
        "applied": bool(applied),
        "message": (
            "ACLI applied the model change."
            if applied
            else "Model command was posted to Rocket.Chat; ACLI acknowledgement is still pending."
        ),
        "rocket_chat_msg_ids": [str(message_id)],
        "posted_command": snapshot.exact_message,
        "agent": agent_key,
        "effective": applied,
    }

# Route to serve frontend assets
FRONTEND_CACHE_HEADERS = {"Cache-Control": "no-store, max-age=0"}


@app.get("/")
async def get_index():
    # The console is a continuously running local tab.  Do not let the browser
    # keep an older UI shell after a product update; stale JavaScript can make a
    # deployed backend feature look completely inactive.
    return FileResponse("frontend/index.html", headers=FRONTEND_CACHE_HEADERS)

@app.get("/index.css")
async def get_css():
    return FileResponse("frontend/index.css", headers=FRONTEND_CACHE_HEADERS)

@app.get("/index.js")
async def get_js():
    return FileResponse("frontend/index.js", headers=FRONTEND_CACHE_HEADERS)

@app.get("/history_state.js")
async def get_history_state_js():
    return FileResponse("frontend/history_state.js", headers=FRONTEND_CACHE_HEADERS)
