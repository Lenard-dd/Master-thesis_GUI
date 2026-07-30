from nicegui import ui

from hitl_gui.app_state import HitlDecision


def create_hitl_panel(controller):
    @ui.refreshable
    def content():
        request = controller.state.pending_hitl_request
        with ui.card().classes("w-full"):
            ui.label("Current HITL Request").classes("text-lg font-semibold")
            if request is None:
                ui.label("No review request is pending.").classes("text-grey")
            else:
                ui.label(request.title).classes("font-medium")
                ui.label(f"Trajectory: {request.trajectory_id}")
                ui.label(request.description).classes("text-grey")
            with ui.row().classes("w-full gap-2 mt-2 flex-wrap"):
                ui.button("Open RViz", on_click=lambda: ui.notify("功能尚未连接", type="info"), color="primary")
                ui.button("Preview").props("outline").disable()
                for label, decision, color in [
                    ("Approve", HitlDecision.APPROVE, "positive"),
                    ("Reject", HitlDecision.REJECT, "negative"),
                    ("Replan", HitlDecision.REPLAN, "primary"),
                ]:
                    button = ui.button(
                        label, color=color,
                        on_click=lambda d=decision: request and controller.submit_hitl_decision(request.request_id, d),
                    )
                    if request is None:
                        button.disable()
                ui.button("Cancel", on_click=controller.cancel_task).props("outline")
    content()
    return content
