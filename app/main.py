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
from collections import deque
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set
from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
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
from app.rc_webhook import (
    WebhookAuthError,
    WebhookReplayError,
    authenticate_webhook_request,
    load_webhook_state,
    rollback_message_id,
    update_webhook_state,
    webhook_event_is_stale,
    webhook_configured,
)
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
from app.read_cursor import (
    _extract_msg_timestamp,
    evaluate_room_unread_status,
    get_last_real_conversation_timestamp,
    get_all_read_cursors,
    get_read_cursor,
    is_real_agent_reply,
    is_real_conversation_message,
    update_read_cursor,
)

ROOM_MESSAGES_CACHE: Dict[str, List[Dict[str, Any]]] = {}
# Serialize webhook wake processing per room. Rocket.Chat can emit a burst of
# user, routing, heartbeat, and final-reply events for one ACLI interaction;
# those events must not race the same volatile history baseline.
ROOM_WEBHOOK_WAKE_LOCKS: Dict[str, asyncio.Lock] = {}
WEBHOOK_DEBOUNCE_SECONDS = max(
    0.0,
    min(2.0, float(os.environ.get("GATEWAY_RC_WEBHOOK_DEBOUNCE_SECONDS", "0.75"))),
)
WEBHOOK_MAX_CONCURRENCY = max(
    1,
    min(16, int(os.environ.get("GATEWAY_RC_WEBHOOK_MAX_CONCURRENCY", "4"))),
)
WEBHOOK_WAKE_SEMAPHORE = asyncio.Semaphore(WEBHOOK_MAX_CONCURRENCY)
WEBHOOK_PENDING_BATCHES: Dict[str, Dict[str, Any]] = {}
# Volatile, metadata-only cache.  The marker is Rocket.Chat's room update value;
# the timestamp is derived only from a substantive non-system message.
ROOM_REAL_ACTIVITY_CACHE: Dict[str, Dict[str, Any]] = {}

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

    # Start Gateway-owned background channel monitor loop
    asyncio.create_task(_gateway_channel_monitor_loop())



class RocketChatDDPAdapter:
    """Gateway-owned native DDP websocket adapter for Rocket.Chat events.

    Implements U-11E-a architecture:
    - Connects directly to Rocket.Chat websocket DDP server.
    - Authenticates using existing RC_AUTH_TOKEN / RC_USER_ID or GATEWAY_RC_* fallback.
    - Stream 1 (stream-notify-user/<uid>/rooms-changed): User-wide room recency & unread updates.
    - Stream 2 (stream-room-messages/<rid>): Subscribed ONLY for narration_active ∧ attention_active channels.
    - Normalizes real agent reply events and dispatches directly to _schedule_background_narration_prewarm.
    """
    def __init__(self):
        self.state = "disconnected" # "disconnected", "connecting", "connected", "reconnecting", "degraded_polling"
        self.last_connected_at: Optional[float] = None
        self.reconnect_count: int = 0
        self.subscribed_rooms: Set[str] = set()
        self._task: Optional[asyncio.Task] = None

    def get_status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "last_connected_at": self.last_connected_at,
            "reconnect_count": self.reconnect_count,
            "subscribed_rooms_count": len(self.subscribed_rooms),
        }

    async def start(self):
        enabled = os.environ.get("GATEWAY_RC_EVENTS", "0").strip().lower() in ("1", "true", "yes")
        if not enabled:
            logger.info("Gateway Rocket.Chat DDP event transport disabled (GATEWAY_RC_EVENTS!=1). Using poll fallback.")
            self.state = "degraded_polling"
            return
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self):
        import websockets
        while True:
            try:
                base_url = get_rc_base_url()
                ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://") + "/websocket"
                auth_token = GATEWAY_RC_AUTH_TOKEN or RC_AUTH_TOKEN
                user_id = GATEWAY_RC_USER_ID or RC_USER_ID

                self.state = "connecting"
                logger.info(f"Connecting Gateway DDP websocket to {ws_url}...")
                async with websockets.connect(ws_url) as ws:
                    # DDP Handshake
                    await ws.send(json.dumps({"msg": "connect", "version": "1", "support": ["1"]}))
                    resp = await ws.recv()
                    msg = json.loads(resp)
                    if msg.get("msg") != "connected":
                        raise RuntimeError(f"DDP connect failed: {msg}")

                    # DDP Auth
                    auth_id = "auth_1"
                    await ws.send(json.dumps({
                        "msg": "method",
                        "method": "login",
                        "id": auth_id,
                        "params": [{"resume": auth_token}]
                    }))

                    authed = False
                    while not authed:
                        resp = await ws.recv()
                        msg = json.loads(resp)
                        if msg.get("msg") == "result" and msg.get("id") == auth_id:
                            if msg.get("error"):
                                raise RuntimeError(f"DDP authentication failed: {msg['error']}")
                            authed = True

                    self.state = "connected"
                    self.last_connected_at = time.time()
                    logger.info("Gateway DDP websocket authenticated successfully.")

                    # Stream 1: User rooms changed (user-wide recency)
                    await ws.send(json.dumps({
                        "msg": "sub",
                        "id": "sub_rooms_changed",
                        "name": "stream-notify-user",
                        "params": [f"{user_id}/rooms-changed", False]
                    }))

                    # Sync Stream 2 subscriptions based on narration_active channels
                    await self._sync_subscriptions(ws)

                    # Main Event Receiver Loop
                    while True:
                        raw_msg = await ws.recv()
                        data = json.loads(raw_msg)
                        if data.get("msg") == "ping":
                            await ws.send(json.dumps({"msg": "pong"}))
                        elif data.get("msg") == "changed" and data.get("collection") == "stream-room-messages":
                            await self._handle_room_message_event(data)
                        elif data.get("msg") == "changed" and data.get("collection") == "stream-notify-user":
                            await self._handle_user_notify_event(data)

            except asyncio.CancelledError:
                self.state = "disconnected"
                logger.info("Gateway DDP websocket task cancelled.")
                break
            except Exception as err:
                self.reconnect_count += 1
                self.state = "reconnecting"
                logger.warning("Gateway DDP websocket disconnected (%s). Retrying in 5s...", err)
                await asyncio.sleep(5)

    async def _sync_subscriptions(self, ws):
        attn_cfg = load_channel_attention_config()
        narration_active_cnames = {
            cname.lower()
            for cname, entry in attn_cfg.channels.items()
            if entry.narration_active and entry.attention_active
        }
        # Fetch current RC rooms mapping name -> rid
        base_url = get_rc_base_url()
        auth_token = GATEWAY_RC_AUTH_TOKEN or RC_AUTH_TOKEN
        user_id = GATEWAY_RC_USER_ID or RC_USER_ID
        headers = {"X-Auth-Token": auth_token, "X-User-Id": user_id}

        target_rids = set()
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{base_url}/api/v1/rooms.get?count=100", headers=headers)
                if resp.status_code == 200 and resp.json().get("success"):
                    for r in resp.json().get("update", []):
                        rname = (r.get("name") or r.get("fname") or "").lower()
                        rid = r.get("_id")
                        if rid and rname in narration_active_cnames:
                            target_rids.add(rid)
        except Exception as err:
            logger.debug("Could not resolve room IDs for DDP stream subscription: %s", err)

        new_subs = target_rids - self.subscribed_rooms
        for rid in new_subs:
            sub_id = f"sub_msg_{rid}"
            await ws.send(json.dumps({
                "msg": "sub",
                "id": sub_id,
                "name": "stream-room-messages",
                "params": [rid, False]
            }))
            self.subscribed_rooms.add(rid)
            logger.info(f"DDP subscribed to stream-room-messages for narration_active room {rid}")

    async def _handle_room_message_event(self, data: Dict[str, Any]):
        args = data.get("fields", {}).get("args", [])
        if not args:
            return
        msg_obj = args[0]
        rid = msg_obj.get("rid")
        if not rid:
            return

        # Check if message is a new real agent reply
        cached_msgs = ROOM_MESSAGES_CACHE.get(rid, [])
        known_ids = {str(m.get("id") or m.get("_id") or "") for m in cached_msgs}
        msg_id = str(msg_obj.get("id") or msg_obj.get("_id") or "")

        # Watermark & duplication check
        if msg_id in known_ids:
            return

        # Append to ROOM_MESSAGES_CACHE
        if rid not in ROOM_MESSAGES_CACHE:
            ROOM_MESSAGES_CACHE[rid] = []

        lane_info = classify_message(msg_obj)
        converted_msg = {
            "id": msg_id,
            "text": msg_obj.get("msg", ""),
            "lane": lane_info["lane"],
            "name": msg_obj.get("u", {}).get("username", "unknown"),
            "timestamp": _extract_msg_timestamp(msg_obj),
            "event": lane_info["event"],
        }
        ROOM_MESSAGES_CACHE[rid].append(converted_msg)

        # Broadcast SSE event to browser clients for recency update
        await sse_broadcaster.publish({
            "type": "message",
            "room_id": rid,
            "msg_id": msg_id,
            "timestamp": time.time(),
        })

        # Real agent reply filter
        if is_real_agent_reply(converted_msg):
            # Dispatch to prewarm immediately
            queue = [{
                "room_id": rid,
                "channel_name": msg_obj.get("alias") or rid,
                "queue_category": "ranked",
                "rank": 1,
            }]
            await _schedule_background_narration_prewarm(
                queue=queue,
                new_replies_by_room={rid: [converted_msg]},
                supervised_room_ids={rid},
            )

    async def _handle_user_notify_event(self, data: Dict[str, Any]):
        # Stream 1 room-changed event handles room recency updates
        args = data.get("fields", {}).get("args", [])
        if args and isinstance(args[0], str) and args[0] == "updated":
            room_data = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            rid = room_data.get("_id")
            await sse_broadcaster.publish({
                "type": "room_changed",
                "room_id": rid,
                "timestamp": time.time(),
            })

ddp_adapter = RocketChatDDPAdapter()


class SSEBroadcaster:
    """Broadcaster for Gateway-to-browser Server-Sent Events (SSE)."""
    def __init__(self):
        self.subscribers: Set[asyncio.Queue] = set()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subscribers.discard(q)

    async def publish(self, event: Dict[str, Any]):
        if not self.subscribers:
            return
        dead = set()
        for q in self.subscribers:
            try:
                q.put_nowait(event)
            except Exception:
                dead.add(q)
        for q in dead:
            self.subscribers.discard(q)

sse_broadcaster = SSEBroadcaster()


@app.get("/api/events")
async def sse_events_endpoint(request: Request):
    """Gateway-to-browser Server-Sent Events (SSE) push endpoint.

    Pushes lightweight room-recency and prewarm-ready signals to open browser tabs,
    allowing browser polling intervals to be retired without sending raw message bodies.
    """
    queue = await sse_broadcaster.subscribe()

    async def event_generator():
        try:
            # Initial ping
            yield "event: ping\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            sse_broadcaster.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _gateway_channel_monitor_loop():
    """Server-side background loop running independently of browser tabs.

    Periodically polls history for channels configured with narration_active=True
    (and active task rooms), and schedules text prewarming for newly observed real agent replies.
    Starts DDP websocket event transport if GATEWAY_RC_EVENTS=1.
    """
    logger.info("Starting Gateway-owned background channel monitor loop.")
    await ddp_adapter.start()
    while True:
        try:
            await asyncio.sleep(25)
            # If DDP is connected, polling loop acts as fallback or periodic sync
            attn_cfg = load_channel_attention_config()
            active_cnames = {
                cname.lower()
                for cname, entry in attn_cfg.channels.items()
                if entry.narration_active and entry.attention_active
            }
            active_supervised = {
                task.room_id
                for task in task_supervisor.all()
                if task.state in {TaskState.POSTED, TaskState.ROUTED, TaskState.WORKING}
            }
            if active_cnames or active_supervised:
                await get_attention_queue()
        except asyncio.CancelledError:
            logger.info("Gateway channel monitor loop cancelled.")
            break
        except Exception as err:
            logger.debug("Error in Gateway channel monitor loop: %s", err)

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

# Background narration prewarm is intentionally opt-in at the gateway layer
# and stores configuration only. Generated text remains in the existing bounded
# in-memory response-assistant cache; no message text or audio is added to a
# new durable store.
NARRATION_PREWARM_CONFIG_PATH = os.environ.get(
    "VC_NARRATION_PREWARM_CONFIG_PATH",
    "acli/gateway_state/narration_prewarm_settings.json",
)
DEFAULT_NARRATION_PREWARM_CONFIG = {
    "enabled": True,
    "scope": "top_attention",
    "top_n": 5,
    "history_limit": 20,
    "hourly_generation_cap": 12,
}
NARRATION_PREWARM_SCOPES = {"top_attention", "supervised"}
narration_prewarm_lock = asyncio.Lock()
narration_prewarm_generation_starts = deque()


def _normalized_narration_prewarm_config(value: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return bounded, schema-free operator settings for background text prewarm."""
    raw = value if isinstance(value, dict) else {}
    scope = raw.get("scope")
    if scope not in NARRATION_PREWARM_SCOPES:
        scope = DEFAULT_NARRATION_PREWARM_CONFIG["scope"]

    def bounded_int(key: str, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(raw.get(key, DEFAULT_NARRATION_PREWARM_CONFIG[key]))))
        except (TypeError, ValueError):
            return DEFAULT_NARRATION_PREWARM_CONFIG[key]

    return {
        "enabled": bool(raw.get("enabled", DEFAULT_NARRATION_PREWARM_CONFIG["enabled"])),
        "scope": scope,
        "top_n": bounded_int("top_n", 1, 20),
        "history_limit": bounded_int("history_limit", 5, 100),
        "hourly_generation_cap": bounded_int("hourly_generation_cap", 1, 50),
    }


def load_narration_prewarm_config() -> Dict[str, Any]:
    try:
        with open(NARRATION_PREWARM_CONFIG_PATH, "r", encoding="utf-8") as f:
            return _normalized_narration_prewarm_config(json.load(f))
    except FileNotFoundError:
        return dict(DEFAULT_NARRATION_PREWARM_CONFIG)
    except Exception as err:
        logger.warning("Could not read narration prewarm settings: %s", err)
        return dict(DEFAULT_NARRATION_PREWARM_CONFIG)


def save_narration_prewarm_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalized_narration_prewarm_config(config)
    directory = os.path.dirname(NARRATION_PREWARM_CONFIG_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = NARRATION_PREWARM_CONFIG_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2)
    os.replace(temp_path, NARRATION_PREWARM_CONFIG_PATH)
    return normalized


@app.get("/api/narration/prewarm/config")
async def get_narration_prewarm_config():
    return {"success": True, "config": load_narration_prewarm_config()}


@app.put("/api/narration/prewarm/config")
async def update_narration_prewarm_config(payload: Dict[str, Any] = Body(...)):
    try:
        return {
            "success": True,
            "config": save_narration_prewarm_config({
                **load_narration_prewarm_config(),
                **payload,
            }),
        }
    except Exception as err:
        logger.warning("Could not save narration prewarm settings: %s", err)
        raise HTTPException(status_code=500, detail="Could not save narration prewarm settings")

def _get_webhook_status() -> Dict[str, Any]:
    configured = webhook_configured()
    persisted = load_webhook_state()
    last_verified_at = persisted.get("last_verified_at")
    status_dict = {
        "status": "configured" if configured else "not_configured",
        "path": "/api/rc/webhook/message",
        "last_received_at": persisted.get("last_received_at"),
        "last_verified_at": last_verified_at,
        "covered_rooms": persisted.get("covered_rooms") or {},
        "metrics": persisted.get("metrics") or {},
        "last_error": persisted.get("last_error"),
        "last_error_at": persisted.get("last_error_at"),
        "last_event": persisted.get("last_event"),
        "debounce_seconds": WEBHOOK_DEBOUNCE_SECONDS,
        "max_concurrency": WEBHOOK_MAX_CONCURRENCY,
        "state": "not_configured",
    }
    if configured:
        if last_verified_at and (time.time() - float(last_verified_at)) < 120:
            status_dict["state"] = "healthy"
        else:
            status_dict["state"] = "degraded_polling"
    return status_dict


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
        },
        "ddp": ddp_adapter.get_status(),
        "rc_webhook": _get_webhook_status(),
    }


async def process_rocket_chat_message_wake(
    room_id: str,
    *,
    channel_name: Optional[str] = None,
    hint_message_id: Optional[str] = None,
    hint_message_ids: Optional[List[str]] = None,
    source: str = "wake",
) -> Dict[str, Any]:
    """Handle a room wake-up without trusting the event body as transcript truth.

    Rocket.Chat remains the source of truth: the Gateway re-fetches that room,
    classifies messages, prewarms only newly observed real agent replies, and
    pushes lightweight SSE signals (never raw message text).
    """
    rid = str(room_id or "").strip()
    if not rid:
        raise HTTPException(status_code=400, detail="room_id is required")

    hints: List[str] = []
    for value in list(hint_message_ids or []) + ([hint_message_id] if hint_message_id else []):
        normalized_hint = str(value or "").strip()
        if normalized_hint and normalized_hint not in hints:
            hints.append(normalized_hint)

    wake_lock = ROOM_WEBHOOK_WAKE_LOCKS.setdefault(rid, asyncio.Lock())
    async with wake_lock:
        had_baseline = rid in ROOM_MESSAGES_CACHE
        async with httpx.AsyncClient(timeout=10.0) as client:
            refreshed_replies = await _refresh_active_task_room(client, rid)

        new_replies = refreshed_replies
        stale_message_ids: List[str] = []
        if hints:
            # An authenticated outgoing webhook identifies one Rocket.Chat
            # message (or a short coalesced batch). A full history refresh may
            # also discover older messages
            # absent from a browser's short cache; never narrate that wider diff.
            # Re-fetching still establishes Rocket.Chat as the source of truth,
            # while exact-ID selection keeps concurrent ACLI event bursts bounded
            # to the messages that caused this wake-up.
            candidates = list(refreshed_replies or [])
            candidates.extend(ROOM_MESSAGES_CACHE.get(rid, []))
            selected_replies: List[Dict[str, Any]] = []
            selected_ids: Set[str] = set()
            for hint in hints:
                hinted_reply = next(
                    (
                        message
                        for message in candidates
                        if str(message.get("id") or message.get("_id") or "") == hint
                        and is_real_agent_reply(message)
                    ),
                    None,
                )
                if hinted_reply is None:
                    continue
                msg_ts = _extract_msg_timestamp(hinted_reply)
                # Explicit wake => treat as fresh unless clearly old backlog (>15 min).
                if msg_ts > 0 and (time.time() - msg_ts) > 900:
                    stale_message_ids.append(hint)
                    continue
                reply_id = str(hinted_reply.get("id") or hinted_reply.get("_id") or hint)
                if reply_id not in selected_ids:
                    selected_ids.add(reply_id)
                    selected_replies.append(hinted_reply)
            new_replies = selected_replies

    await sse_broadcaster.publish({
        "type": "room_changed",
        "room_id": rid,
        "timestamp": time.time(),
        "source": source,
    })
    for hint in hints:
        await sse_broadcaster.publish({
            "type": "message",
            "room_id": rid,
            "msg_id": hint,
            "timestamp": time.time(),
            "source": source,
        })

    resolved_name = (channel_name or "").strip()
    if not resolved_name:
        for item in (await _channel_name_hints()).get(rid, []):
            resolved_name = item
            break

    scheduled = 0
    if new_replies:
        queue = [{
            "room_id": rid,
            "channel_name": resolved_name or rid,
        }]
        scheduled = await _schedule_background_narration_prewarm(
            queue=queue,
            new_replies_by_room={rid: new_replies},
            supervised_room_ids=set(),
        )

    return {
        "success": True,
        "room_id": rid,
        "channel_name": resolved_name or None,
        "source": source,
        "new_real_agent_replies": len(new_replies or []),
        "prewarm_scheduled": int(scheduled or 0),
        "had_baseline": had_baseline,
        "hint_count": len(hints),
        "stale_rejections": len(stale_message_ids),
        "stale_message_ids": stale_message_ids,
    }


async def _drain_webhook_room_batch(room_id: str) -> None:
    """Coalesce a short same-room burst into one Rocket.Chat history fetch."""
    if WEBHOOK_DEBOUNCE_SECONDS:
        await asyncio.sleep(WEBHOOK_DEBOUNCE_SECONDS)
    batch = WEBHOOK_PENDING_BATCHES.pop(room_id, None)
    if not batch:
        return
    items = batch.get("items") or []
    futures = [item[1] for item in items]
    hints = list(dict.fromkeys(
        str(item[0].get("message_id") or "").strip()
        for item in items
        if str(item[0].get("message_id") or "").strip()
    ))
    channel_name = next(
        (
            str(item[0].get("channel_name") or "").strip()
            for item in items
            if str(item[0].get("channel_name") or "").strip()
        ),
        "",
    )
    if len(items) > 1:
        update_webhook_state(increments={"coalesced_count": len(items) - 1})
    try:
        async with WEBHOOK_WAKE_SEMAPHORE:
            result = await process_rocket_chat_message_wake(
                room_id,
                channel_name=channel_name or None,
                hint_message_ids=hints,
                source="rc_webhook",
            )
    except Exception as error:
        for future in futures:
            if not future.done():
                future.set_exception(error)
        return
    stale_ids = set(result.get("stale_message_ids") or [])
    for normalized, future in items:
        if not future.done():
            event_result = dict(result)
            event_result["stale_rejections"] = (
                1 if str(normalized.get("message_id") or "") in stale_ids else 0
            )
            future.set_result(event_result)


async def _enqueue_webhook_wake(normalized: Dict[str, str]) -> Dict[str, Any]:
    """Queue one authenticated event and await its room's coalesced refresh."""
    room_id = str(normalized.get("room_id") or "").strip()
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    batch = WEBHOOK_PENDING_BATCHES.get(room_id)
    if not batch:
        batch = {"items": []}
        WEBHOOK_PENDING_BATCHES[room_id] = batch
        batch["task"] = asyncio.create_task(_drain_webhook_room_batch(room_id))
    batch["items"].append((dict(normalized), future))
    return await future


async def _channel_name_hints() -> Dict[str, List[str]]:
    """Best-effort room_id -> channel name map from the live RC room list."""
    hints: Dict[str, List[str]] = {}
    base_url = get_rc_base_url()
    headers = {
        "X-Auth-Token": GATEWAY_RC_AUTH_TOKEN or RC_AUTH_TOKEN,
        "X-User-Id": GATEWAY_RC_USER_ID or RC_USER_ID,
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/api/v1/rooms.get?count=100", headers=headers)
            if resp.status_code != 200 or not resp.json().get("success"):
                return hints
            for room in resp.json().get("update", []):
                rid = str(room.get("_id") or "").strip()
                name = str(room.get("name") or room.get("fname") or "").strip()
                if rid and name:
                    hints.setdefault(rid, []).append(name)
    except Exception as err:
        logger.debug("Could not build channel name hints for webhook wake: %s", err)
    return hints


@app.post("/api/rc/webhook/message")
async def rocket_chat_message_webhook(request: Request, payload: Dict[str, Any] = Body(...)):
    """Rocket.Chat outgoing-integration wake-up for message-sent events.

    Configure RC: Administration → Integrations → Outgoing → Event: Message Sent
    → URL: http://<gateway-host>:6891/api/rc/webhook/message
    → Token: same value as GATEWAY_RC_WEBHOOK_SECRET (min 32 chars).

    Live experiment (required before relying on this path):
    1) Ed user message → webhook received, no prewarm.
    2) Real ACLI agent reply → webhook received, prewarm scheduled.
    3) Heartbeat/routing operational message → webhook optional; never prewarm.
    If (2) never fires because RC skips bot-flagged messages, keep DDP as primary.
    """
    if not webhook_configured():
        raise HTTPException(
            status_code=503,
            detail="Rocket.Chat webhook is not configured (set GATEWAY_RC_WEBHOOK_SECRET)",
        )

    headers = {k.lower(): v for k, v in request.headers.items()}
    received_at = time.time()
    try:
        normalized = authenticate_webhook_request(headers, payload if isinstance(payload, dict) else {})
        update_webhook_state(
            received_at=received_at,
            increments={"received_count": 1},
        )
    except WebhookReplayError as err:
        update_webhook_state(
            received_at=received_at,
            increments={"received_count": 1, "replayed_count": 1},
        )
        # Idempotent success: RC retries must not re-run work or return 5xx.
        logger.info("Ignoring replayed Rocket.Chat webhook: %s", err)
        return {
            "success": True,
            "replayed": True,
            "detail": str(err),
        }
    except WebhookAuthError as err:
        update_webhook_state(
            increments={"auth_failures": 1},
            error=str(err),
        )
        raise HTTPException(status_code=401, detail=str(err))

    if webhook_event_is_stale(normalized, now=received_at):
        update_webhook_state(
            increments={"stale_rejections": 1},
            last_event={
                "message_id": normalized["message_id"],
                "room_id": normalized["room_id"],
                "verified_at": None,
                "new_real_agent_replies": 0,
                "prewarm_scheduled": 0,
                "stale": True,
            },
            error=f"Rejected stale webhook event {normalized['message_id']}",
        )
        return {
            "success": True,
            "replayed": False,
            "stale": True,
            "room_id": normalized["room_id"],
        }

    try:
        result = await _enqueue_webhook_wake(normalized)
        verified_at = time.time()
        update_webhook_state(
            verified_at=verified_at,
            covered_room_id=normalized["room_id"],
            increments={
                "verified_count": 1,
                "stale_rejections": int(result.get("stale_rejections") or 0),
                "real_agent_replies": int(result.get("new_real_agent_replies") or 0),
                "prewarm_scheduled": int(result.get("prewarm_scheduled") or 0),
            },
            last_event={
                "message_id": normalized["message_id"],
                "room_id": normalized["room_id"],
                "verified_at": verified_at,
                "new_real_agent_replies": int(result.get("new_real_agent_replies") or 0),
                "prewarm_scheduled": int(result.get("prewarm_scheduled") or 0),
                "stale": bool(result.get("stale_rejections")),
            },
        )
        result["replayed"] = False
        result["user_name"] = normalized.get("user_name") or None
        result["bot_flag"] = normalized.get("bot") == "1"
    except Exception as error:
        rollback_message_id(normalized.get("message_id") or "")
        update_webhook_state(
            increments={"processing_failures": 1},
            error=f"{type(error).__name__}: {error}",
        )
        raise
    return result


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

            # Format and filter rooms with recency timestamps & unread evaluation
            rooms = []
            for room in raw_rooms:
                room_type = room.get("t")
                if room_type in ("c", "p"):
                    rid = room.get("_id")
                    cname = room.get("name") or room.get("fname", "Unnamed")
                    cached_msgs = ROOM_MESSAGES_CACHE.get(rid, [])
                    unread_eval = evaluate_room_unread_status(rid, cached_msgs)
                    rooms.append({
                        "id": rid,
                        "name": cname,
                        "type": room_type,
                        "lm": room.get("lm"),
                        "_updatedAt": room.get("_updatedAt") or room.get("lm"),
                        "has_unread": unread_eval["has_unread"],
                        "has_unseen_real_activity": unread_eval["has_unseen_real_activity"],
                        "unread_count": unread_eval["unread_count"],
                        "last_real_message_at": unread_eval["last_real_message_at"],
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

@app.post("/api/read_cursor")
async def post_read_cursor(payload: Dict[str, Any] = Body(...)):
    """POST /api/read_cursor: Update read cursor for a room when viewed by Ed."""
    room_id = payload.get("room_id")
    if not room_id:
        raise HTTPException(status_code=400, detail="room_id is required")
    msg_id = payload.get("message_id")
    ts = payload.get("timestamp")
    cursor = update_read_cursor(room_id, msg_id=msg_id, ts=ts, actor="ed")
    return {"success": True, "read_cursor": cursor}

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

    # Build room lookup.  Rocket.Chat's lm/_updatedAt is used only as a cheap
    # cache invalidation marker; it is never used as conversation recency.
    room_by_cname: Dict[str, Dict[str, Any]] = {}
    room_marker_by_id: Dict[str, str] = {}
    for r in rc_rooms:
        rname = r.get("name") or r.get("fname")
        if rname:
            cname_key = rname.lower()
            room_by_cname[cname_key] = r
            room_id = r.get("_id")
            if room_id:
                room_marker_by_id[room_id] = str(r.get("lm") or r.get("_updatedAt") or "")

    # A configured Rocket.Chat room is live even when its optional ACLI matter
    # folder is not mounted in this container.  Add only missing live rooms to
    # the in-memory registry used for this request; explicit inactive registry
    # entries remain authoritative and no registry file is modified.
    registered_names = {
        str(item.get("channel_name") or item.get("name") or "").casefold()
        for item in channel_registry
    }
    configured_names = {str(name).casefold() for name in config.channels}
    for room in room_by_cname.values():
        room_name = str(room.get("name") or room.get("fname") or "").strip()
        room_key = room_name.casefold()
        if room_name and room_key in configured_names and room_key not in registered_names:
            channel_registry.append({
                "channel_name": room_name,
                "active": True,
                "source": "rocket_chat",
            })
            registered_names.add(room_key)

    # /api/history normally hydrates the supervisor, but the browser polls that
    # endpoint only for the focused room. Refresh active tasks here as part of
    # the all-channel attention poll so a background response can clear Busy
    # before the queue is scored.
    active_supervised_room_ids = {
        task.room_id
        for task in task_supervisor.all()
        if task.state in {TaskState.POSTED, TaskState.ROUTED, TaskState.WORKING}
    }
    narration_active_cnames = {
        cname.lower()
        for cname, entry in config.channels.items()
        if entry.narration_active and entry.attention_active
    }
    attention_active_cnames = {
        cname.lower()
        for cname, entry in config.channels.items()
        if entry.attention_active
    }
    background_room_ids = {
        room.get("_id")
        for room in room_by_cname.values()
        if room.get("_id")
        and (
            room.get("_id") in active_supervised_room_ids
            or (room.get("name") or room.get("fname") or "").lower() in narration_active_cnames
        )
    }
    refreshed_real_agent_replies: Dict[str, List[dict]] = {}
    if background_room_ids:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                background_room_id_list = sorted(background_room_ids)
                refreshes = [
                    _refresh_active_task_room(client, room_id)
                    for room_id in background_room_id_list
                ]
                results = await asyncio.gather(*refreshes, return_exceptions=True)
                for room_id, result in zip(background_room_id_list, results):
                    if isinstance(result, Exception):
                        logger.debug("Could not refresh a background task room: %s", result)
                    elif result:
                        refreshed_real_agent_replies[room_id] = result
        except Exception as err:
            logger.debug("Could not refresh background task rooms for attention: %s", err)

    # Sync last real conversation timestamps for rooms whose server marker has
    # changed.  The history is classified locally so a routing line, heartbeat,
    # or other system event can never make a room appear freshly active.
    for room_id in background_room_ids:
        if room_id in ROOM_MESSAGES_CACHE:
            ROOM_REAL_ACTIVITY_CACHE[room_id] = {
                "marker": room_marker_by_id.get(room_id, ""),
                "last_real_message_at": get_last_real_conversation_timestamp(ROOM_MESSAGES_CACHE[room_id]),
            }

    rooms_to_refresh_activity = [
        room.get("_id")
        for cname, room in room_by_cname.items()
        if room.get("_id")
        and cname in attention_active_cnames
        and ROOM_REAL_ACTIVITY_CACHE.get(room["_id"], {}).get("marker") != room_marker_by_id.get(room["_id"], "")
    ]
    if rooms_to_refresh_activity:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                results = await asyncio.gather(
                    *[_refresh_room_real_activity(client, room_id) for room_id in rooms_to_refresh_activity],
                    return_exceptions=True,
                )
            for room_id, result in zip(rooms_to_refresh_activity, results):
                if isinstance(result, Exception):
                    logger.debug("Could not refresh real activity for room %s: %s", room_id, result)
                    continue
                ROOM_REAL_ACTIVITY_CACHE[room_id] = {
                    "marker": room_marker_by_id.get(room_id, ""),
                    "last_real_message_at": get_last_real_conversation_timestamp(ROOM_MESSAGES_CACHE.get(room_id, [])),
                }
        except Exception as err:
            logger.debug("Could not refresh channel real activity: %s", err)

    # Collect task supervisor summaries per channel & compute unread status
    room_summaries: Dict[str, Dict[str, Any]] = {}
    room_unread_map: Dict[str, Dict[str, Any]] = {}
    real_activity_map: Dict[str, Optional[float]] = {}
    # Attention configuration can legitimately know about a Rocket.Chat room
    # whose ACLI matter folder is not mounted in this Gateway container.  Those
    # rooms were previously omitted here, leaving the queue item "orphaned"
    # with no room id or unread state even while /api/rooms displayed "New".
    # Merge registry and attention-config names so live RC state remains the
    # source of truth for rail ordering regardless of matter-folder access.
    tracked_channel_names: Dict[str, str] = {}
    for citem in channel_registry:
        cname = str(citem.get("channel_name") or citem.get("name") or "").strip()
        if cname:
            tracked_channel_names[cname.casefold()] = cname
    for cname in config.channels:
        clean_name = str(cname or "").strip()
        if clean_name:
            tracked_channel_names.setdefault(clean_name.casefold(), clean_name)

    for cname in tracked_channel_names.values():
        cname_lower = cname.lower()
        matched_rc_room = room_by_cname.get(cname_lower)
        room_id = matched_rc_room.get("_id") if matched_rc_room else None

        if room_id:
            summary = task_supervisor.get_channel_attention_summary(room_id)
            cached_msgs = ROOM_MESSAGES_CACHE.get(room_id, [])
            unread_eval = evaluate_room_unread_status(room_id, cached_msgs)
            real_activity_map[cname] = ROOM_REAL_ACTIVITY_CACHE.get(room_id, {}).get("last_real_message_at")
        else:
            summary = {
                "room_id": None,
                "is_busy": False,
                "working_since": None,
                "attention_state": AttentionState.UNKNOWN,
                "ready_since": None,
                "active_task_count": 0,
            }
            unread_eval = {
                "has_unread": False,
                "unread_count": 0,
                "last_agent_reply_ts": None,
                "has_unseen_real_activity": False,
            }
            real_activity_map[cname] = None

        room_summaries[cname] = summary
        room_unread_map[cname] = unread_eval
        if room_id:
            room_unread_map[room_id] = unread_eval

    # Build attention queue items
    queue = build_attention_queue(
        config=config,
        channel_registry=channel_registry,
        room_summaries=room_summaries,
        room_activity_map=real_activity_map,
        now=now,
        room_unread_map=room_unread_map,
    )

    if refreshed_real_agent_replies:
        await _schedule_background_narration_prewarm(
            queue,
            refreshed_real_agent_replies,
            background_room_ids,
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


async def _refresh_active_task_room(client: httpx.AsyncClient, room_id: str) -> List[dict]:
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
        raise RuntimeError(f"Failed to fetch history for room {room_id}, status {response.status_code}")
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(f"Failed to fetch history for room {room_id}, payload success=False")

    had_cache_baseline = room_id in ROOM_MESSAGES_CACHE
    previous_message_ids = {
        str(message.get("id") or message.get("_id") or "")
        for message in ROOM_MESSAGES_CACHE.get(room_id, [])
    }
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
    ROOM_MESSAGES_CACHE[room_id] = cleaned_messages
    if not had_cache_baseline:
        # The first refresh establishes a volatile baseline only. Without this,
        # a Gateway restart could spend the prewarm budget narrating old history.
        return []
    return [
        message for message in cleaned_messages
        if str(message.get("id") or message.get("_id") or "") not in previous_message_ids
        and is_real_agent_reply(message)
    ]


async def _refresh_room_real_activity(client: httpx.AsyncClient, room_id: str) -> Optional[float]:
    """Refresh only the timestamp needed to order active portfolio channels.

    This intentionally remains separate from active-task hydration: it does not
    change the scope of narration/task monitoring, but it lets the rail inspect
    the newest substantive conversation in every active configured channel.
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
        return None
    payload = response.json()
    if not payload.get("success"):
        return None

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
    ROOM_MESSAGES_CACHE[room_id] = cleaned_messages
    return get_last_real_conversation_timestamp(cleaned_messages)


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
    ROOM_MESSAGES_CACHE[room_id] = cleaned_messages

    # Loading the visible room means Ed has reviewed every substantive message
    # on the latest page, including Ed's own posts. Marking only agent replies
    # can leave a room artificially "unseen" when its newest real message came
    # from Ed (especially for the room restored at startup).
    if offset == 0 and not effective_latest:
        real_messages = [m for m in cleaned_messages if is_real_conversation_message(m)]
        latest_m = max(real_messages, key=_extract_msg_timestamp) if real_messages else None
    else:
        latest_m = None
    if latest_m:
        m_id = str(latest_m.get("id") or latest_m.get("_id") or "")
        m_ts = _extract_msg_timestamp(latest_m)
        update_read_cursor(room_id, msg_id=m_id, ts=m_ts, actor="ed")

    return {
        "success": True,
        "room_id": room_id,
        "count": len(cleaned_messages),
        "offset": offset,
        "has_more": has_more,
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
        + "The suggested_message must optimize for forward progress through the existing "
          "goal and plan. In coding channels, do not turn minor or non-blocking findings "
          "into another review cycle: preserve them in the existing record and make the next "
          "bounded implementation step the main ask. Only a material blocker may interrupt "
          "the AGY implementation -> Grok one-pass review -> AGY next-step loop. After AGY "
          "remediates a material blocker, route the closure review to Codex, never back to "
          "Grok. Count the bounded history since the last Claude overall review: after two or "
          "three completed implementation steps, or a substantial accumulation of changes, "
          "route one broader integration review to Claude, then resume the next step if no "
          "blocker remains. Use phase=closure for the Codex post-remediation check and "
          "phase=overall_review for the periodic Claude review.\n"
        + "Also produce exactly two quick_suggestions for the composer chip row. Each "
          "item needs a 1–2 word label (very short; e.g. Double-check, Next action, Your "
          "take, Overall plan, Green light?, Fix gaps, Pressure-test) and a full command "
          "string Ed can click to drop into the input. Prefer commands that start with "
          "@worker when another agent should act next. These chips are not the long "
          "suggested_message draft; they are alternate one-click asks. Match the situation: "
          "coding phases (one-pass review, next implementation step, blocker remediation, "
          "post-remediation closure, periodic checkpoint) vs "
          "non-coding (go deeper, assumptions, alternatives, clarify, another viewpoint). "
          "In coding channels, suggestions may implement, review, remediate, or supervise. "
          "In non-coding channels, do not imitate that workflow: ask useful questions, deepen "
          "the discussion, or invite a genuinely different perspective without framing another "
          "agent as a formal reviewer or requiring an agent rotation."
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

        room_id, trigger_message_id = key
        await sse_broadcaster.publish({
            "type": "prewarm_ready",
            "room_id": room_id,
            "msg_id": trigger_message_id,
            "timestamp": time.time(),
        })
        return result
    finally:
        async with response_assistant_idempotency_lock:
            current = response_assistant_inflight.get(key)
            if current is asyncio.current_task():
                response_assistant_inflight.pop(key, None)


def _log_background_prewarm_result(task: asyncio.Task) -> None:
    """Consume a detached background task exception without retrying it."""
    if task.cancelled():
        return
    try:
        error = task.exception()
    except asyncio.CancelledError:
        return
    if error is not None:
        logger.warning("Background narration prewarm failed: %s", error)


async def _schedule_background_narration_prewarm(
    queue: List[Dict[str, Any]],
    new_replies_by_room: Dict[str, List[dict]],
    supervised_room_ids: set,
) -> int:
    """Start bounded text-only prewarm jobs for newly observed real replies.

    This is called by the all-channel attention refresh after it has already
    classified the room history. It never refetches history, never writes audio,
    and uses the same idempotency key as a browser request.
    """
    config = load_narration_prewarm_config()
    if not config["enabled"]:
        return 0

    eligible_rooms = set()
    if config["scope"] == "supervised":
        eligible_rooms = set(supervised_room_ids)
    else:
        eligible_rooms = {
            item["room_id"]
            for item in queue
            if item.get("room_id")
            and item.get("queue_category") == "ranked"
            and item.get("rank") is not None
            and item["rank"] <= config["top_n"]
        }

    # Explicitly include any channels configured with narration_active=True
    attn_cfg = load_channel_attention_config()
    narration_active_cnames = {
        cname.lower()
        for cname, entry in attn_cfg.channels.items()
        if entry.narration_active and entry.attention_active
    }
    for item in queue:
        rid = item.get("room_id")
        cname = str(item.get("channel_name") or "").lower()
        if rid and cname in narration_active_cnames:
            eligible_rooms.add(rid)

    candidates = []
    channel_name_by_room = {
        item["room_id"]: item.get("channel_name")
        for item in queue
        if item.get("room_id")
    }
    for room_id, replies in new_replies_by_room.items():
        if room_id not in eligible_rooms or not replies:
            continue
        newest_reply = max(replies, key=lambda message: _extract_msg_timestamp(message))
        trigger_message_id = str(newest_reply.get("id") or newest_reply.get("_id") or "")
        if trigger_message_id:
            candidates.append((room_id, trigger_message_id))

    rank_by_room = {
        item["room_id"]: item.get("rank") if item.get("rank") is not None else 999999
        for item in queue
        if item.get("room_id")
    }
    candidates.sort(key=lambda pair: rank_by_room.get(pair[0], 999999))

    scheduled = 0
    for room_id, trigger_message_id in candidates:
        key = (room_id, trigger_message_id)
        async with response_assistant_idempotency_lock:
            now = time.monotonic()
            _prune_response_assistant_results(now)
            if key in response_assistant_results or key in response_assistant_inflight:
                continue
            async with narration_prewarm_lock:
                while (
                    narration_prewarm_generation_starts
                    and now - narration_prewarm_generation_starts[0] >= 3600
                ):
                    narration_prewarm_generation_starts.popleft()
                if len(narration_prewarm_generation_starts) >= config["hourly_generation_cap"]:
                    logger.info(
                        "Background narration prewarm cap reached (%s/hour).",
                        config["hourly_generation_cap"],
                    )
                    break
                narration_prewarm_generation_starts.append(now)
                request = ResponseAssistantRequest(
                    messages=ROOM_MESSAGES_CACHE.get(room_id, []),
                    roomId=room_id,
                    room_name=channel_name_by_room.get(room_id),
                    trigger_message_id=trigger_message_id,
                    history_limit=config["history_limit"],
                )
                task = asyncio.create_task(_run_and_cache_response_assistant(key, request))
                response_assistant_inflight[key] = task
                task.add_done_callback(_log_background_prewarm_result)
                scheduled += 1
    return scheduled


@app.get("/api/response-assistant/cached")
async def get_cached_response_assistant(room_id: str, trigger_message_id: str):
    """Return a completed background result without creating a new generation."""
    key = (room_id, trigger_message_id)
    async with response_assistant_idempotency_lock:
        _prune_response_assistant_results(time.monotonic())
        cached = response_assistant_results.get(key)
        if cached is None:
            raise HTTPException(status_code=404, detail="No prepared narration for this reply")
        return {"success": True, "result": dict(cached[1])}


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
