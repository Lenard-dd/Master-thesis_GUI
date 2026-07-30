"""Static execution-flow tree."""

from nicegui import ui


FLOW_STEPS = [
    "understand_instruction", "detect_objects", "select_target",
    "generate_grasp_candidates", "validate_grasp", "plan_motion",
    "trajectory_review", "execute_motion", "verify_grasp",
]


def create_tool_flow_panel() -> None:
    with ui.card().classes("w-full h-full min-h-[520px]"):
        ui.label("Agent Execution Flow").classes("text-lg font-semibold")
        nodes = [{"id": step, "label": f"{step}  —  pending"} for step in FLOW_STEPS]
        ui.tree(nodes, label_key="label").classes("w-full")
