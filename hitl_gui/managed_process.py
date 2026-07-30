from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProcessStatus(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    EXITED = "EXITED"
    ERROR = "ERROR"


@dataclass
class ManagedProcess:
    component_id: str
    display_name: str
    command: list[str]
    working_directory: str | None
    process: Any = None
    pid: int | None = None
    process_group_id: int | None = None
    start_time: str | None = None
    exit_code: int | None = None
    status: ProcessStatus = ProcessStatus.STOPPED
    recent_output: list[str] = field(default_factory=list)
    started_by_gui: bool = False
