"""The sole state-mutation boundary for the Phase 2 mock GUI."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from hitl_gui.app_state import (
    AppState, ChatEntry, ExecutionEvent, HitlDecision, HitlRequest,
    SystemComponentStatus, TaskStatus, ToolNode, ToolStatus, utc_now,
)
from hitl_gui.mock.mock_task_runner import MockTaskRunner
from nicegui import ui
from hitl_gui.panels.chat_panel import create_chat_panel
from hitl_gui.panels.header_panel import create_header_panel
from hitl_gui.panels.hitl_panel import create_hitl_panel
from hitl_gui.panels.log_panel import create_log_panel
from hitl_gui.panels.status_panel import create_status_panel
from hitl_gui.panels.tool_flow_panel import create_tool_flow_panel


FLOW = [
    "understand_instruction", "detect_objects", "select_target",
    "generate_grasp_candidates", "validate_grasp", "plan_motion",
    "trajectory_review", "execute_motion", "verify_grasp",
]

TASK_STATUS_BY_TOOL = {
    "understand_instruction": TaskStatus.UNDERSTANDING_TASK,
    "detect_objects": TaskStatus.PERCEIVING,
    "select_target": TaskStatus.TARGET_REVIEW,
    "generate_grasp_candidates": TaskStatus.GENERATING_GRASPS,
    "validate_grasp": TaskStatus.GRASP_REVIEW,
    "plan_motion": TaskStatus.PLANNING,
}


class GuiController:
    """Coordinates state changes while remaining entirely mock-only."""

    def __init__(self, step_delay: float = 0.6) -> None:
        self.state = AppState(hardware_status={
            "ROS 2": SystemComponentStatus.IDLE,
            "UR5": SystemComponentStatus.DISCONNECTED,
            "Robotiq 2F-140": SystemComponentStatus.DISCONNECTED,
            "D435i": SystemComponentStatus.DISCONNECTED,
            "MoveIt": SystemComponentStatus.IDLE,
            "SAM3": SystemComponentStatus.IDLE,
            "GraspGenX": SystemComponentStatus.IDLE,
            "RViz2": SystemComponentStatus.DISCONNECTED,
        })
        self.runner = MockTaskRunner(self, step_delay=step_delay)
        self.append_event("GUI_INITIALIZED", metadata={"message": "GUI initialized"})

    def build_page(self) -> None:
        ui.colors(primary="#1d4f91", secondary="#546e7a", accent="#1976d2")
        ui.add_head_html("<style>body { background: #f5f7fa; }</style>")
        with ui.column().classes("w-full min-h-screen gap-4 p-4"):
            renderers = [create_header_panel(self)]
            with ui.splitter(value=32).classes("w-full flex-grow min-h-[520px]") as outer:
                with outer.before:
                    renderers.append(create_chat_panel(self))
                with outer.after:
                    with ui.splitter(value=64).classes("w-full h-full") as inner:
                        with inner.before:
                            renderers.append(create_tool_flow_panel(self))
                        with inner.after:
                            renderers.append(create_status_panel(self))
            renderers.append(create_hitl_panel(self))
            renderers.append(create_log_panel(self))
        ui.timer(0.25, lambda: [renderer.refresh() for renderer in renderers])

    def start_task(self, task_name: str) -> str | None:
        task_name = task_name.strip()
        if not task_name or self.state.task_status not in {TaskStatus.IDLE, TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}:
            return None
        self.reset_task(clear_conversation=False)
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        self.state.current_task_id = task_id
        self.state.current_task_name = task_name
        self.state.task_status = TaskStatus.UNDERSTANDING_TASK
        self.state.agent_status = SystemComponentStatus.RUNNING
        self.add_chat_message(task_name, sent=True, name="Operator")
        self.add_chat_message("Agent has received the task. Starting mock workflow.", sent=False, name="Agent")
        self.initialize_tool_tree()
        self.append_event("TASK_STARTED", new_value=task_name)
        self.runner.start(task_id)
        return task_id

    def add_chat_message(self, text: str, *, sent: bool, name: str) -> None:
        self.state.conversation.append(ChatEntry(text=text, sent=sent, name=name))
        self.append_event("CHAT_MESSAGE", metadata={"sender": name})

    def clear_conversation(self) -> None:
        self.state.conversation.clear()
        self.append_event("CHAT_CLEARED")

    def initialize_tool_tree(self) -> None:
        self.state.tool_nodes = [
            ToolNode(
                node_id=name, parent_id=None, tool_name=name,
                display_name=name.replace("_", " ").title(),
                requires_approval=name == "trajectory_review",
                editable=False, plan_version=self.state.current_plan_version,
            )
            for name in FLOW
        ]
        self.append_event("TOOL_TREE_INITIALIZED")

    def update_tool_status(self, node_id: str, status: ToolStatus, **output: Any) -> bool:
        node = self._node(node_id)
        if node is None:
            return False
        old_status = node.status
        node.status = status
        if status == ToolStatus.RUNNING:
            node.start_time = utc_now()
        if status in {ToolStatus.SUCCEEDED, ToolStatus.FAILED, ToolStatus.REJECTED, ToolStatus.CANCELLED, ToolStatus.INVALIDATED}:
            node.end_time = utc_now()
            node.duration_ms = self._duration_ms(node.start_time, node.end_time)
        if output:
            node.output_data.update(output)
        self.append_event("TOOL_STATUS_CHANGED", node_id=node_id, old_value=old_status.value, new_value=status.value)
        return True

    def create_trajectory(self) -> str:
        trajectory_id = f"trajectory-{uuid.uuid4().hex[:8]}"
        self.state.current_trajectory_id = trajectory_id
        node = self._node("plan_motion")
        if node:
            node.output_data["trajectory_id"] = trajectory_id
        self.append_event("TRAJECTORY_CREATED", node_id="plan_motion", new_value=trajectory_id)
        return trajectory_id

    def create_hitl_request(self) -> HitlRequest:
        request = HitlRequest(
            request_id=f"review-{uuid.uuid4().hex[:8]}", task_id=self.state.current_task_id or "",
            request_type="trajectory_review", target_id="selected_target",
            title="Trajectory approval required", description="Review mock motion trajectory before execution.",
            options=[HitlDecision.APPROVE, HitlDecision.REJECT, HitlDecision.REPLAN, HitlDecision.CANCEL],
            created_at=utc_now(), trajectory_id=self.state.current_trajectory_id,
            grasp_candidate_id=None,
        )
        self.state.pending_hitl_request = request
        self.append_event("HITL_REQUEST_CREATED", node_id="trajectory_review", new_value=request.request_id)
        return request

    def submit_hitl_decision(self, request_id: str, decision: HitlDecision) -> bool:
        request = self.state.pending_hitl_request
        if request is None or request.status != "PENDING":
            return False
        if request.request_id != request_id or request.task_id != self.state.current_task_id:
            return False
        if request.trajectory_id != self.state.current_trajectory_id:
            return False
        request.status = decision.value
        self.append_event("HITL_DECISION", node_id="trajectory_review", new_value=decision.value)
        if decision == HitlDecision.CANCEL:
            self.cancel_task()
            return True
        self.state.pending_hitl_request = None
        self.runner.submit_decision(decision)
        return True

    def cancel_task(self) -> None:
        if self.state.task_status in {TaskStatus.IDLE, TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            return
        self.state.task_status = TaskStatus.CANCELLED
        self.state.agent_status = SystemComponentStatus.IDLE
        self.state.pending_hitl_request = None
        for node in self.state.tool_nodes:
            if node.status in {ToolStatus.PENDING, ToolStatus.RUNNING, ToolStatus.WAITING_APPROVAL}:
                self.update_tool_status(node.node_id, ToolStatus.CANCELLED)
        self.add_chat_message("Task cancelled by user.", sent=False, name="Agent")
        self.append_event("TASK_CANCELLED")
        self.runner.cancel()

    def reject_task(self) -> None:
        review = self._active_trajectory_review()
        if review:
            self.update_tool_status(review.node_id, ToolStatus.REJECTED)
        for node in self.state.tool_nodes:
            if node.status == ToolStatus.PENDING:
                self.update_tool_status(node.node_id, ToolStatus.CANCELLED)
        self.state.task_status = TaskStatus.CANCELLED
        self.state.agent_status = SystemComponentStatus.IDLE
        self.add_chat_message("Task was rejected by the user.", sent=False, name="Agent")
        self.append_event("TASK_REJECTED")

    def replan_task(self) -> None:
        old_review = self._active_trajectory_review()
        if old_review:
            self.update_tool_status(old_review.node_id, ToolStatus.INVALIDATED)
        self.state.current_plan_version += 1
        attempt = self.state.current_plan_version
        plan_id = f"plan_motion_attempt_{attempt}"
        review_id = f"trajectory_review_attempt_{attempt}"
        self.state.tool_nodes.extend([
            ToolNode(plan_id, "plan_motion", "plan_motion", f"Plan Motion (Attempt {attempt})", plan_version=attempt),
            ToolNode(review_id, "trajectory_review", "trajectory_review", f"Trajectory Review (Attempt {attempt})", requires_approval=True, plan_version=attempt),
        ])
        self.set_task_status(TaskStatus.PLANNING)
        self.update_tool_status(plan_id, ToolStatus.RUNNING)
        self.update_tool_status(plan_id, ToolStatus.SUCCEEDED)
        trajectory_id = f"trajectory-{uuid.uuid4().hex[:8]}"
        self.state.current_trajectory_id = trajectory_id
        self._node(plan_id).output_data["trajectory_id"] = trajectory_id
        self.update_tool_status(review_id, ToolStatus.WAITING_APPROVAL)
        self.set_task_status(TaskStatus.WAITING_APPROVAL)
        self.create_hitl_request()
        self.append_event("PLAN_REPLANNED", node_id=plan_id, new_value=trajectory_id, metadata={"plan_version": attempt})

    def complete_task(self) -> None:
        self.state.task_status = TaskStatus.COMPLETED
        self.state.agent_status = SystemComponentStatus.IDLE
        self.add_chat_message("Task completed successfully in mock simulation.", sent=False, name="Agent")
        self.append_event("TASK_COMPLETED")

    def complete_active_trajectory_review(self) -> None:
        review = self._active_trajectory_review()
        if review:
            self.update_tool_status(review.node_id, ToolStatus.SUCCEEDED)

    def set_task_status(self, status: TaskStatus) -> None:
        old_status = self.state.task_status
        self.state.task_status = status
        self.append_event("TASK_STATUS_CHANGED", old_value=old_status.value, new_value=status.value)

    def append_event(self, event_type: str, *, node_id: str | None = None, old_value: Any = None, new_value: Any = None, metadata: dict[str, Any] | None = None) -> ExecutionEvent:
        event = ExecutionEvent(f"event-{uuid.uuid4().hex[:8]}", self.state.current_task_id, node_id, event_type, utc_now(), old_value, new_value, metadata or {})
        self.state.event_log.append(event)
        if event_type in {"HITL_DECISION", "PLAN_REPLANNED"}:
            self.state.modification_history.append(event)
        return event

    def reset_task(self, *, clear_conversation: bool = True) -> None:
        self.runner.cancel()
        self.state.current_task_id = None
        self.state.current_task_name = "None"
        self.state.current_plan_version = 1
        self.state.task_status = TaskStatus.IDLE
        self.state.agent_status = SystemComponentStatus.IDLE
        self.state.tool_nodes.clear()
        self.state.pending_hitl_request = None
        self.state.current_trajectory_id = None
        self.state.modification_history.clear()
        if clear_conversation:
            self.state.conversation.clear()
        self.append_event("TASK_RESET")

    def is_current_task(self, task_id: str) -> bool:
        return self.state.current_task_id == task_id and self.state.task_status not in {TaskStatus.CANCELLED, TaskStatus.IDLE}

    def _node(self, node_id: str) -> ToolNode | None:
        return next((node for node in self.state.tool_nodes if node.node_id == node_id), None)

    def _active_trajectory_review(self) -> ToolNode | None:
        nodes = [
            node for node in self.state.tool_nodes
            if node.tool_name == "trajectory_review"
            and node.status == ToolStatus.WAITING_APPROVAL
        ]
        return nodes[-1] if nodes else None

    @staticmethod
    def _duration_ms(start: str | None, end: str | None) -> int | None:
        if not start or not end:
            return None
        return int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() * 1000)
