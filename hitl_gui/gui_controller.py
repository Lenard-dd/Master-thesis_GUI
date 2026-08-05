"""The sole state-mutation boundary for the Phase 2 mock GUI."""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timezone
from typing import Any

import yaml

from hitl_gui.app_state import (
    AppState, ChatEntry, ExecutionEvent, HitlDecision, HitlRequest,
    SystemComponentStatus, TaskStatus, ToolNode, ToolStatus, utc_now,
)
from hitl_gui.mock.mock_task_runner import MockTaskRunner
from hitl_gui.session_logger import SessionLogger
from hitl_gui.rviz_process_manager import RvizProcessManager, load_gui_config
from hitl_gui.ros_worker import RosWorker
from hitl_gui.message_converter import component_status
from hitl_gui.component_process_manager import ComponentProcessManager
from hitl_gui.agent_bridge import ExistingAgentBridge
from hitl_gui.trajectory_review_adapter import ExistingTrajectoryReviewAdapter
from hitl_gui.runtime_adapters import RuntimeAdapterRegistry, RuntimeBackendConfig
from hitl_gui.gui_skill_runtime import GuiSkillRuntimeAdapter
from nicegui import ui
from hitl_gui.panels.chat_panel import create_chat_panel
from hitl_gui.panels.header_panel import create_header_panel
from hitl_gui.panels.hitl_panel import create_hitl_panel
from hitl_gui.panels.log_panel import create_log_panel
from hitl_gui.panels.status_panel import create_status_panel
from hitl_gui.panels.tool_flow_panel import create_tool_flow_panel
from hitl_gui.panels.component_log_panel import create_component_log_panel


FLOW = [
    "understand_instruction", "detect_objects", "select_target",
    "generate_grasp_candidates", "validate_grasp", "plan_motion",
    "trajectory_review", "execute_motion", "verify_grasp",
]

WELCOME_MESSAGES = (
    "Hello, I am {agent_name}, your robot-task assistant. What would you like us to work on together today? You can also ask me what I can currently do.",
    "Welcome back. I am {agent_name}, ready to help turn a robot task into a reviewed plan. What should we work on today? Ask about my current capabilities at any time.",
    "Hi, I am {agent_name}. It is good to work with you. Tell me the robot task you have in mind, or ask ‘what can you do?’ to see the currently supported basics.",
)

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

    def __init__(self, step_delay: float = 0.6, log_root: str = "logs") -> None:
        # Assigned once a browser page is built. Audit events may also be
        # created during controller construction, before a panel exists.
        self._log_renderer = None
        self._event_renderers = []
        self.trajectory_adapter = None
        self._invalidated_trajectory_ids: set[str] = set()
        self._last_trajectory_task = None
        self._last_execution_task = None
        self._last_skill_task = None
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
        self.session_logger = SessionLogger(log_root)
        self.gui_config = load_gui_config()
        self.agent_name = self.gui_config.get("agent_bridge", {}).get("display_name", "Milo")
        self.state.robot_mode = self.gui_config.get("mode", "SIMULATION").upper()
        rviz_settings = self.gui_config.get("rviz", {})
        self.rviz_manager = RvizProcessManager(
            rviz_settings.get("config_path", ""),
            executable=rviz_settings.get("executable", "rviz2"),
        )
        self.ros_worker: RosWorker | None = None
        self.component_manager = ComponentProcessManager(self.gui_config.get("system_launcher", {}))
        self.runtime_backend_config = RuntimeBackendConfig.from_gui_config(self.gui_config)
        self.runtime_adapters = RuntimeAdapterRegistry(self.runtime_backend_config)
        self.skill_runtime = GuiSkillRuntimeAdapter(self, self.runtime_adapters)
        self.set_gui_mode(self.gui_config.get("gui_mode", "MOCK"))
        self.append_event("gui_initialized", metadata={"message": "GUI initialized"})

    def build_page(self) -> None:
        ui.colors(primary="#1d4f91", secondary="#546e7a", accent="#1976d2")
        ui.add_head_html("<style>body { background: #f5f7fa; }</style>")
        self.add_welcome_message()
        with ui.column().classes("w-full min-h-screen gap-4 p-4"):
            header_renderer = create_header_panel(self)
            renderers = []
            with ui.splitter(value=32).classes("w-full flex-grow min-h-[520px]") as outer:
                with outer.before:
                    with ui.column().classes("w-full h-full gap-4 pr-2"):
                        chat_renderer = create_chat_panel(self)
                        hitl_renderer = create_hitl_panel(self)
                with outer.after:
                    # Keep System Status as a compact sidebar; the execution
                    # tree benefits more from horizontal space.
                    with ui.splitter(value=76).classes("w-full h-full") as inner:
                        with inner.before:
                            tool_flow_renderer = create_tool_flow_panel(self)
                        with inner.after:
                            renderers.append(create_status_panel(self))
            # Audit log updates are event-driven. Keeping it out of the ROS
            # monitor's 5 Hz renderer list preserves pagination and selection.
            self._log_renderer = create_log_panel(self)
            component_log_renderer = create_component_log_panel(self)
        self._event_renderers = [header_renderer.refresh, chat_renderer, tool_flow_renderer, hitl_renderer]
        # Header contains ROS state, while component output arrives from child
        # processes. They need periodic updates, but not monitor-frequency UI
        # reconstruction.
        ui.timer(1.0, header_renderer.refresh)
        ui.timer(1.0, component_log_renderer.refresh)
        refresh_hz = self.gui_config.get("ros_monitor", {}).get("refresh_hz", 5)
        ui.timer(1.0 / max(1, refresh_hz), lambda: self._refresh_ui(renderers))

    def _refresh_ui(self, renderers) -> None:
        self.refresh_rviz_status()
        self.refresh_component_processes()
        self.consume_ros_status()
        for renderer in renderers:
            renderer.refresh()

    def start_task(self, task_name: str) -> str | None:
        task_name = task_name.strip()
        if not task_name:
            return None
        if ExistingAgentBridge.is_capability_question(task_name):
            self.add_chat_message(task_name, sent=True, name="Operator")
            self.add_chat_message(ExistingAgentBridge.capabilities_message(), sent=False, name=self.agent_name)
            return "capabilities-query"
        if self.gui_config.get("agent_bridge", {}).get("mode", "mock") != "mock":
            return self._start_agent_task(task_name)
        if self.state.task_status not in {TaskStatus.IDLE, TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}:
            return None
        self.reset_task(clear_conversation=False)
        self.state.event_log.clear()
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        self.state.current_task_id = task_id
        self.state.current_task_name = task_name
        self.state.task_status = TaskStatus.UNDERSTANDING_TASK
        self.state.agent_status = SystemComponentStatus.RUNNING
        self.append_event("task_created", new_value=task_name)
        self.add_chat_message(task_name, sent=True, name="Operator")
        self.add_chat_message(f"{self.agent_name} has received the task. Starting mock workflow.", sent=False, name=self.agent_name)
        self.initialize_tool_tree()
        self.append_event("task_started", new_value=task_name)
        self.runner.start(task_id)
        return task_id

    def add_welcome_message(self) -> None:
        """Show one friendly, varied greeting per GUI server session."""
        if self.state.welcome_shown:
            return
        self.state.welcome_shown = True
        welcome = random.SystemRandom().choice(WELCOME_MESSAGES).format(agent_name=self.agent_name)
        self.add_chat_message(welcome, sent=False, name=self.agent_name)

    def _start_agent_task(self, task_name: str) -> str | None:
        task_name = task_name.strip()
        if not task_name or self.state.agent_request_running:
            return None
        self.reset_task(clear_conversation=False)
        self.state.event_log.clear()
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        self.state.current_task_id = task_id
        self.state.current_task_name = task_name
        self.state.agent_status = SystemComponentStatus.RUNNING
        self.state.agent_request_running = True
        self.state.agent_request_cancelled = False
        self.add_chat_message(task_name, sent=True, name="Operator")
        self.append_event("agent_task_submitted", new_value=task_name,
                          metadata={"task_id": task_id, "instruction": task_name,
                                    "execution_mode": self.gui_config["agent_bridge"].get("execution_mode", "plan_only"),
                                    "source": "nicegui"})
        asyncio.create_task(self._request_agent(task_id, task_name))
        return task_id

    async def _request_agent(self, task_id: str, instruction: str) -> None:
        config = self.gui_config["agent_bridge"]
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    ExistingAgentBridge(config["mode"]).submit, instruction,
                    config.get("execution_mode", "plan_only"),
                ),
                timeout=config.get("timeout_sec", 15),
            )
            if self.state.agent_request_cancelled or self.state.current_task_id != task_id:
                return
            self.add_chat_message(response.message, sent=False, name=self.agent_name)
            for event in response.tool_events:
                self.add_agent_tool_event(event)
        except asyncio.TimeoutError:
            self.add_chat_message("Agent request timed out.", sent=False, name="System")
            self.append_event("agent_error", metadata={"reason": "timeout"})
        except Exception as exc:
            self.add_chat_message(str(exc), sent=False, name="System")
            self.append_event("agent_error", metadata={"reason": str(exc)})
        finally:
            self.state.agent_request_running = False
            self.state.agent_status = SystemComponentStatus.IDLE
            # No audit event is emitted for this internal completion flag, but
            # Chat owns the Send button state and must re-enable it promptly.
            self._refresh_event_views()

    def add_agent_tool_event(self, event) -> None:
        status_map = {
            "pending": ToolStatus.PENDING, "running": ToolStatus.RUNNING,
            "succeeded": ToolStatus.SUCCEEDED, "failed": ToolStatus.FAILED,
            "waiting_approval": ToolStatus.WAITING_APPROVAL, "rejected": ToolStatus.REJECTED,
            "cancelled": ToolStatus.CANCELLED,
        }
        node = ToolNode(
            node_id=event.node_id, parent_id=event.parent_id, tool_name=event.tool_name,
            display_name=event.display_name, status=status_map[event.status],
            input_data=event.input_json, output_data=event.output_json,
            input_summary=event.input_json, output_summary=event.output_json,
            error_message=event.error_message, requires_approval=event.requires_approval,
            plan_version=self.state.current_plan_version,
        )
        self.state.tool_nodes.append(node)
        self.append_event("agent_tool_event", node_id=node.node_id, new_value=node.status.value,
                          metadata={"tool_name": node.tool_name, "parent_id": node.parent_id,
                                    "input_json": node.input_data, "output_json": node.output_data,
                                    "requires_approval": node.requires_approval,
                                    "approval_stages": event.approval_stages})
        if node.requires_approval and event.approval_stages:
            self.create_agent_hitl_request(node, event.approval_stages)

    def add_chat_message(self, text: str, *, sent: bool, name: str) -> None:
        self.state.conversation.append(ChatEntry(text=text, sent=sent, name=name))
        self.append_event("chat_message_added", metadata={"sender": name})

    def clear_conversation(self) -> None:
        self.state.conversation.clear()
        self.append_event("chat_cleared")

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
        self.append_event("tool_tree_initialized")

    def update_tool_status(self, node_id: str, status: ToolStatus, **output: Any) -> bool:
        node = self._node(node_id)
        if node is None:
            return False
        old_status = node.status
        node.status = status
        if status in {ToolStatus.RUNNING, ToolStatus.WAITING_APPROVAL}:
            node.start_time = utc_now()
            node.input_summary.update(output.pop("input_summary", {}))
        if status in {ToolStatus.SUCCEEDED, ToolStatus.FAILED, ToolStatus.REJECTED, ToolStatus.CANCELLED, ToolStatus.INVALIDATED}:
            node.end_time = utc_now()
            node.duration_ms = self._duration_ms(node.start_time, node.end_time)
        output_summary = output.pop("output_summary", {})
        input_summary = output.pop("input_summary", {})
        error_message = output.pop("error_message", None)
        if input_summary:
            node.input_summary.update(input_summary)
        if output_summary:
            node.output_summary.update(output_summary)
        if error_message:
            node.error_message = error_message
        if output:
            node.output_data.update(output)
        event_type = {
            ToolStatus.RUNNING: "tool_started",
            ToolStatus.SUCCEEDED: "tool_succeeded",
            ToolStatus.FAILED: "tool_failed",
        }.get(status, "tool_status_changed")
        self.append_event(
            event_type, node_id=node_id, old_value=old_status.value, new_value=status.value,
            metadata={"status": status.value, "duration_ms": node.duration_ms,
                      "input_summary": node.input_summary, "output_summary": node.output_summary,
                      "error_message": node.error_message},
        )
        return True

    def create_trajectory(self) -> str:
        trajectory_id = f"trajectory-{uuid.uuid4().hex[:8]}"
        self.state.current_trajectory_id = trajectory_id
        node = self._node("plan_motion")
        if node:
            node.output_data["trajectory_id"] = trajectory_id
        self.append_event("trajectory_created", node_id="plan_motion", new_value=trajectory_id)
        return trajectory_id

    def create_hitl_request(self) -> HitlRequest:
        request = HitlRequest(
            request_id=f"review-{uuid.uuid4().hex[:8]}", task_id=self.state.current_task_id or "",
            request_type="trajectory_review", target_id="selected_target",
            title="Trajectory approval required", description="Review mock motion trajectory before execution.",
            options=[HitlDecision.APPROVE, HitlDecision.REJECT, HitlDecision.REPLAN, HitlDecision.CANCEL],
            created_at=utc_now(), trajectory_id=self.state.current_trajectory_id,
            grasp_candidate_id=None,
            plan_version=self.state.current_plan_version,
        )
        self.state.pending_hitl_request = request
        self.append_event("hitl_requested", node_id="trajectory_review", new_value=request.request_id,
                          metadata={"request_id": request.request_id, "trajectory_id": request.trajectory_id})
        return request

    def set_trajectory_adapter(self, adapter: ExistingTrajectoryReviewAdapter) -> None:
        """Inject the existing MoveIt-backed adapter (also used by unit tests)."""
        self.trajectory_adapter = adapter

    def _ensure_trajectory_adapter(self) -> ExistingTrajectoryReviewAdapter:
        if self.trajectory_adapter is not None:
            return self.trajectory_adapter
        if self.ros_worker is None:
            if (
                self.runtime_backend_config.perception_mode == "mock"
                and self.runtime_backend_config.grasp_mode == "mock"
            ):
                from hitl_gui.mock.mock_trajectory_backend import MockMotionBackend, MockTrajectoryValidator
                self.trajectory_adapter = ExistingTrajectoryReviewAdapter(
                    MockMotionBackend(), MockTrajectoryValidator(), visualizer=None,
                )
                self.trajectory_adapter.run_in_worker = False
                return self.trajectory_adapter
            raise RuntimeError("ROS monitoring must be enabled before requesting a MoveIt trajectory.")
        from llm_skill_robot.core.execution_mode import load_execution_config
        from llm_skill_robot.core.trajectory_validator import TrajectoryValidator
        from llm_skill_robot.core.trajectory_visualizer import TrajectoryVisualizer
        from llm_skill_robot.robot.ur5_moveit_plan_backend import UR5MoveItPlanBackend
        from llm_skill_robot.safety.hardware_mode import load_hardware_mode

        real_robot = self.state.robot_mode in {"REAL", "REAL ROBOT"}
        execution_config = load_execution_config().model_dump()
        execution_config["mode"] = "real_robot" if real_robot else "rviz_sim"
        if not real_robot:
            execution_config["allowed_execution_modes"] = list(
                set(execution_config.get("allowed_execution_modes", [])) | {"rviz_sim"}
            )
        backend = UR5MoveItPlanBackend(
            dry_run=False,
            mode=execution_config["mode"],
            execution_config=execution_config,
            hardware_mode=load_hardware_mode(),
        )
        self.trajectory_adapter = ExistingTrajectoryReviewAdapter(
            backend,
            TrajectoryValidator(self._load_safety_config()),
            TrajectoryVisualizer(node=backend.node, topic=execution_config.get("visual_preview_topic", "/display_planned_path")),
        )
        return self.trajectory_adapter

    def request_named_target_trajectory(self, target: str, *, source_node_id: str | None = None):
        """Schedule existing MoveIt planning; never create a new execution path."""
        self._last_trajectory_task = asyncio.create_task(self._plan_named_target_trajectory(target, source_node_id))
        return self._last_trajectory_task

    def request_pose_trajectory(
        self, pose: dict[str, Any], *, skill_id: str, source_node_id: str | None = None,
        velocity_scale: float = 0.03, acceleration_scale: float = 0.03,
        planning_kwargs: dict[str, Any] | None = None,
    ):
        """Schedule one existing MoveIt pose plan for a Safe Pick motion node."""
        self._last_trajectory_task = asyncio.create_task(
            self._plan_pose_trajectory(
                pose, skill_id, source_node_id, velocity_scale,
                acceleration_scale, planning_kwargs or {},
            )
        )
        return self._last_trajectory_task

    async def _plan_named_target_trajectory(self, target: str, source_node_id: str | None) -> None:
        self.state.task_status = TaskStatus.PLANNING
        self.append_event("trajectory_planning_started", node_id=source_node_id, metadata={"target": target})
        try:
            adapter = self._ensure_trajectory_adapter()
            if adapter.run_in_worker:
                record = await asyncio.to_thread(adapter.plan_named_target, target, self.state.current_plan_version)
            else:
                record = adapter.plan_named_target(target, self.state.current_plan_version)
        except Exception as exc:
            self.state.task_status = TaskStatus.FAILED
            self.append_event("tool_failed", node_id=source_node_id, metadata={"reason": str(exc), "target": target})
            self.add_chat_message(f"MoveIt planning failed: {exc}", sent=False, name="System")
            return
        self._create_trajectory_review_request(record, source_node_id)
        # Publishing one DisplayTrajectory is non-blocking; keeping it on this
        # coroutine also avoids racing a planning worker with a second executor.
        preview = adapter.preview(record.trajectory_id)
        self.append_event("trajectory_preview_published", node_id=source_node_id,
                          metadata={"trajectory_id": record.trajectory_id, "preview": preview})

    async def _plan_pose_trajectory(
        self, pose: dict[str, Any], skill_id: str, source_node_id: str | None,
        velocity_scale: float, acceleration_scale: float, planning_kwargs: dict[str, Any],
    ) -> None:
        self.state.task_status = TaskStatus.PLANNING
        self.append_event("trajectory_planning_started", node_id=source_node_id,
                          metadata={"skill_id": skill_id, "target_pose": pose,
                                    "velocity_scale": velocity_scale,
                                    "acceleration_scale": acceleration_scale,
                                    "planning_kwargs": planning_kwargs})
        try:
            adapter = self._ensure_trajectory_adapter()
            if adapter.run_in_worker:
                record = await asyncio.to_thread(
                    adapter.plan_pose, pose, self.state.current_plan_version, skill_id=skill_id,
                    velocity_scale=velocity_scale, acceleration_scale=acceleration_scale,
                    planning_kwargs=planning_kwargs,
                )
            else:
                record = adapter.plan_pose(
                    pose, self.state.current_plan_version, skill_id=skill_id,
                    velocity_scale=velocity_scale, acceleration_scale=acceleration_scale,
                    planning_kwargs=planning_kwargs,
                )
        except Exception as exc:
            self.state.task_status = TaskStatus.FAILED
            self.update_tool_status(source_node_id, ToolStatus.FAILED, error_message=str(exc)) if source_node_id else None
            self.append_event("tool_failed", node_id=source_node_id, metadata={"reason": str(exc), "skill_id": skill_id})
            self.add_chat_message(f"MoveIt pose planning failed: {exc}", sent=False, name="System")
            return
        self._create_trajectory_review_request(record, source_node_id)
        preview = adapter.preview(record.trajectory_id)
        self.append_event("trajectory_preview_published", node_id=source_node_id,
                          metadata={"trajectory_id": record.trajectory_id, "preview": preview})

    def _create_trajectory_review_request(self, record, source_node_id: str | None) -> HitlRequest:
        summary = record.summary
        trajectory_id = record.trajectory_id
        self.state.current_trajectory_id = trajectory_id
        # A Safe Pick contains several motion reviews under the same plan
        # version.  The trajectory ID prevents later approvals from resolving
        # the first review node by mistake.
        review_node_id = f"trajectory_review_{trajectory_id}"
        review_node = ToolNode(
            node_id=review_node_id, parent_id=source_node_id, tool_name="trajectory_review",
            display_name=f"Trajectory Review (Attempt {record.plan_version})",
            status=ToolStatus.WAITING_APPROVAL, requires_approval=True,
            plan_version=record.plan_version,
            output_data={"trajectory_id": trajectory_id, "summary": summary,
                         "validation": record.validation_result},
            output_summary={"trajectory_id": trajectory_id,
                            "trajectory_points": summary.get("num_trajectory_points", 0)},
        )
        self.state.tool_nodes.append(review_node)
        request = HitlRequest(
            request_id=f"trajectory-review-{uuid.uuid4().hex[:8]}",
            task_id=self.state.current_task_id or "", request_type="trajectory_review",
            target_id=review_node_id, title="Trajectory approval required",
            description="Review the MoveIt trajectory in RViz before it is executed.",
            options=[HitlDecision.APPROVE, HitlDecision.REJECT, HitlDecision.REPLAN, HitlDecision.CANCEL],
            created_at=utc_now(), trajectory_id=trajectory_id, grasp_candidate_id=None,
            plan_version=record.plan_version, planning_success=bool(summary.get("success")),
            trajectory_points=int(summary.get("num_trajectory_points", 0)),
            trajectory_duration=summary.get("duration_sec"), planning_time=record.planning_time_ms,
            collision_check=record.validation_result.get("decision", "UNKNOWN"),
            target_summary=record.target_summary,
        )
        self.state.pending_hitl_request = request
        self.state.task_status = TaskStatus.WAITING_APPROVAL
        self.append_event("hitl_requested", node_id=review_node_id, new_value=request.request_id,
                          metadata={"request_id": request.request_id, "trajectory_id": trajectory_id,
                                    "plan_version": record.plan_version, "planning_success": request.planning_success,
                                    "collision_check": request.collision_check})
        return request

    def create_agent_hitl_request(self, node: ToolNode, approval_stages: list[str]) -> HitlRequest | None:
        """Create a GUI review request for an Agent proposal, not robot motion."""
        if self.state.pending_hitl_request is not None:
            return None
        stage = approval_stages[0]
        titles = {
            "task_intent": "Task and target approval required",
            "grasp_candidate": "Grasp candidate approval required",
            "trajectory": "Trajectory approval required",
            "execution": "Execution release required",
        }
        descriptions = {
            "task_intent": "Review the Agent's proposed task and target before it is sent to the Runtime.",
            "grasp_candidate": "Review the selected grasp candidate before motion planning.",
            "trajectory": "Review the planned trajectory before motion execution.",
            "execution": "Confirm the final actuation release. No robot command is sent by this GUI yet.",
        }
        request = HitlRequest(
            request_id=f"agent-review-{uuid.uuid4().hex[:8]}",
            task_id=self.state.current_task_id or "",
            request_type=stage,
            target_id=node.node_id,
            title=titles.get(stage, "Agent approval required"),
            description=descriptions.get(stage, "Review the Agent proposal before continuing."),
            options=[HitlDecision.APPROVE, HitlDecision.REJECT, HitlDecision.CANCEL],
            created_at=utc_now(), trajectory_id=None, grasp_candidate_id=None,
        )
        self.state.pending_hitl_request = request
        self.state.task_status = TaskStatus.WAITING_APPROVAL
        self.append_event(
            "hitl_requested", node_id=node.node_id, new_value=request.request_id,
            metadata={"request_id": request.request_id, "request_type": stage,
                      "approval_stages": approval_stages, "tool_name": node.tool_name},
        )
        return request

    def submit_hitl_decision(self, request_id: str, decision: HitlDecision, *, real_confirmed: bool = False) -> bool:
        request = self.state.pending_hitl_request
        if request is None or request.status != "PENDING":
            return False
        if request.request_id != request_id or request.task_id != self.state.current_task_id:
            return False
        if request.request_type != "trajectory_review":
            return self._submit_agent_hitl_decision(request, decision)
        if request.plan_version != self.state.current_plan_version:
            return False
        if request.trajectory_id != self.state.current_trajectory_id:
            return False
        if request.trajectory_id in self._invalidated_trajectory_ids:
            return False
        if (
            decision == HitlDecision.APPROVE
            and self.state.robot_mode in {"REAL", "REAL ROBOT"}
            and not real_confirmed
        ):
            return False
        if self.trajectory_adapter is not None and request.trajectory_id in self.trajectory_adapter.records:
            return self._submit_existing_trajectory_decision(request, decision)
        request.status = decision.value
        review = self._active_trajectory_review()
        if review:
            review.output_summary.update({
                "decision": decision.value,
                "approval_latency_ms": self._duration_ms(review.start_time, utc_now()),
            })
        event_type = {
            HitlDecision.APPROVE: "hitl_approved", HitlDecision.REJECT: "hitl_rejected",
            HitlDecision.REPLAN: "hitl_replan_requested", HitlDecision.CANCEL: "task_cancelled",
        }[decision]
        self.append_event(event_type, node_id="trajectory_review", new_value=decision.value,
                          metadata={"request_id": request.request_id, "trajectory_id": request.trajectory_id,
                                    "user_decision": decision.value})
        if decision == HitlDecision.CANCEL:
            self.cancel_task()
            return True
        self.state.pending_hitl_request = None
        self.runner.submit_decision(decision)
        return True

    def _submit_existing_trajectory_decision(self, request: HitlRequest, decision: HitlDecision) -> bool:
        """Resolve a GUI review against the exact cached MoveIt plan only."""
        if decision == HitlDecision.REPLAN:
            return self.replan_trajectory(request.request_id, "other")
        request.status = decision.value
        self.state.pending_hitl_request = None
        node = self._node(request.target_id)
        if decision in {HitlDecision.REJECT, HitlDecision.CANCEL}:
            if node:
                node.status = ToolStatus.REJECTED if decision == HitlDecision.REJECT else ToolStatus.CANCELLED
            self.state.task_status = TaskStatus.CANCELLED
            self.append_event("hitl_rejected" if decision == HitlDecision.REJECT else "task_cancelled",
                              node_id=request.target_id, new_value=decision.value,
                              metadata={"request_id": request.request_id, "trajectory_id": request.trajectory_id,
                                        "plan_version": request.plan_version})
            return True
        if decision != HitlDecision.APPROVE:
            return False
        if node:
            node.status = ToolStatus.SUCCEEDED
        self.state.task_status = TaskStatus.EXECUTING
        self.append_event("hitl_approved", node_id=request.target_id, new_value=decision.value,
                          metadata={"request_id": request.request_id, "trajectory_id": request.trajectory_id,
                                    "plan_version": request.plan_version})
        self._last_execution_task = asyncio.create_task(self._execute_existing_trajectory(request))
        return True

    async def _execute_existing_trajectory(self, request: HitlRequest) -> None:
        trajectory_id = request.trajectory_id
        if trajectory_id is None or trajectory_id != self.state.current_trajectory_id or trajectory_id in self._invalidated_trajectory_ids:
            return
        self.append_event("execution_started", node_id=request.target_id,
                          metadata={"trajectory_id": trajectory_id, "plan_version": request.plan_version})
        try:
            real_robot = self.state.robot_mode in {"REAL", "REAL ROBOT"}
            if self.trajectory_adapter.run_in_worker:
                result = await asyncio.to_thread(self.trajectory_adapter.execute, trajectory_id, real_robot=real_robot)
            else:
                result = self.trajectory_adapter.execute(trajectory_id, real_robot=real_robot)
        except Exception as exc:
            result = {"success": False, "message": str(exc), "plan_id": trajectory_id}
        if trajectory_id != self.state.current_trajectory_id or trajectory_id in self._invalidated_trajectory_ids:
            return
        success = bool(result.get("success"))
        event_type = "execution_succeeded" if success else "execution_failed"
        self.state.task_status = TaskStatus.COMPLETED if success else TaskStatus.FAILED
        self.append_event(event_type, node_id=request.target_id,
                          metadata={"trajectory_id": trajectory_id, "plan_version": request.plan_version,
                                    "execution_duration": result.get("execution_duration"),
                                    "controller_result": result})
        self.add_chat_message(result.get("message", "Trajectory execution finished."), sent=False, name="System")
        review = self._node(request.target_id)
        source_node_id = review.parent_id if review else None
        if success and source_node_id:
            self.update_tool_status(source_node_id, ToolStatus.SUCCEEDED,
                                    output_summary={"trajectory_id": trajectory_id, "controller_result": result})
            self.skill_runtime.on_motion_execution_completed(source_node_id)

    def replan_trajectory(self, request_id: str, reason: str) -> bool:
        request = self.state.pending_hitl_request
        if request is None or request.request_id != request_id or request.request_type != "trajectory_review":
            return False
        trajectory_id = request.trajectory_id
        if trajectory_id is None or self.trajectory_adapter is None:
            return False
        record = self.trajectory_adapter.records.get(trajectory_id)
        if record is None or record.invalidated:
            return False
        request.status = "INVALIDATED"
        record.invalidated = True
        self._invalidated_trajectory_ids.add(trajectory_id)
        self.state.pending_hitl_request = None
        review = self._node(request.target_id)
        if review:
            review.status = ToolStatus.INVALIDATED
        old_version = self.state.current_plan_version
        self.state.current_plan_version += 1
        self.append_event("trajectory_invalidated", node_id=request.target_id,
                          metadata={"trajectory_id": trajectory_id, "request_id": request.request_id,
                                    "reason": reason})
        self.append_event("plan_version_changed", old_value=old_version, new_value=self.state.current_plan_version,
                          metadata={"reason": reason})
        target = record.summary.get("target_name")
        source_node_id = review.parent_id if review else None
        if target:
            self.request_named_target_trajectory(target, source_node_id=source_node_id)
        elif record.planning_request.get("kind") == "pose":
            self.request_pose_trajectory(
                record.planning_request["pose"],
                skill_id=record.planning_request.get("skill_id", "move_to_pose"),
                source_node_id=source_node_id,
                velocity_scale=record.planning_request.get("velocity_scale", 0.03),
                acceleration_scale=record.planning_request.get("acceleration_scale", 0.03),
                planning_kwargs=record.planning_request.get("planning_kwargs", {}),
            )
        else:
            self.state.task_status = TaskStatus.FAILED
            return False
        return True

    def _submit_agent_hitl_decision(self, request: HitlRequest, decision: HitlDecision) -> bool:
        """Resolve a plan-only A/B/C/D gate without implying robot execution."""
        if decision == HitlDecision.REPLAN:
            return False
        request.status = decision.value
        node = self._node(request.target_id)
        event_type = {
            HitlDecision.APPROVE: "hitl_approved",
            HitlDecision.REJECT: "hitl_rejected",
            HitlDecision.CANCEL: "task_cancelled",
        }[decision]
        self.append_event(
            event_type, node_id=request.target_id, new_value=decision.value,
            metadata={"request_id": request.request_id, "request_type": request.request_type,
                      "user_decision": decision.value},
        )
        self.state.pending_hitl_request = None
        if decision == HitlDecision.APPROVE:
            if node:
                node.status = ToolStatus.PENDING
                node.output_data["approval"] = "APPROVED"
                node.output_summary["approval"] = "APPROVED"
            self.state.task_status = TaskStatus.APPROVED_PENDING_EXECUTION
            self.state.agent_status = SystemComponentStatus.READY
            self.add_chat_message(
                f"{request.title} approved. The proposal is queued for Agent Runtime; no robot action has been executed.",
                sent=False, name="System",
            )
            if node and node.tool_name == "move_to_named_target":
                target = node.input_data.get("target_name") or node.input_data.get("target")
                if target:
                    self.request_named_target_trajectory(str(target), source_node_id=node.node_id)
            elif node and node.tool_name in {"safe_pick_object", "safe_pick"} and request.request_type == "task_intent":
                # This runs only sensor/grasp proposal stages.  It does not
                # invoke MoveIt or any gripper/robot command.
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    # This branch is for synchronous integration callers. The
                    # browser callback always owns an event loop and starts
                    # the runtime immediately.
                    self.add_chat_message(
                        "Task intent was approved. Start this workflow from the GUI event loop to run its perception stages.",
                        sent=False, name="System",
                    )
                else:
                    self._last_skill_task = loop.create_task(
                        self.skill_runtime.run_safe_pick_observation(node)
                    )
            elif node and node.tool_name == "review_grasp_candidate" and request.request_type == "grasp_candidate":
                node.status = ToolStatus.SUCCEEDED
                self.skill_runtime.continue_after_grasp_review(node)
            elif node and node.tool_name in {"open_gripper", "close_gripper"} and request.request_type == "execution":
                self._last_skill_task = asyncio.create_task(
                    self.skill_runtime.execute_gripper_after_release(node)
                )
        else:
            if node:
                node.status = ToolStatus.REJECTED if decision == HitlDecision.REJECT else ToolStatus.CANCELLED
                node.output_data["approval"] = decision.value
            self.state.task_status = TaskStatus.CANCELLED
            self.state.agent_status = SystemComponentStatus.IDLE
            self.add_chat_message("Agent proposal was not approved by the user.", sent=False, name="System")
        self._refresh_event_views()
        return True

    @staticmethod
    def _load_safety_config() -> dict:
        try:
            from llm_skill_robot.utils import get_config_dir
            path = get_config_dir() / "safety.yaml"
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
        except Exception:
            return {}

    def cancel_task(self) -> None:
        self.skill_runtime.cancel(self.state.current_task_id)
        if self.state.agent_request_running:
            self.state.agent_request_cancelled = True
            self.state.agent_request_running = False
            self.state.agent_status = SystemComponentStatus.IDLE
            self.append_event("agent_task_cancelled", metadata={"task_id": self.state.current_task_id})
            self.add_chat_message("Agent request cancelled by user.", sent=False, name="System")
            return
        if self.state.task_status in {TaskStatus.IDLE, TaskStatus.COMPLETED, TaskStatus.CANCELLED}:
            return
        self.state.task_status = TaskStatus.CANCELLED
        self.state.agent_status = SystemComponentStatus.IDLE
        self.state.pending_hitl_request = None
        for node in self.state.tool_nodes:
            if node.status in {ToolStatus.PENDING, ToolStatus.RUNNING, ToolStatus.WAITING_APPROVAL}:
                self.update_tool_status(node.node_id, ToolStatus.CANCELLED)
        self.add_chat_message("Task cancelled by user.", sent=False, name=self.agent_name)
        self.append_event("task_cancelled")
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
        self.add_chat_message("Task was rejected by the user.", sent=False, name=self.agent_name)
        self.append_event("task_cancelled", metadata={"reason": "hitl_rejected"})

    def replan_task(self) -> None:
        old_review = self._active_trajectory_review()
        if old_review:
            self.update_tool_status(old_review.node_id, ToolStatus.INVALIDATED)
            self.append_event("trajectory_invalidated", node_id=old_review.node_id,
                              metadata={"trajectory_id": self.state.current_trajectory_id})
        old_version = self.state.current_plan_version
        self.state.current_plan_version += 1
        self.append_event("plan_version_changed", old_value=old_version,
                          new_value=self.state.current_plan_version)
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
        self.append_event("plan_replanned", node_id=plan_id, new_value=trajectory_id, metadata={"plan_version": attempt})

    def complete_task(self) -> None:
        self.state.task_status = TaskStatus.COMPLETED
        self.state.agent_status = SystemComponentStatus.IDLE
        self.add_chat_message("Task completed successfully in mock simulation.", sent=False, name=self.agent_name)
        self.append_event("task_completed")

    def complete_active_trajectory_review(self) -> None:
        review = self._active_trajectory_review()
        if review:
            self.update_tool_status(review.node_id, ToolStatus.SUCCEEDED)

    def set_task_status(self, status: TaskStatus) -> None:
        old_status = self.state.task_status
        self.state.task_status = status
        self.append_event("task_status_changed", old_value=old_status.value, new_value=status.value)

    def append_event(self, event_type: str, *, node_id: str | None = None, old_value: Any = None, new_value: Any = None, metadata: dict[str, Any] | None = None) -> ExecutionEvent:
        event = ExecutionEvent(f"event-{uuid.uuid4().hex[:8]}", self.state.current_task_id, node_id, event_type, utc_now(), self.state.current_plan_version, old_value, new_value, metadata or {})
        self.state.event_log.append(event)
        if event_type in {"hitl_approved", "hitl_rejected", "hitl_replan_requested", "plan_version_changed"}:
            self.state.modification_history.append(event)
        if self._log_renderer is not None:
            self._log_renderer.refresh()
        self._refresh_event_views()
        return event

    def _refresh_event_views(self) -> None:
        """Refresh state-machine views only when their underlying data changes."""
        for renderer in self._event_renderers:
            renderer()

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
        self.append_event("task_reset")

    def export_task_log(self):
        return self.session_logger.export_task(self.state)

    def start_rviz(self) -> dict:
        result = self.rviz_manager.start_rviz()
        self._record_rviz_result("rviz_start_requested", result)
        return result

    def stop_rviz(self) -> dict:
        result = self.rviz_manager.stop_rviz()
        self._record_rviz_result("rviz_stop_requested", result)
        return result

    def restart_rviz(self) -> dict:
        result = self.rviz_manager.restart_rviz()
        self._record_rviz_result("rviz_restart_requested", result)
        return result

    def refresh_rviz_status(self) -> dict:
        result = self.rviz_manager.get_process_status()
        self.state.rviz_process_status = result["status"]
        self.state.rviz_running = result["running"]
        self.state.hardware_status["RViz2"] = (
            SystemComponentStatus.RUNNING if result["running"] else SystemComponentStatus.DISCONNECTED
        )
        return result

    def request_trajectory_preview(self) -> None:
        self.append_event("trajectory_preview_requested", node_id="trajectory_review",
                          metadata={"trajectory_id": self.state.current_trajectory_id, "mode": "mock"})

    def preview_current_trajectory(self) -> None:
        trajectory_id = self.state.current_trajectory_id
        if self.trajectory_adapter is None or trajectory_id is None:
            self.request_trajectory_preview()
            return
        asyncio.create_task(self._preview_existing_trajectory(trajectory_id))

    async def _preview_existing_trajectory(self, trajectory_id: str) -> None:
        result = self.trajectory_adapter.preview(trajectory_id)
        self.append_event("trajectory_preview_requested", node_id="trajectory_review",
                          metadata={"trajectory_id": trajectory_id, "preview": result})

    def shutdown(self) -> None:
        self.stop_rviz()
        if self.ros_worker:
            self.ros_worker.shutdown()
        self.component_manager.shutdown()

    def refresh_component_processes(self) -> None:
        self.state.component_processes = self.component_manager.refresh()

    def start_component(self, component_id: str, *, confirmed: bool = False):
        self.append_event("component_start_requested", metadata={"component_id": component_id, "initiated_by": "gui"})
        managed = self.component_manager.start_component(component_id, confirmed=confirmed)
        if component_id == "ur5_fake" and managed.status.value == "RUNNING":
            self.state.robot_mode = "SIMULATION"
        elif component_id == "ur5_real" and managed.status.value == "RUNNING":
            self.state.robot_mode = "REAL ROBOT"
        event = "component_started" if managed.status.value == "RUNNING" else "component_start_failed"
        self.append_event(event, metadata={"component_id": component_id, "command_summary": managed.command, "pid": managed.pid, "robot_mode": component_id, "initiated_by": "gui"})
        self.refresh_component_processes()
        return managed

    def stop_component(self, component_id: str):
        self.append_event("component_stop_requested", metadata={"component_id": component_id, "initiated_by": "gui"})
        managed = self.component_manager.stop_component(component_id)
        if managed:
            self.append_event("component_stopped", metadata={"component_id": component_id, "pid": managed.pid, "exit_code": managed.exit_code, "initiated_by": "gui"})
        self.refresh_component_processes()
        return managed

    def restart_component(self, component_id: str, *, confirmed: bool = False):
        self.append_event("component_restart_requested", metadata={"component_id": component_id, "initiated_by": "gui"})
        managed = self.component_manager.restart_component(component_id, confirmed=confirmed)
        self.refresh_component_processes()
        return managed

    def confirm_real_ur5_start(self):
        self.append_event("real_robot_driver_confirmed", metadata={"component_id": "ur5_real", "robot_ip": "192.168.10.27", "initiated_by": "gui"})
        return self.start_component("ur5_real", confirmed=True)

    def start_simulation_components(self):
        """Launch only the configured simulation stack, with ROS-health gating."""
        if self.state.simulation_launch_status == "RUNNING":
            return None
        self.state.simulation_launch_status = "RUNNING"
        return asyncio.create_task(self._start_simulation_components())

    async def _start_simulation_components(self):
        ur5 = self.start_component("ur5_fake")
        if ur5.status.value != "RUNNING":
            self.state.simulation_launch_status = "FAILED: UR5 Fake process"
            return
        timeout = self.gui_config["system_launcher"]["components"]["ur5_fake"].get("timeout_sec", 20)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            self.consume_ros_status()
            if self.state.hardware_status["UR5"] == SystemComponentStatus.READY:
                break
            await asyncio.sleep(0.2)
        else:
            self.state.simulation_launch_status = "FAILED: UR5 ROS Health not READY"
            self.append_event("component_start_failed", metadata={"component_id": "ur5_fake", "reason": "ROS health timeout"})
            return

        rviz = self.start_rviz()
        if rviz["status"] != "RUNNING":
            self.state.simulation_launch_status = "FAILED: RViz"
            return
        for component_id in ("camera", "gripper", "graspgenx"):
            managed = self.start_component(component_id)
            if managed.status.value != "RUNNING":
                self.state.simulation_launch_status = f"FAILED: {component_id}"
                return
        self.state.simulation_launch_status = "COMPLETED"

    def stop_gui_managed_components(self):
        for component_id in list(self.component_manager.processes):
            self.stop_component(component_id)

    def set_gui_mode(self, mode: str) -> None:
        mode = mode.upper()
        if mode == "ROS":
            self.ros_worker = RosWorker(self.gui_config.get("ros_monitor", {}))
            self.ros_worker.start()
            self.state.ros_status = SystemComponentStatus.IDLE
        else:
            if self.ros_worker:
                self.ros_worker.shutdown()
            self.ros_worker = None
            self.state.ros_status = SystemComponentStatus.IDLE

    def consume_ros_status(self) -> None:
        if self.ros_worker is None:
            self.state.ros_executor_running = False
            self.state.ros_node_initialized = False
            return
        snapshot = self.ros_worker.snapshot()
        # Adapter construction is lazy, so this only makes the existing
        # monitor node available; it neither starts a second executor nor
        # changes its callback ownership.
        self.runtime_adapters.ros_node = getattr(self.ros_worker, "node", None)
        self.state.ros_executor_running = snapshot["executor_running"]
        self.state.ros_node_initialized = snapshot["node_initialized"]
        if snapshot.get("worker_error"):
            self.state.ros_status = SystemComponentStatus.ERROR
        elif snapshot["executor_running"] and snapshot["node_initialized"]:
            self.state.ros_status = SystemComponentStatus.RUNNING
        else:
            self.state.ros_status = SystemComponentStatus.IDLE
        monitor = self.gui_config.get("ros_monitor", {})
        details = component_status(snapshot, monitor.get("ready_age_sec", 1.0), monitor.get("warning_age_sec", 3.0))
        self.state.ros_monitor_data = details
        for component in ("UR5", "D435i", "Robotiq 2F-140", "MoveIt", "SAM3", "GraspGenX"):
            self.state.hardware_status[component] = details[component]["status"]

    def _record_rviz_result(self, event_type: str, result: dict) -> None:
        self.refresh_rviz_status()
        self.append_event(event_type, metadata=result)

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
