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


@dataclass
class AgentResponse:
    message: str
    tool_events: list[AgentToolEvent] = field(default_factory=list)


class ExistingAgentBridge:
    def __init__(self, mode: str = "existing_scripted") -> None:
        self.mode = mode

    def submit(self, instruction: str, execution_mode: str = "plan_only") -> AgentResponse:
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
            return AgentResponse(decision.message, [AgentToolEvent(
                node_id=f"agent-{call.tool_name}-1", parent_id=None,
                tool_name=call.tool_name, display_name=call.tool_name.replace("_", " ").title(),
                status="waiting_approval" if proposal.requires_human_gate else "pending",
                input_json=dict(call.arguments),
                output_json={"approval_stages": approval_stages},
                requires_approval=proposal.requires_human_gate,
                approval_stages=approval_stages,
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
            )])
        return AgentResponse(decision.message)
