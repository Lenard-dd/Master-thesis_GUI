"""Header summary panel."""

from nicegui import ui


def create_header_panel(state) -> None:
    with ui.card().classes("w-full"):
        with ui.row().classes("w-full items-center justify-between gap-4"):
            ui.label("LLM Robot HITL Interface").classes("text-2xl font-bold text-primary")
            with ui.row().classes("items-center gap-3 text-sm"):
                ui.badge("Mode: SIMULATION", color="primary")
                ui.badge("ROS: MOCK", color="grey-7")
                ui.badge(f"Agent: {state.agent_status}", color="grey-7")
                ui.label(f"Current Task: {state.current_task}").classes("font-medium")
