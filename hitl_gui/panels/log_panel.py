"""Structured, filterable in-memory audit log and task export controls."""

from nicegui import ui


def _category(event) -> str:
    if event.event_type == "tool_failed" or "error" in event.event_type:
        return "Error"
    if event.event_type.startswith("tool_"):
        return "Tool"
    if event.event_type.startswith("hitl_") or event.event_type == "trajectory_invalidated":
        return "HITL"
    if event.event_type in {"plan_version_changed", "plan_replanned"}:
        return "User Modification"
    return "All"


def create_log_panel(controller):
    selected = {"event": None}
    table_state = {"page": 1, "rows_per_page": 8}
    with ui.expansion("Execution Log", icon="receipt_long", value=False).classes(
        "w-full bg-white border border-grey-3 rounded-lg"
    ):
        with ui.row().classes("w-full items-end justify-between gap-2 flex-wrap px-2"):
            filter_select = ui.select(
                ["All", "Tool", "HITL", "Error", "User Modification"], value="All", label="Filter",
            ).classes("w-48").props("dense outlined")
            export = ui.button(
                "Export Task Log", icon="download",
                on_click=lambda: ui.notify(f"Exported to {controller.export_task_log()}", type="positive"),
            ).props("dense outline")
            if not controller.state.current_task_id:
                export.disable()

        @ui.refreshable
        def details():
            event = selected["event"]
            with ui.card().classes("w-full bg-grey-1 p-2"):
                if event is None:
                    ui.label("Select an event row to inspect structured details.").classes("text-xs text-grey")
                    return
                node = next((item for item in controller.state.tool_nodes if item.node_id == event.node_id), None)
                with ui.grid(columns=2).classes("w-full grid-cols-[130px_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs"):
                    for label, value in [
                        ("Tool", node.tool_name if node else "-"),
                        ("Input", node.input_summary if node else {}),
                        ("Output", node.output_summary if node else {}),
                        ("Error", node.error_message if node else "-"),
                        ("request_id", event.metadata.get("request_id", "-")),
                        ("trajectory_id", event.metadata.get("trajectory_id", controller.state.current_trajectory_id or "-")),
                        ("User decision", event.metadata.get("user_decision", "-")),
                        ("Metadata", event.metadata),
                    ]:
                        ui.label(label).classes("font-medium text-grey-7")
                        ui.label(str(value)).classes("break-all")

        @ui.refreshable
        def content():
            if controller.state.current_task_id:
                export.enable()
            else:
                export.disable()
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

            def keep_pagination(event_args):
                pagination = event_args.value
                table_state["page"] = pagination.get("page", 1)
                table_state["rows_per_page"] = pagination.get("rowsPerPage", 8)

            with ui.scroll_area().classes("w-full h-[360px] max-h-[45vh]"):
                ui.table(
                    columns=[
                        {"name": "time", "label": "Time", "field": "time", "align": "left"},
                        {"name": "event", "label": "Event", "field": "event", "align": "left"},
                        {"name": "step", "label": "Step", "field": "step", "align": "left"},
                        {"name": "status", "label": "Status", "field": "status", "align": "left"},
                        {"name": "duration", "label": "Duration", "field": "duration", "align": "left"},
                        {"name": "plan_version", "label": "Plan", "field": "plan_version", "align": "left"},
                    ], rows=rows, row_key="event_id", selection="single", on_select=select_row,
                    pagination={"page": table_state["page"], "rowsPerPage": table_state["rows_per_page"]},
                    on_pagination_change=keep_pagination,
                ).classes("w-full").props("dense flat")

        with ui.expansion("Selected Event Details", icon="info", value=False).classes("w-full text-sm"):
            details()

        def refresh_for_filter(_):
            table_state["page"] = 1
            content.refresh()

        filter_select.on_value_change(refresh_for_filter)
        content()
    return content
