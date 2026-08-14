from __future__ import annotations

import os
import signal
import subprocess
import threading
import shutil
from datetime import datetime, timezone

from hitl_gui.launcher_config import build_command, validate_component
from hitl_gui.managed_process import ManagedProcess, ProcessStatus


class ComponentProcessManager:
    def __init__(
        self, launcher_config: dict, popen_factory=subprocess.Popen,
        killpg=os.killpg, conflict_checker=None,
    ) -> None:
        self.config = launcher_config
        self._popen = popen_factory
        self._killpg = killpg
        self._conflict_checker = conflict_checker
        self.processes: dict[str, ManagedProcess] = {}
        self._shutting_down = False

    def start_component(self, component_id: str, *, confirmed=False) -> ManagedProcess:
        component = self.config["components"][component_id]
        existing = self.processes.get(component_id)
        if existing and existing.process and existing.process.poll() is None:
            return existing
        if component.get("requires_confirmation") and not confirmed:
            return ManagedProcess(component_id, component["display_name"], [], None, status=ProcessStatus.ERROR, recent_output=["Real robot confirmation is required."])
        conflict = self._conflict_checker(component_id, component) if self._conflict_checker else None
        if conflict:
            managed = ManagedProcess(
                component_id, component["display_name"], build_command(component),
                component.get("working_directory"), status=ProcessStatus.ERROR,
                recent_output=[conflict],
            )
            self.processes[component_id] = managed
            return managed
        error = validate_component(component_id, component, self.config)
        command = build_command(component)
        managed = ManagedProcess(component_id, component["display_name"], command, component.get("working_directory"), status=ProcessStatus.STARTING)
        self.processes[component_id] = managed
        if error:
            managed.status, managed.recent_output = ProcessStatus.ERROR, [error]
            return managed
        env = os.environ.copy()
        env["ROS_DOMAIN_ID"] = str(self.config["ros_domain_id"])
        try:
            launch_command = self._with_parent_death_signal(command)
            managed.process = self._popen(launch_command, cwd=managed.working_directory or self.config["workspace_path"], env=env, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            managed.pid = managed.process.pid
            managed.process_group_id = managed.pid
            managed.start_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
            managed.started_by_gui, managed.status = True, ProcessStatus.RUNNING
            threading.Thread(target=self._read_output, args=(managed,), daemon=True).start()
        except OSError as exc:
            managed.status, managed.recent_output = ProcessStatus.ERROR, [str(exc)]
        return managed

    def stop_component(self, component_id: str) -> ManagedProcess | None:
        managed = self.processes.get(component_id)
        if not managed or not managed.started_by_gui or not managed.process:
            return managed
        if managed.process.poll() is None:
            managed.status = ProcessStatus.STOPPING
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
                try:
                    self._killpg(managed.process_group_id, sig)
                except ProcessLookupError:
                    break
                try:
                    managed.process.wait(timeout=5)
                    break
                except subprocess.TimeoutExpired:
                    continue
        managed.exit_code = managed.process.poll()
        managed.status = ProcessStatus.STOPPED
        return managed

    def restart_component(self, component_id: str, *, confirmed=False) -> ManagedProcess:
        self.stop_component(component_id)
        return self.start_component(component_id, confirmed=confirmed)

    def refresh(self) -> dict[str, ManagedProcess]:
        for managed in self.processes.values():
            if managed.process and managed.process.poll() is not None and managed.status == ProcessStatus.RUNNING:
                managed.exit_code, managed.status = managed.process.poll(), ProcessStatus.EXITED
        return self.processes

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        # Stop independent process groups concurrently. Sequential 15-second
        # escalation windows can exceed a web-server shutdown deadline and
        # leave the later components orphaned.
        threads = [
            threading.Thread(target=self.stop_component, args=(component_id,))
            for component_id in list(self.processes)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=16)

    def _with_parent_death_signal(self, command: list[str]) -> list[str]:
        signal_name = str(self.config.get("parent_death_signal", "")).strip()
        setpriv = shutil.which("setpriv")
        if not signal_name or not setpriv:
            return command
        return [setpriv, "--pdeathsig", signal_name, "--", *command]

    @staticmethod
    def _read_output(managed: ManagedProcess) -> None:
        if not managed.process or not managed.process.stdout:
            return
        for line in managed.process.stdout:
            managed.recent_output.append(line.rstrip())
            del managed.recent_output[:-200]
