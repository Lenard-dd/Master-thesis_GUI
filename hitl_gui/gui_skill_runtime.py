"""Stepwise, HITL-gated execution tree for the existing Safe Pick composite.

Only this coordinator decides which *next* primitive becomes visible.  Every
arm motion is handed to the Stage 7 MoveIt/trajectory-review adapter, rather
than being planned or executed here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from hitl_gui.app_state import TaskStatus, ToolNode, ToolStatus


class GuiSkillRuntimeAdapter:
    """Expose every Safe Pick primitive while retaining all human gates."""

    def __init__(self, controller, adapters) -> None:
        self.controller = controller
        self.adapters = adapters
        self._cancelled_task_ids: set[str] = set()
        self._contexts: dict[str, dict[str, Any]] = {}
        self._parents: dict[str, str] = {}

    def cancel(self, task_id: str | None) -> None:
        if task_id:
            self._cancelled_task_ids.add(task_id)

    async def run_safe_pick_observation(self, parent: ToolNode) -> None:
        """Start at the required observation pose, then wait for its C gate."""
        task_id = self.controller.state.current_task_id
        if not task_id:
            return
        self._cancelled_task_ids.discard(task_id)
        self._parents[task_id] = parent.node_id
        self._contexts[task_id] = {"task_id": task_id}
        parent.status = ToolStatus.RUNNING
        self.controller.state.task_status = TaskStatus.PLANNING
        self.controller.append_event(
            "skill_runtime_started", node_id=parent.node_id,
            metadata={"tool_name": parent.tool_name, "backend": self.adapters.mode_summary},
        )
        self._request_named_motion(parent, "observe", "Move To Observe")

    def on_motion_execution_completed(self, motion_node_id: str) -> None:
        """Called by GuiController only after the exact reviewed plan executed."""
        task_id = self.controller.state.current_task_id
        if not task_id or task_id in self._cancelled_task_ids:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        node = self.controller._node(motion_node_id)
        if node is None:
            return
        if node.tool_name == "move_to_named_target":
            loop.create_task(self._run_sensor_stages(task_id))
        elif node.tool_name == "move_to_pregrasp":
            self._request_pose_motion(task_id, "approach_grasp", "grasp", "Approach Grasp")
        elif node.tool_name == "approach_grasp":
            self._request_gripper_gate(task_id, "close_gripper", "Close Gripper")
        elif node.tool_name == "retreat_grasp":
            loop.create_task(self._run_verify(task_id))

    async def _run_sensor_stages(self, task_id: str) -> None:
        parent = self._parent(task_id)
        if parent is None:
            return
        self.controller.state.task_status = TaskStatus.PERCEIVING
        query = _query_from_parent(parent, self.controller.state.current_task_name)
        context = self._contexts[task_id]
        for skill_id, parameters, display_name in (
            ("detect_object", {"query": query, "require_pose": True}, "Detect Object"),
            ("build_object_point_cloud", {"object_id": "<resolved>"}, "Build Object Point Cloud"),
            ("generate_grasp_pose", {"object_id": "<resolved>"}, "Generate Grasp Candidates"),
        ):
            if not await self._run_non_motion(task_id, parent, skill_id, display_name, parameters, context):
                return
        self._request_grasp_review(task_id, parent, context)

    def continue_after_grasp_review(self, review_node: ToolNode) -> None:
        task_id = self.controller.state.current_task_id
        if task_id and task_id not in self._cancelled_task_ids:
            try:
                self._prepare_grasp_motion_context(task_id)
            except Exception as exc:
                parent = self._parent(task_id)
                if parent:
                    parent.status = ToolStatus.FAILED
                self.controller.state.task_status = TaskStatus.FAILED
                self.controller.add_chat_message(
                    f"Could not prepare the reviewed grasp motion: {exc}",
                    sent=False, name="System",
                )
                return
            self._request_gripper_gate(task_id, "open_gripper", "Open Gripper")

    async def execute_gripper_after_release(self, node: ToolNode) -> None:
        task_id = self.controller.state.current_task_id
        if not task_id or task_id in self._cancelled_task_ids:
            return
        # Real contact commands remain deliberately outside this GUI adapter.
        # The existing real backend requires its own configured driver and
        # explicit confirmation path; no guessed driver command is sent here.
        if self.controller.state.robot_mode in {"REAL", "REAL ROBOT"}:
            self.controller.update_tool_status(
                node.node_id, ToolStatus.FAILED,
                error_message="Real gripper release is not connected to this GUI runtime yet.",
            )
            self.controller.state.task_status = TaskStatus.FAILED
            return
        from llm_skill_robot.robot.robotiq_2f140_sim_backend import Robotiq2F140SimBackend

        backend = Robotiq2F140SimBackend()
        method = getattr(backend, node.tool_name)
        result = method(**node.input_data)
        if not result.get("success", False):
            self.controller.update_tool_status(node.node_id, ToolStatus.FAILED,
                                               error_message=result.get("message", "Gripper failed."))
            self.controller.state.task_status = TaskStatus.FAILED
            return
        self.controller.update_tool_status(node.node_id, ToolStatus.SUCCEEDED,
                                           output_summary=result.get("output", {}))
        if node.tool_name == "open_gripper":
            self._request_pose_motion(task_id, "move_to_pregrasp", "pregrasp", "Move To Pregrasp")
        else:
            self._request_pose_motion(task_id, "retreat_grasp", "retreat", "Retreat Grasp")

    async def _run_verify(self, task_id: str) -> None:
        parent = self._parent(task_id)
        if parent is None:
            return
        context = self._contexts[task_id]
        node = self._add_node(
            parent, "verify_grasp", "Verify Grasp",
            {"object_id": context.get("resolved_object_id", "<resolved>")},
        )
        self.controller.update_tool_status(node.node_id, ToolStatus.RUNNING)
        if self.controller.state.robot_mode in {"REAL", "REAL ROBOT"}:
            self.controller.update_tool_status(
                node.node_id, ToolStatus.FAILED,
                error_message="Real grasp verification is not connected to this GUI runtime yet.",
            )
            parent.status = ToolStatus.FAILED
            self.controller.state.task_status = TaskStatus.FAILED
            return
        # In simulation the object/contact signal is not available, so retain
        # an explicit simulated verification receipt rather than claiming a
        # camera-derived real-world verification result.
        self.controller.update_tool_status(
            node.node_id, ToolStatus.SUCCEEDED,
            output_summary={"verification": "simulated", "object_id": context.get("resolved_object_id")},
        )
        success = True
        if success:
            parent.status = ToolStatus.SUCCEEDED
            self.controller.complete_task()

    async def _run_non_motion(self, task_id, parent, skill_id, display_name, parameters, context) -> bool:
        if task_id in self._cancelled_task_ids:
            return False
        node = self._add_node(parent, skill_id, display_name, parameters)
        self.controller.update_tool_status(node.node_id, ToolStatus.RUNNING)
        step = _new_plan_step(node.node_id, skill_id, display_name, parameters)
        is_mock = self.adapters.config.perception_mode == "mock" and (
            skill_id != "generate_grasp_pose" or self.adapters.config.grasp_mode == "mock"
        )
        result = self.adapters.execute(step, context) if is_mock else await asyncio.to_thread(self.adapters.execute, step, context)
        if task_id in self._cancelled_task_ids:
            return False
        output = result.get("output", {}) if isinstance(result, dict) else {}
        output = output if isinstance(output, dict) else {"raw_output": output}
        if not bool(result.get("success", False)):
            self.controller.update_tool_status(node.node_id, ToolStatus.FAILED, output_summary=output,
                                               error_message=str(result.get("message", "Tool failed.")))
            parent.status = ToolStatus.FAILED
            self.controller.state.task_status = TaskStatus.FAILED
            self.controller.add_chat_message(f"{display_name} could not continue: {result.get('message', 'unknown error')}", sent=False, name="System")
            return False
        self.controller.update_tool_status(node.node_id, ToolStatus.SUCCEEDED, output_summary=output)
        return True

    def _request_grasp_review(self, task_id, parent, context) -> None:
        review = self._add_node(parent, "review_grasp_candidate", "Review Grasp Candidate", {})
        review.status = ToolStatus.WAITING_APPROVAL
        review.requires_approval = True
        review.output_data = {"candidate": context.get("selected_grasp_candidate")}
        review.output_summary = dict(review.output_data)
        self.controller.state.task_status = TaskStatus.WAITING_APPROVAL
        self.controller.create_agent_hitl_request(review, ["grasp_candidate"])

    def _request_gripper_gate(self, task_id, skill_id, display_name) -> None:
        parent = self._parent(task_id)
        if parent is None:
            return
        parameters = {"during_contact": True} if skill_id == "close_gripper" else {}
        node = self._add_node(parent, skill_id, display_name, parameters)
        node.status = ToolStatus.WAITING_APPROVAL
        node.requires_approval = True
        self.controller.state.task_status = TaskStatus.WAITING_APPROVAL
        self.controller.create_agent_hitl_request(node, ["execution"])

    def _request_named_motion(self, parent, target, display_name) -> None:
        node = self._add_node(parent, "move_to_named_target", display_name, {"target_name": target})
        self.controller.update_tool_status(node.node_id, ToolStatus.RUNNING)
        self.controller.request_named_target_trajectory(target, source_node_id=node.node_id)

    def _request_pose_motion(self, task_id, skill_id, pose_key, display_name) -> None:
        parent = self._parent(task_id)
        context = self._contexts.get(task_id, {})
        poses = context.get("grasp_motion_poses")
        pose = poses.get(pose_key) if isinstance(poses, dict) else None
        if parent is None or not isinstance(pose, dict):
            if parent:
                parent.status = ToolStatus.FAILED
            self.controller.state.task_status = TaskStatus.FAILED
            self.controller.add_chat_message(f"{display_name} requires a reviewed base_link grasp pose.", sent=False, name="System")
            return
        node = self._add_node(parent, skill_id, display_name, {"object_id": context.get("resolved_object_id")})
        self.controller.update_tool_status(node.node_id, ToolStatus.RUNNING)
        policy = context.get("grasp_motion_policy")
        planning_kwargs = policy.kwargs_for(skill_id) if policy is not None else {}
        velocity_scale = policy.velocity_scale if policy is not None else 0.03
        acceleration_scale = policy.acceleration_scale if policy is not None else 0.03
        node.input_data.update({
            "planner": planning_kwargs.get("planner_id", "default"),
            "pipeline": planning_kwargs.get("pipeline_id", "default"),
        })
        self.controller.request_pose_trajectory(
            pose, skill_id=skill_id, source_node_id=node.node_id,
            velocity_scale=velocity_scale, acceleration_scale=acceleration_scale,
            planning_kwargs=planning_kwargs,
        )

    def _prepare_grasp_motion_context(self, task_id: str) -> None:
        """Reuse terminal grasp pose conversion and stage-specific MoveIt policy."""
        from llm_skill_robot.core.grasp_motion_policy import GraspMotionPolicy
        from llm_skill_robot.ros_nl_rviz_sim_demo import _load_grasping_config
        from llm_skill_robot.safety.real_arm_safety import load_real_arm_safety

        context = self._contexts[task_id]
        document = _load_grasping_config()
        grasping = document.get("grasping", document)
        safety = load_real_arm_safety()
        context["grasp_motion_policy"] = GraspMotionPolicy.from_grasping_config(
            grasping,
            max_velocity_scale=safety.limits.max_velocity_scale,
            max_acceleration_scale=safety.limits.max_acceleration_scale,
        )
        # MockPerceptionAdapter already gives deterministic base_link poses.
        if isinstance(context.get("grasp_motion_poses"), dict):
            return

        candidate = context.get("selected_grasp_candidate")
        point_cloud = context.get("point_cloud")
        if not isinstance(candidate, dict) or not isinstance(point_cloud, dict):
            raise ValueError("A selected candidate and point cloud are required.")
        from llm_skill_robot.perception.tf_pose_transformer import TFPoseTransformer
        from llm_skill_robot.ros_graspgenx_plan_only_demo import prepare_candidate_preview

        transformer = TFPoseTransformer(self.adapters.ros_node, target_frame="base_link")
        context["grasp_motion_poses"] = prepare_candidate_preview(
            candidate,
            point_cloud,
            grasping.get("plan_only_preview", {}),
            "base_link",
            transformer,
        )

    def _add_node(self, parent: ToolNode, skill_id: str, display_name: str, parameters: dict[str, Any]) -> ToolNode:
        node = ToolNode(
            node_id=f"{parent.node_id}:{skill_id}:{len(self.controller.state.tool_nodes)}",
            parent_id=parent.node_id, tool_name=skill_id, display_name=display_name,
            plan_version=self.controller.state.current_plan_version,
            input_data=dict(parameters), input_summary=dict(parameters),
        )
        self.controller.state.tool_nodes.append(node)
        return node

    def _parent(self, task_id: str) -> ToolNode | None:
        parent_id = self._parents.get(task_id)
        return self.controller._node(parent_id) if parent_id else None


def _new_plan_step(step_id: str, skill_id: str, description: str, parameters: dict[str, Any]):
    from llm_skill_robot.core.plan import PlanStep
    return PlanStep(step_id=step_id, skill_id=skill_id, description=description, parameters=parameters)


def _query_from_parent(parent: ToolNode, fallback: str) -> str:
    for key in ("object_query", "query", "object", "target", "target_name"):
        value = parent.input_data.get(key)
        if value:
            return str(value)
    return fallback
