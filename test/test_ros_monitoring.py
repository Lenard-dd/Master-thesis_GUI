import time

from hitl_gui.app_state import SystemComponentStatus
from hitl_gui.gui_controller import GuiController
from hitl_gui.message_converter import component_status, heartbeat_status


def test_real_monitoring_mode_initializes_without_blocking_gui():
    controller = GuiController(step_delay=0.001)
    assert controller.state.robot_mode == "REAL ROBOT"
    assert controller.ros_worker is not None
    assert controller.state.task_status.value == "IDLE"
    controller.shutdown()


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
    original_worker = controller.ros_worker
    if original_worker is not None:
        original_worker.shutdown()
    controller.ros_worker = FailedWorker()
    controller.consume_ros_status()
    assert controller.state.ros_status == SystemComponentStatus.ERROR
    assert controller.state.hardware_status["UR5"] == SystemComponentStatus.DISCONNECTED
    assert controller.state.hardware_status["MoveIt"] == SystemComponentStatus.DISCONNECTED
    assert controller.state.robot_mode == "REAL ROBOT"
    controller.shutdown()


def test_inactive_executor_invalidates_cached_ros_health():
    now = time.monotonic()
    details = component_status(
        {
            "executor_running": False,
            "node_initialized": True,
            "joint_last_time": now,
            "rgb_last_time": now,
            "node_names": ["move_group"],
            "service_names": ["/get_planning_scene"],
        },
        1.0,
        3.0,
    )

    assert details["UR5"]["status"] == SystemComponentStatus.DISCONNECTED
    assert details["D435i"]["status"] == SystemComponentStatus.DISCONNECTED
    assert details["MoveIt"]["status"] == SystemComponentStatus.DISCONNECTED
