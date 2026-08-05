from nicegui import ui


def create_tool_flow_panel(controller):
    selected = {"node": None}
    with ui.card().classes("w-full h-full min-h-[520px]"):
        ui.label("Agent Execution Flow").classes("text-lg font-semibold")

        @ui.refreshable
        def content():
            by_parent = {}
            for node in controller.state.tool_nodes:
                by_parent.setdefault(node.parent_id, []).append(node)

            def make_nodes(parent_id=None):
                return [{"id": node.node_id, "label": f"{node.display_name} — {node.status.value}",
                         "children": make_nodes(node.node_id)}
                        for node in by_parent.get(parent_id, [])]

            def select(event):
                selected["node"] = next((node for node in controller.state.tool_nodes if node.node_id in event.selection), None)
                details.refresh()

            ui.tree(make_nodes() or [{"id": "empty", "label": "No tool event received"}],
                    label_key="label", on_select=select).classes("w-full")

        @ui.refreshable
        def details():
            node = selected["node"]
            if node:
                ui.label(f"Tool Details — {node.display_name}").classes("font-semibold")
                ui.label(f"Input: {node.input_data}")
                ui.label(f"Output: {node.output_data}")
                ui.label(f"Error: {node.error_message or '-'}")

        content()
        details()
    return content
