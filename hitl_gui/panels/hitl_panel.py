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
                elif request.request_type == "target_review":
                    selected = next((item for item in request.candidate_objects
                                     if str(item.get("object_id")) == str(request.object_id)), {})
                    ui.label(f"Selected: {selected.get('label', '-')} · {request.object_id or '-'}").classes("text-sm font-medium")
                    confidence = selected.get("confidence")
                    ui.label(f"Confidence: {confidence if confidence is not None else '-'} · Position: {selected.get('position_summary', selected.get('position', '-'))}").classes("text-xs text-grey")
                    ui.label(f"{len(request.candidate_objects)} candidate object(s)").classes("text-xs")
                    with ui.expansion("Candidate objects", icon="view_list").classes("w-full text-xs"):
                        for item in request.candidate_objects:
                            marker = "→" if str(item.get("object_id")) == str(request.object_id) else "·"
                            ui.label(
                                f"{marker} {item.get('label', 'object')} · {item.get('object_id', '-')} · confidence {item.get('confidence', '-')}"
                            ).classes("text-xs")
                elif request.request_type == "grasp_review":
                    candidate = request.grasp_candidates[request.selected_index] if request.grasp_candidates else {}
                    scores = [item.get("score") for item in request.grasp_candidates if item.get("score") is not None]
                    ui.label(
                        f"{len(request.grasp_candidates)} valid candidate(s) · best score {max(scores) if scores else '-'}"
                    ).classes("text-xs text-grey")
                    ui.label(f"Candidate: {request.grasp_candidate_id or '-'} · rank {candidate.get('rank', request.selected_index + 1)}").classes("text-sm font-medium")
                    ui.label(
                        f"score {candidate.get('score', '-')} · IK {candidate.get('ik_result', 'UNKNOWN')} · "
                        f"collision {candidate.get('collision_result', 'UNKNOWN')}"
                    ).classes("text-xs")
                    ui.label(
                        f"approach {candidate.get('approach_distance', '-')} · joint margin {candidate.get('joint_margin', '-')} · "
                        f"orientation change {candidate.get('orientation_change', '-')}"
                    ).classes("text-xs text-grey")
                elif request.request_type == "error_recovery":
                    ui.badge(request.error_type or "error", color="negative")
                    ui.label(request.description).classes("text-sm text-grey")
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

        @ui.refreshable
        def action_view():
            request = controller.state.pending_hitl_request
            if request is None:
                return
            with ui.row().classes("w-full gap-2 mt-2 flex-wrap"):
                if request.request_type == "target_review":
                    ui.button("Confirm", color="positive", on_click=lambda: controller.skill_runtime.select_target(
                        request.request_id, str(request.object_id))).props("dense")
                    ui.button("Select Another", on_click=lambda: controller.skill_runtime.select_next_target(request.request_id)).props("dense outline")
                    ui.button("Reject Task", color="negative", on_click=controller.cancel_task).props("dense outline")
                elif request.request_type == "grasp_review":
                    ui.button("Approve Grasp", color="positive", on_click=lambda: controller.approve_grasp_candidate(request.request_id)).props("dense")
                    ui.button("Next Candidate", on_click=lambda: controller.skill_runtime.select_next_grasp(request.request_id)).props("dense outline")
                    ui.button("Reject", color="negative", on_click=controller.cancel_task).props("dense outline")
                    ui.button("Regenerate", on_click=lambda: controller.skill_runtime.regenerate_grasps(request.request_id)).props("dense outline")
                elif request.request_type == "error_recovery":
                    for action in request.recovery_actions:
                        ui.button(action, color="negative" if action == "Cancel" else "primary",
                                  on_click=lambda selected=action: controller.skill_runtime.handle_recovery(
                                      request.request_id, selected)).props("dense outline" if action != "Retry" else "dense")
                else:
                    def submit(decision: HitlDecision) -> None:
                        current = controller.state.pending_hitl_request
                        if current is not None:
                            controller.submit_hitl_decision(current.request_id, decision)
                    ui.button("Approve", color="positive", on_click=lambda: _approve(controller, submit)).props("dense")
                    ui.button("Reject", color="negative", on_click=lambda: submit(HitlDecision.REJECT)).props("dense")
                    if request.request_type == "trajectory_review":
                        ui.button("Replan", color="primary", on_click=lambda: _replan_dialog(controller)).props("dense")
                    ui.button("Cancel", on_click=controller.cancel_task).props("dense outline")

        action_view()

    def refresh() -> None:
        request_view.refresh()
        action_view.refresh()

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
