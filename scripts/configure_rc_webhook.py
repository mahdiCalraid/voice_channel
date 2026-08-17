#!/usr/bin/env python3
"""Idempotently configure the Voice Gateway Rocket.Chat outgoing webhook.

Secrets are read from environment variables or explicitly supplied env files and
are never printed.  The default scope is public channels only; private groups and
direct messages require a separate, deliberate rollout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


INTEGRATION_TYPE = "webhook-outgoing"
DEFAULT_NAME = "voice_test"
DEFAULT_SCOPE = "all_public_channels"
DEFAULT_URL = "http://host.docker.internal:6891/api/rc/webhook/message"
DEFAULT_USERNAME = "acli_bot"


class ConfigurationError(RuntimeError):
    pass


def read_env_file(path: Optional[Path]) -> Dict[str, str]:
    """Read dotenv assignments plus the RC stack's legacy ``Key: value`` lines."""
    values: Dict[str, str] = {}
    if path is None:
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ConfigurationError(f"Could not read env file {path}: {error}") from error
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        separator = "=" if "=" in line else (":" if ":" in line else "")
        if not separator:
            continue
        key, value = line.split(separator, 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            values[key] = value
    return values


def setting(
    name: str,
    *sources: Mapping[str, str],
    aliases: tuple[str, ...] = (),
) -> str:
    for source in sources:
        for candidate in (name, *aliases):
            value = str(source.get(candidate) or "").strip()
            if value:
                return value
    return ""


class RocketChatClient:
    def __init__(self, base_url: str, user_id: str, auth_token: str, *, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.auth_token = auth_token
        self.timeout = timeout

    @classmethod
    def authenticate(
        cls,
        base_url: str,
        *,
        user_id: str = "",
        auth_token: str = "",
        username: str = "",
        password: str = "",
        timeout: int = 15,
    ) -> "RocketChatClient":
        if user_id and auth_token:
            return cls(base_url, user_id, auth_token, timeout=timeout)
        if not username or not password:
            raise ConfigurationError(
                "Rocket.Chat admin credentials are missing; provide user-id/token or username/password"
            )
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/v1/login",
            data=json.dumps({"user": username, "password": password}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        payload = cls._execute(request, timeout=timeout)
        if payload.get("status") != "success":
            raise ConfigurationError("Rocket.Chat admin login failed")
        data = payload.get("data") or {}
        return cls(
            base_url,
            str(data.get("userId") or ""),
            str(data.get("authToken") or ""),
            timeout=timeout,
        )

    @staticmethod
    def _execute(request: urllib.request.Request, *, timeout: int) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read().decode("utf-8"))
                detail = body.get("error") or body.get("message") or "request rejected"
            except (ValueError, OSError):
                detail = "request rejected"
            raise ConfigurationError(f"Rocket.Chat HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise ConfigurationError(f"Rocket.Chat request failed: {error}") from error
        if payload.get("success") is False:
            raise ConfigurationError(
                f"Rocket.Chat rejected the request: {payload.get('error') or 'unknown error'}"
            )
        return payload

    def request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {
            "X-User-Id": self.user_id,
            "X-Auth-Token": self.auth_token,
            "Content-Type": "application/json",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        return self._execute(request, timeout=self.timeout)

    def find_outgoing_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        query = urllib.parse.urlencode({"name": name, "type": INTEGRATION_TYPE})
        payload = self.request("GET", f"/api/v1/integrations.list?{query}")
        matches = [
            item
            for item in payload.get("integrations") or []
            if item.get("type") == INTEGRATION_TYPE and item.get("name") == name
        ]
        if len(matches) > 1:
            raise ConfigurationError(f"Multiple outgoing integrations are named {name!r}")
        return matches[0] if matches else None

    def create(self, desired: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("POST", "/api/v1/integrations.create", mutation_payload(desired))

    def update(self, integration_id: str, desired: Dict[str, Any]) -> Dict[str, Any]:
        return self.request(
            "PUT",
            "/api/v1/integrations.update",
            {**mutation_payload(desired), "integrationId": integration_id},
        )


def desired_integration(
    *,
    name: str,
    scope: str,
    webhook_url: str,
    webhook_secret: str,
    username: str,
    enabled: bool = True,
) -> Dict[str, Any]:
    if len(webhook_secret) < 32:
        raise ConfigurationError("GATEWAY_RC_WEBHOOK_SECRET must contain at least 32 characters")
    return {
        "type": INTEGRATION_TYPE,
        "name": name,
        "enabled": bool(enabled),
        "username": username,
        "scriptEnabled": False,
        "script": "",
        "responding": "",
        "channel": scope,
        "urls": [webhook_url],
        "event": "sendMessage",
        "triggerWords": "",
        "token": webhook_secret,
    }


def mutation_payload(desired: Dict[str, Any]) -> Dict[str, Any]:
    """Return only fields accepted by Rocket.Chat's current REST write schema."""
    writable = (
        "type",
        "name",
        "enabled",
        "username",
        "scriptEnabled",
        "script",
        "channel",
        "urls",
        "event",
        "triggerWords",
        "token",
    )
    return {field: desired[field] for field in writable}


def _normalized_channel(value: Any) -> list[str]:
    if isinstance(value, list):
        return sorted(str(item).strip() for item in value if str(item).strip())
    return sorted(item.strip() for item in str(value or "").split(",") if item.strip())


def integration_drift(current: Optional[Dict[str, Any]], desired: Dict[str, Any]) -> list[str]:
    if current is None:
        return ["integration_missing"]
    drift: list[str] = []
    exact_fields = (
        "type",
        "name",
        "enabled",
        "username",
        "scriptEnabled",
        "script",
        "responding",
        "urls",
        "event",
        "token",
    )
    for field in exact_fields:
        current_value = current.get(field)
        desired_value = desired.get(field)
        if field in ("script", "responding"):
            current_value = current_value or ""
            desired_value = desired_value or ""
        if field == "urls":
            current_value = sorted(current_value or [])
            desired_value = sorted(desired_value or [])
        if current_value != desired_value:
            drift.append(field)
    if _normalized_channel(current.get("channel")) != _normalized_channel(desired.get("channel")):
        drift.append("channel")
    current_triggers = current.get("triggerWords") or []
    if isinstance(current_triggers, str):
        current_triggers = [item for item in current_triggers.split(",") if item]
    if current_triggers:
        drift.append("triggerWords")
    return sorted(set(drift))


def redacted_plan(desired: Dict[str, Any]) -> Dict[str, Any]:
    return {**desired, "token": "<redacted>"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rc-env-file", type=Path, help="Rocket.Chat admin env file")
    parser.add_argument("--gateway-env-file", type=Path, help="Voice Gateway secret env file")
    parser.add_argument("--rc-url", help="Rocket.Chat base URL")
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--webhook-url", default=DEFAULT_URL)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument(
        "--disabled",
        action="store_true",
        help="Temporarily disable the named integration (default is enabled)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show sanitized planned changes")
    mode.add_argument("--verify", action="store_true", help="Fail if live configuration differs")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    rc_file = read_env_file(args.rc_env_file)
    gateway_file = read_env_file(args.gateway_env_file)
    process_env = dict(os.environ)

    rc_url = args.rc_url or setting(
        "RC_ADMIN_URL",
        process_env,
        rc_file,
        aliases=("ROOT_URL", "ACLI_RC_URL"),
    )
    webhook_secret = setting(
        "GATEWAY_RC_WEBHOOK_SECRET",
        process_env,
        gateway_file,
    )
    if not rc_url:
        raise ConfigurationError("Rocket.Chat URL is missing")

    user_id = setting("RC_ADMIN_USER_ID", process_env, rc_file)
    auth_token = setting("RC_ADMIN_AUTH_TOKEN", process_env, rc_file)
    admin_username = setting(
        "RC_ADMIN_USERNAME",
        process_env,
        rc_file,
        aliases=("Email", "Username"),
    )
    admin_password = setting(
        "RC_ADMIN_PASSWORD",
        process_env,
        rc_file,
        aliases=("Password", "password"),
    )
    desired = desired_integration(
        name=args.name,
        scope=args.scope,
        webhook_url=args.webhook_url,
        webhook_secret=webhook_secret,
        username=args.username,
        enabled=not args.disabled,
    )
    client = RocketChatClient.authenticate(
        rc_url,
        user_id=user_id,
        auth_token=auth_token,
        username=admin_username,
        password=admin_password,
    )
    current = client.find_outgoing_by_name(args.name)
    drift = integration_drift(current, desired)

    if args.dry_run:
        print(json.dumps({
            "mode": "dry_run",
            "action": "create" if current is None else ("update" if drift else "none"),
            "integration_id": (current or {}).get("_id"),
            "drift": drift,
            "desired": redacted_plan(desired),
        }, indent=2, sort_keys=True))
        return 0

    if args.verify:
        print(json.dumps({
            "verified": not drift,
            "integration_id": (current or {}).get("_id"),
            "drift": drift,
        }, indent=2, sort_keys=True))
        return 0 if not drift else 2

    if current is None:
        client.create(desired)
        action = "created"
    elif drift:
        client.update(str(current.get("_id") or ""), desired)
        action = "updated"
    else:
        action = "unchanged"

    verified = client.find_outgoing_by_name(args.name)
    remaining_drift = integration_drift(verified, desired)
    print(json.dumps({
        "action": action,
        "verified": not remaining_drift,
        "integration_id": (verified or {}).get("_id"),
        "remaining_drift": remaining_drift,
    }, indent=2, sort_keys=True))
    return 0 if not remaining_drift else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigurationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
