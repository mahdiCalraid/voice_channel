"""Versioned Gateway Contracts for Voice Channel.

Defines versioned data models for interaction requests, interpretations,
confirmation snapshots, task states, events, and gateway results according to
Section 4 of the Adaptive Implementation Plan.
"""

from __future__ import annotations

import time
from enum import Enum
from uuid import uuid4
from typing import Optional, List, Dict, Any, Set
from pydantic import BaseModel, Field, validator, Extra

CURRENT_SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS: Set[str] = {CURRENT_SCHEMA_VERSION}

def validate_schema_version(version: str) -> str:
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"Unsupported schema version: '{version}'. Supported: {sorted(list(SUPPORTED_SCHEMA_VERSIONS))}"
        )
    return version

class InputMode(str, Enum):
    TEXT = "text"
    VOICE = "voice"

class PermissionTier(str, Enum):
    READ = "read"
    PREPARE = "prepare"
    COMMIT = "commit"
    PRIVILEGED = "privileged"

class TaskState(str, Enum):
    CAPTURED = "captured"
    INTERPRETING = "interpreting"
    NEEDS_CLARIFICATION = "needs_clarification"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    POSTED = "posted"
    ROUTED = "routed"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    SUMMARIZED = "summarized"
    SPOKEN = "spoken"

class AttentionState(str, Enum):
    UNKNOWN = "unknown"
    NONE = "none"
    NEEDS_REVIEW = "needs_review"
    NEEDS_DECISION = "needs_decision"
    NEEDS_HELP = "needs_help"
    READY_FOR_INSTRUCTION = "ready_for_instruction"

class BaseContractModel(BaseModel):
    class Config:
        extra = Extra.forbid

class InteractionRequest(BaseContractModel):
    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION)
    interaction_id: str = Field(default_factory=lambda: f"int_{uuid4().hex[:12]}")
    actor_id: str = Field(default="user")
    client_id: str = Field(default="browser_console")
    input_mode: InputMode = Field(default=InputMode.TEXT)
    raw_input: str
    created_at: float = Field(default_factory=time.time)
    requested_room_id: Optional[str] = None
    requested_agent: Optional[str] = None

    @validator("schema_version")
    def check_schema_version(cls, v: str) -> str:
        return validate_schema_version(v)

    @validator("raw_input")
    def check_raw_input_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("raw_input cannot be empty")
        return v

class Interpretation(BaseContractModel):
    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION)
    selected_action: str
    selected_room_id: Optional[str] = None
    selected_agent: Optional[str] = None
    refined_draft: str = ""
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    alternative_destinations: List[str] = Field(default_factory=list)
    requires_clarification: bool = False
    audit_explanation: str = ""
    source_context_ids: List[str] = Field(default_factory=list)

    @validator("schema_version")
    def check_schema_version(cls, v: str) -> str:
        return validate_schema_version(v)

class ConfirmationSnapshot(BaseContractModel):
    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION)
    immutable_interaction_id: str
    room_id: str
    agent: str
    exact_message: str
    permission_tier: PermissionTier = Field(default=PermissionTier.COMMIT)
    expires_at: float
    nonce: str

    class Config:
        frozen = True
        extra = Extra.forbid

    @validator("schema_version")
    def check_schema_version(cls, v: str) -> str:
        return validate_schema_version(v)

    @validator("exact_message")
    def check_message_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("exact_message cannot be empty")
        return v

    def is_expired(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        return now >= self.expires_at

class TaskEvent(BaseContractModel):
    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION)
    event_id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    interaction_id: str
    timestamp: float = Field(default_factory=time.time)
    state: TaskState
    source_message_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)

    @validator("schema_version")
    def check_schema_version(cls, v: str) -> str:
        return validate_schema_version(v)


class TaskRecord(BaseContractModel):
    """Durable gateway-side state for one interaction tracked through Rocket.Chat."""

    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION)
    interaction_id: str
    actor_id: str
    client_id: str
    room_id: str
    agent: str
    state: TaskState = Field(default=TaskState.CAPTURED)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    rocket_chat_msg_ids: List[str] = Field(default_factory=list)
    source_message_ids: List[str] = Field(default_factory=list)
    task_events: List[TaskEvent] = Field(default_factory=list)
    confirmation_snapshot: Optional[ConfirmationSnapshot] = None
    terminal_summary: str = ""
    attention_state: AttentionState = Field(default=AttentionState.UNKNOWN)
    working_since: Optional[float] = None
    ready_since: Optional[float] = None

    @validator("schema_version")
    def check_schema_version(cls, v: str) -> str:
        return validate_schema_version(v)

    @validator("attention_state", pre=True, always=True)
    def check_attention_state_migration(cls, v: Any, values: Dict[str, Any]) -> AttentionState:
        if v and isinstance(v, (str, AttentionState)) and v != AttentionState.UNKNOWN.value:
            return AttentionState(v)
        state = values.get("state")
        if state in {TaskState.POSTED, TaskState.ROUTED, TaskState.WORKING}:
            return AttentionState.NONE
        elif state == TaskState.COMPLETED:
            return AttentionState.NEEDS_REVIEW
        elif state == TaskState.FAILED:
            return AttentionState.NEEDS_HELP
        elif state in {TaskState.AWAITING_CONFIRMATION, TaskState.NEEDS_CLARIFICATION}:
            return AttentionState.NEEDS_DECISION
        elif state in {TaskState.CANCELLED, TaskState.SUPERSEDED}:
            return AttentionState.NONE
        return AttentionState.UNKNOWN

class GatewayResult(BaseContractModel):
    schema_version: str = Field(default=CURRENT_SCHEMA_VERSION)
    status: TaskState = Field(default=TaskState.COMPLETED)
    selected_room_id: Optional[str] = None
    selected_agent: Optional[str] = None
    rocket_chat_msg_ids: List[str] = Field(default_factory=list)
    task_events: List[TaskEvent] = Field(default_factory=list)
    confirmation_snapshot: Optional[ConfirmationSnapshot] = None
    full_response: str = ""
    concise_summary: str = ""
    file_references: List[str] = Field(default_factory=list)
    suggested_followups: List[str] = Field(default_factory=list)
    provider_metadata: Dict[str, Any] = Field(default_factory=dict)
    timing_ms: float = 0.0
    structured_error: Optional[Dict[str, Any]] = None
    fallback_label: Optional[str] = None

    @validator("schema_version")
    def check_schema_version(cls, v: str) -> str:
        return validate_schema_version(v)
