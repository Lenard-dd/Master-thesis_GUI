from hitl_gui.agent_bridge import AgentToolEvent
from hitl_gui.gui_controller import GuiController
from hitl_gui.plan_event_converter import PlanEventConverter
from hitl_gui.task_plan_adapter import TaskPlanAdapter


def test_structured_robot_plan_conversion_preserves_sequence_and_phase_sorting():
    plan = TaskPlanAdapter().from_structured_plan(
        {
            "task": "pick object",
            "steps": [
                {"step_id": "2", "skill_id": "generate_grasp_pose", "description": "grasp", "parameters": {}},
                {"step_id": "1", "skill_id": "detect_object", "description": "detect", "parameters": {"query": "cube"}},
                {"step_id": "3", "skill_id": "verify_grasp", "description": "verify", "parameters": {}},
            ],
        },
        task_id="task-structured",
    )
    assert plan.node_ids == ["2", "1", "3"]
    assert plan.nodes["2"].sequence_index == 0
    assert plan.nodes["1"].phase == "perception"
    assert [node.node_id for node in TaskPlanAdapter.ordered_nodes(plan)] == ["1", "2", "3"]


def test_structured_tool_event_incrementally_creates_and_updates_task_node():
    controller = GuiController()
    controller.state.current_task_id = "task-event"
    controller.state.current_task_name = "observe cube"
    controller.initialize_task_plan("task-event", "observe cube")
    controller.add_agent_tool_event(AgentToolEvent(
        node_id="detect-1", parent_id=None, tool_name="detect_object",
        display_name="Detect Object", description="Detect a reviewed target.",
        status="running", input_json={"query": "cube"},
    ))
    node = controller.state.current_task_plan.nodes["detect-1"]
    assert node.phase == "perception"
    assert node.status == "RUNNING"
    assert node.description == "Detect a reviewed target."
    assert node.current_attempt == 1


def test_unknown_node_status_event_is_ignored_safely():
    adapter = TaskPlanAdapter()
    plan = adapter.create_empty(task_id="task-unknown", title="unknown")
    attempts = {}
    assert PlanEventConverter().apply_status(plan, attempts, "missing", "RUNNING") is False
    assert plan.node_ids == []
    assert attempts == {}


def test_replan_preserves_old_attempt_and_makes_new_attempt_current():
    adapter = TaskPlanAdapter()
    plan = adapter.from_structured_plan(
        {"task": "move", "steps": [{
            "step_id": "motion", "skill_id": "move_to_pregrasp",
            "description": "move", "parameters": {},
        }]},
        task_id="task-replan",
    )
    attempts = {}
    converter = PlanEventConverter()
    assert converter.apply_status(
        plan, attempts, "motion", "RUNNING",
        trajectory_id="trajectory-old", request_id="request-old",
    )
    assert converter.apply_status(plan, attempts, "motion", "INVALIDATED")
    plan.version = 2
    plan.nodes["motion"].plan_version = 2
    assert converter.apply_status(
        plan, attempts, "motion", "RUNNING",
        trajectory_id="trajectory-new", request_id="request-new",
    )
    assert len(attempts["motion"]) == 2
    assert attempts["motion"][0].trajectory_id == "trajectory-old"
    assert attempts["motion"][0].request_id == "request-old"
    assert attempts["motion"][0].status == "INVALIDATED"
    assert attempts["motion"][1].trajectory_id == "trajectory-new"
    assert plan.nodes["motion"].current_attempt == 2
    assert plan.version == 2


def test_controller_plan_version_updates_structured_plan():
    controller = GuiController()
    controller.state.current_task_id = "task-version"
    controller.initialize_task_plan("task-version", "versioned task")
    controller.set_plan_version(3)
    assert controller.state.current_plan_version == 3
    assert controller.state.current_task_plan.version == 3
