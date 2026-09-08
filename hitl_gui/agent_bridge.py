"""Thin GUI adapter over the existing LLM_Ros Python Agent API.

It deliberately calls only AgentController.propose_next_action: no runtime,
tool executor, trajectory, or hardware interface is invoked here.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import urlopen


@dataclass
class AgentToolEvent:
    node_id: str
    parent_id: str | None
    tool_name: str
    display_name: str
    status: str
    input_json: dict[str, Any] = field(default_factory=dict)
    output_json: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    requires_approval: bool = False
    approval_stages: list[str] = field(default_factory=list)
    description: str = ""
    node_type: str = "tool"
    phase: str | None = None
    sequence_index: int | None = None
    dependencies: list[str] = field(default_factory=list)


@dataclass
class AgentResponse:
    message: str
    tool_events: list[AgentToolEvent] = field(default_factory=list)


class ExistingAgentBridge:
    CONVERSATION_SYSTEM_PROMPT = (
        "You are Milo, a helpful robot-workspace assistant having a brief casual conversation. "
        "Reply in the user's language in at most two short sentences. Be warm and natural. "
        "Do not output JSON, commands, tool calls, or safety instructions. Do not claim that any "
        "robot action was performed, and do not promise to perform an action. If the user asks for "
        "a robot task, say you can propose it for the normal reviewed workflow."
    )

    def __init__(
        self,
        mode: str = "existing_scripted",
        semantic_intent_parser: Callable[[str, list[dict[str, Any]]], dict[str, Any] | None] | None = None,
    ) -> None:
        self.mode = mode
        # Populated only from successful RGB-D localization output. This is
        # intentionally not conversational memory: LLM language understanding
        # must never become a source of manipulable geometry.
        self._trusted_scene_objects: list[dict[str, Any]] = []
        self._scene_candidate_queries: list[str] = []
        self._scene_lock = threading.RLock()
        self._semantic_intent_parser = semantic_intent_parser

    def record_localization_result(self, output: dict[str, Any]) -> None:
        """Replace memory with the latest verified, pose-bearing scene objects."""
        scene = output.get("scene") if isinstance(output, dict) else None
        objects = scene.get("objects", []) if isinstance(scene, dict) else []
        trusted: list[dict[str, Any]] = []
        if isinstance(objects, list):
            for item in objects:
                if not isinstance(item, dict):
                    continue
                object_id, label = item.get("object_id"), item.get("label")
                if not isinstance(object_id, str) or not object_id.strip():
                    continue
                if not isinstance(label, str) or not label.strip():
                    continue
                if not item.get("pose_available") or not isinstance(item.get("pose"), dict):
                    continue
                # Deliberately retain no coordinates, images, masks, or clouds
                # in the data sent to the semantic parser.
                trusted.append({
                    "object_id": object_id.strip(), "label": label.strip(), "pose_available": True,
                })
        with self._scene_lock:
            self._trusted_scene_objects = trusted

    def clear_localization_memory(self) -> None:
        with self._scene_lock:
            self._trusted_scene_objects = []

    def record_scene_description(self, description: dict[str, Any]) -> None:
        """Store the newest VLM candidate queries as unverified localization hints.

        They are never treated as poses or targets.  They merely let a later
        request such as "localize all candidates" expand to one SAM3 query per
        candidate rather than querying the literal phrase "all objects".
        """
        raw_candidates = description.get("candidate_objects", []) if isinstance(description, dict) else []
        queries: list[str] = []
        seen: set[str] = set()
        if isinstance(raw_candidates, list):
            for item in raw_candidates:
                query = item.get("query") if isinstance(item, dict) else None
                if not isinstance(query, str) or not query.strip():
                    continue
                cleaned = query.strip()
                key = cleaned.casefold()
                if key not in seen:
                    seen.add(key)
                    queries.append(cleaned)
        with self._scene_lock:
            self._scene_candidate_queries = queries

    def trusted_scene_objects(self) -> list[dict[str, Any]]:
        with self._scene_lock:
            return [dict(item) for item in self._trusted_scene_objects]

    def scene_candidate_queries(self) -> list[str]:
        with self._scene_lock:
            return list(self._scene_candidate_queries)

    def submit(
        self,
        instruction: str,
        execution_mode: str = "plan_only",
        conversation_config: dict[str, Any] | None = None,
    ) -> AgentResponse:
        if self.is_capability_question(instruction):
            return AgentResponse(self.capabilities_message())
        if self.is_named_target_question(instruction):
            return AgentResponse(self.named_target_message())
        conversation = self.short_conversation_response(
            instruction, conversation_config or {}
        )
        if conversation is not None:
            return AgentResponse(conversation)
        if self.is_small_talk(instruction):
            return AgentResponse(self._llm_conversation_reply(instruction, conversation_config or {}))
        candidate_response = self._scene_candidate_localization_response(instruction)
        if candidate_response is not None:
            return candidate_response
        semantic_response = self._semantic_relative_place_response(instruction)
        if semantic_response is not None:
            return semantic_response
        try:
            from llm_skill_robot.agent.agent_controller import AgentController, AgentDecisionKind
            from llm_skill_robot.agent.agent_state import AgentState
            if self.mode == "existing_openai":
                from llm_skill_robot.agent.llm_client import OpenAILLMClient
                client = OpenAILLMClient()
            else:
                from llm_skill_robot.agent_runtime_demo import ScriptedDemoLLMClient
                client = ScriptedDemoLLMClient()
            proposal = AgentController(client).propose_next_action(
                AgentState(user_goal=instruction, safety_mode=execution_mode)
            )
        except Exception as exc:
            raise RuntimeError(f"Existing Agent interface is unavailable: {exc}") from exc

        decision = proposal.decision
        approval_stages = [stage.value for stage in proposal.approval_stages]
        if decision.kind == AgentDecisionKind.TOOL_CALL and decision.tool_call:
            call = decision.tool_call
            plan_step = proposal.plan_step
            arguments = self._normalise_tool_arguments(call.tool_name, call.arguments)
            primary_node_id = f"agent-{call.tool_name}-1"
            events = [AgentToolEvent(
                node_id=primary_node_id, parent_id=None,
                tool_name=call.tool_name, display_name=call.tool_name.replace("_", " ").title(),
                status="waiting_approval" if proposal.requires_human_gate else "pending",
                input_json=arguments,
                output_json={"approval_stages": approval_stages},
                requires_approval=proposal.requires_human_gate,
                approval_stages=approval_stages,
                description=plan_step.description if plan_step is not None else "",
                node_type="tool",
                sequence_index=1,
            )]
            followup = self._relative_place_followup(instruction, call.tool_name, arguments)
            if followup is not None:
                events.append(AgentToolEvent(
                    node_id="agent-compute_place_pose-2", parent_id=primary_node_id,
                    tool_name="compute_place_pose", display_name="Compute Place Pose",
                    status="pending", input_json=followup,
                    description=(
                        "Compute the requested plan-only relative placement pose "
                        "from the newly localized objects."
                    ),
                    node_type="tool", phase="motion_planning", sequence_index=2,
                    dependencies=[primary_node_id],
                ))
                message = (
                    "I will first localize the requested objects from one RGB-D frame, "
                    "then compute the plan-only relative placement pose."
                )
            else:
                message = decision.message
            return AgentResponse(message, events)
        if decision.kind == AgentDecisionKind.COMPOSITE_SKILL_CALL and decision.composite_skill_call:
            call = decision.composite_skill_call
            return AgentResponse(decision.message, [AgentToolEvent(
                node_id=f"agent-{call.skill_name}-1", parent_id=None,
                tool_name=call.skill_name, display_name=call.skill_name.replace("_", " ").title(),
                status="waiting_approval", input_json=dict(call.arguments),
                output_json={"approval_stages": approval_stages},
                requires_approval=True,
                approval_stages=approval_stages,
                description=getattr(proposal.composite_skill, "description", ""),
                node_type="composite",
            )])
        return AgentResponse(decision.message)

    def _scene_candidate_localization_response(self, instruction: str) -> AgentResponse | None:
        """Expand 'all objects' into queries from the latest scene scan."""
        text = " ".join(instruction.casefold().split())
        asks_for_all = any(phrase in text for phrase in (
            "all the objects", "all objects", "every object", "all candidates",
            "all the candidates", "everything from the scan", "所有物体", "全部物体",
            "所有目标", "全部目标", "所有候选", "全部候选",
        ))
        asks_for_localization = any(phrase in text for phrase in (
            "localize", "localise", "detect", "identify", "find", "recognize", "recognise",
            "定位", "识别", "检测", "找到",
        ))
        queries = self.scene_candidate_queries()
        if not asks_for_all or not asks_for_localization or not queries:
            return None
        return AgentResponse(
            f"I will localize the {len(queries)} unverified candidate(s) from the latest scene scan with SAM3 + RGB-D.",
            [AgentToolEvent(
                node_id="agent-detect_objects-1", parent_id=None,
                tool_name="detect_objects", display_name="Localize Scene Candidates",
                status="pending", input_json={"queries": queries},
                requires_approval=False,
                description="Localize every unverified candidate from the latest scene description.",
                node_type="tool", phase="perception", sequence_index=1,
            )],
        )

    def _semantic_relative_place_response(self, instruction: str) -> AgentResponse | None:
        """Interpret flexible placement language, then ground it locally.

        The LLM may choose a relation and identify an object from a minimal
        catalog. The returned IDs are accepted only if they exactly match the
        latest successful localization result.
        """
        if self.mode != "existing_openai" and self._semantic_intent_parser is None:
            return None
        objects = self.trusted_scene_objects()
        candidates = self.scene_candidate_queries()
        try:
            intent = (
                self._semantic_intent_parser(instruction, objects)
                if self._semantic_intent_parser is not None
                else self._request_semantic_intent(instruction, objects, candidates)
            )
        except Exception:
            # Preserve the previous AgentController path as a compatibility
            # fallback whenever the semantic request is unavailable.
            return None
        if not isinstance(intent, dict):
            return None
        kind = str(intent.get("kind", "unknown"))
        if kind not in {"relative_place", "localize_then_relative_place"}:
            return None
        try:
            confidence = float(intent.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if bool(intent.get("needs_clarification")) or confidence < 0.65:
            return AgentResponse(
                "I can help plan that placement, but the object or spatial relation is ambiguous. "
                "Please name the object to move and the reference object(s)."
            )
        relation = str(intent.get("relation", ""))
        references = intent.get("references")
        source = intent.get("source")
        required_references = 2 if relation == "between" else 1
        if relation not in {"between", "right_of", "left_of", "on_top_of"}:
            return AgentResponse("I need a clearer relative placement relation before planning a pose.")
        if not isinstance(source, str) or not source.strip() or not isinstance(references, list):
            return AgentResponse("I need the object to move and its reference object(s) before planning a pose.")
        references = [item.strip() for item in references if isinstance(item, str) and item.strip()]
        if len(references) != required_references:
            return AgentResponse(
                "A between placement needs two reference objects; other relative placements need one reference object."
            )

        pick_required = bool(intent.get("pick_required", False))
        if kind == "relative_place":
            source_id = self._resolve_trusted_object_id(source, objects)
            reference_ids = [self._resolve_trusted_object_id(item, objects) for item in references]
            if source_id is None or any(item is None for item in reference_ids):
                # A scene scan is semantically useful but not geometrically
                # verified. If every reference names one of its candidates,
                # recover by planning the mandatory localization stage.
                queries = self._candidate_queries_for([source, *references], candidates)
                if queries is not None:
                    return (
                        self._observe_localize_then_place_response(queries, relation)
                        if pick_required
                        else self._localize_then_place_response(queries, relation, False)
                    )
                return AgentResponse(
                    "I cannot safely match that description to one localized object. "
                    "Please name it more specifically or localize the scene again."
                )
            if pick_required:
                # A previous localization may have been captured from an
                # arbitrary arm/camera pose. Pick/place always refreshes it
                # after the approved move-to-observe stage.
                return self._observe_localize_then_place_response(
                    [self._trusted_label(source_id, objects) or source,
                     *[self._trusted_label(str(item), objects) or reference for item, reference in zip(reference_ids, references)]],
                    relation,
                )
            else:
                events = [self._compute_place_event(source_id, relation, [str(item) for item in reference_ids])]
                message = "Using the current verified scene, I will compute a plan-only relative placement pose."
            return AgentResponse(message, events)

        # The LLM supplies descriptions only. The dependent computation starts
        # only after this new SAM3 + RGB-D localization succeeds.  A requested
        # pick must use the same fresh localization for its grasp stages: the
        # legacy safe_pick_object composite performs its own detect_object
        # call, which would duplicate SAM3 inference and discard the
        # multi-object scene needed for relative placement.
        queries = [source.strip(), *references]
        return (
            self._observe_localize_then_place_response(queries, relation)
            if pick_required else self._localize_then_place_response(queries, relation, False)
        )

    def _observe_localize_then_place_response(self, queries: list[str], relation: str) -> AgentResponse:
        observe_id = "agent-move_to_observe-1"
        detect_id = "agent-detect_objects-2"
        compute_id = "agent-compute_place_pose-3"
        return AgentResponse(
            "I planned a supervised pick-and-place workflow: move to the observation pose, localize the objects, compute the target pose, then request approval for the pick/place sequence.",
            [
                AgentToolEvent(
                    node_id=observe_id, parent_id=None, tool_name="move_to_named_target",
                    display_name="Move To Observe", status="waiting_approval",
                    input_json={"target_name": "observe", "purpose": "agent_observe"},
                    output_json={"approval_stages": ["task_intent"]},
                    requires_approval=True, approval_stages=["task_intent"],
                    description="Move to the approved observation pose before fresh RGB-D localization.",
                    node_type="tool", phase="motion_planning", sequence_index=1,
                ),
                AgentToolEvent(
                    node_id=detect_id, parent_id=observe_id, tool_name="detect_objects",
                    display_name="Localize Objects", status="pending", input_json={"queries": queries},
                    requires_approval=False, description="Localize requested objects after reaching observe.",
                    node_type="tool", phase="perception", sequence_index=2, dependencies=[observe_id],
                ),
                AgentToolEvent(
                    node_id=compute_id, parent_id=detect_id, tool_name="compute_place_pose",
                    display_name="Compute Place Pose", status="pending",
                    input_json={"source_id": queries[0], "relation": relation, "reference_ids": queries[1:]},
                    requires_approval=False, description="Compute the relative placement pose from this fresh localization.",
                    node_type="tool", phase="motion_planning", sequence_index=3, dependencies=[detect_id],
                ),
                self._supervised_pick_from_localization_event(compute_id, queries[0]),
            ],
        )

    def _localize_then_place_response(
        self, queries: list[str], relation: str, _pick_required: bool = False,
    ) -> AgentResponse:
        detect_id = "agent-detect_objects-1"
        compute_id = "agent-compute_place_pose-2"
        events = [
            AgentToolEvent(
                node_id=detect_id, parent_id=None, tool_name="detect_objects",
                display_name="Localize Objects", status="pending", input_json={"queries": queries},
                requires_approval=False, description="Localize requested objects with SAM3 and RGB-D.",
                node_type="tool", phase="perception", sequence_index=1,
            ),
            AgentToolEvent(
                node_id=compute_id, parent_id=detect_id,
                tool_name="compute_place_pose", display_name="Compute Place Pose", status="pending",
                input_json={"source_id": queries[0], "relation": relation, "reference_ids": queries[1:]},
                description="Compute a plan-only relative placement pose from verified localization.",
                node_type="tool", phase="motion_planning", sequence_index=2, dependencies=[detect_id],
            ),
        ]
        return AgentResponse(
            "I will first localize the requested objects from one RGB-D frame, then compute the plan-only relative placement pose.",
            events,
        )

    @staticmethod
    def _supervised_pick_from_localization_event(dependency_id: str, object_query: str) -> AgentToolEvent:
        return AgentToolEvent(
            node_id="agent-supervised_pick_from_localization-4", parent_id=dependency_id,
            tool_name="supervised_pick_from_localization", display_name="Supervised Pick and Place",
            status="pending", input_json={"object_query": object_query},
            output_json={"approval_stages": ["task_intent"]},
            requires_approval=True, approval_stages=["task_intent"],
            description="Reuse the fresh trusted localization; generate a grasp without another SAM3 query.",
            node_type="composite", phase="grasp_generation", sequence_index=4, dependencies=[dependency_id],
        )

    @staticmethod
    def _trusted_label(object_id: str, objects: list[dict[str, Any]]) -> str | None:
        for item in objects:
            if item.get("object_id") == object_id:
                label = item.get("label")
                return str(label) if isinstance(label, str) else None
        return None

    @staticmethod
    def _candidate_queries_for(values: list[str], candidates: list[str]) -> list[str] | None:
        by_key = {candidate.casefold(): candidate for candidate in candidates}
        resolved = [by_key.get(value.casefold()) for value in values]
        return [str(item) for item in resolved] if all(resolved) else None

    @staticmethod
    def _compute_place_event(source_id: str, relation: str, reference_ids: list[str]) -> AgentToolEvent:
        return AgentToolEvent(
            node_id="agent-compute_place_pose-1", parent_id=None,
            tool_name="compute_place_pose", display_name="Compute Place Pose", status="pending",
            input_json={"source_id": source_id, "relation": relation, "reference_ids": reference_ids},
            description="Compute a plan-only relative placement pose from the verified scene.",
            node_type="tool", phase="motion_planning", sequence_index=1,
        )

    @staticmethod
    def _resolve_trusted_object_id(identifier: str, objects: list[dict[str, Any]]) -> str | None:
        needle = identifier.strip().casefold()
        matches = [
            item["object_id"] for item in objects
            if needle in {str(item.get("object_id", "")).casefold(), str(item.get("label", "")).casefold()}
        ]
        return matches[0] if len(matches) == 1 else None

    def _request_semantic_intent(
        self, instruction: str, objects: list[dict[str, Any]], candidates: list[str],
    ) -> dict[str, Any] | None:
        from llm_skill_robot.agent.llm_client import OpenAILLMClient

        prompt = (
            "You are a semantic parser for a robot plan-only relative placement request. "
            "Return exactly one JSON object and no Markdown. Schema: "
            '{"kind":"relative_place|localize_then_relative_place|unknown","source":"string",'
            '"relation":"between|right_of|left_of|on_top_of","references":["string"],'
            '"pick_required":false,"confidence":0.0,"needs_clarification":false}. '
            "Interpret natural language flexibly: stack/atop/above means on_top_of; "
            "to the right/left means right_of/left_of; midway/in the middle means between. "
            "Set pick_required=true only when the user asks to pick/grasp/lift the source object. "
            "For relative_place, source and references MUST be exact object_id values from the trusted catalog, "
            "and every object must be present. For localize_then_relative_place use concise object descriptions "
            "as queries; when an unverified scene candidate matches, copy that candidate query exactly. "
            "between requires two references; other relations require one. "
            "Do not infer coordinates, trajectories, grasps, or robot actions. "
            f"Trusted catalog (no coordinates or images): {json.dumps(objects, ensure_ascii=False)}. "
            f"Unverified scene candidates: {json.dumps(candidates, ensure_ascii=False)}"
        )
        raw = OpenAILLMClient().generate_text([
            {"role": "system", "content": prompt},
            {"role": "user", "content": instruction},
        ]).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def is_capability_question(instruction: str) -> bool:
        text = instruction.lower().strip()
        phrases = (
            "what can you do", "what tasks", "your capabilities", "supported tasks",
            "你能做什么", "能做什么", "可以做什么", "支持什么", "有哪些功能", "可以完成什么",
        )
        return any(phrase in text for phrase in phrases)

    @staticmethod
    def is_named_target_question(instruction: str) -> bool:
        """Recognise questions about configured movement targets, not a move request."""
        text = instruction.lower().strip()
        refers_to_target = any(term in text for term in (
            "target", "targets", "named target", "目标", "位置", "地点",
        ))
        refers_to_motion = any(term in text for term in (
            "move", "moving", "movement", "go to", "移动", "前往", "去",
        ))
        asks_a_question = any(term in text for term in (
            "which", "what", "allowed", "available", "can i", "哪些", "什么", "允许", "可用", "能否", "可以",
        ))
        return refers_to_target and refers_to_motion and asks_a_question

    @staticmethod
    def _allowed_named_targets() -> tuple[str, ...]:
        """Read the same restricted-real-arm allow-list used during execution."""
        try:
            from llm_skill_robot.safety.real_arm_safety import load_real_arm_safety

            return load_real_arm_safety().allowed_named_targets
        except Exception:
            # Do not claim that an unverified target is permitted if the safety
            # configuration cannot be read.
            return ()

    @classmethod
    def named_target_message(cls, instruction: str = "") -> str:
        targets = cls._allowed_named_targets()
        target_text = ", ".join(targets) if targets else "not available"
        chinese = any("\u4e00" <= char <= "\u9fff" for char in instruction)
        if chinese:
            if not targets:
                return "暂时无法读取真机可用的命名目标配置。"
            return f"当前真机允许的命名目标是：{target_text}。如需回到初始安全位，请使用 safe_home；home 会自动映射为 safe_home。"
        if not targets:
            return "I can't read the configured real-robot named targets right now."
        return f"The currently allowed real-robot named targets are: {target_text}. Use safe_home for the home position; home is mapped to safe_home."

    @staticmethod
    def _normalise_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Map friendly target aliases to the safety-approved canonical name."""
        normalised = dict(arguments)
        if tool_name != "move_to_named_target":
            return normalised
        for key in ("target_name", "target"):
            value = normalised.get(key)
            if isinstance(value, str) and value.strip().casefold() in {"home", "home position"}:
                normalised[key] = "safe_home"
        return normalised

    @staticmethod
    def _relative_place_followup(
        instruction: str, tool_name: str, arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build the deterministic second stage of a safe relative-place workflow.

        The Agent remains responsible for grounding the requested object queries.
        This bridge only composes the reviewed sequence once those exact queries
        are available: localize first, then calculate a pose from trusted RGB-D
        output.  It never adds a pick, place, or robot-motion action.
        """
        if tool_name != "detect_objects":
            return None
        queries = arguments.get("queries")
        if not isinstance(queries, list) or not all(isinstance(query, str) and query.strip() for query in queries):
            return None
        text = " ".join(instruction.lower().split())
        relation = None
        expected_references = 0
        if any(phrase in text for phrase in ("between", "middle of", "中间")):
            relation, expected_references = "between", 2
        elif any(phrase in text for phrase in ("right of", "right_of", "右边", "右侧")):
            relation, expected_references = "right_of", 1
        elif any(phrase in text for phrase in ("left of", "left_of", "左边", "左侧")):
            relation, expected_references = "left_of", 1
        elif any(phrase in text for phrase in ("on top of", "on_top_of", "above", "over", "stack", "叠放", "正上方")):
            relation, expected_references = "on_top_of", 1
        if relation is None or len(queries) != expected_references + 1:
            return None
        return {
            # The localization tool uses the same user-grounded query strings
            # as labels.  The spatial planner accepts them only when they
            # resolve unambiguously in the newly created scene model.
            "source_id": queries[0].strip(),
            "relation": relation,
            "reference_ids": [query.strip() for query in queries[1:]],
        }

    @classmethod
    def short_conversation_response(
        cls, instruction: str, config: dict[str, Any]
    ) -> str | None:
        """Return a concise non-task reply without invoking the task agent."""
        if not config.get("enabled", True):
            return None
        text = instruction.lower().strip()
        chinese = any("\u4e00" <= char <= "\u9fff" for char in instruction)
        if cls._is_weather_question(text):
            location = cls._weather_location(instruction, str(config.get("weather_location", "Berlin, Germany")))
            timeout_sec = float(config.get("weather_timeout_sec", 3.0))
            return cls._weather_reply(location, timeout_sec, chinese)

        replies = (
            (("干得好", "做得好", "不错", "棒", "good job", "well done", "nice work"),
             "谢谢！还需要我做点什么吗？", "Thank you. What else can I help with?"),
            (("谢谢", "感谢", "thank you", "thanks"),
             "不客气。还需要我做点什么吗？", "You're welcome. What else can I help with?"),
            (("你好", "嗨", "早上好", "下午好", "晚上好", "hello", "hi there", "good morning", "good afternoon", "good evening"),
             "你好！有什么可以帮你处理的吗？", "Hello. What can I help you with?"),
        )
        for phrases, chinese_reply, english_reply in replies:
            if any(phrase in text for phrase in phrases):
                return chinese_reply if chinese else english_reply
        return None

    @staticmethod
    def is_small_talk(instruction: str) -> bool:
        text = instruction.lower().strip()
        phrases = (
            "你好吗", "最近怎么样", "聊聊天", "讲个笑话", "早上好", "下午好", "晚上好",
            "how are you", "how's it going", "what's up", "tell me a joke", "good morning",
            "good afternoon", "good evening",
        )
        return any(phrase in text for phrase in phrases)

    def _llm_conversation_reply(self, instruction: str, config: dict[str, Any]) -> str:
        """Use the configured LLM for bounded casual conversation only."""
        fallback = "你好！有什么想聊的，或需要我协助规划的任务吗？"
        if self.mode != "existing_openai" or not config.get("use_llm", True):
            return fallback if any("\u4e00" <= char <= "\u9fff" for char in instruction) else "Hello. How can I help?"
        try:
            from llm_skill_robot.agent.llm_client import OpenAILLMClient

            reply = OpenAILLMClient().generate_text([
                {"role": "system", "content": self.CONVERSATION_SYSTEM_PROMPT},
                {"role": "user", "content": instruction},
            ]).strip()
            return self._limit_sentences(reply, int(config.get("max_sentences", 2))) or fallback
        except Exception:
            return fallback if any("\u4e00" <= char <= "\u9fff" for char in instruction) else "Hello. How can I help?"

    @staticmethod
    def _is_weather_question(text: str) -> bool:
        return any(phrase in text for phrase in (
            "天气", "气温", "温度", "下雨", "weather", "temperature", "rain",
        ))

    @staticmethod
    def _weather_location(instruction: str, default_location: str) -> str:
        """Extract a city from common Chinese and English weather questions."""
        english_match = re.search(r"\b(?:in|at|for)\s+([A-Za-z][A-Za-z .'-]{1,60}?)(?:[?!.]|$)", instruction, re.I)
        if english_match:
            return english_match.group(1).strip()
        chinese_match = re.search(r"([\u4e00-\u9fff]{2,12})(?:的)?(?:天气|气温|温度)", instruction)
        if chinese_match:
            candidate = chinese_match.group(1)
            for time_word in ("今天", "明天", "后天", "现在"):
                candidate = candidate.removesuffix(time_word)
            if candidate and candidate not in {"今天", "明天", "后天", "现在", "当地"}:
                return candidate
        return default_location

    @classmethod
    def _weather_reply(cls, location: str, timeout_sec: float, chinese: bool) -> str:
        try:
            weather = cls._fetch_current_weather(location, timeout_sec)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return (
                f"暂时无法获取{location}的实时天气，请稍后再试。"
                if chinese else f"I can't retrieve the current weather for {location} right now."
            )
        if chinese:
            return (
                f"{weather['location']}当前{cls._weather_condition(weather['weather_code'], chinese=True)}，{weather['temperature']:.0f}°C，"
                f"体感{weather['apparent_temperature']:.0f}°C。"
            )
        return (
            f"Current weather in {weather['location']}: {cls._weather_condition(weather['weather_code'], chinese=False)}, "
            f"{weather['temperature']:.0f}°C (feels like {weather['apparent_temperature']:.0f}°C)."
        )

    @staticmethod
    def _fetch_current_weather(location: str, timeout_sec: float) -> dict[str, Any]:
        """Fetch current conditions from Open-Meteo's public, keyless API."""
        geocode_query = urlencode({"name": location, "count": 1, "language": "en", "format": "json"})
        with urlopen(
            f"https://geocoding-api.open-meteo.com/v1/search?{geocode_query}",
            timeout=timeout_sec,
        ) as response:
            places = json.load(response).get("results", [])
        if not places:
            raise ValueError(f"No weather location found for {location!r}.")
        place = places[0]
        forecast_query = urlencode({
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": "temperature_2m,apparent_temperature,weather_code",
        })
        with urlopen(
            f"https://api.open-meteo.com/v1/forecast?{forecast_query}", timeout=timeout_sec
        ) as response:
            current = json.load(response)["current"]
        return {
            "location": place["name"],
            "temperature": float(current["temperature_2m"]),
            "apparent_temperature": float(current["apparent_temperature"]),
            "weather_code": int(current["weather_code"]),
        }

    @staticmethod
    def _weather_condition(code: int, *, chinese: bool) -> str:
        chinese_conditions = {
            0: "晴朗", 1: "大部晴朗", 2: "局部多云", 3: "阴天",
            45: "有雾", 48: "雾凇", 51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
            61: "小雨", 63: "中雨", 65: "大雨", 71: "小雪", 73: "中雪", 75: "大雪",
            80: "阵雨", 81: "阵雨", 82: "强阵雨", 95: "雷暴",
        }
        english_conditions = {
            0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
            45: "foggy", 48: "rime fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
            61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow",
            80: "rain showers", 81: "rain showers", 82: "heavy rain showers", 95: "thunderstorm",
        }
        conditions = chinese_conditions if chinese else english_conditions
        return conditions.get(int(code), "天气状况未知" if chinese else "unknown conditions")

    @staticmethod
    def _limit_sentences(text: str, max_sentences: int) -> str:
        max_sentences = max(1, min(max_sentences, 2))
        sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", text) if part.strip()]
        return " ".join(sentences[:max_sentences])

    @staticmethod
    def capabilities_message(*, real_execution_enabled: bool = False) -> str:
        """Describe current registered capabilities without contacting an LLM."""
        try:
            from llm_skill_robot.agent.composite_skills.registry import CompositeSkillRegistry
            from llm_skill_robot.agent.tool_registry import AgentToolRegistry

            tools = {tool.name for tool in AgentToolRegistry().list_tools()}
            composites = {item["skill_name"] for item in CompositeSkillRegistry().list_skills()}
        except Exception:
            tools = set()
            composites = set()

        items = []
        if "describe_scene" in tools:
            items.append("describe the current scene and propose unverified object candidates")
        if "detect_object" in tools:
            items.append("observe or detect a specified object")
        if "safe_pick_object" in composites:
            items.append("propose a supervised pick for a clearly specified object")
        if "move_to_named_target" in tools:
            items.append("propose movement to configured named targets")
        if "place_object" in tools:
            items.append("propose placing an object that is already held")
        if {"open_gripper", "close_gripper", "get_gripper_state"} & tools:
            items.append("check or propose approved gripper operations")

        capability_list = "; ".join(items) if items else "inspect the currently registered robot skills"
        safety_message = (
            "This GUI can execute approved real-robot actions in this session. "
            "Every motion and gripper command remains subject to configured safety checks and human approval."
            if real_execution_enabled
            else "This GUI is currently plan-only, so no robot motion is executed from this chat."
        )
        return (
            f"I can currently help you {capability_list}. "
            "I will ask for clarification when the target or task is ambiguous, and I will stop at the required human review points. "
            f"{safety_message}"
        )
