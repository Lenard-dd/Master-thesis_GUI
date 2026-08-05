from nicegui import ui

from hitl_gui.app_state import HitlDecision


def create_hitl_panel(controller):
    with ui.card().classes("w-full p-3"):
        ui.label("Current HITL Request").classes("text-base font-semibold")

        @ui.refreshable
        def request_view():
            request = controller.state.pending_hitl_request
            if request is None:
                ui.label("No review request is pending.").classes("text-grey")
            else:
                ui.label(request.title).classes("font-medium")
                if request.request_type == "trajectory_review":
                    ui.label(f"Trajectory: {request.trajectory_id or '-'}").classes("text-xs font-mono")
                    ui.label(
                        f"Plan {request.plan_version} · {request.target_summary or 'Target unavailable'}"
                    ).classes("text-sm")
                    with ui.row().classes("w-full gap-2 items-center"):
                        result = "SUCCESS" if request.planning_success else "FAILED"
                        ui.badge(result, color="positive" if request.planning_success else "negative")
                        ui.badge(f"Collision: {request.collision_check}", color=(
                            "positive" if request.collision_check == "ALLOW" else "warning"
                        ))
                    duration = f"{request.trajectory_duration:.2f}s" if isinstance(request.trajectory_duration, (int, float)) else "-"
                    planning = f"{request.planning_time}ms" if request.planning_time is not None else "-"
                    ui.label(
                        f"{request.trajectory_points} points · duration {duration} · planning {planning} · {controller.state.robot_mode}"
                    ).classes("text-xs text-grey")
                else:
                    ui.label(request.description).classes("text-sm text-grey")

        request_view()
        with ui.row().classes("w-full gap-2 mt-2 flex-wrap"):
            def launch_rviz():
                result = controller.start_rviz()
                ui.notify(result["message"] or result["error"] or f"RViz: {result['status']}",
                          type="negative" if result["status"] == "ERROR" else "info")

            ui.button("Open RViz", on_click=launch_rviz, color="primary").props("dense")
            ui.button("Restart", on_click=controller.restart_rviz).props("dense outline")
            ui.button("Stop RViz", on_click=controller.stop_rviz).props("dense outline")
            ui.button("Preview", on_click=controller.preview_current_trajectory).props("dense outline")

            def submit(decision: HitlDecision) -> None:
                request = controller.state.pending_hitl_request
                if request is not None:
                    controller.submit_hitl_decision(request.request_id, decision)

            approve_button = ui.button("Approve", color="positive", on_click=lambda: _approve(controller, submit)).props("dense")
            reject_button = ui.button("Reject", color="negative", on_click=lambda: submit(HitlDecision.REJECT)).props("dense")
            replan_button = ui.button("Replan", color="primary", on_click=lambda: _replan_dialog(controller)).props("dense")
            ui.button("Cancel", on_click=controller.cancel_task).props("dense outline")

    def refresh() -> None:
        request_view.refresh()
        request = controller.state.pending_hitl_request
        enabled = request is not None
        approve_button.set_enabled(enabled)
        reject_button.set_enabled(enabled)
        replan_button.set_enabled(enabled and request.request_type == "trajectory_review")

    refresh()
    return refresh


def _approve(controller, submit) -> None:
    request = controller.state.pending_hitl_request
    if request and request.request_type == "trajectory_review" and controller.state.robot_mode in {"REAL", "REAL ROBOT"}:
        with ui.dialog() as dialog, ui.card():
            ui.label("REAL ROBOT EXECUTION CONFIRMATION").classes("text-lg font-bold text-negative")
            ui.label(f"Trajectory ID: {request.trajectory_id}")
            ui.label("This will request real robot motion after all backend safety checks pass.")
            with ui.row():
                ui.button(
                    "Confirm Execute",
                    on_click=lambda: (controller.submit_hitl_decision(
                        request.request_id, HitlDecision.APPROVE, real_confirmed=True), dialog.close()),
                    color="negative",
                )
                ui.button("Cancel", on_click=dialog.close).props("outline")
        dialog.open()
        return
    submit(HitlDecision.APPROVE)


def _replan_dialog(controller) -> None:
    request = controller.state.pending_hitl_request
    if request is None or request.request_type != "trajectory_review":
        return
    with ui.dialog() as dialog, ui.card():
        ui.label("Request trajectory replan").classes("text-lg font-semibold")
        reason = ui.select(
            ["trajectory unsafe", "orientation unsuitable", "path too long", "target incorrect", "other"],
            value="other", label="Reason",
        ).classes("w-72")
        with ui.row():
            ui.button("Request Replan", on_click=lambda: (controller.replan_trajectory(request.request_id, reason.value), dialog.close()), color="primary")
            ui.button("Cancel", on_click=dialog.close).props("outline")
    dialog.open()
