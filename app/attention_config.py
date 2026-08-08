"""Durable Operator-Owned Channel Attention Configuration for Voice Channel (U-10a).

Manages operator-set attention parameters at acli/gateway_state/channel_attention_config.json
(or custom path via CHANNEL_ATTENTION_CONFIG_PATH).
"""

from __future__ import annotations

import json
import os
import fcntl
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, Extra, validator, root_validator
from app.contracts import BaseContractModel, CURRENT_SCHEMA_VERSION

DEFAULT_CONFIG_PATH = os.path.join("acli", "gateway_state", "channel_attention_config.json")


def get_channel_attention_config_path() -> str:
    return os.environ.get("CHANNEL_ATTENTION_CONFIG_PATH") or DEFAULT_CONFIG_PATH


class UrgencyLevel(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


def _validate_iso_timestamp(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("Timestamp must be a string or null")
    v_str = v.strip()
    if not v_str:
        return None
    try:
        dt = datetime.fromisoformat(v_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("Timestamp must be timezone-aware (e.g. including 'Z' or '+00:00')")
    except Exception as e:
        raise ValueError(f"Invalid timezone-aware ISO 8601 timestamp '{v}': {e}")
    return v_str


class ChannelAttentionEntry(BaseContractModel):
    attention_active: bool = True
    base_importance: int = Field(default=3)
    urgency: UrgencyLevel = Field(default=UrgencyLevel.NORMAL)
    deadline: Optional[str] = None
    blocking: bool = False
    temporary_boost_until: Optional[str] = None
    snoozed_until: Optional[str] = None
    visible: bool = True
    narration_active: bool = False
    voice_active: bool = False

    class Config:
        extra = Extra.forbid

    @validator("base_importance", pre=True)
    def check_importance_range(cls, v: Any) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"base_importance must be an integer between 1 and 5, got {type(v).__name__}")
        if v < 1 or v > 5:
            raise ValueError(f"base_importance must be between 1 and 5, got {v}")
        return v

    @validator("deadline", "temporary_boost_until", "snoozed_until", pre=True)
    def check_timestamps(cls, v: Any) -> Optional[str]:
        return _validate_iso_timestamp(v)

    @root_validator(skip_on_failure=True)
    def normalize_channel_automation(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        if values.get("voice_active"):
            values["narration_active"] = True
        return values


class ChannelAttentionConfig(BaseContractModel):
    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION)
    channels: Dict[str, ChannelAttentionEntry] = Field(default_factory=dict)

    class Config:
        extra = Extra.forbid

    @validator("schema_version")
    def check_schema_version(cls, v: str) -> str:
        if v != CURRENT_SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema_version '{v}', expected '{CURRENT_SCHEMA_VERSION}'")
        return v

    @validator("channels", pre=True)
    def check_channels(cls, v: Any) -> Dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("channels must be a dictionary")
        # Check for case-insensitive duplicate channel names
        seen_lower = {}
        for key in v.keys():
            lower_key = str(key).strip().lower()
            if lower_key in seen_lower:
                raise ValueError(f"Duplicate channel name in configuration: '{key}' conflicts with '{seen_lower[lower_key]}'")
            seen_lower[lower_key] = key
        return v


def _model_dump(model: BaseModel) -> Dict[str, Any]:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _model_validate(model_cls: Any, data: Any) -> Any:
    return model_cls.model_validate(data) if hasattr(model_cls, "model_validate") else model_cls.parse_obj(data)


def load_channel_attention_config(config_path: Optional[str] = None) -> ChannelAttentionConfig:
    path_str = config_path or get_channel_attention_config_path()
    path = Path(path_str)
    if not path.exists():
        return ChannelAttentionConfig(schema_version=CURRENT_SCHEMA_VERSION, channels={})
    
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_SH)
        try:
            content = path.read_text(encoding="utf-8")
            if not content.strip():
                return ChannelAttentionConfig(schema_version=CURRENT_SCHEMA_VERSION, channels={})
            data = json.loads(content)
            return _model_validate(ChannelAttentionConfig, data)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def save_channel_attention_config(config: ChannelAttentionConfig, config_path: Optional[str] = None) -> None:
    # Ensure valid config object before saving
    if not isinstance(config, ChannelAttentionConfig):
        config = _model_validate(ChannelAttentionConfig, _model_dump(config))

    path_str = config_path or get_channel_attention_config_path()
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    payload = _model_dump(config)

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp_path, path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def resolve_merged_channel_attention_status(
    config: ChannelAttentionConfig,
    channel_registry: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Merges operator attention configuration with registry entries without mutating either source.
    
    Status definitions:
    - 'active': Present in registry, active in registry, present in attention config, attention_active=True.
    - 'inactive_in_attention': Present in registry, active in registry, present in attention config, attention_active=False.
    - 'disabled_by_registry': Present in registry with active=False in registry.
    - 'unconfigured': Present in registry, but missing from attention config.
    - 'orphaned': Present in attention config, but missing from channel registry.
    """
    registry_map = {}
    for entry in channel_registry:
        cname = entry.get("channel_name") or entry.get("name")
        if cname:
            registry_map[cname.lower()] = (cname, entry)

    config_map = {}
    for cname, entry in config.channels.items():
        config_map[cname.lower()] = (cname, entry)

    merged: Dict[str, Dict[str, Any]] = {}
    all_keys = set(registry_map.keys()) | set(config_map.keys())

    for key in sorted(all_keys):
        in_reg = key in registry_map
        in_cfg = key in config_map

        if in_reg and not in_cfg:
            orig_name, reg_entry = registry_map[key]
            reg_active = reg_entry.get("active", True)
            status = "disabled_by_registry" if reg_active is False else "unconfigured"
            merged[orig_name] = {
                "channel_name": orig_name,
                "status": status,
                "registry_active": reg_active,
                "attention_active": None,
                "entry": None
            }
        elif in_cfg and not in_reg:
            orig_name, cfg_entry = config_map[key]
            merged[orig_name] = {
                "channel_name": orig_name,
                "status": "orphaned",
                "registry_active": None,
                "attention_active": cfg_entry.attention_active,
                "entry": _model_dump(cfg_entry)
            }
        else:
            orig_name, reg_entry = registry_map[key]
            _, cfg_entry = config_map[key]
            reg_active = reg_entry.get("active", True)
            if reg_active is False:
                status = "disabled_by_registry"
            elif cfg_entry.attention_active:
                status = "active"
            else:
                status = "inactive_in_attention"

            merged[orig_name] = {
                "channel_name": orig_name,
                "status": status,
                "registry_active": reg_active,
                "attention_active": cfg_entry.attention_active,
                "entry": _model_dump(cfg_entry)
            }

    return merged
