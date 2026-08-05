"""Adapters from existing structured plans and ToolNodes into TaskPlan."""

from __future__ import annotations

from typing import Any, Iterable

from hitl_gui.models.task_plan import TaskNode, TaskPlan


PHASES = (
    "understanding", "perception", "target_selection", "grasp_generation",
    "motion_planning", "hitl_review", "execution", "verification",
)
PHASE_ORDER = {phase: index for index, phase in enumerate(PHASES)}

TOOL_PHASE = {
    "understand_instruction": "understanding",
    "safe_pick_object": "understanding",
    "detect_object": "perception",
    "detect_objects": "perception",
    "build_object_point_cloud": "perception",
    "select_target": "target_selection",
    "review_grasp_candidate": "hitl_review",
    "generate_grasp_pose": "grasp_generation",
    "generate_grasp_candidates": "grasp_generation",
    "validate_grasp": "grasp_generation",
    "move_to_named_target": "motion_planning",
    "move_to_pregrasp": "motion_planning",
    "approach_grasp": "motion_planning",
    "retreat_grasp": "motion_planning",
    "plan_motion": "motion_planning",
    "trajectory_review": "hitl_review",
    "open_gripper": "execution",
    "close_gripper": "execution",
    "execute_motion": "execution",
    "verify_grasp": "verification",
    "error_recovery": "hitl_review",
}


class TaskPlanAdapter:
    """Create plan snapshots without inspecting natural-language text."""

    def create_empty(
        self, *, task_id: str, title: str, version: int = 1,
        description: str = "", plan_id: str | None = None,
    ) -> TaskPlan:
        return TaskPlan(
            task_id=task_id, plan_id=plan_id or f"plan-{task_id}", version=version,
            title=title, description=description, status="ACTIVE",
        )

    def from_structured_plan(
        self, plan: Any, *, task_id: str, plan_id: str | None = None,
        version: int = 1,
    ) -> TaskPlan:
        """Adapt core RobotPlan/Pydantic/dict data; never parse plan text."""
        data = plan.model_dump() if hasattr(plan, "model_dump") else dict(plan)
        task = str(data.get("task", "Robot task"))
        result = self.create_empty(
            task_id=task_id, plan_id=plan_id, title=task, version=version,
            description=str(data.get("description", "")),
        )
        steps = data.get("steps", []) or []
        previous_id: str | None = None
        for index, raw_step in enumerate(steps):
            step = raw_step.model_dump() if hasattr(raw_step, "model_dump") else dict(raw_step)
            node_id = str(step.get("step_id", f"step-{index + 1}"))
            tool_name = str(step.get("skill_id", "unknown_tool"))
            node = TaskNode(
                node_id=node_id, parent_id=None,
                display_name=tool_name.replace("_", " ").title(),
                description=str(step.get("description", "")), node_type="tool",
                phase=phase_for_tool(tool_name), sequence_index=index,
                status="PENDING", tool_name=tool_name,
                dependencies=[previous_id] if previous_id else [],
                input_data=dict(step.get("parameters", {}) or {}), plan_version=version,
            )
            self.upsert_node(result, node)
            previous_id = node_id
        return result

    def from_tool_nodes(
        self, tool_nodes: Iterable[Any], *, task_id: str, title: str,
        version: int = 1,
    ) -> TaskPlan:
        plan = self.create_empty(task_id=task_id, title=title, version=version)
        for index, node in enumerate(tool_nodes):
            self.upsert_node(plan, self.from_tool_node(node, sequence_index=index))
        return plan

    def from_tool_node(self, node: Any, *, sequence_index: int) -> TaskNode:
        tool_name = str(getattr(node, "tool_name", "") or "unknown_tool")
        status = getattr(node, "status", "PENDING")
        input_data = dict(getattr(node, "input_data", {}) or {})
        input_data.update(dict(getattr(node, "input_summary", {}) or {}))
        output_data = dict(getattr(node, "output_data", {}) or {})
        output_data.update(dict(getattr(node, "output_summary", {}) or {}))
        return TaskNode(
            node_id=str(node.node_id), parent_id=node.parent_id,
            display_name=str(node.display_name), description="",
            node_type="hitl" if bool(node.requires_approval) else (
                "composite" if tool_name.endswith("_object") and node.parent_id is None else "tool"
            ),
            phase=phase_for_tool(tool_name), sequence_index=sequence_index,
            status=getattr(status, "value", str(status)), tool_name=tool_name,
            # parent_id describes containment; dependencies describe execution
            # order. They must not be inferred from one another.
            dependencies=list(getattr(node, "dependencies", []) or []),
            input_data=input_data, output_data=output_data,
            error_message=node.error_message,
            requires_approval=bool(node.requires_approval), editable=bool(node.editable),
            editable_fields=list(node.editable_fields), plan_version=int(node.plan_version),
            start_time=node.start_time, end_time=node.end_time,
            duration_ms=node.duration_ms,
        )

    @staticmethod
    def upsert_node(plan: TaskPlan, node: TaskNode) -> TaskNode:
        existing = plan.nodes.get(node.node_id)
        if existing is None:
            plan.nodes[node.node_id] = node
            plan.node_ids.append(node.node_id)
            result = node
        else:
            current_attempt = existing.current_attempt
            for name in TaskNode.__dataclass_fields__:
                setattr(existing, name, getattr(node, name))
            if current_attempt and node.current_attempt == 0:
                existing.current_attempt = current_attempt
            result = existing
        plan.touch()
        return result

    @staticmethod
    def ordered_nodes(plan: TaskPlan) -> list[TaskNode]:
        return sorted(
            plan.nodes.values(),
            key=lambda node: (PHASE_ORDER.get(node.phase, len(PHASE_ORDER)), node.sequence_index),
        )


def phase_for_tool(tool_name: str) -> str:
    """Explicit schema mapping; unknown tools remain safe and deterministic."""
    return TOOL_PHASE.get(tool_name, "understanding")
