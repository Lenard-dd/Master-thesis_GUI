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
                def launch_rviz():
                    result = controller.start_rviz()
                    ui.notify(result["message"] or result["error"] or f"RViz: {result['status']}",
                              type="negative" if result["status"] == "ERROR" else "info")

                ui.button("Open RViz", on_click=launch_rviz, color="primary")
                ui.button("Restart RViz", on_click=lambda: controller.restart_rviz()).props("outline")
                ui.button("Stop RViz", on_click=lambda: controller.stop_rviz()).props("outline")
                ui.button("Preview", on_click=controller.request_trajectory_preview).props("outline")
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
