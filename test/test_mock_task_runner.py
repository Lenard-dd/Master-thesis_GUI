import asyncio

from hitl_gui.app_state import HitlDecision, TaskStatus, ToolStatus
from hitl_gui.gui_controller import GuiController


async def wait_for_status(controller, status):
    for _ in range(200):
        if controller.state.task_status == status:
            return
        await asyncio.sleep(0.002)
    raise AssertionError(f"Expected {status}, got {controller.state.task_status}")


async def start_until_approval():
    controller = GuiController(step_delay=0.001)
    assert controller.start_task("pick the red cube")
    await wait_for_status(controller, TaskStatus.WAITING_APPROVAL)
    return controller


def test_approve_completes_task():
    async def scenario():
        controller = await start_until_approval()
        request = controller.state.pending_hitl_request
        assert request
        assert controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
        await controller.runner.wait()
        assert controller.state.task_status == TaskStatus.COMPLETED
        assert controller.state.tool_nodes[-2].status == ToolStatus.SUCCEEDED
        assert controller.state.tool_nodes[-1].status == ToolStatus.SUCCEEDED
    asyncio.run(scenario())


def test_reject_cancels_task():
    async def scenario():
        controller = await start_until_approval()
        request = controller.state.pending_hitl_request
        assert request
        assert controller.submit_hitl_decision(request.request_id, HitlDecision.REJECT)
        await controller.runner.wait()
        assert controller.state.task_status == TaskStatus.CANCELLED
        review = next(node for node in controller.state.tool_nodes if node.node_id == "trajectory_review")
        assert review.status == ToolStatus.REJECTED
    asyncio.run(scenario())


def test_replan_creates_new_trajectory_and_accepts_only_new_request():
    async def scenario():
        controller = await start_until_approval()
        old_request = controller.state.pending_hitl_request
        old_trajectory = controller.state.current_trajectory_id
        assert old_request and controller.submit_hitl_decision(old_request.request_id, HitlDecision.REPLAN)
        for _ in range(100):
            if controller.state.pending_hitl_request:
                break
            await asyncio.sleep(0.002)
        new_request = controller.state.pending_hitl_request
        assert new_request and new_request.request_id != old_request.request_id
        assert new_request.trajectory_id != old_trajectory
        assert not controller.submit_hitl_decision(old_request.request_id, HitlDecision.APPROVE)
        assert controller.submit_hitl_decision(new_request.request_id, HitlDecision.APPROVE)
        await controller.runner.wait()
        assert controller.state.task_status == TaskStatus.COMPLETED
        assert controller.state.current_plan_version == 2
    asyncio.run(scenario())


def test_cancel_during_running_task():
    async def scenario():
        controller = GuiController(step_delay=0.05)
        assert controller.start_task("pick the blue cube")
        await asyncio.sleep(0.005)
        controller.cancel_task()
        await controller.runner.wait()
        assert controller.state.task_status == TaskStatus.CANCELLED
        assert all(
            node.status not in {ToolStatus.PENDING, ToolStatus.RUNNING, ToolStatus.WAITING_APPROVAL}
            for node in controller.state.tool_nodes
        )
    asyncio.run(scenario())
