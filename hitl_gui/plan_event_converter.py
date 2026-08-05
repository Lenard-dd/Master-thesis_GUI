"""Incrementally apply structured Tool Events and statuses to TaskPlan."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hitl_gui.app_state import utc_now
from hitl_gui.models.task_plan import NodeExecutionAttempt, TaskNode, TaskPlan
from hitl_gui.task_plan_adapter import PHASES, phase_for_tool


TERMINAL_STATUSES = {
    "SUCCEEDED", "FAILED", "REJECTED", "CANCELLED", "INVALIDATED",
}


class PlanEventConverter:
    def upsert_tool_event(self, plan: TaskPlan, event: Any) -> TaskNode:
        existing = plan.nodes.get(event.node_id)
        if existing is None:
            explicit_phase = str(getattr(event, "phase", "") or "")
            node = TaskNode(
                node_id=event.node_id, parent_id=event.parent_id,
                display_name=event.display_name,
                description=str(getattr(event, "description", "")),
                node_type=str(getattr(event, "node_type", "tool")),
                phase=(explicit_phase if explicit_phase in PHASES else phase_for_tool(event.tool_name)),
                sequence_index=(
                    int(event.sequence_index) if getattr(event, "sequence_index", None) is not None
                    else len(plan.node_ids)
                ),
                status=str(event.status).upper(), tool_name=event.tool_name,
                dependencies=list(getattr(event, "dependencies", []) or []),
                input_data=dict(event.input_json), output_data=dict(event.output_json),
                error_message=event.error_message,
                requires_approval=bool(event.requires_approval),
                plan_version=plan.version,
            )
            plan.nodes[node.node_id] = node
            plan.node_ids.append(node.node_id)
        else:
            node = existing
            node.status = str(event.status).upper()
            node.input_data.update(event.input_json)
            node.output_data.update(event.output_json)
            node.error_message = event.error_message
            node.requires_approval = bool(event.requires_approval)
        plan.touch()
        return node

    def apply_status(
        self, plan: TaskPlan, attempts: dict[str, list[NodeExecutionAttempt]],
        node_id: str, status: str, *, input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None, error_message: str | None = None,
        trajectory_id: str | None = None, request_id: str | None = None,
    ) -> bool:
        node = plan.nodes.get(node_id)
        if node is None:
            return False
        normalized = str(status).upper()
        node.status = normalized
        node.input_data.update(input_data or {})
        node.output_data.update(output_data or {})
        if error_message is not None:
            node.error_message = error_message

        history = attempts.setdefault(node_id, [])
        attempt = history[-1] if history else None
        if normalized == "RUNNING" and (
            attempt is None or attempt.status in TERMINAL_STATUSES
        ):
            attempt = NodeExecutionAttempt(
                attempt_id=f"{node_id}:attempt:{len(history) + 1}", node_id=node_id,
                attempt_number=len(history) + 1, status=normalized,
                input_data=dict(input_data or node.input_data), start_time=utc_now(),
                trajectory_id=trajectory_id, request_id=request_id,
            )
            history.append(attempt)
            node.current_attempt = attempt.attempt_number
        elif attempt is None:
            attempt = NodeExecutionAttempt(
                attempt_id=f"{node_id}:attempt:1", node_id=node_id,
                attempt_number=1, status=normalized,
                input_data=dict(input_data or node.input_data),
                start_time=utc_now() if normalized != "PENDING" else None,
            )
            history.append(attempt)
            node.current_attempt = 1

        attempt.status = normalized
        attempt.input_data.update(input_data or {})
        attempt.output_data.update(output_data or {})
        attempt.error_message = error_message if error_message is not None else attempt.error_message
        attempt.trajectory_id = trajectory_id or attempt.trajectory_id
        attempt.request_id = request_id or attempt.request_id
        if normalized == "RUNNING" and attempt.start_time is None:
            attempt.start_time = utc_now()
        if normalized in TERMINAL_STATUSES:
            attempt.end_time = utc_now()
            attempt.duration_ms = _duration_ms(attempt.start_time, attempt.end_time)
        node.start_time = attempt.start_time
        node.end_time = attempt.end_time
        node.duration_ms = attempt.duration_ms
        plan.touch()
        return True

    @staticmethod
    def bind_review_ids(
        plan: TaskPlan, attempts: dict[str, list[NodeExecutionAttempt]], node_id: str,
        *, trajectory_id: str | None, request_id: str | None,
    ) -> bool:
        node = plan.nodes.get(node_id)
        if node is None:
            return False
        history = attempts.setdefault(node_id, [])
        if not history:
            history.append(NodeExecutionAttempt(
                attempt_id=f"{node_id}:attempt:1", node_id=node_id,
                attempt_number=1, status=node.status,
            ))
            node.current_attempt = 1
        history[-1].trajectory_id = trajectory_id
        history[-1].request_id = request_id
        plan.touch()
        return True


def _duration_ms(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    return int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000)
