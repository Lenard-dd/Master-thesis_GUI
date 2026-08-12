# Milo Robot Collaboration Studio

NiceGUI-based human-in-the-loop interface for supervised LLM robot tasks. The
package connects the existing Agent, D435i/SAM3 perception pipeline, GraspGenX,
MoveIt trajectory cache, RViz2 preview and Robotiq interfaces without replacing
their existing algorithms or safety checks.

## Architecture

```text
Browser / NiceGUI
       │
       ▼
GuiController ── AppState / TaskPlan / audit events
       │
       ├── ExistingAgentBridge
       ├── RuntimeAdapterRegistry
       │      ├── MockPerceptionAdapter
       │      └── D435i + SAM3 + GraspGenX
       ├── ExistingTrajectoryReviewAdapter
       │      └── MoveIt cached plan → RViz preview → HITL → exact-plan execution
       ├── RosWorker (background executor and health monitoring)
       └── GUI-owned component/RViz process managers
```

ROS callbacks update thread-safe monitor data. They do not manipulate NiceGUI
controls. GUI panels read one shared `AppState`, and all mutations pass through
`GuiController`.

## Dependencies

- Ubuntu with ROS 2 Humble and `colcon`
- Python 3 with `rclpy`, `nicegui`, `PyYAML` and the project dependencies
- Existing workspace packages for UR5, MoveIt and Robotiq
- Optional for live perception: D435i driver and the configured SAM3 backend
- Optional for live grasp generation: the configured GraspGenX environment/server
- Optional embedded RViz: Xvfb, x11vnc and noVNC/websockify

## Build

```bash
cd /home/lenard/dev_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select hitl_gui
source install/setup.bash
```

Use `setup.zsh` instead when running zsh.

## Unified launch

Safe Mock-backed simulation with an independent RViz window:

```bash
ros2 launch hitl_gui hitl_system.launch.py \
  simulation:=true \
  rviz_enabled:=true
```

Available arguments:

| Argument | Default | Meaning |
|---|---:|---|
| `gui_enabled` | `true` | Start NiceGUI |
| `rviz_enabled` | `false` | Start a separate RViz2 process |
| `agent_enabled` | `false` | Use the configured existing Agent; false uses Mock workflow |
| `perception_enabled` | `false` | Use existing ROS D435i/SAM3 adapter; false uses mock perception |
| `grasp_enabled` | `false` | Use existing GraspGenX adapter; false uses mock candidates |
| `simulation` | `false` | Select GUI robot mode; it never enables real execution |
| `gui_host` | `127.0.0.1` | NiceGUI bind address |
| `gui_port` | `8080` | NiceGUI port |

The feature flags select existing in-process adapters; they do not create a
second Agent, SAM3 implementation or grasp generator. Camera and GraspGenX
services must already be healthy or can be started from the GUI component
manager.

The original standalone commands remain available:

```bash
ros2 run hitl_gui hitl_gui
python3 -m hitl_gui.main --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>.

## Operating modes

### Mock

Mock mode needs no ROS hardware and supports the complete reviewed workflow.
Use the unified launch defaults or set:

```yaml
runtime_backends:
  perception_mode: mock
  grasp_mode: mock
```

### Simulation

Start fake UR5 hardware and the required ROS services, then launch with
`simulation:=true`. Every MoveIt trajectory is previewed and approved before
the exact cached trajectory ID can execute. RViz preview never triggers motion.

For live algorithms add:

```bash
agent_enabled:=true perception_enabled:=true grasp_enabled:=true
```

### Real robot safety

The current default is **REAL ROBOT monitoring mode**, because the workstation
is connected to real hardware. Physical trajectory execution remains disabled:

```yaml
enable_real_execution: false
```

Changing `simulation:=false` does **not** override this safety setting. It only
changes the GUI mode and ROS monitoring context. Real
operation additionally requires the existing hardware safety configuration,
controller readiness, trajectory validation, per-trajectory approval and the
physical emergency stop. The GUI is not a safety controller. Do not enable real
execution until the separate real-hardware validation phase is complete.

## Logs and experiment summaries

Logs are written under the configured directory:

```text
logs/YYYY-MM-DD/<task_id>/
├── task_summary.json
├── execution_events.json
├── conversation.json
├── tool_receipts.json
└── experiment_metrics.json
```

Terminal tasks automatically write `task_summary.json`. The GUI Task Summary
panel can export the complete receipt set or reset the task.

## Tests

```bash
cd /home/lenard/dev_ws/src/hitl_gui
python3 -m compileall -q hitl_gui launch
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q -p no:cacheprovider test

cd /home/lenard/dev_ws
colcon build --packages-select hitl_gui
source install/setup.bash
ros2 launch hitl_gui hitl_system.launch.py --show-args
```

Run the Mock workflow tests before ROS tests. RViz/fake-hardware validation
requires a graphical ROS session and must never be run against a real driver by
automation.

## Common errors

- **`nicegui` cannot be imported**: run the GUI with the Python environment in
  which NiceGUI is installed, while preserving the sourced ROS environment.
- **ROS Health is DISCONNECTED**: verify `ROS_DOMAIN_ID`, `/joint_states`, camera
  topics and MoveIt services in the same sourced terminal.
- **RViz config not found**: rebuild and source `install/setup.*`; the launch
  resolves RViz configuration from the installed package share directory.
- **Agent always uses Mock**: start with `agent_enabled:=true` and verify the
  configured Agent credentials/environment.
- **GraspGenX unavailable**: verify the server/environment and enable
  `grasp_enabled:=true`; live mode never silently falls back to fabricated data.
- **Port 8080 in use**: set `gui_port:=<another_port>`.

## Current limitations

- Stage 10 validation is simulation-first; real UR5 execution remains disabled.
- The place stage currently uses the configured MoveIt named target `home`.
- Real D435i/SAM3/GraspGenX and fake-hardware RViz timing require validation on
  the target workstation with those processes running.
- Embedded RViz additionally depends on local Xvfb/noVNC packages.
