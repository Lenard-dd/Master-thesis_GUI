"""Thin GUI adapter over the existing LLM_Ros Python Agent API.

It deliberately calls only AgentController.propose_next_action: no runtime,
tool executor, trajectory, or hardware interface is invoked here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    def __init__(self, mode: str = "existing_scripted") -> None:
        self.mode = mode

    def submit(self, instruction: str, execution_mode: str = "plan_only") -> AgentResponse:
        if self.is_capability_question(instruction):
            return AgentResponse(self.capabilities_message())
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
            return AgentResponse(decision.message, [AgentToolEvent(
                node_id=f"agent-{call.tool_name}-1", parent_id=None,
                tool_name=call.tool_name, display_name=call.tool_name.replace("_", " ").title(),
                status="waiting_approval" if proposal.requires_human_gate else "pending",
                input_json=dict(call.arguments),
                output_json={"approval_stages": approval_stages},
                requires_approval=proposal.requires_human_gate,
                approval_stages=approval_stages,
                description=plan_step.description if plan_step is not None else "",
                node_type="tool",
            )])
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

    @staticmethod
    def is_capability_question(instruction: str) -> bool:
        text = instruction.lower().strip()
        phrases = (
            "what can you do", "what tasks", "your capabilities", "supported tasks",
            "你能做什么", "能做什么", "可以做什么", "支持什么", "有哪些功能", "可以完成什么",
        )
        return any(phrase in text for phrase in phrases)

    @staticmethod
    def capabilities_message() -> str:
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
        if "detect_object" in tools:
            items.append("observe or detect a specified object")
        if "safe_pick_object" in composites:
            items.append("propose a supervised pick for a clearly specified object")
        if "move_to_named_target" in tools:
            items.append("plan movement to the named targets home, safe_home, or observe")
        if "place_object" in tools:
            items.append("propose placing an object that is already held")
        if {"open_gripper", "close_gripper", "get_gripper_state"} & tools:
            items.append("check or propose approved gripper operations")

        capability_list = "; ".join(items) if items else "inspect the currently registered robot skills"
        return (
            f"I can currently help you {capability_list}. "
            "I will ask for clarification when the target or task is ambiguous, and I will stop at the required human review points. "
            "This GUI is still plan-only, so no robot motion is executed from this chat."
        )
