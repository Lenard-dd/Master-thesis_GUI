import asyncio

from hitl_gui.app_state import HitlDecision, TaskStatus
from hitl_gui.gui_controller import GuiController
from hitl_gui.trajectory_review_adapter import ExistingTrajectoryReviewAdapter


class FakeValidator:
    def validate_motion_plan_summary(self, _summary):
        return {"decision": "ALLOW", "reasons": [], "warnings": []}


class FakeBackend:
    def __init__(self):
        self.counter = 0
        self.executed = []

    def plan_to_named_target(self, target, **_kwargs):
        self.counter += 1
        plan_id = f"plan-{self.counter}"
        return {"plan_id": plan_id, "success": True, "summary": {
            "plan_id": plan_id, "success": True, "target_name": target,
            "num_trajectory_points": 12, "duration_sec": 1.2,
        }}

    def get_cached_plan(self, _plan_id):
        return object()

    def execute_cached_plan_simulated(self, plan_id, **_kwargs):
        self.executed.append(plan_id)
        return {"success": True, "message": "simulated", "plan_id": plan_id}

    def execute_cached_plan_real(self, plan_id, **_kwargs):
        self.executed.append(plan_id)
        return {"success": True, "message": "real", "plan_id": plan_id}


async def _wait_for_request(controller):
    for _ in range(100):
        if controller.state.pending_hitl_request:
            return controller.state.pending_hitl_request
        await asyncio.sleep(0.002)
    raise AssertionError("trajectory request was not created")


def _controller_with_adapter():
    controller = GuiController()
    backend = FakeBackend()
    adapter = ExistingTrajectoryReviewAdapter(backend, FakeValidator())
    adapter.run_in_worker = False
    controller.set_trajectory_adapter(adapter)
    controller.state.current_task_id = "task-trajectory"
    return controller, backend


def test_old_request_cannot_approve_after_replan_and_old_trajectory_cannot_execute():
    async def scenario():
        controller, backend = _controller_with_adapter()
        first_plan = controller.request_named_target_trajectory("home")
        old_request = await _wait_for_request(controller)
        await first_plan
        old_id = old_request.trajectory_id
        assert controller.replan_trajectory(old_request.request_id, "path too long")
        new_request = await _wait_for_request(controller)
        await controller._last_trajectory_task
        while new_request.request_id == old_request.request_id:
            await asyncio.sleep(0.002)
            new_request = await _wait_for_request(controller)
        assert not controller.submit_hitl_decision(old_request.request_id, HitlDecision.APPROVE)
        assert old_id not in backend.executed
        assert new_request.trajectory_id != old_id
    asyncio.run(scenario())


def test_repeated_approve_only_executes_the_current_trajectory_once():
    async def scenario():
        controller, backend = _controller_with_adapter()
        plan_task = controller.request_named_target_trajectory("home")
        request = await _wait_for_request(controller)
        await plan_task
        assert controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
        assert not controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
        await controller._last_execution_task
        assert backend.executed == [request.trajectory_id]
    asyncio.run(scenario())


def test_reject_never_executes_trajectory():
    async def scenario():
        controller, backend = _controller_with_adapter()
        plan_task = controller.request_named_target_trajectory("home")
        request = await _wait_for_request(controller)
        await plan_task
        assert controller.submit_hitl_decision(request.request_id, HitlDecision.REJECT)
        assert controller.state.task_status == TaskStatus.CANCELLED
        assert backend.executed == []
    asyncio.run(scenario())


def test_phase9_real_mode_is_disabled_before_trajectory_review():
    async def scenario():
        controller, backend = _controller_with_adapter()
        controller.state.robot_mode = "REAL ROBOT"
        plan_task = controller.request_named_target_trajectory("home")
        await plan_task
        assert controller.state.pending_hitl_request is None
        assert controller.state.task_status == TaskStatus.FAILED
        assert backend.executed == []
    asyncio.run(scenario())
