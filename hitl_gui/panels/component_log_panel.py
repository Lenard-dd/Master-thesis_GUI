from nicegui import ui


def create_component_log_panel(controller):
    expanded_components: set[str] = set()

    def remember_expansion(component_id: str, expanded: bool) -> None:
        if expanded:
            expanded_components.add(component_id)
        else:
            expanded_components.discard(component_id)

    with ui.expansion("Component Process Logs", icon="terminal", value=False).classes(
        "w-full bg-white border border-grey-3 rounded-lg"
    ):
        @ui.refreshable
        def content():
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Latest 200 lines per GUI-managed process").classes("text-xs text-grey-7")
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
                    with ui.scroll_area().classes("w-full h-[280px] max-h-[40vh] bg-grey-10 text-white rounded p-2"):
                        ui.label("\n".join(managed.recent_output[-200:]) or "No captured output.").classes(
                            "font-mono text-[11px] whitespace-pre-wrap"
                        )
                    def clear_display(m=managed):
                        m.recent_output.clear()
                        content.refresh()
                    ui.button("Clear Display", on_click=clear_display).props("outline")
            if not controller.state.component_processes:
                ui.label("No GUI-managed component process.").classes("text-grey")
        content()
    return content
