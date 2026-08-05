"""Unified in-memory models for the mock HITL prototype."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from hitl_gui.models.task_plan import NodeExecutionAttempt, TaskPlan


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
    APPROVED_PENDING_EXECUTION = "APPROVED_PENDING_EXECUTION"
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
    UNKNOWN = "UNKNOWN"


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
    dependencies: list[str] = field(default_factory=list)
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
    plan_version: int = 1
    planning_success: bool = False
    trajectory_points: int = 0
    trajectory_duration: float | None = None
    planning_time: int | None = None
    collision_check: str = "UNKNOWN"
    target_summary: str = ""
    object_id: str | None = None
    candidate_objects: list[dict[str, Any]] = field(default_factory=list)
    grasp_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_index: int = 0
    recovery_actions: list[str] = field(default_factory=list)
    error_type: str | None = None


@dataclass
class TaskExperimentMetrics:
    task_started_at: str | None = None
    task_finished_at: str | None = None
    total_task_time_ms: int | None = None
    human_wait_started_at: str | None = None
    human_wait_time_ms: int = 0
    replan_count: int = 0
    tool_failure_count: int = 0
    target_change_count: int = 0
    grasp_change_count: int = 0


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
    current_task_plan: TaskPlan | None = None
    selected_task_node_id: str | None = None
    node_attempts: dict[str, list[NodeExecutionAttempt]] = field(default_factory=dict)
    task_status: TaskStatus = TaskStatus.IDLE
    robot_mode: str = "SIMULATION"
    ros_status: SystemComponentStatus = SystemComponentStatus.IDLE
    agent_status: SystemComponentStatus = SystemComponentStatus.IDLE
    conversation: list[ChatEntry] = field(default_factory=list)
    tool_nodes: list[ToolNode] = field(default_factory=list)
    hardware_status: dict[str, SystemComponentStatus] = field(default_factory=dict)
    pending_hitl_request: HitlRequest | None = None
    current_trajectory_id: str | None = None
    current_target_id: str | None = None
    current_grasp_candidate_id: str | None = None
    experiment_metrics: TaskExperimentMetrics = field(default_factory=TaskExperimentMetrics)
    event_log: list[ExecutionEvent] = field(default_factory=list)
    modification_history: list[ExecutionEvent] = field(default_factory=list)
    rviz_running: bool = False
    rviz_process_status: str = "STOPPED"
    ros_executor_running: bool = False
    ros_node_initialized: bool = False
    ros_monitor_data: dict[str, Any] = field(default_factory=dict)
    component_processes: dict[str, Any] = field(default_factory=dict)
    simulation_launch_status: str = "IDLE"
    agent_request_running: bool = False
    agent_request_cancelled: bool = False
    welcome_shown: bool = False
