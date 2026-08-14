from hitl_gui.component_process_manager import ComponentProcessManager
from hitl_gui.managed_process import ProcessStatus
from hitl_gui.gui_controller import GuiController
from hitl_gui.app_state import SystemComponentStatus
import asyncio
import signal


class FakeProcess:
    pid = 4321
    stdout = []
    def __init__(self):
        self.running = True
        self.signals = []
    def poll(self):
        return None if self.running else 0
    def wait(self, timeout=None):
        self.running = False
        return 0


def config(tmp_path):
    return {"ros_domain_id": 27, "workspace_path": str(tmp_path), "components": {
        "ur5_fake": {"type": "ros2_launch", "display_name": "Fake", "package": "p", "launch_file": "f", "arguments": []},
        "ur5_real": {"type": "ros2_launch", "display_name": "Real", "package": "p", "launch_file": "f", "arguments": [], "requires_confirmation": True},
    }}


def test_duplicate_and_real_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("ROS_DISTRO", "humble")
    monkeypatch.setenv("AMENT_PREFIX_PATH", "x")
    monkeypatch.setattr("hitl_gui.launcher_config.shutil.which", lambda _: "/bin/true")
    made = []
    manager = ComponentProcessManager(config(tmp_path), popen_factory=lambda *a, **k: made.append(FakeProcess()) or made[-1], killpg=lambda *a: None)
    assert manager.start_component("ur5_real").status == ProcessStatus.ERROR
    assert manager.start_component("ur5_fake").status == ProcessStatus.RUNNING
    assert manager.start_component("ur5_fake").status == ProcessStatus.RUNNING
    assert len(made) == 1


def test_stop_uses_sigint_and_external_is_not_stopped(monkeypatch, tmp_path):
    monkeypatch.setenv("ROS_DISTRO", "humble")
    monkeypatch.setenv("AMENT_PREFIX_PATH", "x")
    monkeypatch.setattr("hitl_gui.launcher_config.shutil.which", lambda _: "/bin/true")
    signals = []
    process = FakeProcess()
    manager = ComponentProcessManager(config(tmp_path), popen_factory=lambda *a, **k: process, killpg=lambda _pgid, sig: signals.append(sig))
    manager.start_component("ur5_fake")
    manager.stop_component("ur5_fake")
    assert signals[0].name == "SIGINT"
    assert manager.stop_component("unknown") is None


def test_invalid_external_path_returns_error(tmp_path):
    broken = config(tmp_path)
    broken["components"]["graspgenx"] = {"type": "external_process", "display_name": "GX", "conda_environment": "gx", "working_directory": str(tmp_path / "missing"), "executable": "python", "arguments": ["server.py"]}
    manager = ComponentProcessManager(broken)
    assert manager.start_component("graspgenx").status == ProcessStatus.ERROR


def test_simulation_launch_waits_for_ur5_health_before_rviz(monkeypatch):
    async def scenario():
        controller = GuiController()
        order = []
        class Result:
            status = ProcessStatus.RUNNING
        controller.start_component = lambda component_id, **_kwargs: order.append(component_id) or Result()
        controller.start_rviz = lambda: order.append("rviz") or {"status": "RUNNING"}
        controller.consume_ros_status = lambda: controller.state.hardware_status.__setitem__("UR5", SystemComponentStatus.READY)
        task = controller.start_simulation_components()
        await task
        assert order == ["ur5_fake", "rviz", "camera", "gripper", "graspgenx"]
        assert controller.state.simulation_launch_status == "COMPLETED"
    asyncio.run(scenario())


def test_existing_ros_node_blocks_duplicate_without_stopping_external_process(tmp_path):
    manager = ComponentProcessManager(
        config(tmp_path), conflict_checker=lambda component_id, _component: (
            "existing /move_group detected" if component_id == "ur5_real" else None
        ),
    )
    result = manager.start_component("ur5_real", confirmed=True)
    assert result.status == ProcessStatus.ERROR
    assert "existing /move_group" in result.recent_output[0]
    assert result.started_by_gui is False
    assert manager.stop_component("ur5_real") is result


def test_parent_death_signal_wraps_child_command(monkeypatch, tmp_path):
    monkeypatch.setenv("ROS_DISTRO", "humble")
    monkeypatch.setenv("AMENT_PREFIX_PATH", "x")
    monkeypatch.setattr("hitl_gui.launcher_config.shutil.which", lambda _: "/bin/true")
    monkeypatch.setattr("hitl_gui.component_process_manager.shutil.which", lambda _: "/usr/bin/setpriv")
    settings = config(tmp_path)
    settings["parent_death_signal"] = "SIGINT"
    captured = []
    manager = ComponentProcessManager(
        settings,
        popen_factory=lambda command, **_kwargs: captured.append(command) or FakeProcess(),
        killpg=lambda *_args: None,
    )
    manager.start_component("ur5_fake")
    assert captured[0][:4] == ["/usr/bin/setpriv", "--pdeathsig", "SIGINT", "--"]
    manager.shutdown()


def test_shutdown_is_idempotent_and_starts_component_cleanup_concurrently(monkeypatch, tmp_path):
    monkeypatch.setenv("ROS_DISTRO", "humble")
    monkeypatch.setenv("AMENT_PREFIX_PATH", "x")
    monkeypatch.setattr("hitl_gui.launcher_config.shutil.which", lambda _: "/bin/true")
    stopped = []
    manager = ComponentProcessManager(config(tmp_path))
    manager.processes = {
        "ur5_fake": FakeProcess(),
        "ur5_real": FakeProcess(),
    }
    manager.stop_component = lambda component_id: stopped.append(component_id)
    manager.shutdown()
    manager.shutdown()
    assert sorted(stopped) == ["ur5_fake", "ur5_real"]
