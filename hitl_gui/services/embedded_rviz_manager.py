"""Own the local Xvfb -> RViz -> VNC -> noVNC process chain.

This module deliberately has no ROS or NiceGUI dependency. It only terminates
process groups it created itself, leaving manual RViz/VNC/ROS processes alone.
"""

from __future__ import annotations

from collections import deque
from enum import Enum
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import threading
import time
from typing import Any, Callable


class EmbeddedRvizStatus(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    ERROR = "ERROR"


class EmbeddedRvizManager:
    """Start and stop one localhost-only embedded RViz session."""

    PROCESS_NAMES = ("xvfb", "rviz", "x11vnc", "novnc")

    def __init__(self, config: dict[str, Any], *, popen_factory: Callable[..., Any] = subprocess.Popen,
                 which: Callable[[str], str | None] = shutil.which,
                 killpg: Callable[[int, signal.Signals], None] = os.killpg,
                 sleep: Callable[[float], None] = time.sleep,
                 port_checker: Callable[[], None] | None = None) -> None:
        self.config, self._popen, self._which = dict(config), popen_factory, which
        self._killpg, self._sleep, self._lock = killpg, sleep, threading.RLock()
        self._port_checker = port_checker or self._ensure_unused_ports
        self.status, self.error = EmbeddedRvizStatus.STOPPED, None
        self.xvfb_process = self.rviz_process = self.x11vnc_process = self.novnc_process = None
        self._logs = {name: deque(maxlen=100) for name in self.PROCESS_NAMES}

    def start(self) -> bool:
        """Start the chain in order; failures clean up only this instance."""
        with self._lock:
            if self.is_running():
                return True
            self.stop()
            self.status, self.error = EmbeddedRvizStatus.STARTING, None
            try:
                self._validate_prerequisites()
                self._port_checker()
                self.xvfb_process = self._launch("xvfb", self._xvfb_command())
                self._wait_alive("Xvfb", self.xvfb_process, 0.6)
                env = os.environ.copy()
                env["DISPLAY"] = str(self.config.get("display", ":99"))
                if self.config.get("software_rendering", True):
                    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
                self.rviz_process = self._launch("rviz", ["rviz2", "-d", str(self.config["rviz_config"])], env=env)
                self._wait_alive("RViz", self.rviz_process, 0.8)
                self.x11vnc_process = self._launch("x11vnc", self._x11vnc_command())
                self._wait_alive("x11vnc", self.x11vnc_process, 0.5)
                self.novnc_process = self._launch("novnc", self._novnc_command())
                self._wait_alive("noVNC", self.novnc_process, 0.5)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
                self._stop_owned_processes()
                self.status = EmbeddedRvizStatus.ERROR
                return False
            self.status = EmbeddedRvizStatus.RUNNING
            return True

    def stop(self) -> None:
        """Stop GUI-owned processes in reverse dependency order."""
        with self._lock:
            self._stop_owned_processes()
            self.status, self.error = EmbeddedRvizStatus.STOPPED, None

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def cleanup(self) -> None:
        self.stop()

    def is_running(self) -> bool:
        with self._lock:
            processes = (self.xvfb_process, self.rviz_process, self.x11vnc_process, self.novnc_process)
            if all(process is not None and process.poll() is None for process in processes):
                self.status = EmbeddedRvizStatus.RUNNING
                return True
            if any(process is not None and process.poll() is not None for process in processes) and self.status == EmbeddedRvizStatus.RUNNING:
                self.status, self.error = EmbeddedRvizStatus.ERROR, "An embedded RViz process exited unexpectedly."
            return False

    def get_status(self) -> dict[str, Any]:
        self.is_running()
        return {"status": self.status.value, "running": self.status is EmbeddedRvizStatus.RUNNING,
                "error": self.error,
                "processes": {name: (getattr(self, f"{name}_process") is not None and
                                     getattr(self, f"{name}_process").poll() is None) for name in self.PROCESS_NAMES}}

    def get_error(self) -> str | None:
        return self.error

    def get_logs(self) -> dict[str, list[str]]:
        return {name: list(lines) for name, lines in self._logs.items()}

    def _validate_prerequisites(self) -> None:
        if not self.config.get("enabled", True):
            raise ValueError("Embedded RViz is disabled in gui_config.yaml.")
        for key in ("vnc_host", "novnc_host"):
            if self.config.get(key, "127.0.0.1") != "127.0.0.1":
                raise ValueError(f"{key} must be 127.0.0.1 for the local-only demo.")
        config_path = Path(str(self.config.get("rviz_config", "")))
        if not config_path.is_file():
            raise ValueError(f"RViz configuration was not found: {config_path}")
        required = ["Xvfb", "rviz2", "x11vnc"]
        if not self._which("novnc_proxy"):
            required.append("websockify")
            if not Path(str(self.config.get("novnc_web_root", ""))).is_dir():
                raise ValueError(f"noVNC web root was not found: {self.config.get('novnc_web_root')}")
        missing = [command for command in required if not self._which(command)]
        if missing:
            raise ValueError(f"Missing command(s): {', '.join(missing)}")
        display = str(self.config.get("display", ":99"))
        if not display.startswith(":") or not display[1:].isdigit():
            raise ValueError(f"Invalid X display: {display}")
        number = display[1:]
        if Path(f"/tmp/.X11-unix/X{number}").exists() or Path(f"/tmp/.X{number}-lock").exists():
            raise ValueError(f"X display {display} is already in use.")

    def _ensure_unused_ports(self) -> None:
        for host_key, port_key in (("vnc_host", "vnc_port"), ("novnc_host", "novnc_port")):
            host, port = str(self.config.get(host_key, "127.0.0.1")), int(self.config[port_key])
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((host, port))
                except OSError:
                    raise RuntimeError(f"Port {host}:{port} is already in use; no external process was stopped.")

    def _xvfb_command(self) -> list[str]:
        return ["Xvfb", str(self.config.get("display", ":99")), "-screen", "0", str(self.config.get("screen_resolution", "1280x800x24")), "-ac", "-nolisten", "tcp"]

    def _x11vnc_command(self) -> list[str]:
        return ["x11vnc", "-display", str(self.config.get("display", ":99")), "-rfbport", str(self.config.get("vnc_port", 5901)), "-localhost", "-forever", "-shared", "-nopw", "-xkb"]

    def _novnc_command(self) -> list[str]:
        target, listen = f"localhost:{int(self.config.get('vnc_port', 5901))}", f"localhost:{int(self.config.get('novnc_port', 6080))}"
        if self._which("novnc_proxy"):
            return ["novnc_proxy", "--vnc", target, "--listen", listen]
        return ["websockify", "--web", str(self.config["novnc_web_root"]), listen, target]

    def _launch(self, name: str, command: list[str], *, env: dict[str, str] | None = None):
        process = self._popen(command, env=env, start_new_session=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._read_output, args=(name, process), daemon=True).start()
        return process

    def _read_output(self, name: str, process: Any) -> None:
        stream = getattr(process, "stdout", None)
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                self._logs[name].append(line.rstrip())
        finally:
            stream.close()

    def _wait_alive(self, name: str, process: Any, delay: float) -> None:
        self._sleep(delay)
        if process.poll() is not None:
            details = "; ".join(list(self._logs.get(name.lower(), ()))[-5:])
            raise RuntimeError(f"{name} failed to start (exit code {process.poll()})" + (f": {details}" if details else ""))

    def _stop_owned_processes(self) -> None:
        for name in reversed(self.PROCESS_NAMES):
            process = getattr(self, f"{name}_process")
            if process is not None:
                self._terminate_process_group(process)
                setattr(self, f"{name}_process", None)

    def _terminate_process_group(self, process: Any) -> None:
        if process.poll() is not None or getattr(process, "pid", None) is None:
            return
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            try:
                self._killpg(process.pid, sig)
            except ProcessLookupError:
                return
            try:
                process.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                continue
