"""Unified in-memory models for the mock HITL prototype."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TaskStatus(str, Enum):
    IDLE = "IDLE"
    UNDERSTANDING_TASK = "UNDERSTANDING_TASK"
    PERCEIVING = "PERCEIVING"
    TARGET_REVIEW = "TARGET_REVIEW"
    GENERATING_GRASPS = "GENERATING_GRASPS"
    GRASP_REVIEW = "GRASP_REVIEW"
    PLANNING = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ToolStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"


class SystemComponentStatus(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    IDLE = "IDLE"
    READY = "READY"
    RUNNING = "RUNNING"
    WARNING = "WARNING"
    ERROR = "ERROR"


class HitlDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REPLAN = "REPLAN"
    CANCEL = "CANCEL"


@dataclass
class ChatEntry:
    text: str
    sent: bool
    name: str
    timestamp: str = field(default_factory=utc_now)


@dataclass
class ToolNode:
    node_id: str
    parent_id: str | None
    tool_name: str
    display_name: str
    status: ToolStatus = ToolStatus.PENDING
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: int | None = None
    requires_approval: bool = False
    editable: bool = False
    editable_fields: list[str] = field(default_factory=list)
    plan_version: int = 1
    modified_by_user: bool = False
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class HitlRequest:
    request_id: str
    task_id: str
    request_type: str
    target_id: str
    title: str
    description: str
    options: list[HitlDecision]
    created_at: str
    trajectory_id: str | None
    grasp_candidate_id: str | None
    status: str = "PENDING"


@dataclass
class ExecutionEvent:
    event_id: str
    task_id: str | None
    node_id: str | None
    event_type: str
    timestamp: str
    plan_version: int
    old_value: Any = None
    new_value: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppState:
    current_task_id: str | None = None
    current_task_name: str = "None"
    current_plan_version: int = 1
    task_status: TaskStatus = TaskStatus.IDLE
    robot_mode: str = "SIMULATION"
    ros_status: SystemComponentStatus = SystemComponentStatus.IDLE
    agent_status: SystemComponentStatus = SystemComponentStatus.IDLE
    conversation: list[ChatEntry] = field(default_factory=list)
    tool_nodes: list[ToolNode] = field(default_factory=list)
    hardware_status: dict[str, SystemComponentStatus] = field(default_factory=dict)
    pending_hitl_request: HitlRequest | None = None
    current_trajectory_id: str | None = None
    event_log: list[ExecutionEvent] = field(default_factory=list)
    modification_history: list[ExecutionEvent] = field(default_factory=list)
    rviz_running: bool = False
    rviz_process_status: str = "STOPPED"
