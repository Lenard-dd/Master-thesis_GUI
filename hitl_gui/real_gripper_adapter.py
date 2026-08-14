"""Lazy, safety-gated bridge to the project's existing real Robotiq backend."""

from __future__ import annotations

import threading
import uuid
from typing import Any, Callable


class RealGripperRuntimeAdapter:
    """Own a dedicated ROS node for synchronous real-gripper commands.

    The GUI monitor node belongs to ``RosWorker`` and must never be passed to
    a backend which calls ``spin_until_future_complete``.  This adapter creates
    a separate node, serializes commands, and executes only the already
    safety-gated backend shipped with ``llm_skill_robot``.
    """

    def __init__(
        self,
        *,
        backend_factory: Callable[..., Any] | None = None,
        node_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._backend_factory = backend_factory
        self._node_factory = node_factory
        self._backend = None
        self._node = None
        self._lock = threading.Lock()

    def execute(
        self,
        skill_id: str,
        parameters: dict[str, Any],
        *,
        confirmed: bool,
        contact_confirmation: str | None = None,
    ) -> dict[str, Any]:
        if skill_id not in {"open_gripper", "close_gripper"}:
            return self._failure(f"Unsupported real gripper skill: {skill_id}")
        if not confirmed:
            return self._failure("GUI confirmation was not available for this gripper request.", status="REJECTED")

        with self._lock:
            try:
                backend = self._ensure_backend()
                kwargs = {
                    key: value for key, value in dict(parameters or {}).items()
                    if key in {"width_m", "speed_mps", "force_N", "during_contact"}
                }
                if skill_id == "close_gripper" and kwargs.get("during_contact") is True:
                    kwargs["confirmation"] = contact_confirmation
                else:
                    kwargs["confirmed"] = True
                return getattr(backend, skill_id)(**kwargs)
            except Exception as exc:
                return self._failure(f"Real gripper backend failed safely: {exc}")

    def shutdown(self) -> None:
        with self._lock:
            node, self._node, self._backend = self._node, None, None
            if node is not None:
                try:
                    node.destroy_node()
                except Exception:
                    pass

    def _ensure_backend(self):
        if self._backend is not None:
            return self._backend

        import rclpy

        if not rclpy.ok():
            raise RuntimeError("ROS 2 is not initialized; start the GUI in ROS mode.")
        node_factory = self._node_factory or rclpy.create_node
        node = node_factory(f"hitl_gui_real_gripper_{uuid.uuid4().hex[:8]}")
        self._node = node
        try:
            if self._backend_factory is None:
                from llm_skill_robot.robot.robotiq_2f140_real_backend import (
                    Robotiq2F140RealBackend,
                )

                self._backend_factory = Robotiq2F140RealBackend
            self._backend = self._backend_factory(node=node)
        except Exception:
            self._node = None
            try:
                node.destroy_node()
            except Exception:
                pass
            raise
        return self._backend

    @staticmethod
    def _failure(message: str, *, status: str = "NOT_AVAILABLE") -> dict[str, Any]:
        return {
            "success": False,
            "status": status,
            "message": message,
            "output": {"command_sent": False, "mode": "real_hardware"},
        }
