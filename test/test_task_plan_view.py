from hitl_gui.app_state import ToolNode, ToolStatus
from hitl_gui.gui_controller import GuiController
from hitl_gui.models.task_plan import NodeExecutionAttempt, TaskNode
from hitl_gui.task_plan_view import (
    execution_order, grouped_nodes, node_details, node_summary, plan_header_data,
    status_presentation, timeline_entries, timeline_signature,
)


def _node(node_id, tool, phase, sequence, status="PENDING", output=None, error=None, version=1):
    return TaskNode(
        node_id=node_id, parent_id=None, display_name=node_id.replace("_", " ").title(),
        description=f"Description for {node_id}", node_type="tool", phase=phase,
        sequence_index=sequence, status=status, tool_name=tool,
        output_data=output or {}, error_message=error, plan_version=version,
    )


def test_nodes_are_grouped_by_phase_then_sequence_index():
    controller = GuiController()
    plan = controller.initialize_task_plan("task-view", "View task")
    nodes = [
        _node("grasp", "generate_grasp_candidates", "grasp_generation", 4),
        _node("detect-late", "detect_objects", "perception", 3),
        _node("understand", "understand_instruction", "understanding", 8),
        _node("detect-first", "detect_objects", "perception", 1),
    ]
    for node in nodes:
        plan.nodes[node.node_id] = node
        plan.node_ids.append(node.node_id)
    groups = grouped_nodes(plan)
    assert [phase for phase, _ in groups] == ["understanding", "perception", "grasp_generation"]
    assert [node.node_id for node in groups[1][1]] == ["detect-first", "detect-late"]


def test_waiting_approval_has_icon_text_badge_presentation_and_summary():
    node = _node("review", "trajectory_review", "hitl_review", 0, "WAITING_APPROVAL")
    presentation = status_presentation(node.status)
    assert presentation["symbol"] == "!"
    assert presentation["icon"] == "approval"
    assert presentation["color"] == "warning"
    assert node_summary(node) == "Waiting for user approval"


def test_failed_node_summary_displays_error():
    node = _node("failed", "detect_objects", "perception", 0, "FAILED", error="camera timeout")
    assert node_summary(node) == "Error: camera timeout"


def test_missing_output_data_is_safe_for_every_supported_summary():
    tools = [
        "detect_objects", "select_target", "generate_grasp_candidates", "validate_grasp",
        "plan_motion", "trajectory_review", "execute_motion", "verify_grasp",
    ]
    for index, tool in enumerate(tools):
        summary = node_summary(_node(str(index), tool, "understanding", index, output=None))
        assert isinstance(summary, str) and summary


def test_structured_mock_output_summary_reaches_task_node():
    controller = GuiController()
    controller.state.current_task_id = "task-summary"
    controller.initialize_task_plan("task-summary", "summary")
    legacy = ToolNode(
        node_id="detect", parent_id=None, tool_name="detect_objects",
        display_name="Detect Objects", output_summary={"detected_count": 3},
    )
    controller.register_tool_node(legacy)
    assert node_summary(controller.state.current_task_plan.nodes["detect"]) == "3 objects found"


def test_replan_attempt_history_and_current_attempt_are_exposed_in_details():
    controller = GuiController()
    plan = controller.initialize_task_plan("task-attempt", "attempt")
    node = _node("motion", "plan_motion", "motion_planning", 0, version=2)
    plan.nodes[node.node_id] = node
    plan.node_ids.append(node.node_id)
    controller.state.node_attempts[node.node_id] = [
        NodeExecutionAttempt("a1", node.node_id, 1, status="INVALIDATED", trajectory_id="old", request_id="r1"),
        NodeExecutionAttempt("a2", node.node_id, 2, status="WAITING_APPROVAL", trajectory_id="new", request_id="r2"),
    ]
    node.current_attempt = 2
    plan.version = 2
    details = node_details(plan, controller.state.node_attempts, node.node_id)
    header = plan_header_data(plan)
    assert details["attempt_count"] == 2
    assert details["current_attempt"] == 2
    assert details["attempts"][0].trajectory_id == "old"
    assert details["attempts"][1].trajectory_id == "new"
    assert header["version"] == 2
    assert header["replan_count"] == 1


def test_controller_selection_returns_correct_node_details_and_rejects_unknown_id():
    controller = GuiController()
    plan = controller.initialize_task_plan("task-select", "select")
    node = _node("chosen", "detect_objects", "perception", 0)
    plan.nodes[node.node_id] = node
    plan.node_ids.append(node.node_id)
    assert controller.select_task_node("chosen") is node
    assert node_details(plan, controller.state.node_attempts, controller.state.selected_task_node_id)["node"] is node
    assert controller.select_task_node("missing") is None
    assert controller.state.selected_task_node_id == "chosen"


def test_execution_timeline_follows_dependencies_instead_of_phase_grouping():
    controller = GuiController()
    plan = controller.initialize_task_plan("task-order", "ordered task")
    detect = _node("detect", "detect_object", "perception", 30)
    motion = _node("motion", "move_to_named_target", "motion_planning", 10)
    verify = _node("verify", "verify_grasp", "verification", 1)
    detect.dependencies = [motion.node_id]
    verify.dependencies = [detect.node_id]
    for node in (verify, detect, motion):
        plan.nodes[node.node_id] = node
        plan.node_ids.append(node.node_id)
    assert [node.node_id for node in execution_order(plan)] == ["motion", "detect", "verify"]


def test_trajectory_reviews_are_nested_under_their_motion_step():
    controller = GuiController()
    plan = controller.initialize_task_plan("task-review", "review task")
    motion = _node("motion", "move_to_pregrasp", "motion_planning", 0)
    review_old = _node("review-old", "trajectory_review", "hitl_review", 1, "INVALIDATED")
    review_old.parent_id = motion.node_id
    review_old.dependencies = [motion.node_id]
    review_new = _node("review-new", "trajectory_review", "hitl_review", 2, "WAITING_APPROVAL", version=2)
    review_new.parent_id = motion.node_id
    review_new.dependencies = [motion.node_id]
    for node in (motion, review_old, review_new):
        plan.nodes[node.node_id] = node
        plan.node_ids.append(node.node_id)
    entries = timeline_entries(plan)
    assert len(entries) == 1
    assert entries[0][0].node_id == "motion"
    assert [node.node_id for node in entries[0][1]] == ["review-old", "review-new"]
    assert timeline_signature(plan) == ("motion", "review-old", "review-new")


def test_dynamic_safe_pick_nodes_form_an_explicit_execution_chain():
    controller = GuiController()
    controller.state.current_task_id = "task-chain"
    controller.state.current_task_name = "pick cube"
    controller.initialize_task_plan("task-chain", "pick cube")
    parent = ToolNode(
        node_id="safe-pick", parent_id=None, tool_name="safe_pick_object",
        display_name="Safe Pick Object", status=ToolStatus.RUNNING,
    )
    controller.register_tool_node(parent)
    controller.skill_runtime._last_node_ids["task-chain"] = parent.node_id
    detect = controller.skill_runtime._add_node(parent, "detect_object", "Detect Object", {})
    cloud = controller.skill_runtime._add_node(parent, "build_object_point_cloud", "Build Cloud", {})
    assert detect.parent_id == parent.node_id
    assert cloud.parent_id == parent.node_id
    assert detect.dependencies == [parent.node_id]
    assert cloud.dependencies == [detect.node_id]
    assert controller.state.current_task_plan.nodes[cloud.node_id].dependencies == [detect.node_id]


def test_parent_containment_is_not_inferred_as_execution_dependency():
    controller = GuiController()
    controller.state.current_task_id = "task-containment"
    controller.initialize_task_plan("task-containment", "containment")
    child = ToolNode(
        node_id="child", parent_id="composite", tool_name="detect_object",
        display_name="Detect Object",
    )
    controller.register_tool_node(child)
    assert controller.state.current_task_plan.nodes["child"].parent_id == "composite"
    assert controller.state.current_task_plan.nodes["child"].dependencies == []
