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
    SystemComponentStatus, TaskExperimentMetrics, TaskStatus, ToolNode, ToolStatus, utc_now,
)
from hitl_gui.mock.mock_task_runner import MockTaskRunner
from hitl_gui.session_logger import SessionLogger
from hitl_gui.rviz_process_manager import RvizProcessManager, load_gui_config
from hitl_gui.services.embedded_rviz_manager import EmbeddedRvizManager
from hitl_gui.ros_worker import RosWorker
from hitl_gui.message_converter import component_status
from hitl_gui.component_process_manager import ComponentProcessManager
from hitl_gui.agent_bridge import ExistingAgentBridge
from hitl_gui.trajectory_review_adapter import ExistingTrajectoryReviewAdapter
from hitl_gui.runtime_adapters import RuntimeAdapterRegistry, RuntimeBackendConfig
from hitl_gui.gui_skill_runtime import GuiSkillRuntimeAdapter
from hitl_gui.task_plan_adapter import TaskPlanAdapter
from hitl_gui.plan_event_converter import PlanEventConverter
from nicegui import ui
from hitl_gui.panels.chat_panel import create_chat_panel
from hitl_gui.panels.header_panel import create_header_panel
from hitl_gui.panels.hitl_panel import create_hitl_panel
from hitl_gui.panels.log_panel import create_log_panel
from hitl_gui.panels.status_panel import create_status_panel
from hitl_gui.panels.tool_flow_panel import create_tool_flow_panel
from hitl_gui.panels.component_log_panel import create_component_log_panel
from hitl_gui.panels.task_summary_panel import create_task_summary_panel
from hitl_gui.panels.embedded_rviz_panel import EmbeddedRvizPanel
from hitl_gui.panels.visual_perception_panel import VisualPerceptionPanel


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

    def __init__(self, step_delay: float = 0.6, log_root: str | None = None,
                 config_overrides: dict[str, Any] | None = None) -> None:
        # Assigned once a browser page is built. Audit events may also be
        # created during controller construction, before a panel exists.
        self._log_renderer = None
        self._event_renderers = []
        self._event_views_dirty = False
        self._log_view_dirty = False
        self.trajectory_adapter = None
        self._invalidated_trajectory_ids: set[str] = set()
        self._last_trajectory_task = None
        self._last_execution_task = None
        self._last_skill_task = None
        self._real_gripper_confirmed_nodes: set[str] = set()
        self.last_decision_error: str | None = None
        self._last_ros_worker_error: str | None = None
        self._shutdown_complete = False
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
        self.task_plan_adapter = TaskPlanAdapter()
        self.plan_event_converter = PlanEventConverter()
        self.runner = MockTaskRunner(self, step_delay=step_delay)
        self.gui_config = load_gui_config()
        self._apply_config_overrides(config_overrides or {})
        self.session_logger = SessionLogger(log_root or self.gui_config.get("log_directory", "logs"))
        self.agent_name = self.gui_config.get("agent_bridge", {}).get("display_name", "Milo")
        self.state.robot_mode = self.gui_config.get("robot_mode", self.gui_config.get("mode", "SIMULATION")).upper()
        rviz_settings = self.gui_config.get("rviz", {})
        self.rviz_manager = RvizProcessManager(
            rviz_settings.get("config_path", ""),
            executable=rviz_settings.get("executable", "rviz2"),
        )
        self.embedded_rviz_manager = EmbeddedRvizManager(self.gui_config.get("embedded_rviz", {}))
        self.ros_worker: RosWorker | None = None
        self.component_manager = ComponentProcessManager(
            self.gui_config.get("system_launcher", {}),
            conflict_checker=self._component_conflict_reason,
        )
        self.runtime_backend_config = RuntimeBackendConfig.from_gui_config(self.gui_config)
        self.runtime_adapters = RuntimeAdapterRegistry(self.runtime_backend_config)
        self.skill_runtime = GuiSkillRuntimeAdapter(self, self.runtime_adapters)
        self.set_gui_mode(self.gui_config.get("gui_mode", "MOCK"))
        self.append_event("gui_initialized", metadata={"message": "GUI initialized"})

    def build_page(self) -> None:
        ui.colors(primary="#1d4f91", secondary="#546e7a", accent="#1976d2")
        ui.add_head_html(
            "<style>body { background: #f5f7fa; } "
            ".q-card { border-radius: 8px; } "
            ".q-expansion-item { border-radius: 8px; overflow: hidden; }</style>"
        )
        self.add_welcome_message()
        with ui.column().classes("w-full min-h-screen gap-3 p-3"):
            header_renderer = create_header_panel(self)
            renderers = []
            # Keep conversation focused but leave the primary workspace for
            # the task tree and its live RViz trajectory preview.
            # Top workspace: operator intent/review and its corresponding
            # execution views share one deliberate horizontal boundary.
            with ui.splitter(value=24).classes("w-full min-h-[650px]") as outer:
                with outer.before:
                    with ui.column().classes("w-full h-full gap-4 pr-2"):
                        chat_renderer = create_chat_panel(self)
                        hitl_renderer = create_hitl_panel(self)
                with outer.after:
                    with ui.column().classes("w-full h-full gap-3 pl-2"):
                        # Keep 3D RViz wider than the compact plan/evidence
                        # column. It stays usable throughout manipulation.
                        with ui.splitter(value=44).classes("w-full min-h-[650px]") as inner:
                            with inner.before:
                                with ui.column().classes("w-full gap-3"):
                                    tool_flow_renderer = create_tool_flow_panel(self)
                            with inner.after:
                                embedded_rviz_renderer = EmbeddedRvizPanel(
                                    self.embedded_rviz_manager,
                                    self.gui_config.get("embedded_rviz", {}).get("iframe_url", ""),
                                    open_native_rviz=self.start_rviz,
                                ).render()
            # Bottom workspace: System and Scene begin on exactly the same
            # horizontal line. Their widths are intentionally independent of
            # the narrow Chat/HITL column above.
            with ui.splitter(value=29).classes("w-full items-start") as lower:
                with lower.before:
                    with ui.column().classes("w-full pr-2"):
                        status_renderer = create_status_panel(self, compact=True)
                with lower.after:
                    with ui.column().classes("w-full pl-2"):
                        visual_perception_renderer = VisualPerceptionPanel(self).render()
            renderers.extend([status_renderer, visual_perception_renderer])
            # Audit log updates are event-driven. Keeping it out of the ROS
            # monitor's 5 Hz renderer list preserves pagination and selection.
            self._log_renderer = create_log_panel(self)
            task_summary_renderer = create_task_summary_panel(self)
            component_log_renderer = create_component_log_panel(self)
        self._event_renderers = [header_renderer.refresh, chat_renderer, tool_flow_renderer, hitl_renderer,
                                 status_renderer,
                                 embedded_rviz_renderer, visual_perception_renderer,
                                 task_summary_renderer]
        # Header contains ROS state, while component output arrives from child
        # processes. They need periodic updates, but not monitor-frequency UI
        # reconstruction.
        ui.timer(1.0, header_renderer.refresh)
        # Component logs are rebuilt periodically, but the panel preserves
        # which component sections the operator has expanded.
        ui.timer(1.0, component_log_renderer.refresh)
        # Async Agent/Tool tasks only mark views dirty. This page-owned timer
        # is the sole event-driven path that creates or refreshes UI elements,
        # so NiceGUI always has a valid client slot/container context.
        ui.timer(0.1, self._flush_event_views)
        refresh_hz = self.gui_config.get("refresh_rate", self.gui_config.get("ros_monitor", {}).get("refresh_hz", 5))
        ui.timer(1.0 / max(1, refresh_hz), lambda: self._refresh_ui(renderers))

    def _apply_config_overrides(self, overrides: dict[str, Any]) -> None:
        """Apply explicit launch/CLI feature flags before runtime construction."""
        if "agent_enabled" in overrides and not overrides["agent_enabled"]:
            self.gui_config.setdefault("agent_bridge", {})["mode"] = "mock"
        if "perception_enabled" in overrides:
            self.gui_config.setdefault("runtime_backends", {})["perception_mode"] = (
                "ros" if overrides["perception_enabled"] else "mock"
            )
        if "grasp_enabled" in overrides:
            self.gui_config.setdefault("runtime_backends", {})["grasp_mode"] = (
                "graspgenx" if overrides["grasp_enabled"] else "mock"
            )
        if "simulation" in overrides:
            simulation = bool(overrides["simulation"])
            self.gui_config["robot_mode"] = "SIMULATION" if simulation else "REAL ROBOT"
            if simulation:
                self.gui_config["enable_real_execution"] = False
        if "real_execution_enabled" in overrides:
            self.gui_config["enable_real_execution"] = bool(overrides["real_execution_enabled"])
        if "gui_mode" in overrides:
            self.gui_config["gui_mode"] = str(overrides["gui_mode"]).upper()

    def _refresh_ui(self, renderers) -> None:
        self.refresh_rviz_status()
        self.refresh_component_processes()
        self.consume_ros_status()
        for renderer in renderers:
            # Most existing panels provide ``.refresh``; the scene panel
            # returns its refresh callback directly.
            refresh = getattr(renderer, "refresh", renderer)
            refresh()

    def start_task(self, task_name: str) -> str | None:
        task_name = task_name.strip()
        if not task_name:
            return None
        if ExistingAgentBridge.is_capability_question(task_name):
            self.add_chat_message(task_name, sent=True, name="Operator")
            self.add_chat_message(
                ExistingAgentBridge.capabilities_message(
                    real_execution_enabled=(
                        self.state.robot_mode in {"REAL", "REAL ROBOT"}
                        and self.gui_config.get("enable_real_execution", False)
                    )
                ),
                sent=False,
                name=self.agent_name,
            )
            return "capabilities-query"
        if ExistingAgentBridge.is_named_target_question(task_name):
            self.add_chat_message(task_name, sent=True, name="Operator")
            self.add_chat_message(
                ExistingAgentBridge.named_target_message(task_name),
                sent=False,
                name=self.agent_name,
            )
            return "named-target-query"
        if self.gui_config.get("agent_bridge", {}).get("mode", "mock") != "mock":
            return self._start_agent_task(task_name)
        if self.state.task_status not in {TaskStatus.IDLE, TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED}:
            return None
        self.reset_task(clear_conversation=False)
        self.state.event_log.clear()
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        self.state.current_task_id = task_id
        self.state.current_task_name = task_name
        self.state.experiment_metrics = TaskExperimentMetrics(task_started_at=utc_now())
        self.initialize_task_plan(task_id, task_name)
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
        self.state.experiment_metrics = TaskExperimentMetrics(task_started_at=utc_now())
        self.initialize_task_plan(task_id, task_name)
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
                    config.get("conversation", {}),
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
            dependencies=list(getattr(event, "dependencies", []) or []),
            plan_version=self.state.current_plan_version,
        )
        self.register_tool_node(node, description=getattr(event, "description", ""))
        if self.state.current_task_plan is not None:
            self.plan_event_converter.upsert_tool_event(self.state.current_task_plan, event)
            self.plan_event_converter.apply_status(
                self.state.current_task_plan, self.state.node_attempts,
                node.node_id, node.status.value, input_data=node.input_data,
                output_data=node.output_data, error_message=node.error_message,
            )
        self.append_event("agent_tool_event", node_id=node.node_id, new_value=node.status.value,
                          metadata={"tool_name": node.tool_name, "parent_id": node.parent_id,
                                    "input_json": node.input_data, "output_json": node.output_data,
                                    "requires_approval": node.requires_approval,
                                    "approval_stages": event.approval_stages})
        if node.requires_approval and event.approval_stages:
            self.create_agent_hitl_request(node, event.approval_stages)
        elif node.tool_name == "describe_scene":
            # The Agent marks describe_scene as a read-only observation. Start
            # it automatically without creating a HITL approval request.
            self._last_skill_task = asyncio.create_task(
                self.skill_runtime.run_scene_description(node)
            )

    def add_chat_message(self, text: str, *, sent: bool, name: str) -> None:
        self.state.conversation.append(ChatEntry(text=text, sent=sent, name=name))
        self.append_event("chat_message_added", metadata={"sender": name})

    def initialize_task_plan(self, task_id: str, title: str, description: str = ""):
        """Create the single structured plan owned by AppState."""
        self.state.current_task_plan = self.task_plan_adapter.create_empty(
            task_id=task_id, title=title, description=description,
            version=self.state.current_plan_version,
        )
        self.state.selected_task_node_id = None
        self.state.node_attempts.clear()
        return self.state.current_task_plan

    def register_tool_node(
        self, node: ToolNode, *, description: str = "", append_legacy: bool = True,
    ) -> ToolNode:
        """Keep legacy ToolNode and the read-only TaskPlan projection in sync."""
        if append_legacy and self._node(node.node_id) is None:
            self.state.tool_nodes.append(node)
        if self.state.current_task_plan is None and self.state.current_task_id:
            self.initialize_task_plan(
                self.state.current_task_id, self.state.current_task_name,
            )
        plan = self.state.current_task_plan
        if plan is not None:
            task_node = self.task_plan_adapter.from_tool_node(
                node, sequence_index=(
                    plan.node_ids.index(node.node_id)
                    if node.node_id in plan.node_ids else len(plan.node_ids)
                ),
            )
            task_node.description = description
            self.task_plan_adapter.upsert_node(plan, task_node)
            self.plan_event_converter.apply_status(
                plan, self.state.node_attempts, node.node_id, node.status.value,
                input_data=node.input_data, output_data=node.output_data,
                error_message=node.error_message,
                trajectory_id=node.output_data.get("trajectory_id"),
            )
        return node

    def set_plan_version(self, version: int) -> None:
        """Update both compatibility state and the structured plan version."""
        self.state.current_plan_version = int(version)
        if self.state.current_task_plan is not None:
            self.state.current_task_plan.version = int(version)
            self.state.current_task_plan.touch()

    def select_task_node(self, node_id: str | None):
        """Select a TaskPlan node through the controller mutation boundary."""
        plan = self.state.current_task_plan
        if node_id is None:
            self.state.selected_task_node_id = None
            return None
        if plan is None or node_id not in plan.nodes:
            return None
        self.state.selected_task_node_id = node_id
        return plan.nodes[node_id]

    def clear_conversation(self) -> None:
        self.state.conversation.clear()
        self.append_event("chat_cleared")

    def initialize_tool_tree(self) -> None:
        self.state.tool_nodes = []
        previous_id = None
        for name in FLOW:
            node = ToolNode(
                node_id=name, parent_id=None, tool_name=name,
                display_name=name.replace("_", " ").title(),
                requires_approval=name == "trajectory_review",
                editable=False, plan_version=self.state.current_plan_version,
                dependencies=[previous_id] if previous_id else [],
            )
            self.state.tool_nodes.append(node)
            previous_id = name
        for node in self.state.tool_nodes:
            self.register_tool_node(node, append_legacy=False)
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
        if status == ToolStatus.FAILED and old_status != ToolStatus.FAILED:
            self.state.experiment_metrics.tool_failure_count += 1
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
        if self.state.current_task_plan is not None:
            structured_input = {**node.input_data, **node.input_summary}
            structured_output = {**node.output_data, **node.output_summary}
            self.plan_event_converter.apply_status(
                self.state.current_task_plan, self.state.node_attempts,
                node_id, status.value, input_data=structured_input,
                output_data=structured_output, error_message=node.error_message,
                trajectory_id=node.output_data.get("trajectory_id"),
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
        self.state.experiment_metrics.human_wait_started_at = request.created_at
        self.append_event("hitl_requested", node_id="trajectory_review", new_value=request.request_id,
                          metadata={"request_id": request.request_id, "trajectory_id": request.trajectory_id})
        return request

    def create_target_review_request(self, node: ToolNode, objects: list[dict[str, Any]]) -> HitlRequest:
        request = HitlRequest(
            request_id=f"target-review-{uuid.uuid4().hex[:8]}",
            task_id=self.state.current_task_id or "", request_type="target_review",
            target_id=node.node_id, title="Confirm detected target",
            description="The perception result is ambiguous. Confirm the intended object.",
            options=[HitlDecision.APPROVE, HitlDecision.REJECT, HitlDecision.CANCEL],
            created_at=utc_now(), trajectory_id=None, grasp_candidate_id=None,
            plan_version=self.state.current_plan_version,
            object_id=str(objects[0].get("object_id")) if objects else None,
            candidate_objects=[dict(item) for item in objects],
        )
        self.register_tool_node(node, append_legacy=False)
        self._set_pending_hitl(request)
        return request

    def create_grasp_review_request(self, node: ToolNode, candidates: list[dict[str, Any]]) -> HitlRequest:
        candidate_id = None
        if candidates:
            candidate_id = candidates[0].get("grasp_candidate_id") or candidates[0].get("candidate_id") or candidates[0].get("id")
        request = HitlRequest(
            request_id=f"grasp-review-{uuid.uuid4().hex[:8]}",
            task_id=self.state.current_task_id or "", request_type="grasp_review",
            target_id=node.node_id, title="Confirm grasp candidate",
            description="Review the ranked candidate before MoveIt motion planning.",
            options=[HitlDecision.APPROVE, HitlDecision.REJECT, HitlDecision.REPLAN, HitlDecision.CANCEL],
            created_at=utc_now(), trajectory_id=None,
            grasp_candidate_id=str(candidate_id) if candidate_id is not None else None,
            plan_version=self.state.current_plan_version,
            object_id=self.state.current_target_id,
            grasp_candidates=[dict(item) for item in candidates],
        )
        self.register_tool_node(node, append_legacy=False)
        self._set_pending_hitl(request)
        return request

    def create_recovery_request(self, node: ToolNode, error_type: str, description: str,
                                actions: list[str]) -> HitlRequest:
        request = HitlRequest(
            request_id=f"recovery-{uuid.uuid4().hex[:8]}",
            task_id=self.state.current_task_id or "", request_type="error_recovery",
            target_id=node.node_id, title=error_type.replace("_", " ").title(),
            description=description, options=[HitlDecision.CANCEL], created_at=utc_now(),
            trajectory_id=self.state.current_trajectory_id,
            grasp_candidate_id=self.state.current_grasp_candidate_id,
            plan_version=self.state.current_plan_version,
            object_id=self.state.current_target_id, recovery_actions=list(actions),
            error_type=error_type,
        )
        self.register_tool_node(node, append_legacy=False)
        self._set_pending_hitl(request)
        self.append_event(error_type, node_id=node.node_id, metadata={"recovery_actions": actions})
        return request

    def _set_pending_hitl(self, request: HitlRequest) -> None:
        self.state.pending_hitl_request = request
        self.state.experiment_metrics.human_wait_started_at = request.created_at
        self.append_event("hitl_requested", node_id=request.target_id, new_value=request.request_id,
                          metadata={"request_id": request.request_id, "request_type": request.request_type})

    def resolve_special_hitl(self, request: HitlRequest, event_type: str, **metadata: Any) -> None:
        if self.state.pending_hitl_request is not request:
            return
        request.status = "RESOLVED"
        self._finish_human_wait()
        self.state.pending_hitl_request = None
        node = self._node(request.target_id)
        if node:
            node.status = ToolStatus.SUCCEEDED
            node.output_summary.update(metadata)
            self.register_tool_node(node, append_legacy=False)
        self.append_event(event_type, node_id=request.target_id,
                          metadata={"request_id": request.request_id, **metadata})

    def approve_grasp_candidate(self, request_id: str) -> bool:
        request = self.state.pending_hitl_request
        if not request or request.request_id != request_id or request.request_type != "grasp_review":
            return False
        candidate_id = request.grasp_candidate_id
        if not candidate_id or candidate_id != self.state.current_grasp_candidate_id:
            return False
        node = self._node(request.target_id)
        self.resolve_special_hitl(request, "grasp_candidate_approved", grasp_candidate_id=candidate_id)
        if node:
            self.skill_runtime.continue_after_grasp_review(node)
        return True

    def invalidate_lineage(self, reason: str) -> None:
        old_target = self.state.current_target_id
        old_grasp = self.state.current_grasp_candidate_id
        old_trajectory = self.state.current_trajectory_id
        invalidated_trajectories: list[str] = []
        if self.trajectory_adapter:
            for trajectory_id, record in self.trajectory_adapter.records.items():
                record_target = record.planning_request.get("target_id")
                record_grasp = record.planning_request.get("grasp_candidate_id")
                is_downstream = (
                    reason == "target_changed" and old_target is not None and record_target == old_target
                ) or (
                    reason != "target_changed" and old_grasp is not None and record_grasp == old_grasp
                )
                if is_downstream and not record.invalidated:
                    self._invalidated_trajectory_ids.add(trajectory_id)
                    record.invalidated = True
                    invalidated_trajectories.append(trajectory_id)
        for node in list(self.state.tool_nodes):
            node_target = node.input_data.get("target_id")
            node_grasp = node.input_data.get("grasp_candidate_id")
            is_downstream = (
                reason == "target_changed" and old_target is not None and node_target == old_target
            ) or (
                reason != "target_changed" and old_grasp is not None and node_grasp == old_grasp
            )
            if is_downstream and node.status not in {ToolStatus.INVALIDATED, ToolStatus.CANCELLED}:
                self.update_tool_status(node.node_id, ToolStatus.INVALIDATED,
                                        output_summary={"invalidation_reason": reason})
        self.state.current_grasp_candidate_id = None
        self.state.current_trajectory_id = None
        old_version = self.state.current_plan_version
        self.set_plan_version(old_version + 1)
        if reason == "target_changed":
            self.state.experiment_metrics.target_change_count += 1
        self.append_event("downstream_results_invalidated", metadata={
            "reason": reason, "old_target_id": old_target,
            "old_grasp_candidate_id": old_grasp,
            "invalidated_trajectory_ids": invalidated_trajectories,
        })
        self.append_event("plan_version_changed", old_value=old_version,
                          new_value=self.state.current_plan_version, metadata={"reason": reason})

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
            **self._simulation_motion_adapter_options(real_robot=real_robot),
        )
        return self.trajectory_adapter

    def simulation_motion_scales(self) -> tuple[float, float]:
        """Return bounded MoveIt scales for fake-hardware simulation only."""
        settings = self.gui_config.get("simulation_motion", {})
        velocity = float(settings.get("velocity_scale", 0.03))
        acceleration = float(settings.get("acceleration_scale", 0.03))
        if not 0.0 < velocity <= 0.10 or not 0.0 < acceleration <= 0.10:
            raise ValueError("simulation_motion scales must be greater than 0 and at most 0.10.")
        return velocity, acceleration

    def _simulation_motion_adapter_options(self, *, real_robot: bool) -> dict[str, float]:
        if real_robot:
            return {}
        velocity, acceleration = self.simulation_motion_scales()
        return {
            "named_velocity_scale": velocity,
            "named_acceleration_scale": acceleration,
        }

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
            if source_node_id:
                self.update_tool_status(source_node_id, ToolStatus.FAILED, error_message=str(exc))
            self.append_event("tool_failed", node_id=source_node_id, metadata={"reason": str(exc), "target": target})
            self.add_chat_message(f"MoveIt planning failed: {exc}", sent=False, name="System")
            if self.state.current_task_id:
                self.skill_runtime._request_recovery(self.state.current_task_id, "planning_failed", str(exc), ["Retry", "Replan", "Cancel"])
            return
        self._apply_real_motion_safety(record)
        if not bool(record.summary.get("success")) or record.validation_result.get("decision") != "ALLOW":
            reason = _planning_failure_reason(
                record, "MoveIt returned no executable trajectory or validation blocked it."
            )
            self.state.task_status = TaskStatus.FAILED
            if source_node_id:
                self.update_tool_status(source_node_id, ToolStatus.FAILED, error_message=reason,
                                        output_summary={"planning_result": record.summary,
                                                        "validation": record.validation_result})
            self.append_event("planning_failed", node_id=source_node_id,
                              metadata={"reason": reason, "target": target,
                                        "planning_result": record.summary,
                                        "validation": record.validation_result})
            if self.state.current_task_id:
                self.skill_runtime._request_recovery(self.state.current_task_id, "planning_failed", reason, ["Retry", "Replan", "Cancel"])
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
            if self.state.current_task_id:
                self.skill_runtime._request_recovery(self.state.current_task_id, "planning_failed", str(exc), ["Retry", "Select Another Grasp", "Replan", "Cancel"])
            return
        self._apply_real_motion_safety(record)
        if not bool(record.summary.get("success")) or record.validation_result.get("decision") != "ALLOW":
            reason = _planning_failure_reason(
                record,
                "MoveIt returned no executable pose trajectory or validation blocked it.",
            )
            self.state.task_status = TaskStatus.FAILED
            if source_node_id:
                self.update_tool_status(source_node_id, ToolStatus.FAILED, error_message=reason,
                                        output_summary={"planning_result": record.summary,
                                                        "validation": record.validation_result})
            self.append_event("planning_failed", node_id=source_node_id,
                              metadata={"reason": reason, "skill_id": skill_id,
                                        "planning_result": record.summary,
                                        "validation": record.validation_result})
            if self.state.current_task_id:
                self.skill_runtime._request_recovery(self.state.current_task_id, "planning_failed", reason, ["Retry", "Select Another Grasp", "Replan", "Cancel"])
            return
        self._create_trajectory_review_request(record, source_node_id)
        preview = adapter.preview(record.trajectory_id)
        self.append_event("trajectory_preview_published", node_id=source_node_id,
                          metadata={"trajectory_id": record.trajectory_id, "preview": preview})

    def _apply_real_motion_safety(self, record) -> None:
        """Reuse restricted-real-arm checks before exposing a real trajectory."""
        if self.state.robot_mode not in {"REAL", "REAL ROBOT"}:
            return
        from llm_skill_robot.safety.real_arm_safety import (
            is_real_motion_skill_allowed, load_real_arm_safety,
            validate_motion_summary, validate_named_target,
        )

        config = load_real_arm_safety()
        request = record.planning_request
        skill_id = str(request.get("skill_id") or "move_to_named_target")
        checks = [is_real_motion_skill_allowed(skill_id, config)]
        if request.get("kind") == "named_target":
            checks.append(validate_named_target(str(request.get("target", "")), config))
        checks.append(validate_motion_summary(record.summary, config))
        blocked_reasons = [
            str(check.get("reason", "Real-arm safety blocked motion."))
            for check in checks if not check.get("allowed", False)
        ]
        if record.validation_result.get("decision") != "ALLOW":
            blocked_reasons.extend(record.validation_result.get("reasons", [
                "Trajectory validation did not allow this motion.",
            ]))
        if blocked_reasons:
            record.validation_result = {
                "decision": "BLOCK",
                "reasons": blocked_reasons,
                "warnings": [],
            }
            return
        record.validation_result = {
            **record.validation_result,
            "decision": "ALLOW",
            "real_arm_safety": "ALLOW",
        }

    def _create_trajectory_review_request(self, record, source_node_id: str | None) -> HitlRequest:
        summary = record.summary
        trajectory_id = record.trajectory_id
        record.planning_request["task_id"] = self.state.current_task_id
        record.planning_request["target_id"] = self.state.current_target_id
        record.planning_request["grasp_candidate_id"] = self.state.current_grasp_candidate_id
        self.state.current_trajectory_id = trajectory_id
        # A Safe Pick contains several motion reviews under the same plan
        # version.  The trajectory ID prevents later approvals from resolving
        # the first review node by mistake.
        review_node_id = f"trajectory_review_{trajectory_id}"
        review_node = ToolNode(
            node_id=review_node_id, parent_id=source_node_id, tool_name="trajectory_review",
            display_name=f"Trajectory Review (Attempt {record.plan_version})",
            status=ToolStatus.WAITING_APPROVAL, requires_approval=True,
            dependencies=[source_node_id] if source_node_id else [],
            plan_version=record.plan_version,
            input_data={"task_id": self.state.current_task_id,
                        "target_id": self.state.current_target_id,
                        "grasp_candidate_id": self.state.current_grasp_candidate_id},
            output_data={"trajectory_id": trajectory_id, "summary": summary,
                         "validation": record.validation_result},
            output_summary={"trajectory_id": trajectory_id,
                            "trajectory_points": summary.get("num_trajectory_points", 0)},
        )
        self.register_tool_node(review_node)
        request = HitlRequest(
            request_id=f"trajectory-review-{uuid.uuid4().hex[:8]}",
            task_id=self.state.current_task_id or "", request_type="trajectory_review",
            target_id=review_node_id, title="Trajectory approval required",
            description="Review the MoveIt trajectory in RViz before it is executed.",
            options=[HitlDecision.APPROVE, HitlDecision.REJECT, HitlDecision.REPLAN, HitlDecision.CANCEL],
            created_at=utc_now(), trajectory_id=trajectory_id,
            plan_version=record.plan_version, planning_success=bool(summary.get("success")),
            trajectory_points=int(summary.get("num_trajectory_points", 0)),
            trajectory_duration=summary.get("duration_sec"), planning_time=record.planning_time_ms,
            collision_check=record.validation_result.get("decision", "UNKNOWN"),
            target_summary=record.target_summary,
            object_id=self.state.current_target_id,
            grasp_candidate_id=self.state.current_grasp_candidate_id,
        )
        self.state.pending_hitl_request = request
        self.state.experiment_metrics.human_wait_started_at = request.created_at
        if self.state.current_task_plan is not None:
            self.plan_event_converter.bind_review_ids(
                self.state.current_task_plan, self.state.node_attempts,
                review_node_id, trajectory_id=trajectory_id,
                request_id=request.request_id,
            )
            if source_node_id:
                self.plan_event_converter.bind_review_ids(
                    self.state.current_task_plan, self.state.node_attempts,
                    source_node_id, trajectory_id=trajectory_id,
                    request_id=request.request_id,
                )
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
        self.register_tool_node(node, append_legacy=False)
        self.state.pending_hitl_request = request
        self.state.experiment_metrics.human_wait_started_at = request.created_at
        if self.state.current_task_plan is not None:
            self.plan_event_converter.bind_review_ids(
                self.state.current_task_plan, self.state.node_attempts,
                node.node_id, trajectory_id=request.trajectory_id,
                request_id=request.request_id,
            )
        self.state.task_status = TaskStatus.WAITING_APPROVAL
        self.append_event(
            "hitl_requested", node_id=node.node_id, new_value=request.request_id,
            metadata={"request_id": request.request_id, "request_type": stage,
                      "approval_stages": approval_stages, "tool_name": node.tool_name},
        )
        return request

    def submit_hitl_decision(
        self, request_id: str, decision: HitlDecision, *,
        real_confirmed: bool = False, confirmation_phrase: str | None = None,
    ) -> bool:
        self.last_decision_error = None
        request = self.state.pending_hitl_request
        if request is None or request.status != "PENDING":
            self.last_decision_error = "No current pending HITL request is available."
            return False
        if request.request_id != request_id or request.task_id != self.state.current_task_id:
            self.last_decision_error = "The approval does not belong to the current task/request."
            return False
        node = self._node(request.target_id)
        real_robot = self.state.robot_mode in {"REAL", "REAL ROBOT"}
        direct_gripper_request = (
            node is not None
            and node.tool_name in {"open_gripper", "close_gripper"}
            and request.request_type == "task_intent"
        )
        gripper_execution_request = (
            node is not None
            and node.tool_name in {"open_gripper", "close_gripper"}
            and request.request_type == "execution"
        )
        real_command_gate = request.request_type in {"trajectory_review", "execution"} or direct_gripper_request
        if decision == HitlDecision.APPROVE and real_robot and real_command_gate:
            if not self.gui_config.get("enable_real_execution", False):
                self.last_decision_error = (
                    "Real execution is disabled for this GUI session. Start with "
                    "real_execution_enabled:=true only after the work area is safe."
                )
                self.append_event(
                    "real_execution_blocked", node_id=request.target_id,
                    metadata={"request_id": request.request_id, "reason": "enable_real_execution=false"},
                )
                return False
            approve_is_confirmation = (
                direct_gripper_request
                or gripper_execution_request
                or (
                    request.request_type == "trajectory_review"
                    and self.gui_config.get("real_execution", {}).get(
                        "arm_approve_is_confirmation", False
                    )
                )
            )
            expected_phrase = self.real_confirmation_phrase(request.request_type)
            if not approve_is_confirmation and confirmation_phrase != expected_phrase:
                self.last_decision_error = f"Exact {expected_phrase} confirmation is required."
                self.append_event(
                    "real_execution_confirmation_rejected", node_id=request.target_id,
                    metadata={"request_id": request.request_id, "request_type": request.request_type},
                )
                return False
            if request.request_type == "trajectory_review":
                ready, reason = self.real_motion_preflight()
                if not ready:
                    self.last_decision_error = reason
                    self.add_chat_message(reason, sent=False, name="System")
                    self.append_event(
                        "real_execution_preflight_failed", node_id=request.target_id,
                        metadata={"request_id": request.request_id, "reason": reason},
                    )
                    return False
            elif request.request_type == "execution":
                self._real_gripper_confirmed_nodes.add(request.target_id)
            elif direct_gripper_request:
                self._real_gripper_confirmed_nodes.add(request.target_id)
            real_confirmed = True
        if request.request_type != "trajectory_review":
            if request.request_type == "grasp_review":
                if decision == HitlDecision.APPROVE:
                    return self.approve_grasp_candidate(request_id)
                if decision in {HitlDecision.REJECT, HitlDecision.CANCEL}:
                    self.resolve_special_hitl(request, "hitl_rejected", user_decision=decision.value)
                    self.cancel_task()
                    return True
            return self._submit_agent_hitl_decision(request, decision)
        if request.plan_version != self.state.current_plan_version:
            self.last_decision_error = "The request plan version is no longer current."
            return False
        if request.trajectory_id != self.state.current_trajectory_id:
            self.last_decision_error = "The request trajectory is no longer the current trajectory."
            return False
        if request.trajectory_id in self._invalidated_trajectory_ids:
            self.last_decision_error = "This trajectory was invalidated and cannot be executed."
            return False
        if (
            decision == HitlDecision.APPROVE
            and real_robot
            and not real_confirmed
        ):
            return False
        if self.trajectory_adapter is not None and request.trajectory_id in self.trajectory_adapter.records:
            return self._submit_existing_trajectory_decision(request, decision)
        request.status = decision.value
        self._finish_human_wait()
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

    def real_confirmation_phrase(self, request_type: str) -> str:
        settings = self.gui_config.get("real_execution", {})
        key = "gripper_confirmation_phrase" if request_type == "execution" else "arm_confirmation_phrase"
        fallback = "YES" if request_type == "execution" else "EXECUTE"
        return str(settings.get(key, fallback))

    def real_motion_preflight(self) -> tuple[bool, str]:
        """Fail closed unless the live ROS graph reports a usable arm and MoveIt."""
        settings = self.gui_config.get("real_execution", {})
        if not settings.get("require_ros_health_ready", True):
            return True, "Real motion health preflight is disabled by configuration."
        if self.state.ros_status != SystemComponentStatus.RUNNING:
            return False, f"Real execution blocked: ROS status is {self.state.ros_status.value}."
        ur5 = self.state.hardware_status.get("UR5", SystemComponentStatus.DISCONNECTED)
        moveit = self.state.hardware_status.get("MoveIt", SystemComponentStatus.DISCONNECTED)
        if ur5 not in {SystemComponentStatus.READY, SystemComponentStatus.RUNNING}:
            return False, f"Real execution blocked: UR5 ROS Health is {ur5.value}."
        if moveit not in {SystemComponentStatus.READY, SystemComponentStatus.RUNNING}:
            return False, f"Real execution blocked: MoveIt ROS Health is {moveit.value}."
        return True, "Real motion preflight passed."

    def consume_real_gripper_confirmation(self, node_id: str) -> bool:
        """Consume a one-shot GUI confirmation; approvals cannot be replayed."""
        if node_id not in self._real_gripper_confirmed_nodes:
            return False
        self._real_gripper_confirmed_nodes.remove(node_id)
        return True

    def _submit_existing_trajectory_decision(self, request: HitlRequest, decision: HitlDecision) -> bool:
        """Resolve a GUI review against the exact cached MoveIt plan only."""
        if decision == HitlDecision.REPLAN:
            return self.replan_trajectory(request.request_id, "other")
        request.status = decision.value
        self._finish_human_wait()
        self.state.pending_hitl_request = None
        node = self._node(request.target_id)
        if decision in {HitlDecision.REJECT, HitlDecision.CANCEL}:
            if node:
                node.status = ToolStatus.REJECTED if decision == HitlDecision.REJECT else ToolStatus.CANCELLED
                self.register_tool_node(node, append_legacy=False)
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
            self.register_tool_node(node, append_legacy=False)
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
        self.state.task_status = TaskStatus.EXECUTING if success else TaskStatus.FAILED
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
            self.skill_runtime.on_motion_execution_completed(
                source_node_id, review_node_id=request.target_id,
            )
        elif not success and self.state.current_task_id:
            if source_node_id:
                self.update_tool_status(source_node_id, ToolStatus.FAILED,
                                        error_message=str(result.get("message", "Trajectory execution failed")),
                                        output_summary={"trajectory_id": trajectory_id, "controller_result": result})
            self.skill_runtime._request_recovery(
                self.state.current_task_id, "execution_failed",
                str(result.get("message", "Trajectory execution failed")),
                ["Retry", "Replan", "Cancel"],
            )

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
            self.register_tool_node(review, append_legacy=False)
        source_node = self._node(review.parent_id) if review and review.parent_id else None
        if source_node:
            self.update_tool_status(
                source_node.node_id, ToolStatus.INVALIDATED,
                output_summary={"trajectory_id": trajectory_id, "replan_reason": reason},
            )
        old_version = self.state.current_plan_version
        self.set_plan_version(self.state.current_plan_version + 1)
        self.state.experiment_metrics.replan_count += 1
        self.append_event("trajectory_invalidated", node_id=request.target_id,
                          metadata={"trajectory_id": trajectory_id, "request_id": request.request_id,
                                    "reason": reason})
        self.append_event("plan_version_changed", old_value=old_version, new_value=self.state.current_plan_version,
                          metadata={"reason": reason})
        target = record.summary.get("target_name")
        source_node_id = review.parent_id if review else None
        if source_node:
            source_node.plan_version = self.state.current_plan_version
            self.register_tool_node(source_node, append_legacy=False)
            self.update_tool_status(
                source_node.node_id, ToolStatus.RUNNING,
                input_summary={"replan_reason": reason},
            )
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
        self._finish_human_wait()
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
            elif node and node.tool_name == "describe_scene" and request.request_type == "task_intent":
                self._last_skill_task = asyncio.create_task(
                    self.skill_runtime.run_scene_description(node)
                )
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
            elif node and node.tool_name in {"open_gripper", "close_gripper"} and request.request_type == "task_intent":
                # A standalone gripper task has no planning stage. Its initial
                # approval is the final release and may be consumed only once.
                self._last_skill_task = asyncio.create_task(
                    self.skill_runtime.execute_gripper_after_release(node)
                )
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
        if node:
            self.register_tool_node(node, append_legacy=False)
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
        self._finish_human_wait()
        self._finalize_metrics()
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
        self.set_plan_version(self.state.current_plan_version + 1)
        self.append_event("plan_version_changed", old_value=old_version,
                          new_value=self.state.current_plan_version)
        attempt = self.state.current_plan_version
        plan_id = f"plan_motion_attempt_{attempt}"
        review_id = f"trajectory_review_attempt_{attempt}"
        self.register_tool_node(
            ToolNode(plan_id, "plan_motion", "plan_motion", f"Plan Motion (Attempt {attempt})", plan_version=attempt)
        )
        self.register_tool_node(
            ToolNode(review_id, "trajectory_review", "trajectory_review", f"Trajectory Review (Attempt {attempt})", requires_approval=True, plan_version=attempt)
        )
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
        self._finish_human_wait()
        self._finalize_metrics()
        self.state.task_status = TaskStatus.COMPLETED
        self.state.agent_status = SystemComponentStatus.IDLE
        self.add_chat_message("The complete simulated pick-and-place task finished successfully.", sent=False, name=self.agent_name)
        self.append_event("task_completed")

    def fail_task(self, message: str, *, node_id: str | None = None) -> None:
        """Finalize an unrecoverable failure and persist its experiment summary."""
        self._finish_human_wait()
        self._finalize_metrics()
        self.state.pending_hitl_request = None
        self.state.task_status = TaskStatus.FAILED
        self.state.agent_status = SystemComponentStatus.IDLE
        self.add_chat_message(message, sent=False, name="System")
        self.append_event("task_failed", node_id=node_id, metadata={"reason": message})

    def complete_active_trajectory_review(self) -> None:
        review = self._active_trajectory_review()
        if review:
            self.update_tool_status(review.node_id, ToolStatus.SUCCEEDED)

    def set_task_status(self, status: TaskStatus) -> None:
        old_status = self.state.task_status
        self.state.task_status = status
        self.append_event("task_status_changed", old_value=old_status.value, new_value=status.value)

    def append_event(self, event_type: str, *, node_id: str | None = None, old_value: Any = None, new_value: Any = None, metadata: dict[str, Any] | None = None) -> ExecutionEvent:
        metadata = dict(metadata or {})
        metadata.setdefault("target_id", self.state.current_target_id)
        metadata.setdefault("grasp_candidate_id", self.state.current_grasp_candidate_id)
        metadata.setdefault("trajectory_id", self.state.current_trajectory_id)
        event = ExecutionEvent(f"event-{uuid.uuid4().hex[:8]}", self.state.current_task_id, node_id, event_type, utc_now(), self.state.current_plan_version, old_value, new_value, metadata)
        self.state.event_log.append(event)
        if self.state.current_task_plan is not None:
            self.state.current_task_plan.status = self.state.task_status.value
            self.state.current_task_plan.touch()
        if event_type in {"hitl_approved", "hitl_rejected", "hitl_replan_requested", "plan_version_changed"}:
            self.state.modification_history.append(event)
        if self._log_renderer is not None:
            self._log_view_dirty = True
        self._refresh_event_views()
        if event_type in {"task_completed", "task_cancelled", "task_failed"} and self.state.current_task_id:
            try:
                self.session_logger.export_task_summary(self.state)
            except OSError as exc:
                self._last_summary_error = str(exc)
        return event

    def _refresh_event_views(self) -> None:
        """Mark state-machine views dirty without touching UI from workers."""
        self._event_views_dirty = True

    def _flush_event_views(self) -> None:
        """Refresh dirty views from the NiceGUI page timer's slot context."""
        if self._log_view_dirty and self._log_renderer is not None:
            self._log_view_dirty = False
            self._log_renderer.refresh()
        if not self._event_views_dirty:
            return
        self._event_views_dirty = False
        for renderer in self._event_renderers:
            renderer()

    def reset_task(self, *, clear_conversation: bool = True) -> None:
        self.runner.cancel()
        self.state.current_task_id = None
        self.state.current_task_name = "None"
        self.state.current_plan_version = 1
        self.state.current_task_plan = None
        self.state.selected_task_node_id = None
        self.state.node_attempts.clear()
        self.state.task_status = TaskStatus.IDLE
        self.state.agent_status = SystemComponentStatus.IDLE
        self.state.tool_nodes.clear()
        self.state.pending_hitl_request = None
        self.state.current_trajectory_id = None
        self.state.current_target_id = None
        self.state.current_grasp_candidate_id = None
        self.state.experiment_metrics = TaskExperimentMetrics()
        self.state.modification_history.clear()
        if clear_conversation:
            self.state.conversation.clear()
        self.append_event("task_reset")

    def export_task_log(self):
        return self.session_logger.export_task(self.state)

    def task_summary(self) -> dict[str, Any]:
        return self.session_logger.build_task_summary(self.state)

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
        standalone = self.rviz_manager.get_process_status()
        embedded = self.embedded_rviz_manager.get_status()
        # Embedded and native RViz are independent GUI-owned implementations
        # of the same operator-facing component. Report whichever is active;
        # a stopped manager must never hide a running one.
        candidates = [
            ("embedded", embedded),
            ("native", standalone),
        ]
        priority = {"RUNNING": 3, "STARTING": 2, "ERROR": 1, "STOPPED": 0}
        source, selected = max(
            candidates,
            key=lambda item: priority.get(str(item[1].get("status", "STOPPED")), 0),
        )
        result = {
            **selected,
            "source": source,
            "embedded": embedded,
            "native": standalone,
        }
        self.state.rviz_process_status = result["status"]
        self.state.rviz_running = result["running"]
        if result["running"]:
            rviz_health = SystemComponentStatus.RUNNING
        elif result["status"] == "ERROR":
            rviz_health = SystemComponentStatus.ERROR
        else:
            rviz_health = SystemComponentStatus.DISCONNECTED
        self.state.hardware_status["RViz2"] = rviz_health
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
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.embedded_rviz_manager.cleanup()
        self.stop_rviz()
        self.skill_runtime.shutdown()
        if self.ros_worker:
            self.ros_worker.shutdown()
        self.component_manager.shutdown()

    def _component_conflict_reason(self, component_id: str, component: dict) -> str | None:
        """Refuse duplicate ROS stacks without taking ownership of external nodes."""
        expected = {str(name) for name in component.get("conflict_node_names", [])}
        if not expected or self.ros_worker is None:
            return None
        snapshot = self.ros_worker.snapshot()
        existing = [name for name in snapshot.get("node_fqns", []) if name in expected]
        if not existing:
            return None
        counts = {name: existing.count(name) for name in sorted(set(existing))}
        summary = ", ".join(f"{name} (count={count})" for name, count in counts.items())
        return (
            f"Refusing to start {component_id}: existing ROS node(s) detected: {summary}. "
            "The GUI will not terminate externally started or orphaned processes; "
            "stop them explicitly, wait for the ROS graph to clear, then retry."
        )

    def refresh_component_processes(self) -> None:
        self.state.component_processes = self.component_manager.refresh()

    def start_component(self, component_id: str, *, confirmed: bool = False):
        if component_id == "ur5_real" and not self.gui_config.get("enable_real_driver_start", False):
            raise RuntimeError("Real UR5 startup is disabled by enable_real_driver_start=false.")
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
        if component_id == "ur5_real" and not self.gui_config.get("enable_real_driver_start", False):
            raise RuntimeError("Real UR5 startup is disabled by enable_real_driver_start=false.")
        self.append_event("component_restart_requested", metadata={"component_id": component_id, "initiated_by": "gui"})
        managed = self.component_manager.restart_component(component_id, confirmed=confirmed)
        self.refresh_component_processes()
        return managed

    def confirm_real_ur5_start(self):
        if not self.gui_config.get("enable_real_driver_start", False):
            raise RuntimeError("Real UR5 startup is disabled by enable_real_driver_start=false.")
        details = self.real_ur5_launch_details()
        self.append_event("real_robot_driver_confirmed", metadata={
            "component_id": "ur5_real", "robot_ip": details["robot_ip"],
            "ros_domain_id": details["ros_domain_id"], "initiated_by": "gui",
        })
        return self.start_component("ur5_real", confirmed=True)

    def real_ur5_launch_details(self) -> dict[str, Any]:
        """Return operator-visible real-driver settings without hardcoding them in a panel."""
        launcher = self.gui_config.get("system_launcher", {})
        component = launcher.get("components", {}).get("ur5_real", {})
        arguments = component.get("arguments", [])
        values = {
            key: value for item in arguments if ":=" in str(item)
            for key, value in [str(item).split(":=", 1)]
        }
        return {
            "robot_ip": values.get("robot_ip", "UNKNOWN"),
            "ros_domain_id": launcher.get("ros_domain_id", "UNKNOWN"),
            "launch_rviz": values.get("launch_rviz", "UNKNOWN"),
        }

    def _finish_human_wait(self) -> None:
        started = self.state.experiment_metrics.human_wait_started_at
        if started:
            self.state.experiment_metrics.human_wait_time_ms += self._duration_ms(started, utc_now()) or 0
            self.state.experiment_metrics.human_wait_started_at = None

    def _finalize_metrics(self) -> None:
        metrics = self.state.experiment_metrics
        metrics.task_finished_at = utc_now()
        metrics.total_task_time_ms = self._duration_ms(metrics.task_started_at, metrics.task_finished_at)

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

        rviz_started = await asyncio.to_thread(self.embedded_rviz_manager.start)
        self.refresh_rviz_status()
        if not rviz_started:
            self.state.simulation_launch_status = "FAILED: Embedded RViz"
            self.append_event("component_start_failed", metadata={
                "component_id": "embedded_rviz",
                "reason": self.embedded_rviz_manager.get_error() or "startup failed",
            })
            return
        for component_id in ("camera", "gripper", "graspgenx"):
            managed = self.start_component(component_id)
            if managed.status.value != "RUNNING":
                self.state.simulation_launch_status = f"FAILED: {component_id}"
                return
        self.state.simulation_launch_status = "COMPLETED"

    def start_real_components(self, *, confirmed: bool = False):
        """Start the GUI-managed real-driver bundle after one explicit gate.

        This starts drivers and visualization only. It never approves a
        trajectory or sends a robot motion command.
        """
        if not confirmed:
            raise RuntimeError("Real system confirmation is required.")
        if not self.gui_config.get("enable_real_driver_start", False):
            raise RuntimeError("Real UR5 startup is disabled by enable_real_driver_start=false.")
        if self.state.simulation_launch_status == "RUNNING":
            return None
        details = self.real_ur5_launch_details()
        self.append_event("real_robot_driver_confirmed", metadata={
            "component_id": "ur5_real",
            "scope": "required_components",
            "robot_ip": details["robot_ip"],
            "ros_domain_id": details["ros_domain_id"],
            "initiated_by": "gui",
        })
        self.state.simulation_launch_status = "RUNNING"
        return asyncio.create_task(self._start_real_components())

    async def _start_real_components(self):
        ur5 = self.start_component("ur5_real", confirmed=True)
        if ur5.status.value != "RUNNING":
            self.state.simulation_launch_status = "FAILED: UR5 Real process"
            return
        timeout = self.gui_config["system_launcher"]["components"]["ur5_real"].get("timeout_sec", 20)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            self.consume_ros_status()
            if self.state.hardware_status["UR5"] == SystemComponentStatus.READY:
                break
            await asyncio.sleep(0.2)
        else:
            self.state.simulation_launch_status = "FAILED: UR5 ROS Health not READY"
            self.append_event("component_start_failed", metadata={
                "component_id": "ur5_real", "reason": "ROS health timeout",
            })
            return

        rviz_started = await asyncio.to_thread(self.embedded_rviz_manager.start)
        self.refresh_rviz_status()
        if not rviz_started:
            self.state.simulation_launch_status = "FAILED: Embedded RViz"
            self.append_event("component_start_failed", metadata={
                "component_id": "embedded_rviz",
                "reason": self.embedded_rviz_manager.get_error() or "startup failed",
            })
            return
        for component_id in ("camera", "gripper", "graspgenx"):
            managed = self.start_component(component_id)
            if managed.status.value != "RUNNING":
                self.state.simulation_launch_status = f"FAILED: {component_id}"
                return
        self.state.simulation_launch_status = "COMPLETED"

    def stop_gui_managed_components(self):
        return asyncio.create_task(self._stop_gui_managed_components())

    async def _stop_gui_managed_components(self) -> None:
        """Stop all process groups owned by this GUI without blocking its UI."""
        component_ids = list(self.component_manager.processes)
        for component_id in component_ids:
            self.append_event("component_stop_requested", metadata={
                "component_id": component_id, "initiated_by": "gui",
            })
        results = await asyncio.gather(*(
            asyncio.to_thread(self.component_manager.stop_component, component_id)
            for component_id in component_ids
        ))
        for component_id, managed in zip(component_ids, results):
            if managed is not None:
                self.append_event("component_stopped", metadata={
                    "component_id": component_id,
                    "pid": managed.pid,
                    "exit_code": managed.exit_code,
                    "initiated_by": "gui",
                })
        await asyncio.gather(
            asyncio.to_thread(self.embedded_rviz_manager.stop),
            asyncio.to_thread(self.rviz_manager.stop_rviz),
        )
        self.refresh_component_processes()
        self.refresh_rviz_status()
        self.state.simulation_launch_status = "IDLE"

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
        worker_error = snapshot.get("worker_error")
        if worker_error:
            self.state.ros_status = SystemComponentStatus.ERROR
            if worker_error != self._last_ros_worker_error:
                self.append_event(
                    "ros_executor_failed",
                    metadata={"error": worker_error},
                )
            self._last_ros_worker_error = worker_error
        elif snapshot["executor_running"] and snapshot["node_initialized"]:
            self.state.ros_status = SystemComponentStatus.RUNNING
            self._last_ros_worker_error = None
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


def _planning_failure_reason(record, fallback: str) -> str:
    """Expose MoveIt and validator diagnostics instead of a generic failure."""
    parts: list[str] = []
    summary = record.summary if isinstance(record.summary, dict) else {}
    if not bool(summary.get("success")):
        message = summary.get("message")
        if message:
            parts.append(str(message))
        diagnostics = (
            summary.get("trajectory_preview", {}).get("moveit_diagnostics", {})
            if isinstance(summary.get("trajectory_preview"), dict)
            else {}
        )
        error_name = diagnostics.get("error_code_name")
        error_value = diagnostics.get("error_code_value")
        if error_name and str(error_name) not in " ".join(parts):
            parts.append(f"MoveIt error: {error_name} ({error_value}).")
    validation = (
        record.validation_result
        if isinstance(record.validation_result, dict)
        else {}
    )
    if validation.get("decision") != "ALLOW":
        reasons = validation.get("reasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        parts.extend(str(reason) for reason in reasons if reason)
    return " ".join(dict.fromkeys(parts)) or fallback
