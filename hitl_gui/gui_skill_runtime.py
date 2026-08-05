"""Stepwise GUI runner for the perception/grasp portion of ``safe_pick_object``.

It intentionally stops at the grasp-candidate gate.  The later MoveIt
trajectory and execution gates are still handled by the existing Stage 7
trajectory-review adapter; this avoids creating a second trajectory executor.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hitl_gui.app_state import TaskStatus, ToolNode, ToolStatus


class GuiSkillRuntimeAdapter:
    """Advance approved agent proposals through explicit, auditable sensor steps."""

    def __init__(self, controller, adapters) -> None:
        self.controller = controller
        self.adapters = adapters
        self._cancelled_task_ids: set[str] = set()

    def cancel(self, task_id: str | None) -> None:
        if task_id:
            self._cancelled_task_ids.add(task_id)

    async def run_safe_pick_observation(self, parent: ToolNode) -> None:
        task_id = self.controller.state.current_task_id
        if not task_id:
            return
        self._cancelled_task_ids.discard(task_id)
        parent.status = ToolStatus.RUNNING
        self.controller.state.task_status = TaskStatus.PERCEIVING
        self.controller.append_event(
            "skill_runtime_started", node_id=parent.node_id,
            metadata={"tool_name": parent.tool_name, "backend": self.adapters.mode_summary},
        )
        query = _query_from_parent(parent, self.controller.state.current_task_name)
        context: dict[str, Any] = {"task_id": task_id}
        for skill_id, parameters, display_name in (
            ("detect_object", {"query": query, "require_pose": True}, "Detect Object"),
            ("build_object_point_cloud", {"object_id": "<resolved>"}, "Build Object Point Cloud"),
            ("generate_grasp_pose", {"object_id": "<resolved>"}, "Generate Grasp Candidates"),
        ):
            if task_id in self._cancelled_task_ids or self.controller.state.current_task_id != task_id:
                return
            node = ToolNode(
                node_id=f"{parent.node_id}:{skill_id}", parent_id=parent.node_id,
                tool_name=skill_id, display_name=display_name, plan_version=self.controller.state.current_plan_version,
                input_data=dict(parameters), input_summary=dict(parameters),
            )
            self.controller.state.tool_nodes.append(node)
            self.controller.update_tool_status(node.node_id, ToolStatus.RUNNING)
            step = _new_plan_step(node.node_id, skill_id, display_name, parameters)
            # Deterministic mock calls are in-memory and deliberately stay on
            # the event loop.  The potentially slow D435i/SAM3/GraspGenX
            # path is moved off it so browser controls remain responsive.
            if self.adapters.config.perception_mode == "mock" and (
                skill_id != "generate_grasp_pose" or self.adapters.config.grasp_mode == "mock"
            ):
                result = self.adapters.execute(step, context)
            else:
                result = await asyncio.to_thread(self.adapters.execute, step, context)
            if task_id in self._cancelled_task_ids or self.controller.state.current_task_id != task_id:
                return
            output = result.get("output", {}) if isinstance(result, dict) else {}
            if not isinstance(output, dict):
                output = {"raw_output": output}
            if not bool(result.get("success", False)):
                self.controller.update_tool_status(
                    node.node_id, ToolStatus.FAILED, output_summary=output,
                    error_message=str(result.get("message", "Tool failed.")),
                )
                parent.status = ToolStatus.FAILED
                self.controller.state.task_status = TaskStatus.FAILED
                self.controller.add_chat_message(
                    f"{display_name} could not continue: {result.get('message', 'unknown error')}",
                    sent=False, name="System",
                )
                return
            self.controller.update_tool_status(
                node.node_id, ToolStatus.SUCCEEDED, output_summary=output,
            )

        if task_id in self._cancelled_task_ids:
            return
        review = ToolNode(
            node_id=f"{parent.node_id}:review_grasp_candidate", parent_id=parent.node_id,
            tool_name="review_grasp_candidate", display_name="Review Grasp Candidate",
            status=ToolStatus.WAITING_APPROVAL, requires_approval=True,
            plan_version=self.controller.state.current_plan_version,
            output_data={"candidate": context.get("selected_grasp_candidate")},
            output_summary={"candidate": context.get("selected_grasp_candidate")},
        )
        self.controller.state.tool_nodes.append(review)
        self.controller.state.task_status = TaskStatus.WAITING_APPROVAL
        request = self.controller.create_agent_hitl_request(review, ["grasp_candidate"])
        if request is None:
            self.controller.update_tool_status(review.node_id, ToolStatus.FAILED, error_message="Another HITL request is already pending.")
            return
        self.controller.add_chat_message(
            "A grasp candidate is ready for review. Approve it to record the candidate decision; trajectory planning remains a separate reviewed step.",
            sent=False, name=self.controller.agent_name,
        )


def _new_plan_step(step_id: str, skill_id: str, description: str, parameters: dict[str, Any]):
    from llm_skill_robot.core.plan import PlanStep

    return PlanStep(step_id=step_id, skill_id=skill_id, description=description, parameters=parameters)


def _query_from_parent(parent: ToolNode, fallback: str) -> str:
    for key in ("object_query", "query", "object", "target", "target_name"):
        value = parent.input_data.get(key)
        if value:
            return str(value)
    return fallback
