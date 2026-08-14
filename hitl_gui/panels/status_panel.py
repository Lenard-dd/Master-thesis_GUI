"""Stable component controls plus a separately refreshable status view."""

from nicegui import ui


def create_status_panel(controller, *, compact: bool = False):
    """Create buttons once; refresh only the read-only status content."""
    card_classes = "w-full p-3" if compact else "w-full h-full min-h-[520px] p-3"
    with ui.card().classes(card_classes):
        ui.label("System Status").classes("text-base font-semibold")

        @ui.refreshable
        def status_view():
            state = controller.state
            ur5_processes = [
                process for component_id, process in state.component_processes.items()
                if component_id in {"ur5_fake", "ur5_real"}
            ]
            ur5_process = next(
                (process for process in ur5_processes if process.status.value in {"STARTING", "RUNNING", "STOPPING"}),
                ur5_processes[-1] if ur5_processes else None,
            )
            status_classes = (
                "w-full grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-x-4 gap-y-1 text-xs"
                if compact else "w-full gap-1 text-xs"
            )
            with ui.element("div").classes(status_classes):
                _status_line("UR5 Process", ur5_process.status.value if ur5_process else "STOPPED")
                _status_line("UR5 Health", state.hardware_status["UR5"].value)
                _status_line("RViz Process", state.rviz_process_status)
                ui.label(f"Backends: {controller.runtime_adapters.mode_summary}").classes("text-grey text-xs")
                ui.label(f"Launcher: {state.simulation_launch_status}").classes("text-grey text-xs")
                if not compact:
                    ui.separator().classes("my-1")
                for name, status in [(controller.agent_name, state.agent_status), *state.hardware_status.items()]:
                    value = state.rviz_process_status if name == "RViz2" else status.value
                    _status_line(name, value)

        status_view()
        with ui.expansion("Component Controls", icon="settings").classes("w-full text-sm"):
            _create_stable_controls(controller, compact=compact)
    return status_view


def _create_stable_controls(controller, *, compact: bool = False):
    """These buttons intentionally live outside ui.refreshable."""
    layout = "w-full grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3" if compact else "w-full gap-1"
    with ui.element("div").classes(layout):
        for title, component_id in [
            ("D435i Camera", "camera"),
            ("Robotiq 2F-140", "gripper"),
            ("GraspGenX", "graspgenx"),
        ]:
            with ui.column().classes("w-full gap-1"):
                ui.label(title).classes("text-xs font-medium")
                with ui.button_group().props("outline"):
                    ui.button("Start", icon="play_arrow",
                              on_click=lambda cid=component_id: controller.start_component(cid)).props("dense size=sm")
                    ui.button("Stop", icon="stop",
                              on_click=lambda cid=component_id: controller.stop_component(cid)).props("dense size=sm outline")

        with ui.column().classes("w-full gap-1"):
            ui.label("UR5").classes("text-xs font-medium")
            with ui.row().classes("w-full gap-2 flex-wrap"):
                with ui.button_group().props("outline"):
                    ui.button("Fake", icon="play_arrow",
                              on_click=lambda: controller.start_component("ur5_fake")).props("dense size=sm")
                    ui.button("Stop", icon="stop",
                              on_click=lambda: controller.stop_component("ur5_fake")).props("dense size=sm outline")
                with ui.button_group().props("outline"):
                    real_button = ui.button("Real", icon="play_arrow",
                                            on_click=lambda: _real_dialog(controller)).props("dense size=sm color=negative")
                    ui.button("Stop", icon="stop",
                              on_click=lambda: controller.stop_component("ur5_real")).props("dense size=sm outline color=negative")
                if not controller.gui_config.get("enable_real_driver_start", False):
                    real_button.disable()
                    real_button.tooltip("Real driver startup is disabled")
    ui.separator().classes("my-2")
    with ui.row().classes("w-full gap-2 flex-wrap"):
        ui.button("Start Simulation", icon="play_arrow",
                  on_click=controller.start_simulation_components).props("dense size=sm")
        real_system_button = ui.button("Start Real System", icon="play_arrow",
                                       on_click=lambda: _real_system_dialog(controller),
                                       color="negative").props("dense size=sm")
        if not controller.gui_config.get("enable_real_driver_start", False):
            real_system_button.disable()
            real_system_button.tooltip("Real driver startup is disabled")
        ui.button("Stop All GUI-Managed", icon="stop",
                  on_click=controller.stop_gui_managed_components).props("dense size=sm outline")


def _status_line(name: str, value: str) -> None:
    color = "negative" if value in {"DISCONNECTED", "ERROR"} else (
        "warning" if value in {"WARNING", "UNKNOWN"} else "primary"
    )
    with ui.row().classes("w-full items-center justify-between gap-1 no-wrap"):
        ui.label(name).classes("text-xs truncate")
        ui.badge(value, color=color).props("outline").classes("text-[10px]")


def _real_dialog(controller):
    details = controller.real_ur5_launch_details()
    with ui.dialog() as dialog, ui.card():
        ui.label("REAL ROBOT").classes("text-lg font-bold text-negative")
        ui.label(f"robot_ip: {details['robot_ip']}")
        ui.label(f"ROS_DOMAIN_ID: {details['ros_domain_id']}")
        ui.label(f"launch_rviz: {details['launch_rviz']}")
        ui.label(f"UR5 health: {controller.state.hardware_status['UR5'].value}")
        ui.label("This will start the real robot driver.")
        ui.label("Starting the driver does not approve or execute robot motion.").classes("text-xs text-grey")
        with ui.row():
            ui.button("Confirm Start", on_click=lambda: (controller.confirm_real_ur5_start(), dialog.close()), color="negative")
            ui.button("Cancel", on_click=dialog.close).props("outline")
    dialog.open()


def _real_system_dialog(controller):
    details = controller.real_ur5_launch_details()
    with ui.dialog() as dialog, ui.card().classes("max-w-lg"):
        ui.label("START REAL HARDWARE SYSTEM").classes("text-lg font-bold text-negative")
        ui.label(f"robot_ip: {details['robot_ip']}")
        ui.label(f"ROS_DOMAIN_ID: {details['ros_domain_id']}")
        ui.label(f"UR5 health: {controller.state.hardware_status['UR5'].value}")
        ui.label("This starts the real UR5 driver, Embedded RViz, camera, gripper driver, and GraspGenX.")
        ui.label("It does not approve a trajectory or command robot motion.").classes("font-medium")
        with ui.row():
            ui.button(
                "Confirm Start All",
                on_click=lambda: (controller.start_real_components(confirmed=True), dialog.close()),
                color="negative",
            )
            ui.button("Cancel", on_click=dialog.close).props("outline")
    dialog.open()
