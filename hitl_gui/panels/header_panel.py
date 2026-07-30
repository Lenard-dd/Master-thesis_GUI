from nicegui import ui


def create_header_panel(controller):
    @ui.refreshable
    def content():
        state = controller.state
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between gap-4"):
                ui.label("LLM Robot HITL Interface").classes("text-2xl font-bold text-primary")
                with ui.row().classes("items-center gap-3 text-sm"):
                    ui.badge(f"Mode: {state.robot_mode}", color="primary")
                    ui.badge(f"ROS: {state.ros_status.value}", color="grey-7")
                    ui.badge(f"Agent: {state.agent_status.value}", color="grey-7")
                    ui.label(f"Current Task: {state.current_task_name}").classes("font-medium")
    content()
    return content
