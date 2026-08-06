"""Compact, dependency-ordered, read-only TaskPlan timeline."""

from __future__ import annotations

import json

from nicegui import ui

from hitl_gui.task_plan_view import (
    PHASE_LABELS, compact_mapping, elapsed_duration_ms, format_duration,
    node_details, node_summary, plan_header_data, status_presentation,
    timeline_entries, timeline_signature,
)


EXPANDED_STATUSES = {"RUNNING", "FAILED", "WAITING_APPROVAL", "REJECTED", "INVALIDATED"}
GENERIC_SUMMARIES = {"Completed", "Waiting to start", "In progress", "No result available"}


def create_tool_flow_panel(controller):
    # Background Agent/ROS callbacks only mark this view dirty. The timer that
    # performs UI work is created once in the page slot below.
    refresh_state = {"dirty": False}
    scroll_state = {"signature": ()}
    details_state: dict[str, dict[str, bool]] = {}

    def select_node(node_id: str) -> None:
        controller.select_task_node(node_id)
        task_flow.refresh()
        details_panel.refresh()

    with ui.card().classes("w-full h-full min-h-[520px] p-3"):
        ui.label("Agent Execution Flow").classes("text-lg font-semibold")

        @ui.refreshable
        def plan_header():
            plan = controller.state.current_task_plan
            header = plan_header_data(plan)
            if header is None:
                with ui.card().classes("w-full bg-grey-1 p-2"):
                    ui.label("No active task plan").classes("text-grey text-sm")
                return
            composite = next(
                (node for node in plan.nodes.values() if node.node_type == "composite"),
                None,
            )
            with ui.card().classes("w-full bg-blue-1 p-2"):
                with ui.row().classes("w-full items-center justify-between gap-2 no-wrap"):
                    with ui.column().classes("gap-0 min-w-0"):
                        ui.label(header["title"]).classes("text-base font-semibold truncate")
                        ui.label(
                            f"Task {header['task_id']} · Plan {header['plan_id']}"
                        ).classes("text-[11px] text-grey-7 truncate")
                    ui.badge(f"Plan v{header['version']}", color="primary").props("outline")
                with ui.row().classes("w-full items-center gap-x-4 gap-y-1 flex-wrap text-xs"):
                    ui.label(f"Status: {header['status']}")
                    ui.label(f"Current: {header['current_node']}")
                    ui.label(f"Replans: {header['replan_count']}")
                    if composite:
                        presentation = status_presentation(composite.status)
                        ui.badge(
                            f"Composite · {composite.status}", color=presentation["color"],
                        ).props("outline")

        @ui.refreshable
        def task_flow():
            plan = controller.state.current_task_plan
            entries = timeline_entries(plan)
            if not entries:
                ui.label("No executable task step received.").classes("text-grey p-3")
                return

            labels: dict[str, str] = {}
            for candidate in plan.nodes.values() if plan else []:
                if candidate.node_type == "composite":
                    labels[candidate.node_id] = "Task approval"
            for index, (node, reviews) in enumerate(entries, start=1):
                labels[node.node_id] = f"{index:02d}"
                for review_index, review in enumerate(reviews):
                    labels[review.node_id] = f"{index:02d}{chr(ord('a') + review_index)}"

            previous_phase = None
            # Scrolling belongs exclusively to the stable ui.scroll_area
            # outside this refreshable region. Do not add overflow here: this
            # column is rebuilt for Tool Events and would reset to the top.
            with ui.column().classes("w-full gap-0 pr-2"):
                for index, (node, reviews) in enumerate(entries, start=1):
                    if node.phase != previous_phase:
                        _phase_divider(node.phase)
                        previous_phase = node.phase
                    _node_row(
                        controller, node, labels[node.node_id], labels,
                        select_node, plan.version if plan else 1,
                    )
                    for review in reviews:
                        _review_row(
                            controller, review, labels[review.node_id], labels,
                            select_node, plan.version if plan else 1,
                        )
                    if index < len(entries):
                        with ui.row().classes("w-full h-4 items-center"):
                            ui.element("div").classes("ml-[22px] h-4 border-l-2 border-grey-4")

        @ui.refreshable
        def details_panel():
            plan = controller.state.current_task_plan
            details = node_details(
                plan, controller.state.node_attempts,
                controller.state.selected_task_node_id,
            )
            with ui.card().classes("w-full bg-grey-1 p-2"):
                ui.label("Selected Node Details").classes("text-sm font-semibold")
                if details is None:
                    ui.label("Select a step to inspect its data and attempts.").classes("text-xs text-grey-7")
                    return
                node = details["node"]
                presentation = status_presentation(node.status)
                with ui.row().classes("w-full items-center gap-1 no-wrap"):
                    ui.icon(presentation["icon"], color=presentation["color"]).classes("text-base")
                    ui.label(node.display_name).classes("text-sm font-semibold truncate flex-grow")
                    ui.badge(str(node.status), color=presentation["color"]).props("outline").classes("text-[9px]")
                if node.description:
                    ui.label(node.description).classes("text-xs text-grey-8 break-words")
                ui.separator().classes("my-1")
                _detail_row("Node ID", node.node_id, monospace=True)
                _detail_row("Phase", PHASE_LABELS.get(node.phase, node.phase))
                _detail_row("Tool", node.tool_name or "HITL", monospace=True)
                _detail_row("Plan", f"v{node.plan_version}")
                _detail_row("Depends on", ", ".join(node.dependencies) or "—", monospace=True)
                _detail_row("Started", _short_time(node.start_time))
                _detail_row("Ended", _short_time(node.end_time))
                duration = elapsed_duration_ms(node)
                if duration is not None and duration > 0:
                    _detail_row("Duration", format_duration(duration))
                _detail_row("Approval", "Required" if node.requires_approval else "No")
                if node.error_message:
                    ui.label(f"Error: {node.error_message}").classes("text-xs text-negative break-words")
                section_state = details_state.setdefault(node.node_id, {})
                _json_section("Input", node.input_data, section_state, "input")
                _json_section("Output", node.output_data, section_state, "output")
                with ui.expansion(
                    f"Attempt History · {details['attempt_count']}", icon="history",
                    value=section_state.get("history", details["attempt_count"] > 1),
                    on_value_change=lambda event, state=section_state: state.__setitem__("history", bool(event.value)),
                ).classes("w-full text-xs"):
                    if not details["attempts"]:
                        ui.label("No execution attempt recorded.").classes("text-xs text-grey-7")
                    for attempt in details["attempts"]:
                        _attempt_view(
                            attempt, attempt.attempt_number == details["current_attempt"],
                            section_state,
                        )

        plan_header()
        with ui.element("div").classes(
            "w-full grid grid-cols-1 2xl:grid-cols-[minmax(0,70fr)_minmax(250px,30fr)] gap-2 items-start"
        ):
            with ui.column().classes("w-full min-w-0"):
                # Keep the scroll container stable while refreshable rebuilds
                # only its contents; this preserves its scroll position.
                with ui.scroll_area().classes("w-full h-[65vh] min-h-[420px] max-h-[720px]") as task_scroll:
                    task_flow()
            with ui.column().classes("w-full min-w-0 2xl:sticky 2xl:top-2"):
                details_panel()

        scroll_state["signature"] = timeline_signature(controller.state.current_task_plan)

    def flush_refresh() -> None:
        refresh_state["dirty"] = False
        new_signature = timeline_signature(controller.state.current_task_plan)
        step_added = (
            bool(new_signature)
            and new_signature != scroll_state["signature"]
            and len(new_signature) >= len(scroll_state["signature"])
        )
        plan_header.refresh()
        task_flow.refresh()
        details_panel.refresh()
        scroll_state["signature"] = new_signature
        if step_added:
            # Native NiceGUI ScrollArea method; no page reload or custom JS.
            task_scroll.scroll_to(percent=1.0, duration=0.15)

    def refresh() -> None:
        refresh_state["dirty"] = True

    def flush_if_dirty() -> None:
        if refresh_state["dirty"]:
            flush_refresh()

    def refresh_running_duration() -> None:
        plan = controller.state.current_task_plan
        if plan and any(str(node.status).upper() == "RUNNING" for node in plan.nodes.values()):
            task_flow.refresh()
            selected = plan.nodes.get(controller.state.selected_task_node_id or "")
            if selected and str(selected.status).upper() == "RUNNING":
                details_panel.refresh()

    ui.timer(0.15, flush_if_dirty)
    ui.timer(1.0, refresh_running_duration)
    return refresh


def _phase_divider(phase: str) -> None:
    with ui.row().classes("w-full items-center gap-2 py-1"):
        ui.badge(PHASE_LABELS.get(phase, phase.replace("_", " ").title())).props("outline color=grey-7").classes("text-[9px]")
        ui.separator().classes("flex-grow")


def _node_row(controller, node, step_label, labels, on_select, current_plan_version) -> None:
    presentation = status_presentation(node.status)
    attempts = controller.state.node_attempts.get(node.node_id, [])
    selected = controller.state.selected_task_node_id == node.node_id
    border = "border-primary bg-blue-1" if selected else "border-grey-4 bg-white"
    summary = node_summary(node)
    expanded = str(node.status).upper() in EXPANDED_STATUSES or len(attempts) > 1
    card = ui.card().classes(f"w-full px-2 py-1 cursor-pointer border {border} hover:bg-blue-1")
    card.on("click", lambda _event, node_id=node.node_id: on_select(node_id))
    with card:
        with ui.row().classes("w-full min-h-[38px] items-center gap-2 no-wrap"):
            ui.label(step_label).classes("w-8 text-center text-xs font-mono text-grey-7")
            ui.icon(presentation["icon"], color=presentation["color"]).classes("text-base")
            ui.label(node.display_name).classes("text-sm font-medium min-w-0 flex-grow truncate")
            ui.badge(str(node.status), color=presentation["color"]).props("outline").classes("text-[9px]")
        if summary not in GENERIC_SUMMARIES:
            ui.label(summary).classes("w-full pl-10 text-xs text-grey-7 break-words")
        if expanded:
            _node_meta(node, attempts, labels, current_plan_version)


def _review_row(controller, node, step_label, labels, on_select, current_plan_version) -> None:
    presentation = status_presentation(node.status)
    selected = controller.state.selected_task_node_id == node.node_id
    border = "border-primary bg-amber-1" if selected else "border-amber-5 bg-amber-1"
    card = ui.card().classes(f"ml-10 w-[calc(100%-2.5rem)] px-2 py-1 cursor-pointer border-l-4 {border}")
    card.on("click", lambda _event, node_id=node.node_id: on_select(node_id))
    with card:
        with ui.row().classes("w-full min-h-[32px] items-center gap-2 no-wrap"):
            ui.label(step_label).classes("w-8 text-center text-[10px] font-mono text-grey-7")
            ui.icon(presentation["icon"], color=presentation["color"]).classes("text-sm")
            ui.label(node.display_name).classes("text-xs font-medium min-w-0 truncate flex-grow")
            if node.plan_version > 1 or node.plan_version != current_plan_version:
                ui.badge(f"v{node.plan_version}").props("outline color=grey-7").classes("text-[8px]")
            ui.badge(str(node.status), color=presentation["color"]).props("outline").classes("text-[8px]")
        if str(node.status).upper() in EXPANDED_STATUSES:
            _node_meta(node, controller.state.node_attempts.get(node.node_id, []), labels, current_plan_version)


def _node_meta(node, attempts, labels, current_plan_version) -> None:
    duration = elapsed_duration_ms(node)
    dependencies = [labels.get(item, _short_id(item)) for item in node.dependencies]
    with ui.row().classes("w-full pl-10 gap-3 items-center flex-wrap text-[10px] text-grey-7"):
        if dependencies:
            ui.label(f"after {' + '.join(dependencies)}")
        if duration is not None and duration > 0:
            ui.label(format_duration(duration))
        if len(attempts) > 1:
            ui.label(f"Attempt {node.current_attempt}/{len(attempts)}")
        if node.plan_version > 1 or node.plan_version != current_plan_version:
            ui.label(f"Plan v{node.plan_version}")
        if str(node.status).upper() == "WAITING_APPROVAL":
            ui.badge("Human approval required", color="warning").props("outline").classes("text-[8px]")
        if node.error_message:
            ui.label(node.error_message).classes("text-negative break-words")


def _detail_row(label: str, value, *, monospace: bool = False) -> None:
    value_class = "font-mono" if monospace else ""
    with ui.row().classes("w-full items-start gap-1 text-xs no-wrap"):
        ui.label(f"{label}:").classes("font-medium min-w-[76px]")
        ui.label(str(value)).classes(f"break-all min-w-0 {value_class}")


def _json_section(title: str, data, state: dict[str, bool], key: str) -> None:
    with ui.expansion(
        f"{title} · {compact_mapping(data)}", icon="data_object",
        value=state.get(key, False),
        on_value_change=lambda event, target=state, name=key: target.__setitem__(name, bool(event.value)),
    ).classes("w-full text-xs"):
        ui.code(json.dumps(data or {}, ensure_ascii=False, indent=2, default=str)).classes(
            "w-full max-h-48 overflow-auto text-[10px]"
        )


def _attempt_view(attempt, is_current: bool, state: dict[str, bool]) -> None:
    presentation = status_presentation(attempt.status)
    suffix = " · Current" if is_current else ""
    with ui.expansion(
        f"Attempt {attempt.attempt_number} · {attempt.status}{suffix}",
        icon=presentation["icon"],
        value=state.get(f"attempt:{attempt.attempt_id}", False),
        on_value_change=lambda event, target=state, key=f"attempt:{attempt.attempt_id}": target.__setitem__(key, bool(event.value)),
    ).classes("w-full text-xs"):
        if attempt.duration_ms:
            _detail_row("Duration", format_duration(attempt.duration_ms))
        _detail_row("Trajectory", attempt.trajectory_id or "—", monospace=True)
        _detail_row("Request", attempt.request_id or "—", monospace=True)
        if attempt.error_message:
            ui.label(f"Error: {attempt.error_message}").classes("text-xs text-negative break-words")
        ui.label(f"Input: {compact_mapping(attempt.input_data)}").classes("text-[10px] break-words")
        ui.label(f"Output: {compact_mapping(attempt.output_data)}").classes("text-[10px] break-words")


def _short_time(value: str | None) -> str:
    if not value:
        return "—"
    return value.split("T", 1)[-1].replace("+00:00", " UTC")


def _short_id(value: str) -> str:
    return value if len(value) <= 18 else f"…{value[-15:]}"
