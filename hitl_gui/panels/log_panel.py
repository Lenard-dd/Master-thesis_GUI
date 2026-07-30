"""In-memory execution-log panel."""

from nicegui import ui


def create_log_panel(state) -> None:
    with ui.card().classes("w-full"):
        ui.label("Execution Log").classes("text-lg font-semibold")
        ui.table(
            columns=[
                {"name": "time", "label": "Time", "field": "time", "align": "left"},
                {"name": "level", "label": "Level", "field": "level", "align": "left"},
                {"name": "message", "label": "Message", "field": "message", "align": "left"},
            ],
            rows=state.log_rows,
            row_key="message",
        ).classes("w-full")
