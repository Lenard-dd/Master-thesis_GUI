import asyncio

from hitl_gui.agent_bridge import AgentResponse, AgentToolEvent, ExistingAgentBridge
from hitl_gui.app_state import HitlDecision, TaskStatus, ToolStatus
from hitl_gui.gui_controller import GuiController


def test_existing_agent_success_returns_structured_tool_event():
    response = ExistingAgentBridge("existing_scripted").submit("pick a red cube")
    assert response.message
    assert response.tool_events[0].tool_name == "safe_pick_object"


def test_scene_scan_typo_is_inferred_as_a_read_only_scene_description():
    response = ExistingAgentBridge("existing_scripted").submit("scan the senario")

    assert response.tool_events[0].tool_name == "describe_scene"
    assert "read-only scene scan" in response.message


def test_relative_place_followup_uses_the_localization_query_order():
    followup = ExistingAgentBridge._relative_place_followup(
        "detect and compute a plan-only pose to place apple between black cube and white cube",
        "detect_objects",
        {"queries": ["apple", "black cube", "white cube"]},
    )

    assert followup == {
        "source_id": "apple",
        "relation": "between",
        "reference_ids": ["black cube", "white cube"],
    }


def test_all_objects_expands_to_the_latest_scene_candidates():
    bridge = ExistingAgentBridge("existing_scripted")
    bridge.record_scene_description({
        "candidate_objects": [
            {"query": "green apple"},
            {"query": "black cube with number 3"},
            {"query": "white cube with number 5"},
        ]
    })

    response = bridge.submit("localize all the objects")

    assert response.tool_events[0].tool_name == "detect_objects"
    assert response.tool_events[0].input_json["queries"] == [
        "green apple", "black cube with number 3", "white cube with number 5",
    ]


def test_semantic_relative_place_uses_persistent_verified_scene_only():
    bridge = ExistingAgentBridge(
        "existing_openai",
        semantic_intent_parser=lambda _instruction, _objects: {
            "kind": "relative_place",
            "source": "black_cube_1",
            "relation": "on_top_of",
            "references": ["white_cube_1"],
            "confidence": 0.95,
            "needs_clarification": False,
        },
    )
    bridge.record_localization_result({
        "scene": {"objects": [
            {"object_id": "black_cube_1", "label": "black cube", "pose_available": True, "pose": {}},
            {"object_id": "white_cube_1", "label": "white cube", "pose_available": True, "pose": {}},
        ]}
    })

    response = bridge.submit("compute a plan-only pose to put the dark one on the light one")

    assert response.tool_events[0].tool_name == "compute_place_pose"
    assert response.tool_events[0].input_json == {
        "source_id": "black_cube_1",
        "relation": "on_top_of",
        "reference_ids": ["white_cube_1"],
    }


def test_semantic_relative_place_rejects_an_object_not_in_verified_scene():
    bridge = ExistingAgentBridge(
        "existing_openai",
        semantic_intent_parser=lambda _instruction, _objects: {
            "kind": "relative_place",
            "source": "invented_cube",
            "relation": "right_of",
            "references": ["white_cube_1"],
            "confidence": 0.95,
            "needs_clarification": False,
        },
    )
    bridge.record_localization_result({
        "scene": {"objects": [
            {"object_id": "white_cube_1", "label": "white cube", "pose_available": True, "pose": {}},
        ]}
    })

    response = bridge.submit("put it beside the white cube")

    assert response.tool_events == []
    assert "cannot safely match" in response.message


def test_semantic_pick_and_place_compiles_a_dependency_dag():
    bridge = ExistingAgentBridge(
        "existing_openai",
        semantic_intent_parser=lambda _instruction, _objects: {
            "kind": "localize_then_relative_place",
            "source": "green apple",
            "relation": "between",
            "references": ["black cube", "white cube"],
            "pick_required": True,
            "confidence": 0.95,
            "needs_clarification": False,
        },
    )

    response = bridge.submit("pick the apple and put it midway between the cubes")

    assert [event.tool_name for event in response.tool_events] == [
        "move_to_named_target", "detect_objects", "compute_place_pose", "supervised_pick_from_localization",
    ]
    assert response.tool_events[1].dependencies == ["agent-move_to_observe-1"]
    assert response.tool_events[2].dependencies == ["agent-detect_objects-2"]
    assert response.tool_events[3].dependencies == ["agent-compute_place_pose-3"]
    assert response.tool_events[3].requires_approval is True
    assert response.tool_events[3].input_json == {"object_query": "green apple"}


def test_plain_place_request_is_a_supervised_pick_and_place_not_only_a_pose_computation():
    bridge = ExistingAgentBridge(
        "existing_openai",
        semantic_intent_parser=lambda _instruction, _objects: {
            "kind": "localize_then_relative_place",
            "source": "white cube", "relation": "on_top_of", "references": ["black cube"],
            # Simulate an older parser response: the bridge must not allow
            # this false value to downgrade a physical place command.
            "pick_required": False, "confidence": 0.95, "needs_clarification": False,
        },
    )

    response = bridge.submit("place the white cube on top of the black cube")

    assert [event.tool_name for event in response.tool_events] == [
        "move_to_named_target", "detect_objects", "compute_place_pose", "supervised_pick_from_localization",
    ]


def test_structured_multi_subgoal_plan_compiles_two_serial_pick_place_workflows():
    bridge = ExistingAgentBridge(
        "existing_openai",
        structured_task_parser=lambda _instruction, _objects, _candidates: {
            "kind": "task_plan",
            "summary": "I will perform two reviewed placements in order.",
            "needs_clarification": False,
            "subgoals": [
                {
                    "action": "pick_place", "source": "red cube",
                    "relation": "right_of", "references": ["black cube"],
                },
                {
                    "action": "pick_place", "source": "white cube",
                    "relation": "right_of", "references": ["red cube"],
                },
            ],
        },
    )

    response = bridge.submit(
        "First put the red cube right of the black cube, then put the white cube right of the red cube."
    )

    assert [event.tool_name for event in response.tool_events] == [
        "move_to_named_target", "detect_objects", "compute_place_pose", "supervised_pick_from_localization",
        "move_to_named_target", "detect_objects", "compute_place_pose", "supervised_pick_from_localization",
    ]
    first_pick = response.tool_events[3]
    second_observe = response.tool_events[4]
    assert first_pick.input_json == {"object_query": "red cube"}
    assert second_observe.dependencies == [first_pick.node_id]
    assert response.tool_events[7].input_json == {"object_query": "white cube"}


def test_structured_plan_rejects_an_ungrounded_bulk_object_placeholder():
    bridge = ExistingAgentBridge(
        "existing_openai",
        structured_task_parser=lambda _instruction, _objects, _candidates: {
            "kind": "task_plan", "needs_clarification": False,
            "subgoals": [{
                "action": "pick_place", "source": "all objects",
                "relation": "right_of", "references": ["black cube"],
            }],
        },
    )

    response = bridge.submit("First arrange all objects, then continue.")

    assert response.tool_events == []
    assert "source object" in response.message


def test_multi_subgoal_planner_precedes_the_standalone_all_candidates_shortcut():
    bridge = ExistingAgentBridge(
        "existing_openai",
        structured_task_parser=lambda _instruction, _objects, _candidates: {
            "kind": "task_plan", "confidence": 0.9, "needs_clarification": False,
            "subgoals": [
                {"action": "localize", "queries": ["green apple", "black cube"]},
                {
                    "action": "relative_place", "source": "green apple",
                    "relation": "between", "references": ["black cube", "white cube"],
                },
            ],
        },
    )
    bridge.record_scene_description({"candidate_objects": [
        {"query": "green apple"}, {"query": "black cube"}, {"query": "white cube"},
    ]})

    response = bridge.submit(
        "First localize all candidates, then compute a pose for the apple between the two cubes."
    )

    assert [event.tool_name for event in response.tool_events] == [
        "detect_objects", "detect_objects", "compute_place_pose",
    ]
    assert response.tool_events[1].dependencies == [response.tool_events[0].node_id]


def test_two_placement_clauses_joined_by_and_use_the_structured_planner():
    bridge = ExistingAgentBridge(
        "existing_openai",
        semantic_intent_parser=lambda *_args: (_ for _ in ()).throw(
            AssertionError("single-relation parser must not handle two placements")
        ),
        structured_task_parser=lambda _instruction, _objects, _candidates: {
            "kind": "task_plan", "confidence": 0.95, "needs_clarification": False,
            "subgoals": [
                {
                    "action": "pick_place", "source": "white cube",
                    "relation": "on_top_of", "references": ["black cube"],
                },
                {
                    "action": "pick_place", "source": "green apple",
                    "relation": "on_top_of", "references": ["white cube"],
                },
            ],
        },
    )

    response = bridge.submit(
        "place white cube on top of black cube and place apple on top of white cube"
    )

    assert len(response.tool_events) == 8
    assert response.tool_events[0].tool_name == "move_to_named_target"
    assert response.tool_events[0].input_json == {"target_name": "observe", "purpose": "agent_observe"}
    assert response.tool_events[4].dependencies == [response.tool_events[3].node_id]
    assert response.tool_events[7].input_json == {"object_query": "green apple"}


def test_structured_planner_canonicalizes_short_object_names_to_scene_candidates():
    bridge = ExistingAgentBridge(
        "existing_openai",
        structured_task_parser=lambda _instruction, _objects, _candidates: {
            "kind": "task_plan", "confidence": 0.95, "needs_clarification": False,
            "subgoals": [{
                "action": "pick_place", "source": "white cube",
                "relation": "on_top_of", "references": ["black cube"],
            }],
        },
    )
    bridge.record_scene_description({"candidate_objects": [
        {"query": "white block with number 5"},
        {"query": "black block with number 3"},
        {"query": "green apple"},
    ]})

    response = bridge.submit("First place the white cube on the black cube, then stop.")

    assert response.tool_events[1].input_json == {
        "queries": ["white block with number 5", "black block with number 3"],
    }
    assert response.tool_events[2].input_json == {
        "source_id": "white block with number 5", "relation": "on_top_of",
        "reference_ids": ["black block with number 3"],
    }


def test_structured_planner_rejects_an_llm_reference_outside_scene_candidates():
    bridge = ExistingAgentBridge(
        "existing_openai",
        structured_task_parser=lambda _instruction, _objects, _candidates: {
            "kind": "task_plan", "confidence": 0.95, "needs_clarification": False,
            "subgoals": [{
                "action": "pick_place", "source": "black cube",
                "relation": "on_top_of", "references": ["table"],
            }],
        },
    )
    bridge.record_scene_description({"candidate_objects": [
        {"query": "white cube with number 5"}, {"query": "black cube with number 3"},
    ]})

    response = bridge.submit("place the black cube on the table and then continue")

    assert response.tool_events == []
    assert "source object" in response.message


def test_structured_planner_accepts_common_model_aliases_without_relaxing_object_grounding():
    bridge = ExistingAgentBridge(
        "existing_openai",
        structured_task_parser=lambda _instruction, _objects, _candidates: {
            "kind": "task_plan", "confidence": 0.95, "needs_clarification": False,
            "subgoals": [{
                "action": "place", "source": "white cube",
                "relation": "above", "reference": "black cube",
            }],
        },
    )
    bridge.record_scene_description({"candidate_objects": [
        {"query": "white cube with number 5"}, {"query": "black cube with number 3"},
    ]})

    response = bridge.submit("First stack the white cube on the black cube, then stop.")

    assert [event.tool_name for event in response.tool_events] == [
        "move_to_named_target", "detect_objects", "compute_place_pose", "supervised_pick_from_localization",
    ]
    assert response.tool_events[2].input_json["relation"] == "on_top_of"


def test_capability_question_returns_registered_skill_summary_without_a_task():
    response = ExistingAgentBridge("existing_openai").submit("What can you do?")
    assert response.tool_events == []
    assert "supervised pick" in response.message
    assert "plan-only" in response.message


def test_named_target_question_reports_the_real_arm_allow_list_without_a_task():
    response = ExistingAgentBridge("existing_openai").submit("which target is allowed for moving")

    assert response.tool_events == []
    assert "safe_home" in response.message
    assert "observe" in response.message


def test_home_target_is_normalised_to_the_safe_home_alias():
    assert ExistingAgentBridge._normalise_tool_arguments(
        "move_to_named_target", {"target_name": "home"}
    ) == {"target_name": "safe_home"}
    assert ExistingAgentBridge._normalise_tool_arguments(
        "move_to_named_target", {"target": "observe"}
    ) == {"target": "observe"}


def test_simple_chinese_conversation_does_not_enter_the_task_agent():
    response = ExistingAgentBridge("existing_openai").submit("干得好")

    assert response.tool_events == []
    assert "谢谢" in response.message


def test_weather_question_uses_current_weather_reply(monkeypatch):
    monkeypatch.setattr(
        ExistingAgentBridge,
        "_fetch_current_weather",
        staticmethod(lambda _location, _timeout: {
            "location": "Aachen", "weather_code": 2,
            "temperature": 20.0, "apparent_temperature": 19.0,
        }),
    )

    response = ExistingAgentBridge("existing_openai").submit(
        "How's the weather today in Aachen?", conversation_config={"weather_location": "Berlin, Germany"}
    )

    assert response.tool_events == []
    assert "Current weather in Aachen: partly cloudy, 20°C" in response.message


def test_weather_location_is_extracted_from_chinese_question():
    assert ExistingAgentBridge._weather_location("深圳今天天气怎么样？", "Berlin, Germany") == "深圳"


def test_capability_question_reports_approved_real_execution_when_enabled():
    controller = GuiController()
    controller.state.robot_mode = "REAL ROBOT"
    controller.gui_config["enable_real_execution"] = True

    assert controller.start_task("What can you do?") == "capabilities-query"
    assert "execute approved real-robot actions" in controller.state.conversation[-1].text
    assert "human approval" in controller.state.conversation[-1].text


def test_named_target_question_is_answered_in_chat_without_creating_a_plan():
    controller = GuiController()

    assert controller.start_task("which target is allowed for moving") == "named-target-query"
    assert controller.state.current_task_plan is None
    assert "safe_home" in controller.state.conversation[-1].text


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


def test_direct_gripper_task_intent_approval_is_the_single_execution_release():
    async def scenario():
        controller = GuiController()
        controller.state.robot_mode = "SIMULATION"
        controller.state.current_task_id = "task-direct-gripper"
        calls = []

        async def execute(node):
            calls.append(node.node_id)

        controller.skill_runtime.execute_gripper_after_release = execute
        controller.add_agent_tool_event(AgentToolEvent(
            "close-gripper-1", None, "close_gripper", "Close Gripper",
            "waiting_approval", requires_approval=True, approval_stages=["task_intent"],
        ))

        request = controller.state.pending_hitl_request
        assert request is not None and request.request_type == "task_intent"
        assert controller.submit_hitl_decision(request.request_id, HitlDecision.APPROVE)
        await controller._last_skill_task

        assert controller.state.pending_hitl_request is None
        assert calls == ["close-gripper-1"]

    asyncio.run(scenario())


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
