from __future__ import annotations

import signal
from pathlib import Path

from hitl_gui.panels.embedded_rviz_panel import load_embedded_rviz_config
from hitl_gui.services.embedded_rviz_manager import EmbeddedRvizManager


class FakeProcess:
    next_pid = 1000

    def __init__(self, exit_code=None):
        self.exit_code = exit_code
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.wait_calls = []
        self.stdout = None

    def poll(self):
        return self.exit_code

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.exit_code = 0
        return 0


def config(tmp_path: Path) -> dict:
    rviz = tmp_path / "view.rviz"
    rviz.write_text("Visualization Manager:", encoding="utf-8")
    web = tmp_path / "novnc"
    web.mkdir()
    return {
        "enabled": True, "display": ":145", "screen_resolution": "1280x800x24",
        "vnc_host": "127.0.0.1", "vnc_port": 15901,
        "novnc_host": "127.0.0.1", "novnc_port": 16080,
        "novnc_web_root": str(web), "rviz_config": str(rviz),
        "software_rendering": True,
    }


def test_starts_in_required_order_and_does_not_duplicate(tmp_path):
    created = []

    def popen(command, **kwargs):
        created.append((command, kwargs))
        return FakeProcess()

    manager = EmbeddedRvizManager(config(tmp_path), popen_factory=popen,
                                  which=lambda name: "/usr/bin/" + name, sleep=lambda _: None,
                                  port_checker=lambda: None)
    assert manager.start() is True
    assert [command[0] for command, _ in created] == ["Xvfb", "rviz2", "x11vnc", "novnc_proxy"]
    assert created[0][0] == ["Xvfb", ":145", "-screen", "0", "1280x800x24", "-ac", "-nolisten", "tcp"]
    assert created[1][1]["env"]["DISPLAY"] == ":145"
    assert created[1][1]["env"]["LIBGL_ALWAYS_SOFTWARE"] == "1"
    assert all(kwargs["start_new_session"] for _, kwargs in created)
    assert manager.start() is True
    assert len(created) == 4


def test_failure_cleans_only_previously_started_processes(tmp_path):
    created, signals = [], []

    def popen(command, **_kwargs):
        process = FakeProcess(exit_code=2 if command[0] == "rviz2" else None)
        created.append(process)
        return process

    manager = EmbeddedRvizManager(config(tmp_path), popen_factory=popen,
                                  which=lambda name: "/usr/bin/" + name,
                                  killpg=lambda pid, sig: signals.append((pid, sig)), sleep=lambda _: None,
                                  port_checker=lambda: None)
    assert manager.start() is False
    assert manager.get_status()["status"] == "ERROR"
    assert len(created) == 2
    assert signals == [(created[0].pid, signal.SIGINT)]
    assert manager.xvfb_process is None


def test_stop_only_signals_gui_owned_processes_in_reverse_order(tmp_path):
    signals = []

    manager = EmbeddedRvizManager(config(tmp_path), popen_factory=lambda *_a, **_k: FakeProcess(),
                                  which=lambda name: "/usr/bin/" + name,
                                  killpg=lambda pid, sig: signals.append((pid, sig)), sleep=lambda _: None,
                                  port_checker=lambda: None)
    assert manager.start()
    owned = [manager.xvfb_process, manager.rviz_process, manager.x11vnc_process, manager.novnc_process]
    manager.stop()
    assert [pid for pid, _ in signals] == [process.pid for process in reversed(owned)]
    assert all(sig is signal.SIGINT for _, sig in signals)
    assert manager.get_status()["status"] == "STOPPED"


def test_websockify_fallback_is_explicitly_localhost(tmp_path):
    manager = EmbeddedRvizManager(config(tmp_path), which=lambda name: None if name == "novnc_proxy" else "/usr/bin/" + name)
    assert manager._novnc_command()[-2:] == ["localhost:16080", "localhost:15901"]


def test_iframe_url_is_read_from_config_and_panel_html_uses_it(tmp_path):
    gui = tmp_path / "gui_config.yaml"
    url = "http://127.0.0.1:6080/vnc.html?autoconnect=true"
    gui.write_text("embedded_rviz:\n  iframe_url: '" + url + "'\n", encoding="utf-8")
    assert load_embedded_rviz_config(gui)["iframe_url"] == url


def test_embedded_only_config_hides_both_rviz_docks():
    config = Path(__file__).resolve().parents[1] / "config" / "embedded_robot_only.rviz"
    content = config.read_text(encoding="utf-8")
    assert "Hide Left Dock: true" in content
    assert "Hide Right Dock: true" in content
    assert "QMainWindow State:" not in content
    assert content.startswith("Panels: []\n")


def test_embedded_config_enables_camera_pointcloud2_by_default():
    import yaml

    config = Path(__file__).resolve().parents[1] / "config" / "embedded_robot_only.rviz"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    displays = data["Visualization Manager"]["Displays"]
    pointcloud = next(item for item in displays if item.get("Class") == "rviz_default_plugins/PointCloud2")
    assert pointcloud["Enabled"] is True
    assert pointcloud["Name"] == "Camera Point Cloud"
    assert pointcloud["Size (Pixels)"] == 3
    assert pointcloud["Topic"]["Value"] == "/camera/camera/depth/color/points"
    assert pointcloud["Topic"]["Reliability Policy"] == "Best Effort"


def test_reload_only_replaces_iframe_content_without_starting_manager(tmp_path):
    from hitl_gui.panels.embedded_rviz_panel import EmbeddedRvizPanel

    class Manager:
        starts = 0

        def start(self):
            self.starts += 1

    class Iframe:
        content = ""
        updates = 0

        def update(self):
            self.updates += 1

    manager, iframe = Manager(), Iframe()
    panel = EmbeddedRvizPanel(manager, "http://127.0.0.1:6080/vnc.html")
    panel._iframe = iframe
    panel.reload()
    assert manager.starts == 0
    assert iframe.updates == 1
    assert "127.0.0.1:6080" in iframe.content


def test_running_panel_badge_uses_positive_background_color():
    from nicegui import ui
    from hitl_gui.panels.embedded_rviz_panel import EmbeddedRvizPanel

    class Manager:
        def get_status(self):
            return {"status": "RUNNING", "running": True, "error": None}

    panel = EmbeddedRvizPanel(Manager(), "http://127.0.0.1:6080/vnc.html")
    panel._status = ui.badge("STOPPED", color="grey")
    panel._message = ui.label()
    panel._refresh()
    assert panel._status.text == "RUNNING"
    assert panel._status._props["color"] == "positive"
