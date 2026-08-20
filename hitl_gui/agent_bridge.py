"""Thin GUI adapter over the existing LLM_Ros Python Agent API.

It deliberately calls only AgentController.propose_next_action: no runtime,
tool executor, trajectory, or hardware interface is invoked here.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
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

    def __init__(self, mode: str = "existing_scripted") -> None:
        self.mode = mode

    def submit(
        self,
        instruction: str,
        execution_mode: str = "plan_only",
        conversation_config: dict[str, Any] | None = None,
    ) -> AgentResponse:
        if self.is_capability_question(instruction):
            return AgentResponse(self.capabilities_message())
        conversation = self.short_conversation_response(
            instruction, conversation_config or {}
        )
        if conversation is not None:
            return AgentResponse(conversation)
        if self.is_small_talk(instruction):
            return AgentResponse(self._llm_conversation_reply(instruction, conversation_config or {}))
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
