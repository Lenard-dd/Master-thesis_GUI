from __future__ import annotations

import os
import shutil
import subprocess
import json


def build_command(component: dict) -> list[str]:
    if component["type"] == "ros2_launch":
        return ["ros2", "launch", component["package"], component["launch_file"], *component.get("arguments", [])]
    return ["conda", "run", "-n", component["conda_environment"], "--no-capture-output", component.get("executable", "python"), *component.get("arguments", [])]


def validate_component(component_id: str, component: dict, launcher: dict) -> str | None:
    if component["type"] == "ros2_launch":
        if os.environ.get("ROS_DISTRO") != "humble":
            return "ROS 2 Humble environment is not sourced."
        if not os.environ.get("AMENT_PREFIX_PATH") or not shutil.which("ros2"):
            return "ros2 command or AMENT_PREFIX_PATH is unavailable."
        return None
    if not shutil.which("conda"):
        return "conda command is unavailable."
    try:
        environments = json.loads(subprocess.run(["conda", "env", "list", "--json"], capture_output=True, text=True, timeout=5, check=True).stdout).get("envs", [])
        if not any(path.rstrip("/").endswith(f"/{component['conda_environment']}") for path in environments):
            return f"Conda environment does not exist: {component['conda_environment']}"
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return "Unable to verify the requested conda environment."
    workdir = component.get("working_directory", "")
    if not os.path.isdir(workdir):
        return f"Working directory does not exist: {workdir}"
    args = component.get("arguments", [])
    script = os.path.join(workdir, args[0]) if args else ""
    if not os.path.isfile(script):
        return f"Server script does not exist: {script}"
    for flag in ("--config", "--assets_dir"):
        if flag in args and not os.path.exists(args[args.index(flag) + 1]):
            return f"Required path does not exist: {args[args.index(flag) + 1]}"
    return None
