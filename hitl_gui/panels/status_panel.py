"""Compact, stable process/ROS-health table with operator controls."""

from nicegui import ui


def create_status_panel(controller, *, compact: bool = False):
    """Build controls once and update badges without reconstructing buttons."""
    process_badges, health_badges, row_elements = {}, {}, {}
    card_classes = "w-full p-3" if compact else "w-full min-h-[480px] p-3"
    rows = [
        ("ur5", "UR5", "UR5"),
        ("camera", "Camera", "D435i"),
        ("gripper", "Gripper", "Robotiq 2F-140"),
        ("graspgenx", "GraspGenX", None),
        ("rviz", "RViz2", "RViz2"),
        ("moveit", "MoveIt", "MoveIt"),
        ("sam3", "SAM3", None),
    ]

    with ui.card().classes(card_classes):
        with ui.row().classes("w-full items-center justify-between gap-2"):
            ui.label("System").classes("text-base font-semibold")
            with ui.row().classes("items-center gap-2"):
                ui.label(f"{controller.runtime_adapters.mode_summary}").classes("text-[10px] text-grey-6")
                launcher_label = ui.badge("IDLE", color="grey-7").props("outline").classes("text-[9px]")
        with ui.element("div").classes(
            "w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-2"
        ):
            for component_id, display_name, _health_name in rows:
                with ui.element("div").classes(
                    "w-full min-w-0 px-2 py-1.5 rounded border-l-4 border-grey-4 bg-grey-1"
                ) as row_element:
                    row_elements[component_id] = row_element
                    with ui.row().classes("w-full items-center justify-between gap-1 no-wrap"):
                        ui.label(display_name).classes("font-medium truncate min-w-0")
                        _component_actions(controller, component_id)
                    with ui.row().classes("w-full items-center gap-1 no-wrap mt-0.5"):
                        process_badges[component_id] = ui.badge("Process · —", color="grey-7").classes("text-[9px]")
                        if _health_name is not None:
                            health_badges[component_id] = ui.badge("ROS · UNKNOWN", color="warning").classes("text-[9px]")

        ui.separator().classes("my-2")
        with ui.row().classes("w-full items-center justify-end gap-2 flex-wrap"):
            with ui.row().classes("gap-1 flex-wrap"):
                ui.button("Start Simulation", icon="play_arrow",
                          on_click=controller.start_simulation_components).props("dense size=sm")
                real_system_button = ui.button(
                    "Start Real System", icon="play_arrow",
                    on_click=lambda: _real_system_dialog(controller), color="negative",
                ).props("dense size=sm")
                if not controller.gui_config.get("enable_real_driver_start", False):
                    real_system_button.disable()
                    real_system_button.tooltip("Real driver startup is disabled")
                ui.button("Stop All", icon="stop",
                          on_click=controller.stop_gui_managed_components).props("dense size=sm outline")

    def status_view() -> None:
        state = controller.state
        process_values = {
            "ur5": _managed_process_status(state, ("ur5_fake", "ur5_real")),
            "camera": _managed_process_status(state, ("camera",)),
            "gripper": _managed_process_status(state, ("gripper",)),
            "graspgenx": _managed_process_status(state, ("graspgenx",)),
            "rviz": state.rviz_process_status,
            "moveit": "UR5 STACK" if _managed_process_status(state, ("ur5_fake", "ur5_real")) == "RUNNING" else "—",
            "sam3": "EXTERNAL",
        }
        for component_id, _display_name, health_name in rows:
            process_value = process_values[component_id]
            health_value = state.hardware_status[health_name].value if health_name is not None else None
            _set_badge(process_badges[component_id], process_value, "Process")
            if health_name is not None:
                _set_badge(health_badges[component_id], health_value, "ROS")
            _set_row_state(row_elements[component_id], process_value, health_value)
        launcher_value = state.simulation_launch_status
        launcher_label.text = launcher_value
        launcher_label.background_color = _status_color(launcher_value)
        launcher_label.update()

    # GuiController expects renderer.refresh(); keeping it as a stable update
    # avoids replacing clickable controls at the 5 Hz monitor frequency.
    status_view.refresh = status_view
    status_view()
    return status_view


def _component_actions(controller, component_id: str) -> None:
    with ui.row().classes("gap-1 items-center no-wrap"):
        if component_id == "ur5":
            ui.button("Fake", on_click=lambda: controller.start_component("ur5_fake")).props("dense flat size=sm")
            real = ui.button("Real", on_click=lambda: _real_dialog(controller), color="negative").props("dense flat size=sm")
            if not controller.gui_config.get("enable_real_driver_start", False):
                real.disable()
            ui.button(icon="stop", on_click=lambda: (
                controller.stop_component("ur5_fake"), controller.stop_component("ur5_real")
            )).props("dense flat round size=sm").tooltip("Stop GUI-managed UR5 driver")
        elif component_id in {"camera", "gripper", "graspgenx"}:
            ui.button(icon="play_arrow", on_click=lambda cid=component_id: controller.start_component(cid)).props(
                "dense flat round size=sm"
            ).tooltip("Start")
            ui.button(icon="stop", on_click=lambda cid=component_id: controller.stop_component(cid)).props(
                "dense flat round size=sm"
            ).tooltip("Stop")
        elif component_id == "rviz":
            ui.label("Embedded panel").classes("text-[10px] text-grey-6")
        elif component_id == "moveit":
            ui.label("UR5 stack").classes("text-[10px] text-grey-6")
        else:
            ui.label("External").classes("text-[10px] text-grey-6")


def _managed_process_status(state, component_ids: tuple[str, ...]) -> str:
    processes = [state.component_processes[item] for item in component_ids if item in state.component_processes]
    if not processes:
        return "STOPPED"
    active = next((item for item in processes if item.status.value in {"STARTING", "RUNNING", "STOPPING"}), None)
    return (active or processes[-1]).status.value


def _set_badge(badge, value: str, prefix: str) -> None:
    badge.text = f"{prefix} · {value}"
    badge.background_color = _status_color(value)
    badge.update()


def _set_row_state(element, process_value: str, health_value: str | None) -> None:
    base = (
        "w-full min-w-0 px-2 py-1.5 rounded border-l-4 transition-colors"
    )
    if process_value in {"ERROR", "FAILED", "EXITED"} or health_value in {"ERROR", "FAILED"}:
        state_classes = "border-red-5 bg-red-1"
    elif health_value in {"READY", "RUNNING", "SUCCEEDED"}:
        state_classes = "border-green-5 bg-green-1"
    elif health_value is None and process_value == "RUNNING":
        # Components without a ROS interface can only be assessed from the
        # GUI-owned process lifecycle; do not fabricate a ROS health signal.
        state_classes = "border-green-5 bg-green-1"
    elif process_value in {"RUNNING", "STARTING", "STOPPING"} or health_value in {"WARNING", "UNKNOWN"}:
        state_classes = "border-amber-5 bg-amber-1"
    else:
        state_classes = "border-grey-4 bg-grey-1"
    element.classes(replace=f"{base} {state_classes}")


def _status_color(value: str) -> str:
    if value.startswith("FAILED") or value in {"DISCONNECTED", "ERROR", "EXITED"}:
        return "negative"
    if value in {"WARNING", "UNKNOWN", "WAITING_APPROVAL", "STARTING", "STOPPING"}:
        return "warning"
    if value in {"READY", "RUNNING", "SUCCEEDED", "COMPLETED"}:
        return "positive"
    return "grey-7"


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
            ui.button("Confirm Start All",
                      on_click=lambda: (controller.start_real_components(confirmed=True), dialog.close()),
                      color="negative")
            ui.button("Cancel", on_click=dialog.close).props("outline")
    dialog.open()
