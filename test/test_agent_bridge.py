from hitl_gui.agent_bridge import AgentResponse, AgentToolEvent, ExistingAgentBridge
from hitl_gui.gui_controller import GuiController


def test_existing_agent_success_returns_structured_tool_event():
    response = ExistingAgentBridge("existing_scripted").submit("pick a red cube")
    assert response.message
    assert response.tool_events[0].tool_name == "safe_pick_object"


def test_tool_failure_and_retry_are_preserved_in_history():
    controller = GuiController()
    controller.add_agent_tool_event(AgentToolEvent("plan-1", None, "plan_motion", "Plan Motion Attempt 1", "failed", error_message="blocked"))
    controller.add_agent_tool_event(AgentToolEvent("plan-2", "plan-1", "plan_motion", "Plan Motion Attempt 2", "pending"))
    assert [node.node_id for node in controller.state.tool_nodes[-2:]] == ["plan-1", "plan-2"]
    assert controller.state.tool_nodes[-2].error_message == "blocked"
