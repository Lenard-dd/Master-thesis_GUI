"""Thread-safe ROS 2 monitor: callbacks update data only, never NiceGUI."""

from __future__ import annotations

import threading
import time


class RosBridge:
    def __init__(self, config: dict) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._data = {"joint_count": 0, "node_names": [], "service_names": [], "error": None}
        self.node = None

    def attach(self, node) -> None:
        from sensor_msgs.msg import Image, JointState
        from std_msgs.msg import Float32
        self.node = node
        node.create_subscription(JointState, self.config["joint_state_topic"], self._joint_callback, 10)
        node.create_subscription(Image, self.config["rgb_topic"], self._rgb_callback, 2)
        node.create_subscription(Image, self.config["depth_topic"], self._depth_callback, 2)
        node.create_subscription(Float32, self.config["gripper_state_topic"], self._gripper_callback, 10)
        node.create_timer(0.5, self._graph_callback)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._data)

    def _joint_callback(self, message) -> None:
        self._update(joint_last_time=time.monotonic(), joint_count=len(message.name))

    def _rgb_callback(self, _message) -> None:
        self._update(rgb_last_time=time.monotonic())

    def _depth_callback(self, _message) -> None:
        self._update(depth_last_time=time.monotonic())

    def _gripper_callback(self, _message) -> None:
        self._update(gripper_last_time=time.monotonic())

    def _graph_callback(self) -> None:
        if self.node is None:
            return
        try:
            self._update(
                node_names=[name for name, _namespace in self.node.get_node_names_and_namespaces()],
                service_names=[name for name, _types in self.node.get_service_names_and_types()],
            )
        except Exception as exc:
            self._update(error=str(exc))

    def _update(self, **values) -> None:
        with self._lock:
            self._data.update(values)
