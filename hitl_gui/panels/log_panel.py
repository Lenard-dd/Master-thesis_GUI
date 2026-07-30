"""Structured, filterable in-memory audit log and task export controls."""

from nicegui import ui


def _category(event) -> str:
    if event.event_type.startswith("tool_"):
        return "Tool"
    if event.event_type.startswith("hitl_") or event.event_type == "trajectory_invalidated":
        return "HITL"
    if event.event_type in {"plan_version_changed", "plan_replanned"}:
        return "User Modification"
    if event.event_type == "tool_failed":
        return "Error"
    return "All"


def create_log_panel(controller):
    selected = {"event": None}
    filter_select = ui.select(["All", "Tool", "HITL", "Error", "User Modification"], value="All", label="Filter").classes("w-48")

    @ui.refreshable
    def details():
        event = selected["event"]
        with ui.card().classes("w-full bg-grey-1"):
            ui.label("Event Details").classes("font-semibold")
            if event is None:
                ui.label("Select an event row to inspect structured details.").classes("text-grey")
                return
            node = next((item for item in controller.state.tool_nodes if item.node_id == event.node_id), None)
            ui.label(f"Tool name: {node.tool_name if node else '-'}")
            ui.label(f"Input summary: {node.input_summary if node else {}}")
            ui.label(f"Output summary: {node.output_summary if node else {}}")
            ui.label(f"Error: {node.error_message if node else '-'}")
            ui.label(f"request_id: {event.metadata.get('request_id', '-')}")
            ui.label(f"trajectory_id: {event.metadata.get('trajectory_id', controller.state.current_trajectory_id or '-')}")
            ui.label(f"user decision: {event.metadata.get('user_decision', '-')}")
            ui.label(f"metadata: {event.metadata}")

    @ui.refreshable
    def content():
        current_filter = filter_select.value or "All"
        events = [
            event for event in controller.state.event_log
            if current_filter == "All" or _category(event) == current_filter
        ][-30:]
        rows = []
        for event in events:
            node = next((item for item in controller.state.tool_nodes if item.node_id == event.node_id), None)
            rows.append({
                "event_id": event.event_id, "time": event.timestamp.split("T")[-1],
                "event": event.event_type, "step": node.tool_name if node else (event.node_id or "-"),
                "status": event.new_value or "-", "duration": node.duration_ms if node else "-",
                "plan_version": event.plan_version,
            })

        def select_row(event_args):
            selected_id = event_args.selection[0]["event_id"] if event_args.selection else None
            selected["event"] = next((item for item in controller.state.event_log if item.event_id == selected_id), None)
            details.refresh()

        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Execution Log").classes("text-lg font-semibold")
                export = ui.button(
                    "Export Task Log",
                    on_click=lambda: ui.notify(f"Exported to {controller.export_task_log()}", type="positive"),
                )
                if not controller.state.current_task_id:
                    export.disable()
            ui.table(
                columns=[
                    {"name": "time", "label": "Time", "field": "time", "align": "left"},
                    {"name": "event", "label": "Event", "field": "event", "align": "left"},
                    {"name": "step", "label": "Step", "field": "step", "align": "left"},
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                    {"name": "duration", "label": "Duration", "field": "duration", "align": "left"},
                    {"name": "plan_version", "label": "Plan Version", "field": "plan_version", "align": "left"},
                ], rows=rows, row_key="event_id", selection="single", on_select=select_row,
            ).classes("w-full")

    filter_select.on_value_change(lambda _: content.refresh())
    content()
    details()
    return content
