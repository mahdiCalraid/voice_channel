"""Bounded context and strategy helpers for automatic narration and reply drafting."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


APP_DIR = Path(__file__).resolve().parent
BUNDLED_CHANNELS_PATH = APP_DIR / "channel_strategy.json"
AUTOMATIC_SYSTEM_CANDIDATES = (
    Path(os.environ.get("AUTOMATIC_SYSTEM_DIR", "")),
    Path("/Users/ed/King/automatic_system"),
    Path("/automatic_system"),
)
MAX_CONTEXT_FILE_CHARS = 8_000
MAX_PROJECT_CONTEXT_CHARS = 24_000

PROJECT_CONTEXT_FILES = (
    "PROJECT_HANDOFF.md",
    "URGENT_IMPLEMENTATION_PLAN.md",
    "NORTH_STAR.md",
    "OBJECTIVES.md",
    "acli/supervisor_log.md",
    "acli/matter.json",
    "README.md",
    "ADAPTIVE_HANDOFF.md",
    "IMPLEMENTATION_PLAN.md",
)

CODING_SEQUENCE = """
Ed's current coding-channel strategy (takes precedence over older generic role labels):
- Infer the current phase from the actual conversation; do not advance just because a
  worker replied.
- Planning: gather distinct views without repeating the same question. A useful
  sequence is Codex for the initial plan, Claude for broader judgment, then Grok for
  independent technical criticism.
- Plan ready: ask AGY to implement one bounded named step.
- Implementation reported complete: ask Grok for an independent, evidence-based review.
- Review found small issues: ask Codex to diagnose or make a quick bounded fix.
- Review found meaningful scope: ask AGY for a clearly named remediation step, then
  return to Grok for re-review.
- Clean review or major checkpoint: ask Claude for the overall view and explicit
  green light before advancing.
- After a green light: ask AGY for the next already-planned bounded step.
- In this daily-use workflow, AGY is the usual implementer, Grok the independent
  reviewer, Claude the overall leader/checkpoint supervisor, and Codex the planner,
  moderator, and quick-fix worker. Treat an explicit channel-specific assignment or
  Ed's latest instruction as a higher-priority override.
- Preserve the channel's own task IDs. Never invent completion, a task ID, or a green
  light. The next draft must be concrete and grounded in what the latest response says.
"""

NONCODING_SEQUENCE = """
Ed's non-coding-channel strategy:
- Do not apply the coding plan/review rotation.
- Summarize what changed, then propose one light, practical next message using the
  channel's default worker unless the conversation clearly requires another assigned
  worker.
- Leave consequential decisions to Ed. Do not draft an instruction that submits,
  publishes, contacts someone, spends money, or makes an irreversible change.
"""

COMMON_RULES = """
Rules for every suggestion:
- Produce a draft for Ed to review, never a message that has already been sent.
- The exact draft must begin with one @worker mention and contain the full actionable ask.
- Do not include greetings, commentary about being an AI, or a confirmation question.
- Do not merely say "continue"; name the evidence to verify or the bounded outcome wanted.
- Prefer the cheapest existing model/effort for routine work. Do not add a !model
  command unless the current model is known and a switch is genuinely necessary.
- Do not quote system routing/heartbeat messages as project facts.
"""


def _automatic_system_file(filename: str) -> Optional[Path]:
    for root in AUTOMATIC_SYSTEM_CANDIDATES:
        if not str(root):
            continue
        candidate = root / filename
        if candidate.is_file():
            return candidate
    return None


def load_channel_registry() -> Tuple[List[dict], str]:
    """Load the live registry when available, otherwise the bundled daily-use snapshot."""
    live_path = _automatic_system_file("channels.json")
    source = live_path or BUNDLED_CHANNELS_PATH
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        channels = data.get("channels", [])
        if isinstance(channels, list):
            return channels, str(source)
    except (OSError, ValueError, TypeError):
        pass
    return [], str(source)


def resolve_channel_profile(room_name: Optional[str]) -> dict:
    channels, source = load_channel_registry()
    normalized = (room_name or "").casefold()
    for channel in channels:
        if str(channel.get("channel_name", "")).casefold() == normalized:
            result = dict(channel)
            result["strategy_source"] = source
            result["registered"] = True
            if not result.get("default_worker"):
                result["default_worker"] = "codex"
            return result
    return {
        "channel_name": room_name or "unknown",
        "folder_path": None,
        "channel_type": "acli_noncoding",
        "default_worker": "codex",
        "roles": None,
        "notes": "Channel is not present in the automatic-system registry; use a conservative light-touch suggestion.",
        "strategy_source": source,
        "registered": False,
    }


def _safe_read_under(root: Path, relative_path: str) -> Optional[Tuple[str, str]]:
    try:
        resolved_root = root.resolve()
        candidate = (resolved_root / relative_path).resolve()
        candidate.relative_to(resolved_root)
        if not candidate.is_file():
            return None
        text = candidate.read_text(encoding="utf-8", errors="replace")
        return relative_path, text[:MAX_CONTEXT_FILE_CHARS]
    except (OSError, ValueError):
        return None


def _available_project_roots(profile: dict) -> Iterable[Path]:
    configured = profile.get("folder_path")
    if configured:
        yield Path(str(configured))

    host_root = os.environ.get("VOICE_GATEWAY_PROJECTS_ROOT")
    if host_root and configured:
        try:
            relative = Path(str(configured)).relative_to("/Users/ed/King")
            yield Path(host_root) / relative
        except ValueError:
            pass

    # Compatibility fallback for older containers: this matter's core approved files
    # are mounted individually at /app even when the project-root mounts are absent.
    room_name = str(profile.get("channel_name") or "")
    if room_name.casefold() == "voice_channel":
        yield Path.cwd()


def read_project_context(profile: dict) -> Tuple[str, List[str]]:
    """Read only registered, fixed-name matter documents with strict size bounds."""
    parts: List[str] = []
    used: List[str] = []
    seen_roots = set()

    for root in _available_project_roots(profile):
        try:
            root_key = str(root.resolve())
        except OSError:
            continue
        if root_key in seen_roots or not root.is_dir():
            continue
        seen_roots.add(root_key)

        for relative_path in PROJECT_CONTEXT_FILES:
            result = _safe_read_under(root, relative_path)
            if not result:
                continue
            label, text = result
            block = f"=== {label} ===\n{text}"
            remaining = MAX_PROJECT_CONTEXT_CHARS - sum(len(item) for item in parts)
            if remaining <= 0:
                break
            parts.append(block[:remaining])
            used.append(f"{root_key}/{label}")
        if parts:
            break

    return "\n\n".join(parts), used


def allowed_suggestion_agents(profile: dict) -> List[str]:
    default = str(profile.get("default_worker") or "codex").lower()
    if profile.get("channel_type") != "acli_coding":
        return [default]
    agents = ["codex", "claude", "grok", "agy"]
    roles = profile.get("roles") or {}
    agents.extend(str(value).lower() for value in roles.values() if value)
    agents.append(default)
    return list(dict.fromkeys(agents))


def build_strategy_context(profile: dict) -> str:
    channel_json = json.dumps(
        {
            "channel_name": profile.get("channel_name"),
            "channel_type": profile.get("channel_type"),
            "default_worker": profile.get("default_worker"),
            "roles": profile.get("roles"),
            "notes": profile.get("notes"),
            "registered": profile.get("registered"),
        },
        indent=2,
    )
    sequence = CODING_SEQUENCE if profile.get("channel_type") == "acli_coding" else NONCODING_SEQUENCE
    return (
        f"=== CHANNEL PROFILE ===\n{channel_json}\n\n"
        f"=== WORKFLOW ===\n{sequence.strip()}\n\n"
        f"=== COMMON RULES ===\n{COMMON_RULES.strip()}"
    )


def normalize_two_paragraph_digest(text: str) -> str:
    cleaned = re.sub(r"```(?:json)?|```", "", str(text or "")).strip()
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    if len(paragraphs) == 2:
        normalized = paragraphs
    else:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", " ".join(paragraphs)) if part.strip()]
        if len(sentences) >= 2:
            midpoint = max(1, len(sentences) // 2)
            normalized = [" ".join(sentences[:midpoint]), " ".join(sentences[midpoint:])]
        else:
            one = paragraphs[0] if paragraphs else "No substantive agent update was available to summarize."
            normalized = [
                one,
                "The channel is ready for Ed to review the latest response and choose the next step.",
            ]

    # Prompt compliance is best-effort; enforce the spoken-word size contract at
    # the application boundary as well.
    bounded = []
    for paragraph in normalized:
        words = paragraph.split()
        bounded.append(" ".join(words[:90]) + ("…" if len(words) > 90 else ""))
    return "\n\n".join(bounded)


def _extract_json_object(output: str) -> Dict[str, Any]:
    text = str(output or "").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            pass
    return {}


def fallback_suggestion(profile: dict, latest_message: dict) -> Tuple[str, str, str]:
    latest_agent = str((latest_message.get("event") or {}).get("agent") or latest_message.get("username") or "").lower()
    text = str(latest_message.get("text") or "").lower()
    channel_type = profile.get("channel_type")

    if channel_type != "acli_coding":
        agent = str(profile.get("default_worker") or "codex").lower()
        return agent, f"@{agent} Please review the latest update and propose the most useful low-risk next step for Ed.", "noncoding"

    if latest_agent == "agy":
        return "grok", "@grok Please independently review AGY's reported implementation against the agreed task, inspect the code and tests, and list any concrete gaps before we advance.", "review"
    if latest_agent == "grok":
        if any(phrase in text for phrase in ("additional scope", "larger remediation", "new task", "substantial gap")):
            return "agy", "@agy Please turn Grok's larger findings into one bounded remediation step using the channel's existing task naming, implement it, and report the files changed and verification evidence.", "remediation"
        if any(word in text for word in ("issue", "gap", "fail", "incomplete", "problem", "red flag")):
            return "codex", "@codex Please assess Grok's findings, fix any small in-scope defects you can verify directly, and clearly separate any larger remediation that should go back to AGY.", "remediation"
        return "claude", "@claude Please give the overall checkpoint view based on the plan, implementation, and independent review, and state whether there is a clear green light for the next step.", "checkpoint"
    if latest_agent == "claude":
        if any(phrase in text for phrase in ("green light", "approved to proceed", "ready to implement", "proceed with implementation")):
            return "agy", "@agy Please implement the next bounded step from the agreed plan, preserve the existing task naming, and report the files changed and verification evidence.", "implementation"
        return "grok", "@grok Please independently pressure-test the current plan, identify concrete technical risks or missing acceptance checks, and say what must be resolved before implementation.", "planning"
    if latest_agent == "codex" and any(
        phrase in text
        for phrase in ("implemented", "fixed", "remediation complete", "tests pass", "tests are passing")
    ):
        return "grok", "@grok Please independently re-review Codex's reported fix against the flagged findings and run the relevant tests before we treat the issue as closed.", "review"
    return "claude", "@claude Please review the latest plan or recommendation, add any important strategic concerns, and identify what must be settled before implementation begins.", "planning"


def parse_response_assistant_output(output: str, profile: dict, latest_message: dict) -> dict:
    parsed = _extract_json_object(output)
    fallback_agent, fallback_message, fallback_phase = fallback_suggestion(profile, latest_message)
    allowed = set(allowed_suggestion_agents(profile))

    raw_digest = parsed.get("digest")
    ai_digest_valid = isinstance(raw_digest, str) and bool(raw_digest.strip())
    digest = normalize_two_paragraph_digest(raw_digest if ai_digest_valid else "")

    message = str(parsed.get("suggested_message") or "").strip()
    agent = str(parsed.get("suggested_agent") or "").strip().lstrip("@").lower()
    match = re.match(r"^@([a-zA-Z0-9_]+)\s+(.+)", message, re.DOTALL)
    if match:
        message_agent = match.group(1).lower()
        if not agent:
            agent = message_agent
        if message_agent != agent:
            message = ""
    ai_suggestion_valid = bool(match and message and agent in allowed)
    if not ai_suggestion_valid:
        agent, message = fallback_agent, fallback_message
    elif len(message) > 2_000:
        # The composer is for one focused dispatch, not a second generated report.
        agent, message = fallback_agent, fallback_message
        ai_suggestion_valid = False

    phase = str(parsed.get("phase") or fallback_phase).strip().lower()
    allowed_phases = {
        "planning",
        "implementation",
        "review",
        "remediation",
        "checkpoint",
        "noncoding",
    }
    if phase not in allowed_phases:
        phase = fallback_phase
    rationale = re.sub(r"\s+", " ", str(parsed.get("rationale") or "")).strip()
    return {
        "digest": digest,
        "suggested_agent": agent,
        "suggested_message": message,
        "phase": phase,
        "rationale": rationale,
        "_ai_digest_valid": ai_digest_valid,
        "_ai_suggestion_valid": ai_suggestion_valid,
    }
