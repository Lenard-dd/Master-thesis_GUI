"""Read-only terminal task summary and experiment actions."""

from nicegui import ui

from hitl_gui.app_state import TaskStatus


TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


def create_task_summary_panel(controller):
    @ui.refreshable
    def content():
        if (not controller.state.current_task_id or controller.state.task_status not in TERMINAL
                or controller.state.pending_hitl_request is not None):
            return
        summary = controller.task_summary()
        result = summary["result"]
        color = "positive" if result == "SUCCESS" else ("negative" if result == "FAILED" else "warning")
        with ui.card().classes("w-full p-4"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Task Summary").classes("text-lg font-semibold")
                ui.badge(result, color=color).classes("text-sm")
            with ui.element("div").classes("w-full grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 text-sm"):
                _metric("Total", _milliseconds(summary["total_duration"]))
                _metric("HITL Requests", summary["number_of_hitl_requests"])
                _metric("User Changes", summary["number_of_user_modifications"])
                _metric("Replans", summary["number_of_replans"])
                _metric("Tool Errors", summary["number_of_tool_failures"])
                _metric("Plan Version", summary["final_plan_version"])
            with ui.row().classes("gap-2 mt-2"):
                ui.button(
                    "Export Log", icon="download",
                    on_click=lambda: ui.notify(
                        f"Exported to {controller.export_task_log()}", type="positive"
                    ),
                ).props("dense")
                ui.button("Reset Task", icon="restart_alt", on_click=controller.reset_task).props("dense outline")

    content()
    return content


def _metric(label: str, value) -> None:
    with ui.column().classes("gap-0"):
        ui.label(label).classes("text-xs text-grey")
        ui.label(str(value)).classes("font-medium")


def _milliseconds(value) -> str:
    return "—" if value is None else f"{float(value) / 1000:.2f} s"
