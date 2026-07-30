from nicegui import ui


def create_component_log_panel(controller):
    @ui.refreshable
    def content():
        with ui.card().classes("w-full"):
            ui.label("Component Process Logs").classes("text-lg font-semibold")
            for component_id, managed in controller.state.component_processes.items():
                with ui.expansion(f"{managed.display_name} — {managed.status.value}"):
                    ui.label(f"PID: {managed.pid or '-'}  Started: {managed.start_time or '-'}")
                    ui.label("\n".join(managed.recent_output[-200:]) or "No captured output.").classes("font-mono text-xs whitespace-pre-wrap")
                    ui.button("Clear Display", on_click=lambda m=managed: m.recent_output.clear()).props("outline")
            if not controller.state.component_processes:
                ui.label("No GUI-managed component process.").classes("text-grey")
    content()
    return content
