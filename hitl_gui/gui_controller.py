"""Compose the static NiceGUI layout without integrating robot systems."""

from nicegui import ui

from hitl_gui.app_state import AppState
from hitl_gui.panels.chat_panel import create_chat_panel
from hitl_gui.panels.header_panel import create_header_panel
from hitl_gui.panels.hitl_panel import create_hitl_panel
from hitl_gui.panels.log_panel import create_log_panel
from hitl_gui.panels.status_panel import create_status_panel
from hitl_gui.panels.tool_flow_panel import create_tool_flow_panel


class GuiController:
    """Build and own the prototype's in-memory UI state."""

    def __init__(self) -> None:
        self.state = AppState()

    def build_page(self) -> None:
        ui.colors(primary="#1d4f91", secondary="#546e7a", accent="#1976d2")
        ui.add_head_html("<style>body { background: #f5f7fa; }</style>")

        with ui.column().classes("w-full min-h-screen gap-4 p-4"):
            create_header_panel(self.state)
            with ui.splitter(value=32).classes("w-full flex-grow min-h-[520px]") as outer:
                with outer.before:
                    create_chat_panel(self.state)
                with outer.after:
                    with ui.splitter(value=64).classes("w-full h-full") as inner:
                        with inner.before:
                            create_tool_flow_panel()
                        with inner.after:
                            create_status_panel()
            create_hitl_panel()
            create_log_panel(self.state)
