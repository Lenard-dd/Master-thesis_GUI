# HITL GUI Prototype

Static NiceGUI prototype for the LLM Robot human-in-the-loop interface.

## Build

```bash
cd /home/lenard/dev_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select hitl_gui
source install/setup.bash
```

## Start

```bash
ros2 run hitl_gui hitl_gui
```

Or run the module directly from the package directory:

```bash
python3 -m hitl_gui.main
```

Open <http://127.0.0.1:8080> in a browser.

## Current functionality

- Static three-column HITL interface with Agent chat, execution-flow tree, and system cards.
- User messages are shown locally in the chat panel.
- Static HITL controls and one in-memory `GUI initialized` log record.
- `Open RViz` only displays a "功能尚未连接" notification.

## Current limitations

- No ROS 2 nodes, topics, services, actions, or RViz launch are used.
- No real LLM, Agent, MoveIt, camera, perception, or robot execution is connected.
- No task state machine, editable flow nodes, persistent logging, or real approval workflow is implemented.
