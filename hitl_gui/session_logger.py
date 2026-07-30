"""JSON export for a single mock task; no database or ROS dependency."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
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
        self._write(task_dir / "task_summary.json", {
            "task_id": state.current_task_id,
            "task_name": state.current_task_name,
            "task_status": state.task_status,
            "plan_version": state.current_plan_version,
            "trajectory_id": state.current_trajectory_id,
            "robot_mode": state.robot_mode,
        })
        self._write(task_dir / "execution_events.json", state.event_log)
        self._write(task_dir / "conversation.json", state.conversation)
        self._write(task_dir / "tool_receipts.json", [self._receipt(node) for node in state.tool_nodes])
        return task_dir

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
