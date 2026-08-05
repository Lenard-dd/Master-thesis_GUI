import asyncio

from hitl_gui.app_state import HitlDecision, ToolNode, ToolStatus
from hitl_gui.gui_controller import GuiController
from hitl_gui.runtime_adapters import RuntimeAdapterRegistry, RuntimeBackendConfig
from hitl_gui.trajectory_review_adapter import ExistingTrajectoryReviewAdapter


class _Validator:
    def validate_motion_plan_summary(self, _summary):
        return {"decision": "ALLOW"}


class _Backend:
    def __init__(self):
        self.index = 0
        self.executed = []
        self.pose_calls = []

    def _plan(self, **summary):
        self.index += 1
        plan_id = f"safe-pick-plan-{self.index}"
        summary.update({"plan_id": plan_id, "success": True, "num_trajectory_points": 10, "duration_sec": 0.2})
        return {"success": True, "plan_id": plan_id, "summary": summary}

    def plan_to_named_target(self, target, **_kwargs):
        return self._plan(target_name=target)

    def plan_to_pose(self, frame, position, orientation, **kwargs):
        self.pose_calls.append(kwargs)
        return self._plan(target_pose={"frame": frame, "position": position, "orientation": orientation})

    def get_cached_plan(self, _plan_id):
        return object()

    def execute_cached_plan_simulated(self, plan_id, **_kwargs):
        self.executed.append(plan_id)
        return {"success": True, "message": "simulated", "plan_id": plan_id}


def _step(skill_id, parameters):
    from llm_skill_robot.core.plan import PlanStep

    return PlanStep(step_id=skill_id, skill_id=skill_id, description=skill_id, parameters=parameters)


def test_mock_runtime_adapter_returns_consistent_sensor_and_grasp_data():
    adapters = RuntimeAdapterRegistry(RuntimeBackendConfig())
    context = {}
    detection = adapters.execute(_step("detect_object", {"query": "red cube"}), context)
    cloud = adapters.execute(_step("build_object_point_cloud", {"object_id": "<resolved>"}), context)
    grasp = adapters.execute(_step("generate_grasp_pose", {"object_id": "<resolved>"}), context)
    assert detection["success"] and detection["output"]["object_id"] == "mock_red_cube_1"
    assert cloud["success"] and cloud["output"]["source"] == "mock_perception"
    assert grasp["success"] and grasp["output"]["candidate_id"].startswith("mock_grasp_")


def test_live_backend_request_never_falls_back_to_mock_without_ros_node():
    config = RuntimeBackendConfig(perception_mode="ros", grasp_mode="graspgenx")
    result = RuntimeAdapterRegistry(config).execute(_step("detect_object", {"query": "cup"}), {})
    assert result["success"] is False
    assert result["status"] == "NOT_AVAILABLE"
    assert "Live" in result["message"]


def test_safe_pick_completes_the_full_mock_tree_through_each_hitl_gate():
    async def scenario():
        controller = GuiController()
        adapter = ExistingTrajectoryReviewAdapter(_Backend(), _Validator())
        adapter.run_in_worker = False
        controller.set_trajectory_adapter(adapter)
        controller.state.current_task_id = "task-runtime"
        controller.state.current_task_name = "pick a red cube"
        parent = ToolNode(
            node_id="safe-pick", parent_id=None, tool_name="safe_pick_object",
            display_name="Safe Pick Object", status=ToolStatus.WAITING_APPROVAL,
            requires_approval=True, input_data={"object_query": "red cube"},
        )
        controller.state.tool_nodes.append(parent)
        controller.create_agent_hitl_request(parent, ["task_intent"])
        request = controller.state.pending_hitl_request
        assert request is not None
        assert controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
        await controller._last_skill_task
        assert controller.state.pending_hitl_request.request_type == "trajectory_review"
        assert controller.submit_hitl_decision(controller.state.pending_hitl_request.request_id, HitlDecision.APPROVE)
        await controller._last_execution_task
        await asyncio.sleep(0)
        assert [node.tool_name for node in controller.state.tool_nodes][-4:] == [
            "detect_object", "build_object_point_cloud", "generate_grasp_pose", "review_grasp_candidate",
        ]
        review = controller.state.pending_hitl_request
        assert review is not None and review.request_type == "grasp_candidate"
        assert controller.submit_hitl_decision(review.request_id, HitlDecision.APPROVE)
        # Open gripper uses its own D gate; pregrasp, approach, and retreat
        # each use an independent C trajectory gate.
        for expected in ("execution", "trajectory_review", "trajectory_review", "execution", "trajectory_review"):
            pending = controller.state.pending_hitl_request
            assert pending is not None and pending.request_type == expected
            assert controller.submit_hitl_decision(pending.request_id, HitlDecision.APPROVE)
            if expected == "trajectory_review":
                await controller._last_execution_task
            else:
                await controller._last_skill_task
            await asyncio.sleep(0)
        assert controller.state.task_status.value == "COMPLETED"
        assert [node.tool_name for node in controller.state.tool_nodes] == [
            "safe_pick_object", "move_to_named_target", "trajectory_review",
            "detect_object", "build_object_point_cloud", "generate_grasp_pose",
            "review_grasp_candidate", "open_gripper", "move_to_pregrasp",
            "trajectory_review", "approach_grasp", "trajectory_review",
            "close_gripper", "retreat_grasp", "trajectory_review", "verify_grasp",
        ]
        assert [call["planner_id"] for call in adapter.backend.pose_calls] == ["PTP", "LIN", "LIN"]
        assert all(call["pipeline_id"] == "pilz_industrial_motion_planner" for call in adapter.backend.pose_calls)

    asyncio.run(scenario())
