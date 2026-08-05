from nicegui import ui


def create_tool_flow_panel(controller):
    selected = {"node": None}
    tree_state = {
        # Composite/root nodes are added here the first time they acquire
        # children. User expand/collapse choices are retained across the
        # event-driven refreshes which rebuild the NiceGUI tree component.
        "expanded": set(),
        "user_touched": set(),
    }
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

            expandable = {
                node.node_id for node in controller.state.tool_nodes
                if by_parent.get(node.node_id)
            }
            tree_state["expanded"].intersection_update(expandable)
            tree_state["expanded"].update(expandable - tree_state["user_touched"])

            def remember_expansion(event):
                new_expanded = set(event.value or [])
                tree_state["user_touched"].update(
                    tree_state["expanded"].symmetric_difference(new_expanded)
                )
                tree_state["expanded"] = new_expanded

            tree = ui.tree(
                make_nodes() or [{"id": "empty", "label": "No tool event received"}],
                node_key="id", label_key="label", on_select=select,
                on_expand=remember_expansion,
            ).classes("w-full")
            if tree_state["expanded"]:
                tree.expand(sorted(tree_state["expanded"]))

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

    def refresh() -> None:
        content.refresh()
        details.refresh()

    return refresh
