"""Small in-memory state model used only by the static GUI prototype."""

from dataclasses import dataclass, field


@dataclass
class ChatEntry:
    text: str
    sent: bool
    name: str


@dataclass
class AppState:
    """Presentation state; it does not communicate with ROS or an Agent."""

    agent_status: str = "IDLE"
    current_task: str = "None"
    messages: list[ChatEntry] = field(default_factory=list)
    log_rows: list[dict[str, str]] = field(default_factory=lambda: [
        {"time": "--:--:--", "level": "INFO", "message": "GUI initialized"}
    ])

    def add_user_message(self, text: str) -> None:
        self.messages.append(ChatEntry(text=text, sent=True, name="Operator"))

    def clear_messages(self) -> None:
        self.messages.clear()

    def stop_task(self) -> None:
        self.agent_status = "IDLE"
        self.current_task = "None"
        self.log_rows.append({"time": "--:--:--", "level": "INFO", "message": "Task stopped (mock)."})
