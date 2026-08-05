"""Pure presentation helpers for the read-only TaskPlan view."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from hitl_gui.models.task_plan import NodeExecutionAttempt, TaskNode, TaskPlan
from hitl_gui.task_plan_adapter import PHASES


PHASE_LABELS = {
    "understanding": "Understanding",
    "perception": "Perception",
    "target_selection": "Target Selection",
    "grasp_generation": "Grasp Generation",
    "motion_planning": "Motion Planning",
    "hitl_review": "HITL Review",
    "execution": "Execution",
    "verification": "Verification",
}

STATUS_PRESENTATION = {
    "PENDING": {"symbol": "○", "icon": "radio_button_unchecked", "color": "grey"},
    "RUNNING": {"symbol": "●", "icon": "play_circle", "color": "primary"},
    "SUCCEEDED": {"symbol": "✓", "icon": "check_circle", "color": "positive"},
    "FAILED": {"symbol": "×", "icon": "cancel", "color": "negative"},
    "WAITING_APPROVAL": {"symbol": "!", "icon": "approval", "color": "warning"},
    "REJECTED": {"symbol": "⊘", "icon": "block", "color": "negative"},
    "CANCELLED": {"symbol": "—", "icon": "remove_circle_outline", "color": "grey"},
    "INVALIDATED": {"symbol": "↺", "icon": "history", "color": "warning"},
}


def grouped_nodes(plan: TaskPlan | None) -> list[tuple[str, list[TaskNode]]]:
    """Return non-empty phase groups in canonical phase/sequence order."""
    if plan is None:
        return []
    groups: dict[str, list[TaskNode]] = {phase: [] for phase in PHASES}
    unknown: dict[str, list[TaskNode]] = {}
    for node in plan.nodes.values():
        (groups if node.phase in groups else unknown).setdefault(node.phase, []).append(node)
    result = []
    for phase in (*PHASES, *sorted(unknown)):
        nodes = groups.get(phase, unknown.get(phase, []))
        if nodes:
            result.append((phase, sorted(nodes, key=lambda item: (item.sequence_index, item.node_id))))
    return result


def execution_order(plan: TaskPlan | None) -> list[TaskNode]:
    """Stable topological order using dependencies, with sequence fallback."""
    if plan is None:
        return []
    visible = {
        node_id: node for node_id, node in plan.nodes.items()
        if node.node_type != "composite"
    }
    indegree = {node_id: 0 for node_id in visible}
    dependents: dict[str, list[str]] = {node_id: [] for node_id in visible}
    for node_id, node in visible.items():
        for dependency in node.dependencies:
            if dependency in visible and dependency != node_id:
                indegree[node_id] += 1
                dependents[dependency].append(node_id)

    key = lambda node_id: (visible[node_id].sequence_index, node_id)
    ready = sorted((node_id for node_id, degree in indegree.items() if degree == 0), key=key)
    ordered_ids: list[str] = []
    while ready:
        node_id = ready.pop(0)
        ordered_ids.append(node_id)
        for child_id in sorted(dependents[node_id], key=key):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
                ready.sort(key=key)

    # Malformed/cyclic input remains visible and deterministic instead of
    # breaking the GUI.
    ordered_ids.extend(sorted((node_id for node_id in visible if node_id not in ordered_ids), key=key))
    return [visible[node_id] for node_id in ordered_ids]


def timeline_entries(plan: TaskPlan | None) -> list[tuple[TaskNode, list[TaskNode]]]:
    """Nest trajectory reviews beneath their corresponding motion node."""
    ordered = execution_order(plan)
    reviews: dict[str, list[TaskNode]] = {}
    review_ids: set[str] = set()
    for node in ordered:
        if node.tool_name == "trajectory_review" and node.parent_id:
            reviews.setdefault(node.parent_id, []).append(node)
            review_ids.add(node.node_id)
    return [
        (node, reviews.get(node.node_id, []))
        for node in ordered if node.node_id not in review_ids
    ]


def timeline_signature(plan: TaskPlan | None) -> tuple[str, ...]:
    """Identity/order snapshot used to detect newly appended visual steps."""
    return tuple(
        item.node_id
        for node, reviews in timeline_entries(plan)
        for item in (node, *reviews)
    )


def status_presentation(status: str) -> dict[str, str]:
    return STATUS_PRESENTATION.get(
        str(status).upper(),
        {"symbol": "?", "icon": "help_outline", "color": "grey"},
    )


def node_summary(node: TaskNode) -> str:
    """Build a compact summary exclusively from structured fields."""
    data = node.output_data if isinstance(node.output_data, dict) else {}
    tool = (node.tool_name or "").lower()
    status = str(node.status).upper()
    if status == "FAILED":
        return f"Error: {node.error_message}" if node.error_message else "Tool execution failed"
    if tool in {"detect_object", "detect_objects"}:
        count = _first(data, "detected_count", "object_count", "count")
        if count is not None:
            return f"{count} object{'s' if count != 1 else ''} found"
    if tool == "select_target":
        target = _first(data, "target_id", "object_id", "target", "label", "name")
        confidence = _first(data, "confidence", "score")
        if target is not None:
            return f"{target}{_score_suffix(confidence, 'confidence')}"
    if tool in {"generate_grasp_candidates", "generate_grasp_pose"}:
        count = _first(data, "candidate_count", "grasp_count", "count")
        score = _first(data, "best_score", "score")
        parts = []
        if count is not None:
            parts.append(f"{count} candidates")
        if score is not None:
            parts.append(f"best score {_compact_number(score)}")
        if parts:
            return " · ".join(parts)
    if tool in {"validate_grasp", "review_grasp_candidate"}:
        candidate = _first(data, "candidate_id", "grasp_candidate_id", "candidate")
        if isinstance(candidate, dict):
            candidate = _first(candidate, "candidate_id", "id", "name")
        ik = _first(data, "ik_passed", "ik_success", "reachable")
        parts = [str(candidate)] if candidate is not None else []
        if ik is not None:
            parts.append("IK passed" if bool(ik) else "IK failed")
        if parts:
            return " · ".join(parts)
    if tool in {"plan_motion", "move_to_named_target", "move_to_pregrasp", "approach_grasp", "retreat_grasp"}:
        nested = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        points = _first(data, "trajectory_points", "num_trajectory_points")
        points = points if points is not None else _first(nested, "trajectory_points", "num_trajectory_points")
        duration = _first(data, "trajectory_duration", "duration_sec")
        duration = duration if duration is not None else _first(nested, "trajectory_duration", "duration_sec")
        collision = _first(data, "collision_free", "collision_check")
        parts = []
        if points is not None:
            parts.append(f"{points} points")
        if duration is not None:
            parts.append(f"{_compact_number(duration)} s")
        if collision is not None:
            parts.append("collision free" if collision is True or str(collision).upper() in {"ALLOW", "PASSED", "FREE"} else str(collision))
        if parts:
            return " · ".join(parts)
    if tool == "trajectory_review":
        if status == "WAITING_APPROVAL":
            return "Waiting for user approval"
        decision = _first(data, "decision", "approval")
        if decision is not None:
            return f"User decision: {decision}"
    if tool == "execute_motion":
        success = _first(data, "execution_success", "success")
        duration = _first(data, "execution_duration", "execution_duration_ms")
        parts = []
        if success is not None:
            parts.append("Execution succeeded" if bool(success) else "Execution failed")
        if duration is not None:
            seconds = float(duration) / 1000 if "execution_duration_ms" in data else duration
            parts.append(f"{_compact_number(seconds)} s")
        if parts:
            return " · ".join(parts)
    if tool == "verify_grasp":
        detected = _first(data, "object_detected", "detected", "grasp_verified")
        if detected is not None:
            return "Object detected" if bool(detected) else "Object not detected"
        if data.get("verification"):
            return f"Verification: {data['verification']}"
    for key in ("summary", "message", "result"):
        value = data.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            return str(value)
    return {
        "PENDING": "Waiting to start",
        "RUNNING": "In progress",
        "SUCCEEDED": "Completed",
        "WAITING_APPROVAL": "Waiting for user approval",
        "REJECTED": "Rejected by user",
        "CANCELLED": "Cancelled",
        "INVALIDATED": "Superseded by a newer plan",
    }.get(status, "No result available")


def current_node(plan: TaskPlan | None) -> TaskNode | None:
    if plan is None:
        return None
    ordered = execution_order(plan)
    for status in ("RUNNING", "WAITING_APPROVAL", "FAILED"):
        match = next((node for node in ordered if str(node.status).upper() == status), None)
        if match is not None:
            return match
    # A composite is represented in the Plan Header rather than as a large
    # timeline card, but it can still be the active task-intent approval gate.
    for status in ("RUNNING", "WAITING_APPROVAL", "FAILED"):
        match = next((
            node for node in plan.nodes.values()
            if node.node_type == "composite" and str(node.status).upper() == status
        ), None)
        if match is not None:
            return match
    return None


def plan_header_data(plan: TaskPlan | None) -> dict[str, Any] | None:
    """Return the exact values shown by the read-only plan header."""
    if plan is None:
        return None
    active = current_node(plan)
    return {
        "title": plan.title,
        "task_id": plan.task_id,
        "plan_id": plan.plan_id,
        "version": plan.version,
        "status": plan.status,
        "current_node": active.display_name if active else "—",
        "replan_count": max(0, plan.version - 1),
    }


def elapsed_duration_ms(node: TaskNode, *, now: datetime | None = None) -> int | None:
    if node.duration_ms is not None:
        return node.duration_ms
    if str(node.status).upper() != "RUNNING" or not node.start_time:
        return None
    try:
        start = datetime.fromisoformat(node.start_time)
        current = now or datetime.now(timezone.utc)
        if start.tzinfo is None and current.tzinfo is not None:
            start = start.replace(tzinfo=timezone.utc)
        return max(0, int((current - start).total_seconds() * 1000))
    except (TypeError, ValueError):
        return None


def format_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "—"
    return f"{duration_ms / 1000:.1f} s" if duration_ms >= 1000 else f"{duration_ms} ms"


def node_details(
    plan: TaskPlan | None,
    attempts: dict[str, list[NodeExecutionAttempt]],
    node_id: str | None,
) -> dict[str, Any] | None:
    if plan is None or not node_id or node_id not in plan.nodes:
        return None
    node = plan.nodes[node_id]
    history = list(attempts.get(node_id, []))
    return {
        "node": node,
        "attempts": history,
        "attempt_count": len(history),
        "current_attempt": node.current_attempt,
        "summary": node_summary(node),
    }


def compact_mapping(data: Any, *, limit: int = 3) -> str:
    if not isinstance(data, dict) or not data:
        return "—"
    parts = [f"{key}: {_compact_value(value)}" for key, value in list(data.items())[:limit]]
    if len(data) > limit:
        parts.append(f"+{len(data) - limit} more")
    return " · ".join(parts)


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _score_suffix(value: Any, label: str) -> str:
    return "" if value is None else f" · {label} {_compact_number(value)}"


def _compact_number(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _compact_value(value: Any) -> str:
    if isinstance(value, dict):
        return "{…}"
    if isinstance(value, list):
        return f"[{len(value)} items]"
    text = str(value)
    return text if len(text) <= 50 else f"{text[:47]}…"
