import asyncio
import json

from hitl_gui.app_state import HitlDecision, TaskStatus, ToolStatus
from hitl_gui.gui_controller import GuiController


def mock_controller(*args, **kwargs):
    """Keep phase-2 workflow tests independent of the user-selected LLM mode."""
    controller = GuiController(*args, **kwargs)
    controller.gui_config["agent_bridge"]["mode"] = "mock"
    controller.state.robot_mode = "SIMULATION"
    return controller


async def wait_for_status(controller, status):
    for _ in range(200):
        if controller.state.task_status == status:
            return
        await asyncio.sleep(0.002)
    raise AssertionError(f"Expected {status}, got {controller.state.task_status}")


async def start_until_approval():
    controller = mock_controller(step_delay=0.001)
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
        controller = mock_controller(step_delay=0.05)
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


def test_status_changes_create_structured_events():
    async def scenario():
        controller = await start_until_approval()
        event_types = {event.event_type for event in controller.state.event_log}
        assert {"task_created", "task_started", "chat_message_added", "tool_started", "tool_succeeded", "hitl_requested"} <= event_types
    asyncio.run(scenario())


def test_replan_records_version_and_invalidated_trajectory():
    async def scenario():
        controller = await start_until_approval()
        request = controller.state.pending_hitl_request
        old_trajectory = controller.state.current_trajectory_id
        assert request and controller.submit_hitl_decision(request.request_id, HitlDecision.REPLAN)
        await asyncio.sleep(0.01)
        assert controller.state.current_plan_version == 2
        invalidations = [event for event in controller.state.event_log if event.event_type == "trajectory_invalidated"]
        assert invalidations and invalidations[-1].metadata["trajectory_id"] == old_trajectory
        assert any(event.event_type == "plan_version_changed" and event.new_value == 2 for event in controller.state.event_log)
        controller.cancel_task()
    asyncio.run(scenario())


def test_exported_json_is_readable_and_task_directories_do_not_mix(tmp_path):
    async def scenario():
        controller = mock_controller(step_delay=0.001, log_root=tmp_path)
        first_id = controller.start_task("first task")
        await wait_for_status(controller, TaskStatus.WAITING_APPROVAL)
        first_dir = controller.export_task_log()
        first_events = json.loads((first_dir / "execution_events.json").read_text(encoding="utf-8"))
        assert first_events and all(event["task_id"] == first_id for event in first_events)
        controller.cancel_task()
        await controller.runner.wait()

        second_id = controller.start_task("second task")
        await wait_for_status(controller, TaskStatus.WAITING_APPROVAL)
        second_dir = controller.export_task_log()
        summary = json.loads((second_dir / "task_summary.json").read_text(encoding="utf-8"))
        second_events = json.loads((second_dir / "execution_events.json").read_text(encoding="utf-8"))
        receipts = json.loads((second_dir / "tool_receipts.json").read_text(encoding="utf-8"))
        assert first_dir != second_dir and first_id != second_id
        assert summary["task_id"] == second_id
        assert all(event["task_id"] == second_id for event in second_events)
        assert receipts and "output_summary" in receipts[0]
        controller.cancel_task()
    asyncio.run(scenario())
