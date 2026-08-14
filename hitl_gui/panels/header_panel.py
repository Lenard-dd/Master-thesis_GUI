from nicegui import ui


def create_header_panel(controller):
    @ui.refreshable
    def content():
        state = controller.state
        with ui.card().classes("w-full px-4 py-2"):
            with ui.row().classes("w-full min-h-[48px] items-center justify-between gap-x-5 gap-y-2 flex-wrap"):
                with ui.column().classes("gap-0 min-w-[220px]"):
                    ui.label("Robot Collaboration Studio").classes("text-xl font-bold text-primary leading-tight")
                    ui.label(f"{controller.agent_name} · Human-in-the-loop workspace").classes("text-[11px] text-grey-7")
                with ui.row().classes("items-center gap-2 min-w-0 flex-grow md:justify-center"):
                    ui.icon("assignment", color="grey-7").classes("text-sm")
                    ui.label(state.current_task_name or "None").classes("text-sm font-medium truncate max-w-[420px]")
                with ui.row().classes("items-center gap-2 text-xs no-wrap"):
                    mode_color = "negative" if state.robot_mode in {"REAL", "REAL ROBOT"} else "primary"
                    ui.badge(state.robot_mode, color=mode_color).props("outline").classes("text-[10px]")
                    ui.badge(f"ROS · {state.ros_status.value}", color=_status_color(state.ros_status.value)).props("outline").classes("text-[10px]")
                    ui.badge(f"{controller.agent_name} · {state.agent_status.value}",
                             color=_status_color(state.agent_status.value)).props("outline").classes("text-[10px]")
    content()
    return content


def _status_color(value: str) -> str:
    if value in {"ERROR", "FAILED", "DISCONNECTED"}:
        return "negative"
    if value in {"WARNING", "WAITING_APPROVAL", "UNKNOWN"}:
        return "warning"
    if value in {"RUNNING", "READY", "SUCCEEDED"}:
        return "positive"
    return "grey-7"
