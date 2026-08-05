from nicegui import ui

from hitl_gui.app_state import HitlDecision


def create_hitl_panel(controller):
    with ui.card().classes("w-full"):
        ui.label("Current HITL Request").classes("text-lg font-semibold")

        @ui.refreshable
        def request_view():
            request = controller.state.pending_hitl_request
            if request is None:
                ui.label("No review request is pending.").classes("text-grey")
            else:
                ui.label(request.title).classes("font-medium")
                ui.label(f"Trajectory: {request.trajectory_id}")
                ui.label(request.description).classes("text-grey")

        request_view()
        with ui.row().classes("w-full gap-2 mt-2 flex-wrap"):
            def launch_rviz():
                result = controller.start_rviz()
                ui.notify(result["message"] or result["error"] or f"RViz: {result['status']}",
                          type="negative" if result["status"] == "ERROR" else "info")

            ui.button("Open RViz", on_click=launch_rviz, color="primary")
            ui.button("Restart RViz", on_click=controller.restart_rviz).props("outline")
            ui.button("Stop RViz", on_click=controller.stop_rviz).props("outline")
            ui.button("Preview", on_click=controller.request_trajectory_preview).props("outline")

            def submit(decision: HitlDecision) -> None:
                request = controller.state.pending_hitl_request
                if request is not None:
                    controller.submit_hitl_decision(request.request_id, decision)

            approve_button = ui.button("Approve", color="positive", on_click=lambda: submit(HitlDecision.APPROVE))
            reject_button = ui.button("Reject", color="negative", on_click=lambda: submit(HitlDecision.REJECT))
            replan_button = ui.button("Replan", color="primary", on_click=lambda: submit(HitlDecision.REPLAN))
            ui.button("Cancel", on_click=controller.cancel_task).props("outline")

    def refresh() -> None:
        request_view.refresh()
        request = controller.state.pending_hitl_request
        enabled = request is not None
        approve_button.set_enabled(enabled)
        reject_button.set_enabled(enabled)
        replan_button.set_enabled(enabled and request.request_type == "trajectory_review")

    refresh()
    return refresh
