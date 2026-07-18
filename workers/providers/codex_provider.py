import json
import os
import shutil
import subprocess
from pathlib import Path

from workers.providers.base_provider import BaseProvider


_CODEX_BIN_CANDIDATES = [
    "codex",
    "/usr/local/bin/codex",
]


def _find_codex_binary() -> str:
    for candidate in _CODEX_BIN_CANDIDATES:
        if "/" in candidate:
            if Path(candidate).exists():
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    raise FileNotFoundError(
        "The 'codex' CLI executable could not be found. Checked: "
        + ", ".join(_CODEX_BIN_CANDIDATES)
    )


class CodexProvider(BaseProvider):
    def execute(self, model: str, prompt: str, env_creds: dict, agent: str = None) -> str:
        codex_bin = _find_codex_binary()
        process_env = os.environ.copy()
        process_env.setdefault("TERM", "xterm-256color")

        cmd = [
            codex_bin,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
        ]
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)

        result = subprocess.run(
            cmd,
            env=process_env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            error_msg = self._extract_error(result.stdout, result.stderr, result.returncode)
            raise RuntimeError(f"codex CLI execution failed: {error_msg}")

        response = self._extract_response(result.stdout)
        if not response:
            raise RuntimeError("codex CLI returned success but no assistant response was found in JSON output.")
        return response

    @staticmethod
    def _extract_error(stdout: str, stderr: str, returncode: int) -> str:
        extracted = CodexProvider._find_jsonl_error(stdout)
        if extracted:
            return extracted
        message = (stderr or "").strip()
        if message:
            return message
        return f"Process exited with code {returncode}"

    @staticmethod
    def _find_jsonl_error(stdout: str) -> str:
        for line in reversed((stdout or "").splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("type") == "error":
                return str(obj.get("message") or "").strip()
            if obj.get("type") == "turn.failed":
                err = obj.get("error") or {}
                return str(err.get("message") or "").strip()
        return ""

    @staticmethod
    def _extract_response(stdout: str) -> str:
        response_text = ""
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            raw_type = obj.get("type")
            payload = obj.get("payload") or {}
            item = obj.get("item") or {}
            if raw_type == "item.completed" and item.get("type") == "agent_message":
                message = item.get("text")
                if isinstance(message, str) and message.strip():
                    response_text = message.strip()
            if raw_type == "event_msg":
                payload_type = payload.get("type")
                if payload_type == "agent_message":
                    message = payload.get("message")
                    if isinstance(message, str) and message.strip():
                        response_text = message.strip()
                elif payload_type == "task_complete":
                    message = payload.get("last_agent_message")
                    if isinstance(message, str) and message.strip():
                        response_text = message.strip()
            elif raw_type == "response_item" and payload.get("type") == "message" and payload.get("role") == "assistant":
                parts = payload.get("content") or []
                text_parts = []
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in {"output_text", "text"}:
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            text_parts.append(text.strip())
                if text_parts:
                    response_text = "\n".join(text_parts)
        return response_text.strip()
