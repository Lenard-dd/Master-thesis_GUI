"""Convert ROS callback timestamps into GUI-safe monitoring summaries."""

from __future__ import annotations

import time

from hitl_gui.app_state import SystemComponentStatus


def heartbeat_status(last_timestamp: float | None, ready_age: float, warning_age: float) -> tuple[SystemComponentStatus, float | None]:
    if last_timestamp is None:
        return SystemComponentStatus.DISCONNECTED, None
    age = max(0.0, time.monotonic() - last_timestamp)
    if age < ready_age:
        return SystemComponentStatus.READY, age
    if age <= warning_age:
        return SystemComponentStatus.WARNING, age
    return SystemComponentStatus.DISCONNECTED, age


def component_status(snapshot: dict, ready_age: float, warning_age: float) -> dict:
    monitor_active = bool(
        snapshot.get("executor_running") and snapshot.get("node_initialized")
    )
    # An inactive executor cannot validate cached heartbeat timestamps or ROS
    # graph entries.  Discard them so the UI never presents stale READY state.
    live_snapshot = snapshot if monitor_active else {}
    joint_status, joint_age = heartbeat_status(live_snapshot.get("joint_last_time"), ready_age, warning_age)
    camera_last = max(filter(None, [live_snapshot.get("rgb_last_time"), live_snapshot.get("depth_last_time")]), default=None)
    camera_status, camera_age = heartbeat_status(camera_last, ready_age, warning_age)
    gripper_status, gripper_age = heartbeat_status(live_snapshot.get("gripper_last_time"), ready_age, warning_age)
    nodes = set(live_snapshot.get("node_names", []))
    services = set(live_snapshot.get("service_names", []))
    return {
        "UR5": {"status": joint_status, "last_message_age": joint_age, "joint_count": live_snapshot.get("joint_count", 0)},
        "D435i": {"status": camera_status, "last_frame_age": camera_age},
        "Robotiq 2F-140": {"status": gripper_status, "last_message_age": gripper_age},
        "MoveIt": {"status": SystemComponentStatus.READY if "move_group" in nodes or "/move_group" in nodes else SystemComponentStatus.DISCONNECTED,
                   "planning_scene_available": "/get_planning_scene" in services},
        "SAM3": {"status": SystemComponentStatus.READY if any("sam3" in name.lower() for name in nodes) else SystemComponentStatus.UNKNOWN},
        "GraspGenX": {"status": SystemComponentStatus.READY if any("graspgenx" in name.lower() for name in nodes) else SystemComponentStatus.UNKNOWN},
        "controller_manager": {"active": any("list_controllers" in service for service in services)},
    }
