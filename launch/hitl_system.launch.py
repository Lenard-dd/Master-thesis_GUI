"""Unified, simulation-safe launcher for the HITL system.

Agent, perception and grasp flags select the existing in-process adapters used
by the GUI. They do not create duplicate Agent or algorithm implementations.
RViz remains an independent ROS process with its own lifecycle.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    gui_enabled = LaunchConfiguration("gui_enabled")
    rviz_enabled = LaunchConfiguration("rviz_enabled")
    agent_enabled = LaunchConfiguration("agent_enabled")
    perception_enabled = LaunchConfiguration("perception_enabled")
    grasp_enabled = LaunchConfiguration("grasp_enabled")
    simulation = LaunchConfiguration("simulation")
    real_execution_enabled = LaunchConfiguration("real_execution_enabled")
    gui_host = LaunchConfiguration("gui_host")
    gui_port = LaunchConfiguration("gui_port")
    rviz_config = PathJoinSubstitution([
        FindPackageShare("hitl_gui"), "config", "embedded_robot_only.rviz",
    ])

    arguments = [
        DeclareLaunchArgument("gui_enabled", default_value="true"),
        DeclareLaunchArgument("rviz_enabled", default_value="false"),
        DeclareLaunchArgument("agent_enabled", default_value="false"),
        DeclareLaunchArgument("perception_enabled", default_value="false"),
        DeclareLaunchArgument("grasp_enabled", default_value="false"),
        DeclareLaunchArgument("simulation", default_value="false"),
        DeclareLaunchArgument("real_execution_enabled", default_value="false"),
        DeclareLaunchArgument("gui_host", default_value="127.0.0.1"),
        DeclareLaunchArgument("gui_port", default_value="8080"),
    ]

    gui = Node(
        package="hitl_gui",
        executable="hitl_gui",
        name="hitl_gui",
        output="screen",
        condition=IfCondition(gui_enabled),
        arguments=[
            "--host", gui_host,
            "--port", gui_port,
            "--mode", "ROS",
            "--agent-enabled", agent_enabled,
            "--perception-enabled", perception_enabled,
            "--grasp-enabled", grasp_enabled,
            "--simulation", simulation,
            "--real-execution-enabled", real_execution_enabled,
        ],
    )
    rviz = Node(
        package="rviz2",
        executable="rviz2",
        name="hitl_rviz2",
        output="screen",
        condition=IfCondition(rviz_enabled),
        arguments=["-d", rviz_config],
    )
    return LaunchDescription([*arguments, gui, rviz])
