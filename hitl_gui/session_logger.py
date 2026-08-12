"""JSON export for a single mock task; no database or ROS dependency."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


def _json_default(value: Any):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


class SessionLogger:
    def __init__(self, log_root: str | Path = "logs") -> None:
        self.log_root = Path(log_root)

    def export_task(self, state) -> Path:
        if not state.current_task_id:
            raise ValueError("No current task is available for export.")
        date_dir = datetime.now().strftime("%Y-%m-%d")
        task_dir = self.log_root / date_dir / state.current_task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        self._write(task_dir / "task_summary.json", self.build_task_summary(state))
        self._write(task_dir / "execution_events.json", state.event_log)
        self._write(task_dir / "conversation.json", state.conversation)
        self._write(task_dir / "tool_receipts.json", [self._receipt(node) for node in state.tool_nodes])
        self._write(task_dir / "experiment_metrics.json", state.experiment_metrics)
        return task_dir

    def export_task_summary(self, state) -> Path:
        """Write the terminal experiment summary without duplicating other logs."""
        if not state.current_task_id:
            raise ValueError("No current task is available for export.")
        task_dir = self.log_root / datetime.now().strftime("%Y-%m-%d") / state.current_task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / "task_summary.json"
        self._write(path, self.build_task_summary(state))
        return path

    @staticmethod
    def build_task_summary(state) -> dict[str, Any]:
        result = {
            "COMPLETED": "SUCCESS", "FAILED": "FAILED", "CANCELLED": "CANCELLED",
        }.get(state.task_status.value, state.task_status.value)
        total_duration = state.experiment_metrics.total_task_time_ms
        if total_duration is None and state.experiment_metrics.task_started_at:
            total_duration = _duration_ms(state.experiment_metrics.task_started_at, _utc_now())
        hitl_requests = sum(event.event_type == "hitl_requested" for event in state.event_log)
        user_modifications = (
            state.experiment_metrics.target_change_count
            + state.experiment_metrics.grasp_change_count
            + state.experiment_metrics.replan_count
        )
        return {
            "task_id": state.current_task_id,
            "instruction": state.current_task_name,
            "result": result,
            "total_duration": total_duration,
            "agent_duration": _agent_duration(state),
            "perception_duration": _tool_duration(state, {"detect_object", "detect_objects", "build_object_point_cloud"}),
            "grasp_generation_duration": _tool_duration(state, {"generate_grasp_pose", "generate_grasp_candidates"}),
            "planning_duration": _tool_duration(state, {"move_to_named_target", "move_to_pregrasp", "approach_grasp", "retreat_grasp", "plan_motion"}),
            "execution_duration": _execution_duration(state),
            "total_hitl_waiting_time": state.experiment_metrics.human_wait_time_ms,
            "number_of_hitl_requests": hitl_requests,
            "number_of_user_modifications": user_modifications,
            "number_of_replans": state.experiment_metrics.replan_count,
            "number_of_tool_failures": state.experiment_metrics.tool_failure_count,
            "final_plan_version": state.current_plan_version,
            "selected_target": state.current_target_id,
            "selected_grasp": state.current_grasp_candidate_id,
            "final_trajectory_id": state.current_trajectory_id,
        }

    @staticmethod
    def _receipt(node) -> dict[str, Any]:
        return {
            "node_id": node.node_id, "tool_name": node.tool_name,
            "plan_version": node.plan_version, "status": node.status,
            "start_time": node.start_time, "end_time": node.end_time,
            "duration_ms": node.duration_ms, "input_summary": node.input_summary,
            "output_summary": node.output_summary, "error_message": node.error_message,
        }

    @staticmethod
    def _write(path: Path, payload: Any) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )


def _tool_duration(state, names: set[str]) -> int:
    return sum(int(node.duration_ms or 0) for node in state.tool_nodes if node.tool_name in names)


def _execution_duration(state) -> int:
    total = 0
    for event in state.event_log:
        if event.event_type != "execution_succeeded":
            continue
        value = event.metadata.get("execution_duration")
        if isinstance(value, (int, float)):
            total += int(value * 1000 if value < 1000 else value)
    return total


def _agent_duration(state) -> int:
    submitted = next((event for event in state.event_log if event.event_type == "agent_task_submitted"), None)
    if submitted:
        completed = next((event for event in state.event_log if event.timestamp >= submitted.timestamp
                          and event.event_type in {"agent_tool_event", "hitl_requested", "agent_error"}), None)
        if completed:
            return _duration_ms(submitted.timestamp, completed.timestamp)
    return _tool_duration(state, {"understand_instruction"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _duration_ms(start: str, end: str) -> int:
    return max(0, int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000))
