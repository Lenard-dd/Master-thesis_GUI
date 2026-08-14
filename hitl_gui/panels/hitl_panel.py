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
                    if candidate.get("tabletop_safety") is not None:
                        clearance = candidate.get("tabletop_min_clearance_m")
                        clearance_text = f"{float(clearance) * 1000.0:.1f} mm" if clearance is not None else "-"
                        backoff = candidate.get("grasp_contact_backoff_m")
                        backoff_text = f"{float(backoff) * 1000.0:.1f} mm" if backoff is not None else "-"
                        ui.label(
                            f"Tabletop {candidate.get('tabletop_safety')} · minimum clearance {clearance_text} · "
                            f"contact backoff {backoff_text}"
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
    real_command_gate = request and request.request_type in {"trajectory_review", "execution"}
    if real_command_gate and controller.state.robot_mode in {"REAL", "REAL ROBOT"}:
        if (
            request.request_type == "trajectory_review"
            and controller.gui_config.get("real_execution", {}).get(
                "arm_approve_is_confirmation", False
            )
        ):
            accepted = controller.submit_hitl_decision(
                request.request_id, HitlDecision.APPROVE,
            )
            if not accepted:
                ui.notify(
                    controller.last_decision_error or "Real trajectory approval was rejected.",
                    type="negative",
                )
            return
        expected_phrase = controller.real_confirmation_phrase(request.request_type)
        with ui.dialog() as dialog, ui.card():
            title = "REAL GRIPPER CONFIRMATION" if request.request_type == "execution" else "REAL ROBOT EXECUTION CONFIRMATION"
            ui.label(title).classes("text-lg font-bold text-negative")
            if request.request_type == "trajectory_review":
                ui.label(f"Trajectory ID: {request.trajectory_id}")
                ui.label(f"Plan Version: {request.plan_version}")
                ui.label("This will execute this exact cached trajectory on the real UR5 after health and safety checks pass.")
            else:
                ui.label(request.title)
                ui.label("This will send one approved command to the real Robotiq gripper.")
            ui.label(f"Type {expected_phrase} to continue.").classes("font-medium text-negative")
            confirmation = ui.input("Confirmation phrase").props("outlined autocomplete=off").classes("w-full")

            def confirm() -> None:
                current = controller.state.pending_hitl_request
                accepted = bool(current) and controller.submit_hitl_decision(
                    current.request_id, HitlDecision.APPROVE,
                    confirmation_phrase=str(confirmation.value or ""),
                )
                if accepted:
                    dialog.close()
                else:
                    ui.notify(
                        controller.last_decision_error
                        or "Confirmation rejected or real-system preflight failed.",
                        type="negative",
                    )
            with ui.row():
                ui.button(
                    "Confirm Execute",
                    on_click=confirm,
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
