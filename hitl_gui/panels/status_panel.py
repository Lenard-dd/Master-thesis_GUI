from nicegui import ui


def create_status_panel(controller):
    @ui.refreshable
    def content():
        state = controller.state
        with ui.card().classes("w-full h-full min-h-[520px]"):
            ui.label("System Status").classes("text-lg font-semibold")
            with ui.grid(columns=1).classes("w-full gap-2"):
                for name, status in [("Agent", state.agent_status), *state.hardware_status.items()]:
                    with ui.card().classes("w-full p-2"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label(name).classes("font-medium")
                            value = state.rviz_process_status if name == "RViz2" else status.value
                            ui.badge(value, color="negative" if value in {"DISCONNECTED", "ERROR"} else "primary")
    content()
    return content
