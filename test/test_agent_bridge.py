from hitl_gui.agent_bridge import AgentResponse, AgentToolEvent, ExistingAgentBridge
from hitl_gui.app_state import HitlDecision, TaskStatus, ToolStatus
from hitl_gui.gui_controller import GuiController


def test_existing_agent_success_returns_structured_tool_event():
    response = ExistingAgentBridge("existing_scripted").submit("pick a red cube")
    assert response.message
    assert response.tool_events[0].tool_name == "safe_pick_object"


def test_capability_question_returns_registered_skill_summary_without_a_task():
    response = ExistingAgentBridge("existing_openai").submit("What can you do?")
    assert response.tool_events == []
    assert "supervised pick" in response.message
    assert "plan-only" in response.message


def test_welcome_message_is_added_only_once():
    controller = GuiController()
    controller.add_welcome_message()
    controller.add_welcome_message()
    messages = [entry for entry in controller.state.conversation if entry.name == controller.agent_name]
    assert len(messages) == 1
    assert "work" in messages[0].text.lower() or "task" in messages[0].text.lower()


def test_tool_failure_and_retry_are_preserved_in_history():
    controller = GuiController()
    controller.add_agent_tool_event(AgentToolEvent("plan-1", None, "plan_motion", "Plan Motion Attempt 1", "failed", error_message="blocked"))
    controller.add_agent_tool_event(AgentToolEvent("plan-2", "plan-1", "plan_motion", "Plan Motion Attempt 2", "pending"))
    assert [node.node_id for node in controller.state.tool_nodes[-2:]] == ["plan-1", "plan-2"]
    assert controller.state.tool_nodes[-2].error_message == "blocked"


def test_agent_task_intent_approval_has_a_gui_request_and_does_not_execute():
    controller = GuiController()
    controller.state.current_task_id = "task-agent-review"
    controller.add_agent_tool_event(AgentToolEvent(
        "safe-pick-1", None, "safe_pick_object", "Safe Pick Object",
        "waiting_approval", requires_approval=True, approval_stages=["task_intent"],
    ))

    request = controller.state.pending_hitl_request
    assert request is not None
    assert request.request_type == "task_intent"
    assert controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
    assert controller.state.pending_hitl_request is None
    assert controller.state.task_status == TaskStatus.APPROVED_PENDING_EXECUTION
    assert controller.state.tool_nodes[-1].status == ToolStatus.PENDING
    assert controller.state.tool_nodes[-1].output_data["approval"] == "APPROVED"


def test_async_events_only_mark_ui_dirty_until_page_timer_flushes():
    controller = GuiController()
    calls = []

    class LogRenderer:
        def refresh(self):
            calls.append("log")

    controller._event_renderers = [lambda: calls.append("event")]
    controller._log_renderer = LogRenderer()
    controller.append_event("agent_response_received")

    # An Agent coroutine may call append_event without a NiceGUI slot. No UI
    # callback may run until the page-owned timer invokes the flush method.
    assert calls == []
    assert controller._event_views_dirty is True
    assert controller._log_view_dirty is True

    controller._flush_event_views()
    assert calls == ["log", "event"]
    assert controller._event_views_dirty is False
    assert controller._log_view_dirty is False
