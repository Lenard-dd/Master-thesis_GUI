"""Static system-status cards."""

from nicegui import ui


SYSTEMS = [
    ("ROS 2", "MOCK"), ("Agent", "IDLE"), ("UR5", "DISCONNECTED"),
    ("Robotiq 2F-140", "DISCONNECTED"), ("D435i", "DISCONNECTED"),
    ("MoveIt", "MOCK"), ("SAM3", "MOCK"), ("GraspGenX", "MOCK"),
    ("RViz2", "DISCONNECTED"),
]


def create_status_panel() -> None:
    with ui.card().classes("w-full h-full min-h-[520px]"):
        ui.label("System Status").classes("text-lg font-semibold")
        with ui.grid(columns=1).classes("w-full gap-2"):
            for name, status in SYSTEMS:
                with ui.card().classes("w-full p-2"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(name).classes("font-medium")
                        color = "grey-7" if status == "DISCONNECTED" else "primary"
                        ui.badge(status, color=color)
