"""GUI adapter around the existing MoveIt plan cache, review and execution APIs.

This module deliberately owns no controller/action client. It only delegates to
``UR5MoveItPlanBackend`` (or a compatible injected backend), which remains the
sole component that can execute a cached trajectory.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any


@dataclass
class TrajectoryRecord:
    trajectory_id: str
    plan_version: int
    target_summary: str
    planning_result: dict[str, Any]
    validation_result: dict[str, Any]
    planning_time_ms: int
    invalidated: bool = False

    @property
    def summary(self) -> dict[str, Any]:
        return self.planning_result.get("summary", {})


class ExistingTrajectoryReviewAdapter:
    """Use existing MoveIt, validation, RViz preview and execution interfaces."""

    def __init__(self, backend, validator, visualizer=None) -> None:
        self.backend = backend
        self.validator = validator
        self.visualizer = visualizer
        self.records: dict[str, TrajectoryRecord] = {}
        self.run_in_worker = True

    def plan_named_target(self, target: str, plan_version: int) -> TrajectoryRecord:
        started = monotonic()
        result = self.backend.plan_to_named_target(
            target=target, velocity_scale=0.03, acceleration_scale=0.03,
            skill_id="move_to_named_target",
        )
        elapsed_ms = int((monotonic() - started) * 1000)
        summary = result.get("summary", {})
        validation = self.validator.validate_motion_plan_summary(summary)
        trajectory_id = str(result.get("plan_id") or summary.get("plan_id") or "")
        if not trajectory_id:
            raise RuntimeError("Existing MoveIt backend returned no plan_id.")
        record = TrajectoryRecord(
            trajectory_id=trajectory_id,
            plan_version=plan_version,
            target_summary=f"Named target: {target}",
            planning_result=result,
            validation_result=validation,
            planning_time_ms=elapsed_ms,
        )
        self.records[trajectory_id] = record
        return record

    def preview(self, trajectory_id: str) -> dict[str, Any]:
        record = self._active_record(trajectory_id)
        if record is None:
            return {"success": False, "message": "Trajectory is unavailable or invalidated."}
        if self.visualizer is None:
            return {"success": False, "message": "RViz trajectory visualizer is not configured."}
        return self.visualizer.publish_preview(self.backend.get_cached_plan(trajectory_id))

    def execute(self, trajectory_id: str, *, real_robot: bool) -> dict[str, Any]:
        record = self._active_record(trajectory_id)
        if record is None:
            return {"success": False, "message": "Trajectory is unavailable or invalidated.", "plan_id": trajectory_id}
        if record.validation_result.get("decision") != "ALLOW":
            return {"success": False, "message": "Trajectory validation did not allow execution.", "plan_id": trajectory_id}
        if real_robot:
            return self.backend.execute_cached_plan_real(trajectory_id, final_confirmed=True)
        return self.backend.execute_cached_plan_simulated(
            trajectory_id,
            final_confirmed=True,
            trajectory_validation_allowed=True,
            trajectory_review_approved=True,
        )

    def invalidate(self, trajectory_id: str) -> None:
        record = self.records.get(trajectory_id)
        if record is not None:
            record.invalidated = True

    def _active_record(self, trajectory_id: str) -> TrajectoryRecord | None:
        record = self.records.get(trajectory_id)
        return record if record is not None and not record.invalidated else None
