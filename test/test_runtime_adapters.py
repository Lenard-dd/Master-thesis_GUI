import asyncio

from hitl_gui.app_state import HitlDecision, ToolNode, ToolStatus
from hitl_gui.gui_controller import GuiController
from hitl_gui.runtime_adapters import RuntimeAdapterRegistry, RuntimeBackendConfig


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


def test_safe_pick_uses_mock_sensor_stages_then_requests_grasp_review():
    async def scenario():
        controller = GuiController()
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
        assert [node.tool_name for node in controller.state.tool_nodes][-4:] == [
            "detect_object", "build_object_point_cloud", "generate_grasp_pose", "review_grasp_candidate",
        ]
        review = controller.state.pending_hitl_request
        assert review is not None and review.request_type == "grasp_candidate"
        assert controller.submit_hitl_decision(review.request_id, HitlDecision.APPROVE)
        assert controller.state.tool_nodes[-1].status == ToolStatus.SUCCEEDED

    asyncio.run(scenario())
