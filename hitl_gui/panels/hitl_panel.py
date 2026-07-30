"""Static HITL controls."""

from nicegui import ui


def create_hitl_panel() -> None:
    with ui.card().classes("w-full"):
        ui.label("Current HITL Request").classes("text-lg font-semibold")
        ui.label("No review request is pending. This prototype does not connect to robot systems.").classes("text-grey")
        with ui.row().classes("w-full gap-2 mt-2 flex-wrap"):
            ui.button("Open RViz", on_click=lambda: ui.notify("功能尚未连接", type="info"), color="primary")
            ui.button("Preview").props("outline").disable()
            ui.button("Approve", color="positive").disable()
            ui.button("Reject", color="negative").disable()
            ui.button("Replan").props("outline").disable()
            ui.button("Cancel", on_click=lambda: ui.notify("Mock task cancelled.", type="info")).props("outline")
