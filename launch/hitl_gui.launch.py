"""Launch the standalone static HITL GUI prototype."""

from launch import LaunchDescription
from launch.actions import ExecuteProcess


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        ExecuteProcess(
            cmd=["ros2", "run", "hitl_gui", "hitl_gui"],
            output="screen",
        )
    ])
