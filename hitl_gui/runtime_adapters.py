"""Unified mock and live sensor/grasp adapters for the GUI runtime.

The adapters intentionally cover only sensor-derived stages.  Motion planning
and execution remain owned by :mod:`trajectory_review_adapter`, so selecting a
live backend cannot bypass the existing MoveIt/HITL safety path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hitl_gui.robot_config import load_grasping_config


SENSOR_SKILLS = {
    "describe_scene",
    "detect_object",
    "detect_objects",
    "build_object_point_cloud",
    "generate_grasp_pose",
}


@dataclass(frozen=True)
class RuntimeBackendConfig:
    """Configured source for perception and grasp candidates."""

    perception_mode: str = "mock"
    grasp_mode: str = "mock"
    mock_perception: dict[str, Any] | None = None

    @classmethod
    def from_gui_config(cls, config: dict[str, Any]) -> "RuntimeBackendConfig":
        runtime = config.get("runtime_backends", {}) if isinstance(config, dict) else {}
        return cls(
            perception_mode=str(runtime.get("perception_mode", "mock")).lower(),
            grasp_mode=str(runtime.get("grasp_mode", "mock")).lower(),
            mock_perception=dict(runtime.get("mock_perception", {}) or {}),
        )

    @property
    def uses_live_ros(self) -> bool:
        return self.perception_mode == "ros"

    @property
    def uses_live_graspgenx(self) -> bool:
        return self.grasp_mode == "graspgenx"


class MockSensorGraspAdapter:
    """Reuse the core deterministic mock observations for reporting/demo mode."""

    source_label = "mock"

    def __init__(self, config: RuntimeBackendConfig) -> None:
        from llm_skill_robot.agent.mock_perception import MockPerceptionAdapter

        self._adapter = MockPerceptionAdapter(config.mock_perception)

    def execute(self, step, context: dict[str, Any]) -> dict[str, Any]:
        result = self._adapter.execute(step, context)
        if result is None:
            return _failure(step, "Mock adapter does not implement this tool.")
        return result


class ExistingRosSensorGraspAdapter:
    """Thin bridge to the project's existing D435i/SAM3/GraspGenX pipeline.

    No topic names, detector protocol, or GraspGenX command are invented here:
    ``create_perception_pipeline`` and ``execute_non_motion_skill`` retain
    ownership of those implementation details.
    """

    source_label = "ros"

    def __init__(self, node, config: RuntimeBackendConfig) -> None:
        if node is None:
            raise RuntimeError("ROS runtime backend requires an initialized ROS monitor node.")
        from llm_skill_robot.perception.object_point_cloud_builder import ObjectPointCloudBuilder
        from llm_skill_robot.perception.perception_factory import (
            create_perception_pipeline,
            load_perception_config,
        )
        from llm_skill_robot.grasping.graspgenx_grasp_planner import GraspGenXGraspPlanner
        from llm_skill_robot.grasping.graspgenx_subprocess_adapter import GraspGenXSubprocessAdapter

        self._config = config
        perception_config = load_perception_config()
        self._perception_pipeline = create_perception_pipeline(node=node, config=perception_config)
        grasp_document = load_grasping_config()
        grasp_config = grasp_document.get("grasping", grasp_document)
        self._point_cloud_builder = ObjectPointCloudBuilder(grasp_config.get("point_cloud", {}))
        self._grasp_planner = GraspGenXGraspPlanner(
            GraspGenXSubprocessAdapter(grasp_config.get("graspgenx_subprocess", {}))
        )

    def execute(self, step, context: dict[str, Any]) -> dict[str, Any]:
        from llm_skill_robot.ros_nl_rviz_sim_demo import execute_non_motion_skill

        if step.skill_id in {"describe_scene", "detect_object", "detect_objects", "build_object_point_cloud"} and self._config.perception_mode != "ros":
            return _failure(step, "Live perception is disabled by runtime_backends.perception_mode.")
        if step.skill_id == "generate_grasp_pose" and self._config.grasp_mode != "graspgenx":
            return _failure(step, "Live GraspGenX is disabled by runtime_backends.grasp_mode.")
        return execute_non_motion_skill(
            step,
            gripper_backend=None,
            context=context,
            perception_pipeline=self._perception_pipeline,
            point_cloud_builder=self._point_cloud_builder,
            grasp_planner=self._grasp_planner,
        )


class RuntimeAdapterRegistry:
    """Choose adapters explicitly; never silently replace a live request with mock data."""

    def __init__(self, config: RuntimeBackendConfig, *, ros_node=None) -> None:
        self.config = config
        self.ros_node = ros_node
        self._mock = MockSensorGraspAdapter(config)
        self._live: ExistingRosSensorGraspAdapter | None = None

    @property
    def mode_summary(self) -> str:
        return f"perception={self.config.perception_mode}, grasp={self.config.grasp_mode}"

    def execute(self, step, context: dict[str, Any]) -> dict[str, Any]:
        if step.skill_id not in SENSOR_SKILLS:
            return _failure(step, f"Runtime adapter does not handle {step.skill_id}.")
        if step.skill_id == "describe_scene" and self.config.perception_mode != "ros":
            return _failure(
                step,
                "describe_scene requires runtime_backends.perception_mode=ros; mock mode does not fabricate LLM scene descriptions.",
            )
        if step.skill_id == "generate_grasp_pose":
            live_requested = self.config.grasp_mode == "graspgenx"
        else:
            live_requested = self.config.perception_mode == "ros"
        if not live_requested:
            return self._mock.execute(step, context)
        try:
            if self._live is None:
                self._live = ExistingRosSensorGraspAdapter(self.ros_node, self.config)
            return self._live.execute(step, context)
        except Exception as exc:
            return _failure(step, f"Live {step.skill_id} backend is unavailable: {exc}")


def _failure(step, message: str) -> dict[str, Any]:
    return {
        "step_id": getattr(step, "step_id", "unknown"),
        "skill_id": getattr(step, "skill_id", "unknown"),
        "success": False,
        "status": "NOT_AVAILABLE",
        "message": message,
        "output": {},
    }
