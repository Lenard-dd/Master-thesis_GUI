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
        self._last_node_ids: dict[str, str] = {}
        from llm_skill_robot.robot.robotiq_2f140_sim_backend import Robotiq2F140SimBackend
        self._sim_gripper = Robotiq2F140SimBackend()

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
        self._last_node_ids[task_id] = parent.node_id
        self._contexts[task_id] = {"task_id": task_id, "query": _query_from_parent(parent, self.controller.state.current_task_name)}
        parent.status = ToolStatus.RUNNING
        self.controller.register_tool_node(parent, append_legacy=False)
        self.controller.state.task_status = TaskStatus.PLANNING
        self.controller.append_event(
            "skill_runtime_started", node_id=parent.node_id,
            metadata={"tool_name": parent.tool_name, "backend": self.adapters.mode_summary},
        )
        self._request_named_motion(parent, "observe", "Move To Observe")

    def on_motion_execution_completed(
        self, motion_node_id: str, review_node_id: str | None = None,
    ) -> None:
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
        # The approved review, rather than merely the motion proposal, is the
        # execution predecessor of the next primitive.
        self._last_node_ids[task_id] = review_node_id or motion_node_id
        if node.tool_name == "move_to_named_target":
            if node.input_data.get("purpose") == "place":
                self._request_gripper_gate(task_id, "open_gripper", "Release Object", purpose="release")
            else:
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
        query = self._contexts[task_id].get("query") or _query_from_parent(parent, self.controller.state.current_task_name)
        context = self._contexts[task_id]
        result = await self._run_non_motion(
            task_id, parent, "detect_object", "Detect Object",
            {"query": query, "require_pose": True}, context,
        )
        if not result:
            self._request_recovery(task_id, "perception_failed", "Perception failed", ["Retry", "Cancel"])
            return
        objects = _normalise_objects(context, result)
        if not objects:
            self._mark_latest_failed("detect_object", "No target matched the task.")
            self._request_recovery(task_id, "no_target_found", "No target found", ["Retry", "Cancel"])
            return
        context["candidate_objects"] = objects
        if self._target_review_required(objects):
            self._request_target_review(task_id, parent, context)
            return
        await self._continue_after_target(task_id, objects[0].get("object_id"))

    async def _continue_after_target(self, task_id: str, object_id: str | None) -> None:
        parent = self._parent(task_id)
        context = self._contexts.get(task_id, {})
        if parent is None or not object_id:
            self._request_recovery(task_id, "no_target_found", "No target was selected", ["Select Another Target", "Cancel"])
            return
        selected = next((item for item in context.get("candidate_objects", []) if item.get("object_id") == object_id), None)
        context["selected_object"] = selected or {"object_id": object_id}
        context["resolved_object_id"] = object_id
        self.controller.state.current_target_id = object_id
        self.controller.state.task_status = TaskStatus.GENERATING_GRASPS
        cloud = await self._run_non_motion(
            task_id, parent, "build_object_point_cloud", "Build Object Point Cloud",
            {"object_id": object_id}, context,
        )
        if not cloud:
            self._request_recovery(task_id, "perception_failed", "Object point cloud could not be built", ["Retry", "Select Another Target", "Cancel"])
            return
        generated = await self._run_non_motion(
            task_id, parent, "generate_grasp_pose", "Generate Grasp Candidates",
            {"object_id": object_id}, context,
        )
        if not generated:
            self._request_recovery(task_id, "grasp_generation_failed", "Grasp generation failed", ["Retry", "Select Another Target", "Cancel"])
            return
        try:
            candidates = await self._screen_grasps(_normalise_grasps(context, generated), context)
        except Exception as exc:
            self.controller.add_chat_message(f"Grasp candidate screening failed: {exc}", sent=False, name="System")
            self._mark_latest_failed("generate_grasp_pose", str(exc))
            self._request_recovery(task_id, "no_valid_grasp", str(exc), ["Regenerate", "Select Another Target", "Cancel"])
            return
        valid = [item for item in candidates if item.get("valid", True) and str(item.get("ik_result", "PASSED")).upper() not in {"FAILED", "NO_SOLUTION"} and str(item.get("collision_result", "ALLOW")).upper() not in {"REJECT", "COLLISION"}]
        context["grasp_candidates"] = valid
        if not valid:
            self._mark_latest_failed("generate_grasp_pose", "No candidate passed MoveIt screening.")
            self._request_recovery(task_id, "no_valid_grasp", "No valid grasp candidate passed screening", ["Regenerate", "Select Another Target", "Cancel"])
            return
        context["selected_grasp_candidate"] = valid[0]
        self.controller.state.current_grasp_candidate_id = _candidate_id(valid[0])
        self._request_grasp_review(task_id, parent, context)

    async def _screen_grasps(self, candidates: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
        """Reuse the terminal's MoveIt plan-only candidate screening in live mode."""
        if self.adapters.config.grasp_mode != "graspgenx":
            return candidates
        from llm_skill_robot.perception.tf_pose_transformer import TFPoseTransformer
        from llm_skill_robot.ros_graspgenx_plan_only_demo import (
            _load_grasping_config, screen_grasp_candidates_for_plan_only,
        )

        adapter = self.controller._ensure_trajectory_adapter()
        document = _load_grasping_config()
        grasping = document.get("grasping", document)
        preview = grasping.get("plan_only_preview", {})
        transformer = TFPoseTransformer(self.adapters.ros_node, target_frame="base_link")
        screening = await asyncio.to_thread(
            screen_grasp_candidates_for_plan_only,
            candidates, context.get("point_cloud", {}), preview, "base_link", transformer,
            adapter.backend, adapter.validator,
            float(preview.get("velocity_scale", 0.05)),
            float(preview.get("acceleration_scale", 0.05)),
        )
        accepted: dict[str, dict[str, Any]] = {}
        pose_map: dict[str, dict[str, Any]] = {}
        kwargs_map: dict[str, dict[str, Any]] = {}
        for attempt in screening.get("attempts", []):
            candidate = attempt.get("candidate")
            if not isinstance(candidate, dict):
                continue
            candidate = dict(candidate)
            candidate_id = _candidate_id(candidate)
            if not candidate_id:
                continue
            candidate.update({
                "ik_result": "PASSED" if attempt.get("accepted") else "FAILED",
                "collision_result": attempt.get("validation", {}).get("decision", "UNKNOWN"),
                "orientation_change": attempt.get("variant"),
            })
            if attempt.get("accepted") and candidate_id not in accepted:
                accepted[candidate_id] = candidate
                pose_map[candidate_id] = attempt.get("poses", {})
                kwargs_map[candidate_id] = attempt.get("plan_kwargs", {})
        context["candidate_motion_poses"] = pose_map
        context["candidate_plan_kwargs"] = kwargs_map
        return sorted(accepted.values(), key=lambda item: float(item.get("score", 0.0)), reverse=True)

    def select_target(self, request_id: str, object_id: str) -> bool:
        request = self.controller.state.pending_hitl_request
        task_id = self.controller.state.current_task_id
        if not request or request.request_id != request_id or request.request_type != "target_review" or not task_id:
            return False
        if not any(item.get("object_id") == object_id for item in request.candidate_objects):
            return False
        previous_target = self.controller.state.current_target_id
        self.controller.resolve_special_hitl(request, "target_confirmed", object_id=object_id)
        if previous_target and previous_target != object_id:
            self._invalidate_downstream(task_id, "target_changed")
        self.controller.state.current_target_id = object_id
        asyncio.create_task(self._continue_after_target(task_id, object_id))
        return True

    def select_next_target(self, request_id: str) -> bool:
        request = self.controller.state.pending_hitl_request
        if not request or request.request_id != request_id or request.request_type != "target_review" or len(request.candidate_objects) < 2:
            return False
        ids = [str(item.get("object_id")) for item in request.candidate_objects]
        try:
            index = ids.index(str(request.object_id))
        except ValueError:
            index = 0
        request.object_id = ids[(index + 1) % len(ids)]
        self.controller.state.experiment_metrics.target_change_count += 1
        self.controller.append_event("target_candidate_changed", node_id=request.target_id,
                                     metadata={"request_id": request_id, "object_id": request.object_id})
        return True

    def select_next_grasp(self, request_id: str) -> bool:
        request = self.controller.state.pending_hitl_request
        task_id = self.controller.state.current_task_id
        if not request or request.request_id != request_id or request.request_type != "grasp_review" or not task_id:
            return False
        if len(request.grasp_candidates) < 2:
            return False
        request.selected_index = (request.selected_index + 1) % len(request.grasp_candidates)
        candidate = request.grasp_candidates[request.selected_index]
        request.grasp_candidate_id = _candidate_id(candidate)
        self._contexts[task_id]["selected_grasp_candidate"] = candidate
        self.controller.state.current_grasp_candidate_id = request.grasp_candidate_id
        self.controller.state.experiment_metrics.grasp_change_count += 1
        self.controller.append_event("grasp_candidate_changed", node_id=request.target_id,
                                     metadata={"request_id": request_id, "grasp_candidate_id": request.grasp_candidate_id})
        return True

    def regenerate_grasps(self, request_id: str) -> bool:
        request = self.controller.state.pending_hitl_request
        task_id = self.controller.state.current_task_id
        if not request or request.request_id != request_id or request.request_type != "grasp_review" or not task_id:
            return False
        self.controller.resolve_special_hitl(request, "grasp_regeneration_requested")
        self._invalidate_downstream(task_id, "grasp_regenerated")
        asyncio.create_task(self._continue_after_target(task_id, self.controller.state.current_target_id))
        return True

    def handle_recovery(self, request_id: str, action: str) -> bool:
        request = self.controller.state.pending_hitl_request
        task_id = self.controller.state.current_task_id
        if not request or request.request_id != request_id or request.request_type != "error_recovery" or not task_id:
            return False
        if action == "Cancel":
            self.controller.cancel_task()
            return True
        self.controller.resolve_special_hitl(request, "recovery_action_selected", action=action,
                                             error_type=request.error_type)
        context = self._contexts.get(task_id, {})
        parent = self._parent(task_id)
        if action == "Select Another Target" and parent and context.get("candidate_objects"):
            self._request_target_review(task_id, parent, context)
        elif action == "Select Another Grasp" and parent and context.get("grasp_candidates"):
            self._request_grasp_review(task_id, parent, context)
        elif action == "Regenerate":
            asyncio.create_task(self._continue_after_target(task_id, self.controller.state.current_target_id))
        elif request.error_type in {"perception_failed", "no_target_found"}:
            asyncio.create_task(self._run_sensor_stages(task_id))
        elif request.error_type in {"grasp_generation_failed", "no_valid_grasp"}:
            asyncio.create_task(self._continue_after_target(task_id, self.controller.state.current_target_id))
        elif request.error_type == "grasp_verification_failed":
            asyncio.create_task(self._run_verify(task_id))
        elif request.error_type in {"planning_failed", "execution_failed"}:
            self._retry_last_motion(task_id)
        else:
            return False
        return True

    def _retry_last_motion(self, task_id: str) -> None:
        parent = self._parent(task_id)
        if parent is None:
            return
        motions = [node for node in self.controller.state.tool_nodes if node.parent_id == parent.node_id and
                   node.tool_name in {"move_to_named_target", "move_to_pregrasp", "approach_grasp", "retreat_grasp"}]
        if not motions:
            self._request_recovery(task_id, "planning_failed", "No motion request is available to retry", ["Cancel"])
            return
        latest = motions[-1]
        if latest.tool_name == "move_to_named_target":
            self._request_named_motion(parent, str(latest.input_data.get("target_name", "home")),
                                       latest.display_name, purpose=str(latest.input_data.get("purpose", "observe")))
        else:
            pose_key = {"move_to_pregrasp": "pregrasp", "approach_grasp": "grasp", "retreat_grasp": "retreat"}[latest.tool_name]
            self._request_pose_motion(task_id, latest.tool_name, pose_key, latest.display_name)

    def _mark_latest_failed(self, tool_name: str, message: str) -> None:
        matches = [node for node in self.controller.state.tool_nodes if node.tool_name == tool_name]
        if matches:
            self.controller.update_tool_status(matches[-1].node_id, ToolStatus.FAILED,
                                               error_message=message)

    def continue_after_grasp_review(self, review_node: ToolNode) -> None:
        task_id = self.controller.state.current_task_id
        if task_id and task_id not in self._cancelled_task_ids:
            try:
                self._prepare_grasp_motion_context(task_id)
            except Exception as exc:
                parent = self._parent(task_id)
                if parent:
                    parent.status = ToolStatus.FAILED
                    self.controller.register_tool_node(parent, append_legacy=False)
                self.controller.fail_task(
                    f"Could not prepare the reviewed grasp motion: {exc}",
                    node_id=review_node.node_id,
                )
                return
            self._request_gripper_gate(task_id, "open_gripper", "Open Gripper", purpose="prepare")

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
            self.controller.fail_task("Real gripper execution is not enabled.", node_id=node.node_id)
            return
        method = getattr(self._sim_gripper, node.tool_name)
        result = method(**node.input_data)
        if not result.get("success", False):
            self.controller.update_tool_status(node.node_id, ToolStatus.FAILED,
                                               error_message=result.get("message", "Gripper failed."))
            self.controller.fail_task(str(result.get("message", "Gripper failed.")), node_id=node.node_id)
            return
        self.controller.update_tool_status(node.node_id, ToolStatus.SUCCEEDED,
                                           output_summary=result.get("output", {}))
        if node.tool_name == "open_gripper" and node.input_data.get("purpose") == "release":
            parent = self._parent(task_id)
            if parent:
                parent.status = ToolStatus.SUCCEEDED
                self.controller.register_tool_node(parent, append_legacy=False)
            self.controller.complete_task()
        elif node.tool_name == "open_gripper":
            self._request_pose_motion(task_id, "move_to_pregrasp", "pregrasp", "Move To Pregrasp")
        else:
            self._request_pose_motion(task_id, "retreat_grasp", "retreat", "Retreat Grasp")

    async def _run_verify(self, task_id: str) -> None:
        parent = self._parent(task_id)
        if parent is None:
            return
        context = self._contexts[task_id]
        self.controller.state.task_status = TaskStatus.VERIFYING
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
            self.controller.register_tool_node(parent, append_legacy=False)
            self.controller.fail_task("Real grasp verification is not enabled.", node_id=node.node_id)
            return
        from llm_skill_robot.grasping.grasp_verifier import GraspVerifier
        verification_success = bool(self.controller.gui_config.get("phase9", {}).get("mock_verification_success", True))
        gripper_state = self._sim_gripper.get_gripper_state()
        gripper_state.setdefault("output", {}).update({
            "feedback_available": True, "command_sent": True,
            "object_may_be_held": verification_success,
        })
        verification = GraspVerifier().verify_grasp(
            str(context.get("resolved_object_id", "")), gripper_state,
            {"success": True, "object_no_longer_visible": verification_success},
        )
        verification_success = verification.get("status") == "LIKELY_SUCCESS"
        self.controller.update_tool_status(
            node.node_id, ToolStatus.SUCCEEDED if verification_success else ToolStatus.FAILED,
            output_summary={"verification": "simulated", "success": verification_success,
                            "verifier_status": verification.get("status"),
                            "object_id": context.get("resolved_object_id")},
        )
        if not verification_success:
            self._request_recovery(task_id, "grasp_verification_failed", "Grasp verification failed", ["Retry", "Select Another Grasp", "Cancel"])
            return
        target = str(self.controller.gui_config.get("phase9", {}).get("place_named_target", "home"))
        self._request_named_motion(parent, target, "Move To Place", purpose="place")

    async def _run_non_motion(self, task_id, parent, skill_id, display_name, parameters, context):
        if task_id in self._cancelled_task_ids:
            return None
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
            self.controller.register_tool_node(parent, append_legacy=False)
            self.controller.state.task_status = TaskStatus.FAILED
            self.controller.add_chat_message(f"{display_name} could not continue: {result.get('message', 'unknown error')}", sent=False, name="System")
            return None
        self.controller.update_tool_status(node.node_id, ToolStatus.SUCCEEDED, output_summary=output)
        return output

    def _request_grasp_review(self, task_id, parent, context) -> None:
        review = self._add_node(parent, "review_grasp_candidate", "Review Grasp Candidate", {})
        review.status = ToolStatus.WAITING_APPROVAL
        review.requires_approval = True
        review.output_data = {"candidate": context.get("selected_grasp_candidate")}
        review.output_summary = dict(review.output_data)
        self.controller.state.task_status = TaskStatus.GRASP_REVIEW
        self.controller.create_grasp_review_request(review, context.get("grasp_candidates", []))

    def _request_target_review(self, task_id, parent, context) -> None:
        review = self._add_node(parent, "select_target", "Confirm Target", {})
        review.status = ToolStatus.WAITING_APPROVAL
        review.requires_approval = True
        review.output_summary = {"candidate_count": len(context.get("candidate_objects", []))}
        self.controller.state.task_status = TaskStatus.TARGET_REVIEW
        self.controller.create_target_review_request(review, context.get("candidate_objects", []))

    def _request_recovery(self, task_id: str, error_type: str, description: str, actions: list[str]) -> None:
        parent = self._parent(task_id)
        if parent is None or self.controller.state.pending_hitl_request is not None:
            return
        node = self._add_node(parent, "error_recovery", "Recovery Required", {"error_type": error_type})
        node.status = ToolStatus.WAITING_APPROVAL
        node.requires_approval = True
        self.controller.state.task_status = TaskStatus.FAILED
        self.controller.create_recovery_request(node, error_type, description, actions)

    def _request_gripper_gate(self, task_id, skill_id, display_name, *, purpose: str = "contact") -> None:
        parent = self._parent(task_id)
        if parent is None:
            return
        parameters = {"purpose": purpose, "during_contact": skill_id == "close_gripper"}
        node = self._add_node(parent, skill_id, display_name, parameters)
        node.status = ToolStatus.WAITING_APPROVAL
        node.requires_approval = True
        self.controller.state.task_status = TaskStatus.WAITING_APPROVAL
        self.controller.create_agent_hitl_request(node, ["execution"])

    def _request_named_motion(self, parent, target, display_name, *, purpose: str = "observe") -> None:
        node = self._add_node(parent, "move_to_named_target", display_name, {"target_name": target, "purpose": purpose})
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
                self.controller.register_tool_node(parent, append_legacy=False)
            self.controller.fail_task(
                f"{display_name} requires a reviewed base_link grasp pose.",
                node_id=parent.node_id if parent else None,
            )
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
        from dataclasses import replace

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
        # The real-arm safety limits remain the authoritative cap in REAL
        # mode. Fake hardware uses the GUI's explicit, bounded demo speed.
        if self.controller.state.robot_mode not in {"REAL", "REAL ROBOT"}:
            velocity, acceleration = self.controller.simulation_motion_scales()
            context["grasp_motion_policy"] = replace(
                context["grasp_motion_policy"],
                velocity_scale=velocity,
                acceleration_scale=acceleration,
            )
        # MockPerceptionAdapter already gives deterministic base_link poses.
        if isinstance(context.get("grasp_motion_poses"), dict):
            return

        candidate = context.get("selected_grasp_candidate")
        candidate_id = _candidate_id(candidate) if isinstance(candidate, dict) else None
        screened_poses = context.get("candidate_motion_poses", {}).get(candidate_id)
        if isinstance(screened_poses, dict):
            context["grasp_motion_poses"] = screened_poses
            return
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
        task_id = self.controller.state.current_task_id
        predecessor = self._last_node_ids.get(task_id or "", parent.node_id)
        node = ToolNode(
            node_id=f"{parent.node_id}:{skill_id}:{len(self.controller.state.tool_nodes)}",
            parent_id=parent.node_id, tool_name=skill_id, display_name=display_name,
            plan_version=self.controller.state.current_plan_version,
            dependencies=[predecessor] if predecessor else [],
            input_data={**parameters, "task_id": task_id,
                        "target_id": self.controller.state.current_target_id,
                        "grasp_candidate_id": self.controller.state.current_grasp_candidate_id},
            input_summary=dict(parameters),
        )
        self.controller.register_tool_node(node)
        if task_id:
            self._last_node_ids[task_id] = node.node_id
        return node

    def _target_review_required(self, objects: list[dict[str, Any]]) -> bool:
        if len(objects) <= 1:
            return False
        config = self.controller.gui_config.get("phase9", {}).get("target_review", {})
        if bool(config.get("always_when_multiple", True)):
            return True
        margin = float(config.get("ambiguity_margin", 0.1))
        confidences = sorted((float(item.get("confidence", 0.0)) for item in objects), reverse=True)
        return len(confidences) > 1 and confidences[0] - confidences[1] <= margin

    def _invalidate_downstream(self, task_id: str, reason: str) -> None:
        context = self._contexts.get(task_id, {})
        context.pop("point_cloud", None)
        context.pop("grasp_candidates", None)
        context.pop("selected_grasp_candidate", None)
        context.pop("grasp_motion_poses", None)
        self.controller.invalidate_lineage(reason)

    def _parent(self, task_id: str) -> ToolNode | None:
        parent_id = self._parents.get(task_id)
        return self.controller._node(parent_id) if parent_id else None


def _normalise_objects(context: dict[str, Any], output: dict[str, Any]) -> list[dict[str, Any]]:
    raw = output.get("objects") or context.get("objects") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _normalise_grasps(context: dict[str, Any], output: dict[str, Any]) -> list[dict[str, Any]]:
    raw = output.get("candidates") or context.get("grasp_candidates") or []
    candidates = [dict(item) for item in raw if isinstance(item, dict)]
    candidates.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    for index, item in enumerate(candidates):
        item.setdefault("rank", index + 1)
    return candidates


def _candidate_id(candidate: dict[str, Any]) -> str | None:
    value = candidate.get("grasp_candidate_id") or candidate.get("candidate_id") or candidate.get("id")
    return str(value) if value is not None else None


def _new_plan_step(step_id: str, skill_id: str, description: str, parameters: dict[str, Any]):
    from llm_skill_robot.core.plan import PlanStep
    return PlanStep(step_id=step_id, skill_id=skill_id, description=description, parameters=parameters)


def _query_from_parent(parent: ToolNode, fallback: str) -> str:
    for key in ("object_query", "query", "object", "target", "target_name"):
        value = parent.input_data.get(key)
        if value:
            return str(value)
    return fallback
