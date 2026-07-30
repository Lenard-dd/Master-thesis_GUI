from hitl_gui.rviz_process_manager import RvizProcessManager


class FakeProcess:
    def __init__(self):
        self.running = True
        self.terminated = False

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.terminated = True
        self.running = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.running = False


def test_start_does_not_create_duplicate_process(tmp_path):
    config = tmp_path / "view.rviz"
    config.write_text("Visualization Manager:", encoding="utf-8")
    created = []

    def factory(*args, **kwargs):
        created.append((args, kwargs))
        return FakeProcess()

    manager = RvizProcessManager(config, popen_factory=factory)
    assert manager.start_rviz()["status"] == "RUNNING"
    assert manager.start_rviz()["message"] == "RViz is already running."
    assert len(created) == 1
    assert created[0][0][0] == ["rviz2", "-d", str(config)]


def test_invalid_config_path_reports_error(tmp_path):
    manager = RvizProcessManager(tmp_path / "missing.rviz", popen_factory=lambda *_args, **_kwargs: FakeProcess())
    result = manager.start_rviz()
    assert result["status"] == "ERROR"
    assert "does not exist" in result["error"]


def test_stop_only_handles_gui_managed_process(tmp_path):
    config = tmp_path / "view.rviz"
    config.write_text("Visualization Manager:", encoding="utf-8")
    process = FakeProcess()
    manager = RvizProcessManager(config, popen_factory=lambda *_args, **_kwargs: process)
    manager.start_rviz()
    assert manager.stop_rviz()["status"] == "STOPPED"
    assert process.terminated

    unmanaged = FakeProcess()
    manager.stop_rviz()
    assert not unmanaged.terminated
