"""In-memory trajectory cache used only when GUI runtime_backends is mock."""

from __future__ import annotations

from typing import Any


class MockMotionBackend:
    """Provide deterministic plan IDs/receipts without ROS, MoveIt, or robot I/O."""

    def __init__(self) -> None:
        self._count = 0
        self._plans: dict[str, dict[str, Any]] = {}

    def plan_to_named_target(self, target: str, **_kwargs) -> dict[str, Any]:
        return self._plan({"target_name": target, "target_type": "named_target"})

    def plan_to_pose(self, frame: str, position: dict, orientation: dict, **_kwargs) -> dict[str, Any]:
        return self._plan({"target_type": "pose", "target_pose": {
            "frame": frame, "position": dict(position), "orientation": dict(orientation),
        }})

    def _plan(self, summary: dict[str, Any]) -> dict[str, Any]:
        self._count += 1
        plan_id = f"mock-trajectory-{self._count}"
        summary.update({
            "plan_id": plan_id, "success": True, "message": "Mock trajectory planned.",
            "num_trajectory_points": 12, "duration_sec": 1.0,
        })
        result = {"success": True, "plan_id": plan_id, "summary": summary}
        self._plans[plan_id] = result
        return result

    def get_cached_plan(self, plan_id: str):
        return self._plans.get(plan_id)

    def execute_cached_plan_simulated(self, plan_id: str, **_kwargs) -> dict[str, Any]:
        if plan_id not in self._plans:
            return {"success": False, "message": "Mock trajectory is unavailable.", "plan_id": plan_id}
        return {"success": True, "message": "Mock trajectory execution completed.", "plan_id": plan_id,
                "execution_duration": 1.0}


class MockTrajectoryValidator:
    def validate_motion_plan_summary(self, _summary: dict[str, Any]) -> dict[str, Any]:
        return {"decision": "ALLOW", "reasons": [], "warnings": ["Mock trajectory validation."]}
