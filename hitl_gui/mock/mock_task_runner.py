"""Asynchronous, mock-only task progression for Phase 2."""

from __future__ import annotations

import asyncio

from hitl_gui.app_state import HitlDecision, TaskStatus, ToolStatus


class MockTaskRunner:
    """Simulates reviewed workflow steps without ROS, LLM, or robot access."""

    def __init__(self, controller, step_delay: float = 0.6) -> None:
        self.controller = controller
        self.step_delay = step_delay
        self._decision_event = asyncio.Event()
        self._decision: HitlDecision | None = None
        self._task: asyncio.Task | None = None

    def start(self, task_id: str) -> None:
        self._decision = None
        self._decision_event = asyncio.Event()
        self._task = asyncio.create_task(self.run(task_id))

    async def wait(self) -> None:
        if self._task is not None:
            await self._task

    def submit_decision(self, decision: HitlDecision) -> None:
        self._decision = decision
        self._decision_event.set()

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def run(self, task_id: str) -> None:
        try:
            for tool_name, task_status in [
                ("understand_instruction", TaskStatus.UNDERSTANDING_TASK),
                ("detect_objects", TaskStatus.PERCEIVING),
                ("select_target", TaskStatus.TARGET_REVIEW),
                ("generate_grasp_candidates", TaskStatus.GENERATING_GRASPS),
                ("validate_grasp", TaskStatus.GRASP_REVIEW),
                ("plan_motion", TaskStatus.PLANNING),
            ]:
                if not self.controller.is_current_task(task_id):
                    return
                self.controller.set_task_status(task_status)
                self.controller.update_tool_status(tool_name, ToolStatus.RUNNING)
                await asyncio.sleep(self.step_delay)
                self.controller.update_tool_status(tool_name, ToolStatus.SUCCEEDED)

            self.controller.create_trajectory()
            self.controller.update_tool_status("trajectory_review", ToolStatus.WAITING_APPROVAL)
            self.controller.set_task_status(TaskStatus.WAITING_APPROVAL)
            self.controller.create_hitl_request()
            await self._decision_event.wait()
            await self._resolve_decision(task_id, self._decision)
        except asyncio.CancelledError:
            return

    async def _resolve_decision(self, task_id: str, decision: HitlDecision | None) -> None:
        if not self.controller.is_current_task(task_id) or decision is None:
            return
        if decision == HitlDecision.APPROVE:
            self.controller.complete_active_trajectory_review()
            self.controller.set_task_status(TaskStatus.EXECUTING)
            await self._simulate_step("execute_motion")
            self.controller.set_task_status(TaskStatus.VERIFYING)
            await self._simulate_step("verify_grasp")
            self.controller.complete_task()
        elif decision == HitlDecision.REJECT:
            self.controller.reject_task()
        elif decision == HitlDecision.REPLAN:
            self.controller.replan_task()
            self._decision = None
            self._decision_event = asyncio.Event()
            await self._decision_event.wait()
            await self._resolve_decision(task_id, self._decision)

    async def _simulate_step(self, tool_name: str) -> None:
        self.controller.update_tool_status(tool_name, ToolStatus.RUNNING)
        await asyncio.sleep(self.step_delay)
        self.controller.update_tool_status(tool_name, ToolStatus.SUCCEEDED)
