from nicegui import ui


def create_log_panel(controller):
    @ui.refreshable
    def content():
        with ui.card().classes("w-full"):
            ui.label("Execution Log").classes("text-lg font-semibold")
            rows = [
                {"time": event.timestamp.split("T")[-1], "event": event.event_type, "node": event.node_id or "-"}
                for event in controller.state.event_log[-12:]
            ]
            ui.table(
                columns=[
                    {"name": "time", "label": "Time", "field": "time", "align": "left"},
                    {"name": "event", "label": "Event", "field": "event", "align": "left"},
                    {"name": "node", "label": "Node", "field": "node", "align": "left"},
                ], rows=rows, row_key="time",
            ).classes("w-full")
    content()
    return content
