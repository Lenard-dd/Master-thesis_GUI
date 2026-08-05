import asyncio

from hitl_gui.app_state import HitlDecision, TaskStatus, ToolNode, ToolStatus, utc_now
from hitl_gui.gui_controller import GuiController
from hitl_gui.runtime_adapters import RuntimeAdapterRegistry, RuntimeBackendConfig
from hitl_gui.trajectory_review_adapter import ExistingTrajectoryReviewAdapter

from test_runtime_adapters import _Backend, _Validator


class _PlanningFailureBackend(_Backend):
    def plan_to_named_target(self, target, **_kwargs):
        raise RuntimeError(f"planning unavailable for {target}")


def _controller(mock_perception=None, backend=None):
    controller = GuiController()
    config = RuntimeBackendConfig(mock_perception=mock_perception or {})
    adapters = RuntimeAdapterRegistry(config)
    controller.runtime_backend_config = config
    controller.runtime_adapters = adapters
    controller.skill_runtime.adapters = adapters
    trajectory = ExistingTrajectoryReviewAdapter(backend or _Backend(), _Validator())
    trajectory.run_in_worker = False
    controller.set_trajectory_adapter(trajectory)
    controller.state.current_task_id = "task-phase9"
    controller.state.current_task_name = "pick a cube"
    controller.state.experiment_metrics.task_started_at = utc_now()
    parent = ToolNode(
        node_id="safe-pick", parent_id=None, tool_name="safe_pick_object",
        display_name="Safe Pick Object", status=ToolStatus.WAITING_APPROVAL,
        requires_approval=True, input_data={"object_query": "cube"},
    )
    controller.state.tool_nodes.append(parent)
    controller.create_agent_hitl_request(parent, ["task_intent"])
    return controller, trajectory


async def _reach_first_perception_review(controller):
    request = controller.state.pending_hitl_request
    assert controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
    await controller._last_skill_task
    await controller._last_trajectory_task
    request = controller.state.pending_hitl_request
    assert request.request_type == "trajectory_review"
    assert controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
    await controller._last_execution_task
    await asyncio.sleep(0)
    return controller.state.pending_hitl_request


async def _finish_from_grasp_review(controller):
    request = controller.state.pending_hitl_request
    assert request and request.request_type == "grasp_review"
    assert controller.approve_grasp_candidate(request.request_id)
    for _ in range(12):
        if controller.state.task_status == TaskStatus.COMPLETED:
            return
        pending = controller.state.pending_hitl_request
        assert pending is not None
        assert controller.submit_hitl_decision(pending.request_id, HitlDecision.APPROVE)
        if pending.request_type == "trajectory_review":
            await controller._last_execution_task
        else:
            await controller._last_skill_task
        await asyncio.sleep(0)
    raise AssertionError("closed loop did not complete")


def test_normal_simulated_pick_place_closed_loop_succeeds():
    async def scenario():
        controller, trajectory = _controller()
        request = await _reach_first_perception_review(controller)
        assert request.request_type == "grasp_review"
        await _finish_from_grasp_review(controller)
        assert controller.state.task_status == TaskStatus.COMPLETED
        assert len(trajectory.backend.executed) == 5
        assert any(node.display_name == "Move To Place" for node in controller.state.tool_nodes)
        assert controller.state.experiment_metrics.total_task_time_ms is not None

    asyncio.run(scenario())


def test_user_can_select_another_detected_target_and_invalidate_downstream():
    async def scenario():
        objects = [
            {"object_id": "cube-a", "label": "cube", "confidence": 0.91},
            {"object_id": "cube-b", "label": "cube", "confidence": 0.89},
        ]
        controller, _ = _controller({"objects": objects})
        request = await _reach_first_perception_review(controller)
        assert request.request_type == "target_review"
        old_version = controller.state.current_plan_version
        assert controller.skill_runtime.select_next_target(request.request_id)
        assert request.object_id == "cube-b"
        assert controller.skill_runtime.select_target(request.request_id, "cube-b")
        await asyncio.sleep(0)
        assert controller.state.current_target_id == "cube-b"
        # Selecting the initial confirmed target does not invalidate the
        # already executed observation motion or create a false replan.
        assert controller.state.current_plan_version == old_version
        assert controller.state.pending_hitl_request.request_type == "grasp_review"

    asyncio.run(scenario())


def test_user_can_select_next_grasp_candidate():
    async def scenario():
        candidates = [
            {"candidate_id": "grasp-a", "score": 0.95, "valid": True},
            {"candidate_id": "grasp-b", "score": 0.80, "valid": True},
        ]
        controller, _ = _controller({"grasp_candidates": candidates})
        request = await _reach_first_perception_review(controller)
        assert request.request_type == "grasp_review"
        assert request.grasp_candidate_id == "grasp-a"
        assert controller.skill_runtime.select_next_grasp(request.request_id)
        assert request.grasp_candidate_id == "grasp-b"
        assert controller.state.current_grasp_candidate_id == "grasp-b"

    asyncio.run(scenario())


def test_trajectory_rejection_can_replan_without_executing_old_plan():
    async def scenario():
        controller, trajectory = _controller()
        await _reach_first_perception_review(controller)
        assert controller.approve_grasp_candidate(controller.state.pending_hitl_request.request_id)
        gate = controller.state.pending_hitl_request
        assert controller.submit_hitl_decision(gate.request_id, HitlDecision.APPROVE)
        await controller._last_skill_task
        old = controller.state.pending_hitl_request
        old_id = old.trajectory_id
        assert controller.replan_trajectory(old.request_id, "trajectory unsafe")
        await controller._last_trajectory_task
        new = controller.state.pending_hitl_request
        assert new.trajectory_id != old_id
        assert old_id in controller._invalidated_trajectory_ids
        assert old_id not in trajectory.backend.executed
        assert controller.state.experiment_metrics.replan_count == 1

    asyncio.run(scenario())


def test_motion_planning_failure_creates_recovery_request():
    async def scenario():
        controller, _ = _controller(backend=_PlanningFailureBackend())
        request = controller.state.pending_hitl_request
        assert controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
        await controller._last_skill_task
        await controller._last_trajectory_task
        recovery = controller.state.pending_hitl_request
        assert recovery is not None
        assert recovery.request_type == "error_recovery"
        assert recovery.error_type == "planning_failed"
        assert "Retry" in recovery.recovery_actions

    asyncio.run(scenario())


def test_user_can_cancel_the_closed_loop():
    controller, _ = _controller()
    controller.cancel_task()
    assert controller.state.task_status == TaskStatus.CANCELLED
    assert controller.state.pending_hitl_request is None
    assert any(event.event_type == "task_cancelled" for event in controller.state.event_log)
