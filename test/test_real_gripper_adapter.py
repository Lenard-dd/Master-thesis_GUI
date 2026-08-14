import asyncio

from hitl_gui.app_state import ToolNode, ToolStatus
from hitl_gui.gui_controller import GuiController
from hitl_gui.real_gripper_adapter import RealGripperRuntimeAdapter


class _Backend:
    def __init__(self):
        self.calls = []

    def open_gripper(self, **kwargs):
        self.calls.append(("open_gripper", kwargs))
        return {
            "success": True, "status": "SUCCEEDED", "message": "opened",
            "output": {"command_sent": True, "current_width_m": 0.14},
        }

    def close_gripper(self, **kwargs):
        self.calls.append(("close_gripper", kwargs))
        return {
            "success": True, "status": "SUCCEEDED", "message": "closed",
            "output": {"command_sent": True, "current_width_m": 0.02},
        }


def test_real_adapter_forwards_only_safe_parameters_and_confirmation():
    backend = _Backend()
    adapter = RealGripperRuntimeAdapter()
    adapter._backend = backend

    result = adapter.execute(
        "close_gripper",
        {"during_contact": True, "purpose": "contact", "untrusted": "drop"},
        confirmed=True,
        contact_confirmation="YES",
    )

    assert result["success"] is True
    assert backend.calls == [(
        "close_gripper",
        {"during_contact": True, "confirmation": "YES"},
    )]


def test_real_adapter_rejects_missing_gui_confirmation_without_backend_call():
    backend = _Backend()
    adapter = RealGripperRuntimeAdapter()
    adapter._backend = backend

    result = adapter.execute("open_gripper", {}, confirmed=False)

    assert result["success"] is False
    assert result["status"] == "REJECTED"
    assert backend.calls == []


def test_gui_real_open_gripper_uses_confirmed_existing_backend():
    async def scenario():
        controller = GuiController(config_overrides={"gui_mode": "MOCK"})
        controller.state.robot_mode = "REAL ROBOT"
        controller.state.current_task_id = "task-real-gripper"
        backend = _Backend()
        adapter = RealGripperRuntimeAdapter()
        adapter._backend = backend
        controller.skill_runtime._real_gripper = adapter
        completed = []
        controller.complete_task = lambda: completed.append(True)
        node = ToolNode(
            node_id="release-1", parent_id=None, tool_name="open_gripper",
            display_name="Release Object", status=ToolStatus.RUNNING,
            input_data={"purpose": "release"},
        )
        controller.state.tool_nodes.append(node)
        controller._real_gripper_confirmed_nodes.add(node.node_id)

        await controller.skill_runtime.execute_gripper_after_release(node)

        assert node.status == ToolStatus.SUCCEEDED
        assert backend.calls == [("open_gripper", {"confirmed": True})]
        assert completed == [True]
        controller.shutdown()

    asyncio.run(scenario())


def test_real_grasp_verification_accepts_structured_contact_feedback():
    async def scenario():
        controller = GuiController(config_overrides={"gui_mode": "MOCK"})
        controller.state.robot_mode = "REAL ROBOT"
        controller.state.current_task_id = "task-contact-verify"
        runtime = controller.skill_runtime
        parent = ToolNode(
            node_id="safe-pick", parent_id=None, tool_name="safe_pick_object",
            display_name="Safe Pick", status=ToolStatus.RUNNING,
        )
        controller.state.tool_nodes.append(parent)
        runtime._parents["task-contact-verify"] = parent.node_id
        runtime._last_node_ids["task-contact-verify"] = parent.node_id
        runtime._contexts["task-contact-verify"] = {
            "resolved_object_id": "cube-1",
            "last_gripper_result": {
                "success": True,
                "output": {
                    "object_contact_detected": True,
                    "object_may_be_held": True,
                    "error_code": 2,
                    "current_width_m": 0.05,
                },
            },
        }
        place_requests = []
        runtime._request_named_motion = (
            lambda *args, **kwargs: place_requests.append((args, kwargs))
        )

        await runtime._run_verify("task-contact-verify")

        verify = next(
            node for node in controller.state.tool_nodes
            if node.tool_name == "verify_grasp"
        )
        assert verify.status == ToolStatus.SUCCEEDED
        assert verify.output_summary["object_contact_detected"] is True
        assert len(place_requests) == 1

    asyncio.run(scenario())
