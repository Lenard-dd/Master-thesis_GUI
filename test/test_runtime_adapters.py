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


def test_runtime_registry_can_release_the_live_sam3_worker_for_graspgenx():
    registry = RuntimeAdapterRegistry(
        RuntimeBackendConfig(perception_mode="ros", grasp_mode="graspgenx")
    )

    class _LiveAdapter:
        def __init__(self):
            self.released = False

        def release_sam3_worker(self):
            self.released = True
            return True

    live = _LiveAdapter()
    registry._live = live

    assert registry.release_sam3_worker() is True
    assert live.released is True


def test_verified_relative_place_returns_to_observe_before_place_approach():
    controller = GuiController()
    controller.state.current_task_id = "task-place"
    parent = ToolNode(
        node_id="pick-place", parent_id=None, tool_name="supervised_pick_from_localization",
        display_name="Pick and Place", status=ToolStatus.RUNNING,
    )
    controller.state.tool_nodes.append(parent)
    runtime = controller.skill_runtime
    runtime._parents["task-place"] = parent.node_id
    runtime._contexts["task-place"] = {
        "pending_place_pose_plan": {"relation": "between"},
    }
    runtime._prepare_place_motion_context = lambda _task_id: None
    calls = []
    runtime._request_named_motion = lambda *args, **kwargs: calls.append((args, kwargs))

    runtime._continue_to_place_or_named_target("task-place", parent)

    assert calls == [
        ((parent, "observe", "Return To Observe Before Place"), {"purpose": "place_observe"})
    ]


def test_place_observe_completion_starts_the_place_approach_motion():
    async def scenario():
        controller = GuiController()
        controller.state.current_task_id = "task-place"
        runtime = controller.skill_runtime
        node = ToolNode(
            node_id="return-observe", parent_id=None, tool_name="move_to_named_target",
            display_name="Return To Observe Before Place", status=ToolStatus.SUCCEEDED,
            input_data={"purpose": "place_observe"},
        )
        controller.state.tool_nodes.append(node)
        calls = []
        runtime._request_place_pose_motion = lambda *args: calls.append(args)

        runtime.on_motion_execution_completed(node.node_id)

        assert calls == [
            ("task-place", "move_to_place_approach", "place_approach_pose", "Move To Place Approach")
        ]

    asyncio.run(scenario())


def test_post_place_observe_completion_is_the_terminal_motion():
    async def scenario():
        controller = GuiController()
        controller.state.current_task_id = "task-place"
        runtime = controller.skill_runtime
        parent = ToolNode(
            node_id="pick-place", parent_id=None, tool_name="safe_pick_object",
            display_name="Pick and Place", status=ToolStatus.RUNNING,
        )
        node = ToolNode(
            node_id="post-place-observe", parent_id=parent.node_id,
            tool_name="move_to_named_target", display_name="Return To Observe After Place",
            status=ToolStatus.SUCCEEDED, input_data={"purpose": "post_place_observe"},
        )
        controller.state.tool_nodes.extend([parent, node])
        runtime._parents["task-place"] = parent.node_id

        runtime.on_motion_execution_completed(node.node_id)

        assert parent.status == ToolStatus.SUCCEEDED
        assert controller.state.task_status.value == "COMPLETED"

    asyncio.run(scenario())


def test_post_place_observe_releases_a_dependent_next_subgoal_instead_of_completing():
    async def scenario():
        controller = GuiController()
        controller.state.current_task_id = "task-sequence"
        runtime = controller.skill_runtime
        parent = ToolNode(
            node_id="pick-place-1", parent_id=None, tool_name="supervised_pick_from_localization",
            display_name="Pick and Place 1", status=ToolStatus.RUNNING,
        )
        post = ToolNode(
            node_id="post-place-observe", parent_id=parent.node_id,
            tool_name="move_to_named_target", display_name="Return To Observe After Place",
            status=ToolStatus.SUCCEEDED, input_data={"purpose": "post_place_observe"},
        )
        next_observe = ToolNode(
            node_id="pick-place-2-observe", parent_id=None, tool_name="move_to_named_target",
            display_name="Move To Observe (2)", status=ToolStatus.WAITING_APPROVAL,
            requires_approval=True, dependencies=[parent.node_id],
        )
        controller.state.tool_nodes.extend([parent, post, next_observe])
        runtime._parents["task-sequence"] = parent.node_id
        released = []
        controller.start_ready_agent_tool_dependents = lambda: released.append(True)

        runtime.on_motion_execution_completed(post.node_id)

        assert parent.status == ToolStatus.SUCCEEDED
        assert released == [True]
        assert controller.state.task_status.value != "COMPLETED"

    asyncio.run(scenario())


def test_post_place_observe_never_completes_while_later_agent_subgoal_is_pending():
    """A later compiled subgoal must survive the first pick/place completion.

    This specifically protects against a malformed/repaired dependency edge:
    the terminal guard must inspect the full structured Agent plan, not only
    direct successors of the first composite node.
    """
    async def scenario():
        controller = GuiController()
        controller.state.current_task_id = "task-sequence"
        runtime = controller.skill_runtime
        parent = ToolNode(
            node_id="agent-1-supervised_pick_from_localization", parent_id=None,
            tool_name="supervised_pick_from_localization",
            display_name="Pick and Place 1", status=ToolStatus.RUNNING,
        )
        post = ToolNode(
            node_id="agent-1-supervised_pick_from_localization:post-place:4",
            parent_id=parent.node_id, tool_name="move_to_named_target",
            display_name="Return To Observe After Place", status=ToolStatus.SUCCEEDED,
            input_data={"purpose": "post_place_observe"},
        )
        # This later plan node intentionally has no direct edge from ``parent``.
        # It models the edge case that previously allowed premature completion.
        later = ToolNode(
            node_id="agent-2-move_to_observe", parent_id=None,
            tool_name="move_to_named_target", display_name="Move To Observe (2)",
            status=ToolStatus.PENDING, requires_approval=True,
        )
        controller.state.tool_nodes.extend([parent, post, later])
        runtime._parents["task-sequence"] = parent.node_id
        controller.start_ready_agent_tool_dependents = lambda: None

        runtime.on_motion_execution_completed(post.node_id)

        assert parent.status == ToolStatus.SUCCEEDED
        assert controller.state.task_status.value != "COMPLETED"

    asyncio.run(scenario())


def test_safe_pick_completes_the_full_mock_tree_through_each_hitl_gate():
    async def scenario():
        controller = GuiController()
        controller.state.robot_mode = "SIMULATION"
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
        assert review is not None and review.request_type == "grasp_review"
        assert controller.approve_grasp_candidate(review.request_id)
        # Open gripper uses its own D gate; pregrasp, approach, and retreat
        # each use an independent C trajectory gate.
        for expected in (
            "execution", "trajectory_review", "trajectory_review", "execution",
            "trajectory_review", "trajectory_review", "execution",
        ):
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
            "move_to_named_target", "trajectory_review", "open_gripper",
        ]
        assert [call["planner_id"] for call in adapter.backend.pose_calls] == ["PTP", "LIN", "LIN"]
        assert all(call["pipeline_id"] == "pilz_industrial_motion_planner" for call in adapter.backend.pose_calls)
        assert [record.summary.get("target_name") for record in adapter.records.values() if record.summary.get("target_name")] == ["observe", "home"]

    asyncio.run(scenario())
