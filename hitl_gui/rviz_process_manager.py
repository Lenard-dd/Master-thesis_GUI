"""Own and supervise only the RViz process created by this GUI."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

import yaml


def load_gui_config() -> dict:
    """Load gui_config.yaml for source and installed runs."""
    try:
        from ament_index_python.packages import get_package_share_directory
        config_file = Path(get_package_share_directory("hitl_gui")) / "config" / "gui_config.yaml"
    except Exception:
        config_file = Path(__file__).resolve().parents[1] / "config" / "gui_config.yaml"
    return yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}


def load_rviz_settings() -> dict:
    return load_gui_config().get("rviz", {})


class RvizProcessManager:
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    ERROR = "ERROR"

    def __init__(
        self,
        rviz_config_path: str | Path,
        executable: str = "rviz2",
        popen_factory: Callable = subprocess.Popen,
    ) -> None:
        self.rviz_config_path = Path(rviz_config_path)
        self.executable = executable
        self._popen_factory = popen_factory
        self._process = None
        self._status = self.STOPPED
        self._error: str | None = None

    def start_rviz(self) -> dict:
        if self.is_running():
            return self.get_process_status(message="RViz is already running.")
        if not self.rviz_config_path.is_file():
            self._status = self.ERROR
            self._error = f"RViz config does not exist: {self.rviz_config_path}"
            return self.get_process_status()
        self._status = self.STARTING
        self._error = None
        try:
            self._process = self._popen_factory(
                [self.executable, "-d", str(self.rviz_config_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._status = self.RUNNING if self._process.poll() is None else self.ERROR
            if self._status == self.ERROR:
                self._error = "RViz exited immediately after launch."
        except (OSError, subprocess.SubprocessError) as exc:
            self._process = None
            self._status = self.ERROR
            self._error = f"Unable to start RViz: {exc}"
        return self.get_process_status()

    def stop_rviz(self) -> dict:
        if self._process is None:
            self._status = self.STOPPED
            return self.get_process_status(message="No GUI-managed RViz process is running.")
        if self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            except (OSError, subprocess.SubprocessError) as exc:
                self._status = self.ERROR
                self._error = f"Unable to stop GUI-managed RViz: {exc}"
                return self.get_process_status()
        self._process = None
        self._status = self.STOPPED
        self._error = None
        return self.get_process_status(message="GUI-managed RViz stopped.")

    def restart_rviz(self) -> dict:
        self.stop_rviz()
        return self.start_rviz()

    def is_running(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            self._status = self.RUNNING
            return True
        if self._process is not None and self._status != self.ERROR:
            self._process = None
            self._status = self.STOPPED
        return False

    def get_process_status(self, message: str | None = None) -> dict:
        self.is_running()
        return {
            "status": self._status,
            "running": self._status == self.RUNNING,
            "config_path": str(self.rviz_config_path),
            "error": self._error,
            "message": message,
        }
