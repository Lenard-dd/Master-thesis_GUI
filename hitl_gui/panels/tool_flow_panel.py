from nicegui import ui


def create_tool_flow_panel(controller):
    @ui.refreshable
    def content():
        with ui.card().classes("w-full h-full min-h-[520px]"):
            ui.label("Agent Execution Flow").classes("text-lg font-semibold")
            nodes = [
                {"id": node.node_id, "label": f"{node.display_name} — {node.status.value}"}
                for node in controller.state.tool_nodes
            ] or [{"id": "empty", "label": "No task flow initialized"}]
            ui.tree(nodes, label_key="label").classes("w-full")
    content()
    return content
