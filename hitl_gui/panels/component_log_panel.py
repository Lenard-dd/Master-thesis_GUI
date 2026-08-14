from nicegui import ui


def create_component_log_panel(controller):
    expanded_components: set[str] = set()

    def remember_expansion(component_id: str, expanded: bool) -> None:
        if expanded:
            expanded_components.add(component_id)
        else:
            expanded_components.discard(component_id)

    @ui.refreshable
    def content():
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Component Process Logs").classes("text-lg font-semibold")
                ui.button("Refresh", icon="refresh", on_click=content.refresh).props("dense flat")
            for component_id, managed in controller.state.component_processes.items():
                with ui.expansion(
                    f"{managed.display_name} — {managed.status.value}",
                    value=component_id in expanded_components,
                    on_value_change=lambda event, cid=component_id: remember_expansion(
                        cid, bool(event.value),
                    ),
                ):
                    ui.label(f"PID: {managed.pid or '-'}  Started: {managed.start_time or '-'}")
                    ui.label("\n".join(managed.recent_output[-200:]) or "No captured output.").classes("font-mono text-xs whitespace-pre-wrap")
                    def clear_display(m=managed):
                        m.recent_output.clear()
                        content.refresh()
                    ui.button("Clear Display", on_click=clear_display).props("outline")
            if not controller.state.component_processes:
                ui.label("No GUI-managed component process.").classes("text-grey")
    content()
    return content
