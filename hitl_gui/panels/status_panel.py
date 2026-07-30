from nicegui import ui


def create_status_panel(controller):
    @ui.refreshable
    def content():
        state = controller.state
        ur5_processes = [
            process for component_id, process in state.component_processes.items()
            if component_id in {"ur5_fake", "ur5_real"}
        ]
        ur5_process = next(
            (process for process in ur5_processes if process.status.value in {"STARTING", "RUNNING", "STOPPING"}),
            ur5_processes[-1] if ur5_processes else None,
        )
        with ui.card().classes("w-full h-full min-h-[520px]"):
            ui.label("System Status").classes("text-lg font-semibold")
            with ui.row().classes("w-full gap-4 text-sm"):
                ui.label(f"UR5 Process: {ur5_process.status.value if ur5_process else 'STOPPED'}")
                ui.label(f"UR5 ROS Health: {state.hardware_status['UR5'].value}")
                ui.label(f"RViz Process: {state.rviz_process_status}")
            ui.label(f"Simulation launcher: {state.simulation_launch_status}").classes("text-grey")
            with ui.grid(columns=1).classes("w-full gap-2"):
                for name, status in [("Agent", state.agent_status), *state.hardware_status.items()]:
                    with ui.card().classes("w-full p-2"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label(name).classes("font-medium")
                            value = state.rviz_process_status if name == "RViz2" else status.value
                            ui.badge(value, color="negative" if value in {"DISCONNECTED", "ERROR"} else "primary")
                    component_id = {
                        "UR5": "ur5_fake", "D435i": "camera",
                        "Robotiq 2F-140": "gripper", "GraspGenX": "graspgenx",
                    }.get(name)
                    if component_id:
                        with ui.row().classes("gap-1"):
                            if name == "UR5":
                                ui.button("Start Fake", on_click=lambda: controller.start_component("ur5_fake")).props("dense")
                                ui.button("Start Real", on_click=lambda: _real_dialog(controller)).props("dense color=negative")
                            else:
                                ui.button("Start", on_click=lambda cid=component_id: controller.start_component(cid)).props("dense")
                            ui.button("Stop", on_click=lambda cid=component_id: controller.stop_component(cid)).props("dense outline")
                            ui.button("Restart", on_click=lambda cid=component_id: controller.restart_component(cid)).props("dense outline")
                with ui.row().classes("gap-2 mt-2"):
                    ui.button("Start Simulation Components", on_click=controller.start_simulation_components)
                    ui.button("Stop GUI-Managed Components", on_click=controller.stop_gui_managed_components).props("outline")
    content()
    return content


def _real_dialog(controller):
    with ui.dialog() as dialog, ui.card():
        ui.label("REAL ROBOT").classes("text-lg font-bold text-negative")
        ui.label("robot_ip: 192.168.10.27")
        ui.label("ROS_DOMAIN_ID: 27")
        ui.label(f"UR5 health: {controller.state.hardware_status['UR5'].value}")
        ui.label("This will start the real robot driver.")
        with ui.row():
            ui.button("Confirm Start", on_click=lambda: (controller.confirm_real_ur5_start(), dialog.close()), color="negative")
            ui.button("Cancel", on_click=dialog.close).props("outline")
    dialog.open()
