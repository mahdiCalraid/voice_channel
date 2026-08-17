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
ACLI_MATTERS_REGISTRY_CANDIDATES = (
    Path(os.environ.get("ACLI_MATTERS_REGISTRY", "")),
    Path("/approved_projects/clawd_2/development_channel/acli_matters.json"),
    Path("/Users/ed/King/clawd_2/development_channel/acli_matters.json"),
)
# NemoClaw is a separate nc2 system, not an ACLI matter that the Gateway should
# expose as a selectable/supervised channel.
EXCLUDED_ACLI_MATTER_FOLDERS = {"nemoclaw_nc"}
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
- The overriding goal is steady forward progress through the existing plan. Prefer the
  next useful implementation step whenever no material blocker prevents it. Do not let
  minor findings create a review-remediation-review loop.
- Planning: gather only the distinct views still needed to make the plan executable.
  Once the plan is ready, ask AGY to implement its next bounded, named step.
- Normal delivery loop: AGY implements one bounded step, then Grok performs one focused,
  independent review of that step. If Grok finds no blocking problem, immediately ask
  AGY to implement the next already-planned step.
- Minor or non-blocking findings: preserve them in the existing task record, plan,
  implementation notes, or next AGY report as appropriate, then continue to the next
  planned step. Do not send them to another agent merely for discussion, do not demand
  immediate cleanup, and do not ask Grok to review the same step again.
- A finding blocks progress only when it breaks core behavior or an acceptance criterion,
  creates a material security/data/correctness risk, or requires substantial rework before
  later steps can safely build on it. For such a serious finding, ask AGY for one clearly
  named remediation. After AGY reports that remediation complete, ask Codex for the
  closure review; do not return it to Grok. Once Codex confirms that no blocker remains,
  resume with AGY on the next planned step.
- After two or three successfully delivered small steps, or whenever the accumulated code
  change becomes substantial, ask Claude for a broader integration and direction review
  across those steps. This is a periodic checkpoint, not a review after every step. If
  Claude finds no material blocker, resume immediately with AGY on the next planned step.
- In this daily-use workflow, AGY is the coder, Grok is the one-pass reviewer for ordinary
  implementation steps, Codex is the planner/moderator and post-remediation closure
  reviewer, and Claude is the periodic overall reviewer. Treat an explicit channel-specific
  assignment or Ed's latest instruction as a higher-priority override.
- Preserve the channel's own task IDs. Never invent completion, a task ID, or a green
  light. The next draft must be concrete and grounded in what the latest response says.
"""

NONCODING_SEQUENCE = """
Ed's non-coding-channel strategy:
- Do not apply the coding implementation/review/supervision rotation. A response in a
  business, content, planning, research, outreach, or personal channel does not normally
  need verification, approval, or a formal review from another agent.
- Continue the conversation with the most useful question. Good next moves include asking
  the responding agent to explain its reasoning, develop an idea, compare options, expose
  assumptions, or clarify an important uncertainty.
- When a genuinely different perspective would add value, ask another suitable worker for
  its viewpoint, alternative framing, concern, or question. Phrase this as exploration,
  not as a reviewer checking another agent's work. Do not create a mandatory agent rotation.
- Make the suggested message specific to the subject being discussed. Avoid generic asks
  such as "review this," "verify this," "give a green light," or "what is the next step?"
  when a sharper question can advance the thinking.
- Prefer staying with the current or default worker when the conversation mainly needs
  elaboration. Switch workers only to gain a meaningfully different perspective or skill.
- Leave consequential decisions to Ed. Do not draft an instruction that submits,
  publishes, contacts someone, spends money, or makes an irreversible change.
"""

COMMON_RULES = """
Rules for every suggestion:
- Produce a draft for Ed to review, never a message that has already been sent.
- The exact draft must begin with one @worker mention and contain the full actionable ask.
- Do not include greetings, commentary about being an AI, or a confirmation question.
- Do not merely say "continue"; name the evidence to verify or the bounded outcome wanted.
- When forward progress is allowed, make the next concrete work step the main ask. Mention
  non-blocking findings only as items to record or carry forward, never as a reason to stop.
- Prefer the cheapest existing model/effort for routine work. Do not add a !model
  command unless the current model is known and a switch is genuinely necessary.
- Do not quote system routing/heartbeat messages as project facts.
"""


def _automatic_system_file(filename: str) -> Optional[Path]:
    for root in AUTOMATIC_SYSTEM_CANDIDATES:
        if not str(root):
            continue
        candidate = root / filename
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            # Optional host paths may exist in the registry without being
            # mounted or readable by the Gateway process.
            continue
    return None


def _matter_file_candidates(folder_path: str) -> List[Path]:
    """Return host and container paths for one ACLI-registered matter."""
    configured = Path(folder_path)
    candidates = [configured]
    projects_root = os.environ.get("VOICE_GATEWAY_PROJECTS_ROOT", "").strip()
    if projects_root:
        try:
            candidates.append(
                Path(projects_root) / configured.relative_to("/Users/ed/King")
            )
        except ValueError:
            pass
    return [candidate / "acli" / "matter.json" for candidate in candidates]


def load_channel_registry() -> Tuple[List[dict], str]:
    """Load configured channels and merge ACLI-registered matters.

    ``automatic_system/channels.json`` is a useful strategy registry, but it is
    intentionally not rewritten every time ACLI onboards a matter.  The ACLI
    matter registry is therefore the durable discovery source for newly added
    rooms.  Merging it here keeps the Gateway's model controls and room
    supervision usable immediately after a matter is registered.
    """
    live_path = _automatic_system_file("channels.json")
    source = live_path or BUNDLED_CHANNELS_PATH
    channels: List[dict] = []
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        configured_channels = data.get("channels", [])
        if isinstance(configured_channels, list):
            channels = [item for item in configured_channels if isinstance(item, dict)]
    except (OSError, ValueError, TypeError):
        channels = []

    channel_keys = {
        str(item.get("channel_name") or "").strip().casefold()
        for item in channels
    }
    discovered = []
    for candidate in ACLI_MATTERS_REGISTRY_CANDIDATES:
        if not str(candidate):
            continue
        try:
            registry = json.loads(candidate.read_text(encoding="utf-8"))
            matter_paths = registry.get("matters", [])
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(matter_paths, list):
            continue

        for raw_path in matter_paths:
            folder_path = str(raw_path or "").strip()
            if not folder_path:
                continue
            if Path(folder_path).name.casefold() in EXCLUDED_ACLI_MATTER_FOLDERS:
                continue
            matter_data = None
            for matter_file in _matter_file_candidates(folder_path):
                try:
                    loaded = json.loads(matter_file.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    # Try the mapped/container candidate when a registered
                    # host path is absent or inaccessible to this process.
                    continue
                if isinstance(loaded, dict):
                    matter_data = loaded
                    break
            if matter_data is None:
                # Do not fabricate a channel without its ACLI contract; the
                # restart mount synchronizer handles inaccessible host paths.
                continue
            channel = matter_data.get("channel")
            channel_name = str((channel or {}).get("name") or "").strip()
            if not channel_name or channel_name.casefold() in channel_keys:
                continue
            discovered.append({
                "channel_name": channel_name,
                "folder_path": folder_path,
                "channel_type": "acli_noncoding",
                "active": True,
                "roles": None,
                "default_worker": str(
                    matter_data.get("default_agent") or "codex"
                ).strip().lower(),
                "notes": "Discovered from ACLI's registered matter registry.",
                "strategy_source": str(candidate),
            })
            channel_keys.add(channel_name.casefold())
        break

    return channels + discovered, f"{source}; ACLI matters"


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
    if profile.get("channel_type") != "acli_coding" and not profile.get("registered"):
        return [default]
    agents = ["codex", "claude", "grok", "agy"]
    roles = profile.get("roles") or {}
    agents.extend(str(value).lower() for value in roles.values() if value)
    agents.append(default)
    return list(dict.fromkeys(agents))


def build_strategy_context(profile: dict) -> str:
    is_coding = profile.get("channel_type") == "acli_coding"
    channel_json = json.dumps(
        {
            "channel_name": profile.get("channel_name"),
            "channel_type": profile.get("channel_type"),
            "default_worker": profile.get("default_worker"),
            "registry_roles": profile.get("roles"),
            "effective_suggestion_roles": (
                {
                    "coder": "agy",
                    "one_pass_reviewer": "grok",
                    "post_remediation_reviewer": "codex",
                    "periodic_overall_reviewer": "claude",
                }
                if is_coding
                else None
            ),
            "notes": profile.get("notes"),
            "registered": profile.get("registered"),
        },
        indent=2,
    )
    sequence = CODING_SEQUENCE if is_coding else NONCODING_SEQUENCE
    return (
        f"=== CHANNEL PROFILE ===\n{channel_json}\n\n"
        f"=== WORKFLOW ===\n{sequence.strip()}\n\n"
        f"=== COMMON RULES ===\n{COMMON_RULES.strip()}"
    )


def normalize_narrator_summary(text: str) -> str:
    """Normalize the high-level spoken summary to one bounded paragraph."""
    cleaned = re.sub(r"```(?:json)?|```", "", str(text or "")).strip()
    summary = re.sub(r"\s+", " ", cleaned).strip()
    if not summary:
        summary = "No substantive agent update was available to summarize."
    words = summary.split()
    return " ".join(words[:100]) + ("…" if len(words) > 100 else "")


def normalize_attention_items(value: Any) -> List[dict]:
    """Accept only material, typed attention items from the model."""
    if not isinstance(value, list):
        return []

    allowed_types = {"decision", "issue", "clarification", "approval"}
    normalized = []
    seen = set()
    for item in value[:4]:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if item_type not in allowed_types or not text or item_type in seen:
            continue
        severity = str(item.get("severity") or "").strip().lower() or None
        if item_type == "issue":
            if severity not in {"major", "moderate"}:
                continue
        else:
            severity = None
        words = text.split()
        normalized.append({
            "type": item_type,
            "severity": severity,
            "text": " ".join(words[:45]) + ("…" if len(words) > 45 else ""),
        })
        seen.add(item_type)
    return normalized


def format_narrator_digest(summary: str, attention_items: List[dict]) -> str:
    """Return only the spoken summary; attention items stay structured and separate.

    ``attention_items`` remains in the signature for callers that may still pass
    the parsed list, but it must never become narrator/TTS text. The API returns
    those items independently for attention surfaces.
    """
    del attention_items
    return normalize_narrator_summary(summary)


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


def _contains_serious_blocker(text: str) -> bool:
    """Recognize evidence that should actually stop the coding delivery loop."""
    normalized = re.sub(r"\s+", " ", str(text or "").lower())
    for negated in (
        "no blocker",
        "no blocking issue",
        "no material blocker",
        "no serious issue",
        "no critical issue",
        "no blockers",
        "without blockers",
        "blocker is resolved",
        "blocker resolved",
        "blockers are resolved",
        "blockers resolved",
    ):
        normalized = normalized.replace(negated, "")
    return any(
        phrase in normalized
        for phrase in (
            "cannot proceed",
            "must be fixed before",
            "blocking issue",
            "blocking finding",
            "material blocker",
            "serious blocker",
            "critical issue",
            "critical defect",
            "major defect",
            "security risk",
            "data loss",
            "core behavior fails",
            "core behavior is broken",
            "failed acceptance criterion",
            "acceptance criterion fails",
            "substantial rework",
            "substantial gap",
        )
    ) or bool(re.search(r"\bblockers?\b", normalized))


def _looks_like_completed_remediation(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").lower())
    explicit = (
        "remediation complete",
        "remediation completed",
        "blocker resolved",
        "blockers resolved",
        "blocking issue fixed",
        "blocking issue resolved",
        "serious issue fixed",
        "critical issue fixed",
    )
    if any(phrase in normalized for phrase in explicit):
        return True
    return "remediation" in normalized and any(
        word in normalized for word in ("implemented", "fixed", "resolved", "completed", "done")
    )


def fallback_suggestion(profile: dict, latest_message: dict) -> Tuple[str, str, str]:
    latest_agent = str((latest_message.get("event") or {}).get("agent") or latest_message.get("username") or "").lower()
    text = str(latest_message.get("text") or "").lower()
    channel_type = profile.get("channel_type")

    if channel_type != "acli_coding":
        default = str(profile.get("default_worker") or "codex").lower()
        if profile.get("registered"):
            # Prefer a second perspective when the default worker just responded;
            # otherwise continue with the channel specialist.
            alternatives = [default, "claude", "codex", "grok", "agy"]
            agent = next((name for name in alternatives if name != latest_agent), default)
        else:
            agent = default
        return (
            agent,
            f"@{agent} What is your perspective on the central idea in the latest response, "
            "and what important question or alternative viewpoint should Ed consider?",
            "noncoding",
        )

    serious_blocker = _contains_serious_blocker(text)

    if latest_agent == "agy" and _looks_like_completed_remediation(text):
        return (
            "codex",
            "@codex Please perform the closure review of AGY's completed remediation against the original blocking finding. Confirm whether the blocker is resolved, record any non-blocking follow-ups, and identify the next planned step we can safely resume.",
            "closure",
        )
    if latest_agent == "agy":
        return "grok", "@grok Please independently review AGY's reported implementation against the agreed task, inspect the code and tests, and list any concrete gaps before we advance.", "review"
    if latest_agent == "grok":
        if serious_blocker:
            return (
                "agy",
                "@agy Please remediate Grok's blocking finding as one bounded step using the existing task naming, then report the files changed and verification evidence. Do not expand into unrelated cleanup.",
                "remediation",
            )
        return (
            "agy",
            "@agy Record any minor non-blocking review notes in the existing task record, then implement the next bounded step from the agreed plan and report the verification evidence.",
            "implementation",
        )
    if latest_agent == "claude":
        if serious_blocker:
            return "agy", "@agy Please address Claude's material blocker as one bounded remediation step and report the verification evidence before we resume the plan.", "remediation"
        if any(phrase in text for phrase in ("green light", "approved to proceed", "ready to implement", "proceed with implementation", "overall review", "integration review", "previous steps", "accumulated changes", "checkpoint")):
            return "agy", "@agy Please implement the next bounded step from the agreed plan, preserve the existing task naming, and report the files changed and verification evidence.", "implementation"
        return "grok", "@grok Please independently pressure-test the current plan, identify concrete technical risks or missing acceptance checks, and say what must be resolved before implementation.", "planning"
    if latest_agent == "codex" and serious_blocker:
        return "agy", "@agy Please resolve the material blocker Codex confirmed as one bounded remediation step and report verification evidence.", "remediation"
    if latest_agent == "codex" and any(
        phrase in text
        for phrase in ("closure review", "remediation", "blocker is resolved", "no blocker", "cleared", "ready to resume")
    ):
        return "agy", "@agy Please implement the next bounded step from the agreed plan and carry any recorded non-blocking follow-ups without delaying forward progress.", "implementation"
    return "claude", "@claude Please review the latest plan or recommendation, add any important strategic concerns, and identify what must be settled before implementation begins.", "planning"


def _normalize_quick_label(label: Any) -> str:
    """Keep smart-chip labels to one or two short words."""
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    text = re.sub(r"[^\w\s\-?/]", "", text)
    words = [w for w in text.split(" ") if w][:2]
    cleaned = " ".join(words).strip(" -")
    if not cleaned or len(cleaned) > 22:
        return ""
    return cleaned


def _normalize_quick_command(command: Any, allowed_agents: set) -> str:
    text = re.sub(r"\s+", " ", str(command or "")).strip()
    if not text or len(text) > 500:
        return ""
    match = re.match(r"^@([a-zA-Z0-9_]+)\s+(.+)$", text)
    if match:
        agent = match.group(1).lower()
        if agent not in allowed_agents:
            return ""
        body = match.group(2).strip()
        if not body:
            return ""
        return f"@{agent} {body}"
    return text


def fallback_quick_suggestions(profile: dict, latest_message: dict, phase: str) -> List[dict]:
    """Phase-aware 1–2 word chips when the model omits or botches smart suggestions."""
    latest_agent = str(
        (latest_message.get("event") or {}).get("agent")
        or latest_message.get("username")
        or ""
    ).lower()
    channel_type = profile.get("channel_type")
    default_worker = str(profile.get("default_worker") or "codex").lower()

    if channel_type != "acli_coding":
        allowed = allowed_suggestion_agents(profile)
        perspective_worker = next(
            (agent for agent in allowed if agent != latest_agent),
            default_worker,
        )
        followup_worker = latest_agent if latest_agent in allowed else default_worker
        return [
            {
                "id": "smart-0",
                "label": "Go deeper",
                "command": f"@{followup_worker} Which assumption or part of your latest response deserves a deeper explanation?",
            },
            {
                "id": "smart-1",
                "label": "Another view",
                "command": f"@{perspective_worker} What different viewpoint or important question would you add to the latest response?",
            },
        ]

    if phase == "closure":
        return [
            {
                "id": "smart-0",
                "label": "Closure check",
                "command": "@codex Confirm whether the remediated blocker is closed, record non-blocking follow-ups, and name the next step we can resume.",
            },
            {
                "id": "smart-1",
                "label": "Resume plan",
                "command": "@agy Implement the next bounded step if the blocking issue is now resolved.",
            },
        ]
    if phase == "overall_review":
        return [
            {
                "id": "smart-0",
                "label": "Overall view",
                "command": "@claude Review the last two or three delivered steps together, identify any material integration concern, and advise whether to continue the plan.",
            },
            {
                "id": "smart-1",
                "label": "Resume plan",
                "command": "@agy If the accumulated changes have no material blocker, implement the next bounded step from the agreed plan.",
            },
        ]
    if phase == "review":
        return [
            {
                "id": "smart-0",
                "label": "Double-check",
                "command": "@grok Independently double-check the implementation against the agreed task and list concrete gaps.",
            },
            {
                "id": "smart-1",
                "label": "Next step",
                "command": "@agy If this implementation step already has a clean review, proceed with the next bounded step in the plan.",
            },
        ]
    if phase == "remediation":
        return [
            {
                "id": "smart-0",
                "label": "Fix blocker",
                "command": "@agy Remediate the blocking finding as one bounded step and report verification evidence.",
            },
            {
                "id": "smart-1",
                "label": "Bounded fix",
                "command": "@agy Fix only the material blocker, preserve unrelated notes for later, and report verification evidence.",
            },
        ]
    if phase == "implementation":
        return [
            {
                "id": "smart-0",
                "label": "Next step",
                "command": "@agy Implement the next bounded step from the agreed plan and report verification evidence.",
            },
            {
                "id": "smart-1",
                "label": "Keep moving",
                "command": "@agy Record any non-blocking review notes, then continue with the next planned implementation step.",
            },
        ]
    if phase == "planning" or latest_agent == "codex":
        return [
            {
                "id": "smart-0",
                "label": "Pressure-test",
                "command": "@grok Pressure-test the current plan and name what must be resolved first.",
            },
            {
                "id": "smart-1",
                "label": "Overall plan",
                "command": "@claude Where are we in the overall plan, and what is the next decision?",
            },
        ]
    return [
        {
            "id": "smart-0",
            "label": "Next action",
            "command": "What is the single next action we should take now?",
        },
        {
            "id": "smart-1",
            "label": "Your take",
            "command": f"@{default_worker} What is your opinion on the latest update?",
        },
    ]


def normalize_quick_suggestions(
    raw_items: Any,
    profile: dict,
    latest_message: dict,
    phase: str,
) -> Tuple[List[dict], bool]:
    """Return exactly two smart chips; ai_valid is True only when both AI items were usable."""
    allowed = set(allowed_suggestion_agents(profile))
    cleaned: List[dict] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            label = _normalize_quick_label(item.get("label") or item.get("title"))
            command = _normalize_quick_command(
                item.get("command") or item.get("message") or item.get("text"),
                allowed,
            )
            if not label or not command:
                continue
            # Avoid two chips with the same label.
            if any(existing["label"].casefold() == label.casefold() for existing in cleaned):
                continue
            cleaned.append({
                "id": f"smart-{len(cleaned)}",
                "label": label,
                "command": command,
            })
            if len(cleaned) >= 2:
                break

    ai_valid = len(cleaned) == 2
    if not ai_valid:
        cleaned = fallback_quick_suggestions(profile, latest_message, phase)
    return cleaned[:2], ai_valid


def parse_response_assistant_output(output: str, profile: dict, latest_message: dict) -> dict:
    parsed = _extract_json_object(output)
    fallback_agent, fallback_message, fallback_phase = fallback_suggestion(profile, latest_message)
    allowed = set(allowed_suggestion_agents(profile))

    raw_digest = parsed.get("digest")
    ai_digest_valid = isinstance(raw_digest, str) and bool(raw_digest.strip())
    summary = normalize_narrator_summary(raw_digest if ai_digest_valid else "")
    attention_items = normalize_attention_items(parsed.get("attention_items"))
    digest = format_narrator_digest(summary, attention_items)

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
        "closure",
        "overall_review",
        "noncoding",
    }
    if phase not in allowed_phases:
        phase = fallback_phase
    rationale = re.sub(r"\s+", " ", str(parsed.get("rationale") or "")).strip()
    quick_suggestions, ai_quick_valid = normalize_quick_suggestions(
        parsed.get("quick_suggestions"),
        profile,
        latest_message,
        phase,
    )
    return {
        "digest": digest,
        "attention_items": attention_items,
        "suggested_agent": agent,
        "suggested_message": message,
        "quick_suggestions": quick_suggestions,
        "phase": phase,
        "rationale": rationale,
        "_ai_digest_valid": ai_digest_valid,
        "_ai_suggestion_valid": ai_suggestion_valid,
        "_ai_quick_valid": ai_quick_valid,
    }
