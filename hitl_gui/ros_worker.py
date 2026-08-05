"""Background executor lifecycle for optional ROS monitoring mode."""

from __future__ import annotations

import threading

from hitl_gui.ros_bridge import RosBridge


class RosWorker:
    def __init__(self, config: dict) -> None:
        self.bridge = RosBridge(config)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.executor_running = False
        self.node_initialized = False
        self.error: str | None = None
        self._executor = None
        self._node = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="hitl-gui-ros-monitor", daemon=True)
        self._thread.start()
        return True

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._executor:
            self._executor.wake()
        if self._thread:
            self._thread.join(timeout=3)

    def snapshot(self) -> dict:
        data = self.bridge.snapshot()
        data.update({"executor_running": self.executor_running, "node_initialized": self.node_initialized, "worker_error": self.error})
        return data

    @property
    def node(self):
        """ROS monitor node, exposed read-only for existing runtime adapters.

        Callers must treat it as a shared ROS resource and never spin it.
        The worker remains the only owner of the executor lifecycle.
        """
        return self._node

    def _run(self) -> None:
        try:
            import rclpy
            from rclpy.executors import MultiThreadedExecutor
            rclpy.init(args=None)
            self._node = rclpy.create_node("hitl_gui_ros_monitor")
            self.bridge.attach(self._node)
            self._executor = MultiThreadedExecutor()
            self._executor.add_node(self._node)
            self.node_initialized = True
            self.executor_running = True
            while not self._stop_event.is_set():
                self._executor.spin_once(timeout_sec=0.2)
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.executor_running = False
            if self._executor and self._node:
                self._executor.remove_node(self._node)
            if self._executor:
                self._executor.shutdown()
            if self._node:
                self._node.destroy_node()
            try:
                import rclpy
                if rclpy.ok():
                    rclpy.shutdown()
            except Exception:
                pass
