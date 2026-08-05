"""Read-only task-plan data model used by the GUI state layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class NodeExecutionAttempt:
    attempt_id: str
    node_id: str
    attempt_number: int
    status: str = "PENDING"
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: int | None = None
    trajectory_id: str | None = None
    request_id: str | None = None


@dataclass
class TaskNode:
    node_id: str
    parent_id: str | None
    display_name: str
    description: str
    node_type: str
    phase: str
    sequence_index: int
    status: str
    tool_name: str | None = None
    dependencies: list[str] = field(default_factory=list)
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    requires_approval: bool = False
    editable: bool = False
    editable_fields: list[str] = field(default_factory=list)
    plan_version: int = 1
    current_attempt: int = 0
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: int | None = None


@dataclass
class TaskPlan:
    task_id: str
    plan_id: str
    version: int
    title: str
    description: str
    status: str
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    node_ids: list[str] = field(default_factory=list)
    nodes: dict[str, TaskNode] = field(default_factory=dict)

    def touch(self) -> None:
        self.updated_at = utc_now()
