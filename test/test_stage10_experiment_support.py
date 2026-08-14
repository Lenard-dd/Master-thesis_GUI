import ast
import json

from hitl_gui.app_state import TaskExperimentMetrics, ToolNode, ToolStatus, utc_now
from hitl_gui.gui_controller import GuiController
from hitl_gui.rviz_process_manager import load_gui_config


REQUIRED_SUMMARY_FIELDS = {
    "task_id", "instruction", "result", "total_duration", "agent_duration",
    "perception_duration", "grasp_generation_duration", "planning_duration",
    "execution_duration", "total_hitl_waiting_time", "number_of_hitl_requests",
    "number_of_user_modifications", "number_of_replans", "number_of_tool_failures",
    "final_plan_version", "selected_target", "selected_grasp", "final_trajectory_id",
}


def test_stage10_config_has_canonical_settings_and_resolved_rviz_path():
    config = load_gui_config()
    assert {
        "host", "port", "refresh_rate", "status_timeout", "rviz_config",
        "log_directory", "robot_mode", "enable_editable_nodes",
        "enable_real_driver_start", "enable_real_execution",
    } <= config.keys()
    assert config["rviz_config"].endswith("config/embedded_robot_only.rviz")
    assert config["enable_real_driver_start"] is True
    assert config["enable_real_execution"] is False
    camera_arguments = config["system_launcher"]["components"]["camera"]["arguments"]
    assert "pointcloud.allow_no_texture_points:=true" in camera_arguments


def test_launch_ros_arguments_do_not_terminate_gui_argument_parser(monkeypatch):
    from hitl_gui.main import parse_args

    monkeypatch.setattr("sys.argv", [
        "hitl_gui", "--host", "127.0.0.1", "--simulation", "false",
        "--ros-args", "-r", "__node:=hitl_gui",
    ])
    args = parse_args()
    assert args.host == "127.0.0.1"
    assert args.simulation is False


def test_real_driver_start_is_independent_from_real_motion_execution():
    from hitl_gui.managed_process import ManagedProcess, ProcessStatus

    controller = GuiController()
    controller.gui_config["enable_real_driver_start"] = True
    controller.gui_config["enable_real_execution"] = False
    calls = []
    controller.component_manager.start_component = lambda component_id, confirmed=False: (
        calls.append((component_id, confirmed)) or ManagedProcess(
            component_id, "UR5 Real Hardware", ["ros2", "launch"], None,
            status=ProcessStatus.RUNNING,
        )
    )
    result = controller.confirm_real_ur5_start()
    assert result.status == ProcessStatus.RUNNING
    assert calls == [("ur5_real", True)]
    assert controller.gui_config["enable_real_execution"] is False


def test_unified_launch_declares_all_public_arguments():
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "launch" / "hitl_system.launch.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "DeclareLaunchArgument" and node.args and isinstance(node.args[0], ast.Constant):
            names.add(node.args[0].value)
    assert names == {
        "gui_enabled", "rviz_enabled", "agent_enabled", "perception_enabled",
        "grasp_enabled", "simulation", "gui_port", "gui_host",
        "real_execution_enabled",
    }


def test_terminal_task_automatically_writes_complete_experiment_summary(tmp_path):
    controller = GuiController(log_root=tmp_path)
    controller.state.current_task_id = "task-stage10"
    controller.state.current_task_name = "pick and place the red cube"
    controller.state.experiment_metrics = TaskExperimentMetrics(
        task_started_at=utc_now(), human_wait_time_ms=125,
        replan_count=2, tool_failure_count=1, target_change_count=1,
        grasp_change_count=1,
    )
    controller.state.current_plan_version = 3
    controller.state.current_target_id = "red-cube"
    controller.state.current_grasp_candidate_id = "grasp-2"
    controller.state.current_trajectory_id = "trajectory-final"
    controller.state.tool_nodes.append(ToolNode(
        node_id="detect", parent_id=None, tool_name="detect_object",
        display_name="Detect", status=ToolStatus.SUCCEEDED, duration_ms=42,
    ))

    controller.complete_task()
    files = list(tmp_path.glob("*/task-stage10/task_summary.json"))
    assert len(files) == 1
    summary = json.loads(files[0].read_text(encoding="utf-8"))
    assert REQUIRED_SUMMARY_FIELDS <= summary.keys()
    assert summary["result"] == "SUCCESS"
    assert summary["number_of_user_modifications"] == 4
    assert summary["number_of_replans"] == 2
    assert summary["selected_target"] == "red-cube"
    assert summary["selected_grasp"] == "grasp-2"
    assert summary["final_trajectory_id"] == "trajectory-final"


def test_launch_overrides_keep_real_execution_disabled():
    controller = GuiController(config_overrides={
        "simulation": False,
        "agent_enabled": False,
        "perception_enabled": False,
        "grasp_enabled": False,
    })
    assert controller.state.robot_mode == "REAL ROBOT"
    assert controller.gui_config["enable_real_execution"] is False
    assert controller.runtime_backend_config.perception_mode == "mock"
    assert controller.runtime_backend_config.grasp_mode == "mock"


def test_real_execution_opt_in_is_scoped_to_controller_session():
    enabled = GuiController(config_overrides={"real_execution_enabled": True})
    default = GuiController()
    assert enabled.gui_config["enable_real_execution"] is True
    assert default.gui_config["enable_real_execution"] is False


def test_unrecoverable_failure_automatically_writes_failed_summary(tmp_path):
    controller = GuiController(log_root=tmp_path)
    controller.state.current_task_id = "task-failed"
    controller.state.current_task_name = "failed experiment"
    controller.state.experiment_metrics.task_started_at = utc_now()
    controller.fail_task("controlled test failure")
    path = next(tmp_path.glob("*/task-failed/task_summary.json"))
    summary = json.loads(path.read_text(encoding="utf-8"))
    assert summary["result"] == "FAILED"
