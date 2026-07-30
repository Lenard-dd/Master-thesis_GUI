import time

from hitl_gui.app_state import SystemComponentStatus
from hitl_gui.gui_controller import GuiController
from hitl_gui.message_converter import heartbeat_status


def test_ros_unavailable_keeps_mock_gui_usable():
    controller = GuiController(step_delay=0.001)
    assert controller.state.robot_mode == "MOCK"
    assert controller.ros_worker is None
    assert controller.state.task_status.value == "IDLE"


def test_message_timeout_statuses():
    now = time.monotonic()
    assert heartbeat_status(now, 1.0, 3.0)[0] == SystemComponentStatus.READY
    assert heartbeat_status(now - 2.0, 1.0, 3.0)[0] == SystemComponentStatus.WARNING
    assert heartbeat_status(now - 4.0, 1.0, 3.0)[0] == SystemComponentStatus.DISCONNECTED


def test_ros_worker_error_does_not_break_controller():
    class FailedWorker:
        def snapshot(self):
            return {"executor_running": False, "node_initialized": False, "worker_error": "simulated executor failure"}

        def shutdown(self):
            pass

    controller = GuiController(step_delay=0.001)
    controller.ros_worker = FailedWorker()
    controller.consume_ros_status()
    assert controller.state.ros_status == SystemComponentStatus.ERROR
    assert controller.state.robot_mode == "MOCK"
