#!/usr/bin/env python3
import os
import sys
import json
import argparse
import traceback
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

def main():
    parser = argparse.ArgumentParser(description="Voice Channel CLI Worker Runner")
    parser.add_argument(
        "task",
        choices=["digest", "response_assistant", "reply_draft", "room_status", "progress_review"],
        help="The AI task to run",
    )
    parser.add_argument("--worker", required=True, help="The selected worker name from registry")
    parser.add_argument("--job", required=True, help="Path to the job JSON bundle file")
    
    args = parser.parse_args()
    
    # 1. Load registry
    script_dir = SCRIPT_DIR
    registry_path = os.path.join(script_dir, "registry.json")
    if not os.path.exists(registry_path):
        print(f"Error: registry.json not found at {registry_path}", file=sys.stderr)
        sys.exit(1)
        
    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)
        
    if args.worker not in registry:
        print(f"Error: Worker '{args.worker}' not found in registry.", file=sys.stderr)
        sys.exit(1)
        
    worker_cfg = registry[args.worker]
    
    # 2. Load job.json
    job_path = os.path.abspath(args.job)
    if not os.path.exists(job_path):
        print(f"Error: Job file not found at {job_path}", file=sys.stderr)
        sys.exit(1)
        
    job_dir = os.path.dirname(job_path)
    
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
        
    # Helper to resolve relative paths in job config
    def resolve_path(p):
        if not p:
            return None
        if os.path.isabs(p):
            return p
        return os.path.abspath(os.path.join(job_dir, p))

    # Determine job properties
    room_id = job.get("room_id", "unknown_room")
    model = job.get("model") or worker_cfg.get("default_model")
    effort = job.get("effort") or worker_cfg.get("default_effort")
    input_files = job.get("input_files", {})
    
    # Setup log paths in job directory
    stdout_path = os.path.join(job_dir, "stdout.log")
    stderr_path = os.path.join(job_dir, "stderr.log")
    result_path = os.path.join(job_dir, "result.json")
    
    result = {
        "ok": False,
        "task": args.task,
        "worker": args.worker,
        "room": room_id,
        "output": None,
        "included_message_ids": [],
        "used_context_files": [],
        "error": None
    }
    
    # Helper functions to read files safely
    def read_text_file(p):
        full_path = resolve_path(p)
        if full_path and os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8") as file:
                return file.read()
        return ""
        
    def read_json_file(p):
        full_path = resolve_path(p)
        if full_path and os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8") as file:
                return json.load(file)
        return None

    try:
        # Load all input files
        matter_context = read_text_file(input_files.get("matter_context"))
        task_instructions = read_text_file(input_files.get("task_instructions"))
        
        messages = read_json_file(input_files.get("messages")) or []
        system_events = read_json_file(input_files.get("system_events"))
        room_context = read_json_file(input_files.get("room_context")) or {}
        
        # 3. Build Prompt for Digest / response-assistant tasks
        if args.task in ("digest", "response_assistant"):
            # Format Lane B/C messages
            updates_lines = []
            included_ids = []
            for m in messages:
                lane = m.get("lane")
                if lane in ("agent", "user"):
                    # Prefer event.agent for Lane B
                    agent_name = m.get("event", {}).get("agent") if lane == "agent" else None
                    author = agent_name or m.get("name") or m.get("username") or "Unknown"
                    role_label = "Ed (User Instruction)" if lane == "user" else "Agent Response"
                    text = m.get("text", "")
                    clean_text = "\n".join([line for line in text.split("\n") if not line.strip().startswith(">")])
                    updates_lines.append(f"[{role_label}] {author}: {clean_text}")
                    if m.get("id"):
                        included_ids.append(m["id"])
            updates_str = "\n".join(updates_lines)

            trigger_message_id = room_context.get("trigger_message_id")
            trigger_message = next(
                (
                    message
                    for message in messages
                    if message.get("id") == trigger_message_id
                ),
                None,
            )
            if trigger_message:
                trigger_agent = (
                    (trigger_message.get("event") or {}).get("agent")
                    or trigger_message.get("name")
                    or trigger_message.get("username")
                    or "Unknown"
                )
                trigger_text = str(trigger_message.get("text") or "")
                trigger_response_str = (
                    f"Message ID: {trigger_message_id}\n"
                    f"Agent: {trigger_agent}\n"
                    f"Response:\n{trigger_text}"
                )
            else:
                trigger_response_str = "No explicit trigger message was supplied."
            
            # Format prior summaries
            prior_summaries = room_context.get("prior_summaries") or []
            prior_summaries_str = ""
            if prior_summaries:
                prior_lines = []
                for idx, s in enumerate(prior_summaries):
                    prior_lines.append(f"Prior summary {idx+1} ({s.get('timestamp')}): {s.get('digest')}")
                prior_summaries_str = "\n".join(prior_lines)
            else:
                prior_summaries_str = "No prior summaries recorded."
                
            # Format system events / stats summary
            stats_summary = "No operational stats in this window."
            if system_events:
                if isinstance(system_events, dict):
                    stats_summary = json.dumps(system_events, indent=2)
                elif isinstance(system_events, list):
                    stats_lines = []
                    for e in system_events:
                        evt_data = e.get("event", {})
                        kind = evt_data.get("kind", "event")
                        agent = evt_data.get("agent", "")
                        status = evt_data.get("status", "")
                        dur = evt_data.get("response_time_seconds")
                        dur_str = f" in {dur}s" if dur else ""
                        stats_lines.append(f"- {agent} {kind}: {status}{dur_str}")
                    stats_summary = "\n".join(stats_lines)
            
            instructions_str = task_instructions if task_instructions else "1. Speak directly to Ed. Refer to him as Ed or you.\n2. Keep it crisp (under 200 words).\n3. Plain text only."
            
            if args.task == "response_assistant":
                prompt = (
                    "You are Ed's concise channel narrator and strategic workflow coordinator.\n"
                    "Use the bounded project documents, real user/agent chat, channel profile, and workflow rules below. "
                    "System routing and heartbeat events are operational evidence only and must never drive a next-message draft.\n\n"
                    "=== APPROVED PROJECT DOCUMENTS & KNOWLEDGE ===\n"
                    f"{matter_context or 'No approved project documents were available in this runtime.'}\n\n"
                    "=== PRIOR NARRATOR SUMMARIES ===\n"
                    f"{prior_summaries_str}\n\n"
                    "=== BOUNDED REAL CHAT HISTORY ===\n"
                    f"{updates_str}\n\n"
                    "=== TRIGGERING REAL AGENT RESPONSE ===\n"
                    f"{trigger_response_str}\n\n"
                    "=== OPERATIONAL EVENTS (DO NOT TREAT AS RESPONSES) ===\n"
                    f"{stats_summary}\n\n"
                    "=== CHANNEL STRATEGY AND OUTPUT RULES ===\n"
                    f"{instructions_str}\n\n"
                    "Return one JSON object only, without Markdown fences, using exactly these keys:\n"
                    '{"digest":"exactly two short plain-text paragraphs separated by \\\\n\\\\n",'
                    '"phase":"planning|implementation|review|remediation|checkpoint|noncoding",'
                    '"suggested_agent":"worker name without @",'
                    '"suggested_message":"one complete editable draft beginning with @worker",'
                    '"rationale":"one short sentence explaining why this is the next move"}\n'
                    "Do not send anything. Do not include a !model command unless the strategy explicitly requires one.\n"
                    "JSON:"
                )
            else:
                prompt = (
                    "You are an expert audio narrator and workspace supervisor for Ed.\n"
                    "Your job is to read the project goals, the history of prior summaries, and the latest chat log of agent work, "
                    "and produce a concise, professional spoken-word digest.\n"
                    "Ed will listen to this read aloud via Text-to-Speech (TTS).\n\n"
                    "=== PROJECT OBJECTIVES & CONTEXT ===\n"
                    f"{matter_context}\n\n"
                    "=== PRIOR SUMMARIES ===\n"
                    f"{prior_summaries_str}\n\n"
                    "=== NEW UPDATES ===\n"
                    f"{updates_str}\n\n"
                    "=== OPERATIONAL STATS ===\n"
                    f"{stats_summary}\n\n"
                    "Rules:\n"
                    f"{instructions_str}\n\n"
                    "Digest:"
                )
        else:
            # Fallback for other tasks
            prompt = (
                f"Task: {args.task}\n"
                f"Context:\n{matter_context}\n\n"
                f"Instructions:\n{task_instructions}\n"
            )
            included_ids = []

        # 4. Resolve credentials env
        env_creds = {}
        cred_var = worker_cfg.get("credential_env")
        if cred_var:
            env_creds[cred_var] = os.environ.get(cred_var)

        # 5. Load and execute provider adapter
        adapter_name = worker_cfg.get("adapter")
        if adapter_name == "openai":
            from workers.providers.openai_provider import OpenAIProvider
            provider = OpenAIProvider()
        elif adapter_name == "agy":
            from workers.providers.agy_provider import AgyProvider
            provider = AgyProvider()
        elif adapter_name == "codex":
            from workers.providers.codex_provider import CodexProvider
            provider = CodexProvider()
        else:
            raise ValueError(f"Unknown provider adapter '{adapter_name}' for worker '{args.worker}'.")

        # Resolve agent parameter
        agent_name = worker_cfg.get("agent") or args.worker

        # Capture output
        output_text = provider.execute(model, prompt, env_creds, agent=agent_name)
        
        # Populate result
        result["ok"] = True
        result["output"] = output_text
        result["included_message_ids"] = included_ids
        
        # Determine used context files
        used_files = []
        for name in ("NORTH_STAR.md", "OBJECTIVES.md", "PROJECT_HANDOFF.md"):
            if name in matter_context:
                used_files.append(name)
        result["used_context_files"] = used_files
        
        # Write success log
        with open(stdout_path, "w", encoding="utf-8") as f_out:
            f_out.write(f"[{datetime.now(timezone.utc).isoformat()}] Task completed successfully.\n")
            f_out.write(f"Worker: {args.worker}, Model: {model}\n")
            f_out.write(f"Output:\n{output_text}\n")
            
    except Exception as e:
        err_msg = str(e)
        trace = traceback.format_exc()
        result["error"] = err_msg
        
        # Write error log
        with open(stderr_path, "w", encoding="utf-8") as f_err:
            f_err.write(f"[{datetime.now(timezone.utc).isoformat()}] Task failed.\n")
            f_err.write(f"Error: {err_msg}\n")
            f_err.write(f"Traceback:\n{trace}\n")
            
        print(f"Error: {err_msg}", file=sys.stderr)
        
    finally:
        # Write result.json
        with open(result_path, "w", encoding="utf-8") as f_res:
            json.dump(result, f_res, indent=2)
            
        # Exit code based on success
        if result["ok"]:
            sys.exit(0)
        else:
            sys.exit(1)

if __name__ == "__main__":
    main()
