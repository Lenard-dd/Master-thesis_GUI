"""NiceGUI panel displaying the local noVNC page."""

from __future__ import annotations

import asyncio
from html import escape
from pathlib import Path
from typing import Any, Callable

import yaml
from nicegui import ui

from hitl_gui.services.embedded_rviz_manager import EmbeddedRvizManager


def load_embedded_rviz_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load only the embedded RViz section; useful in panel tests."""
    if config_path is None:
        from hitl_gui.rviz_process_manager import load_gui_config
        return load_gui_config().get("embedded_rviz", {})
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    return data.get("embedded_rviz", {})


class EmbeddedRvizPanel:
    """Render the local-only embedded RViz area inside an existing page."""

    def __init__(self, manager: EmbeddedRvizManager, iframe_url: str,
                 open_native_rviz: Callable[[], Any] | None = None) -> None:
        self.manager, self.iframe_url, self.open_native_rviz = manager, iframe_url, open_native_rviz
        self._iframe = self._status = self._message = None

    def render(self) -> Callable[[], None]:
        with ui.card().classes("w-full p-3"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Embedded RViz").classes("text-h6")
                self._status = ui.badge("STOPPED", color="grey")
            with ui.row().classes("w-full gap-2 mt-2 flex-wrap"):
                ui.button("Start Embedded RViz", on_click=self.start, color="primary").props("dense")
                ui.button("Retry", on_click=self.start).props("dense outline")
                ui.button("Stop", on_click=self.stop).props("dense outline")
                ui.button("Restart", on_click=self.restart).props("dense outline")
                ui.button("Reload View", on_click=self.reload).props("dense outline")
                ui.button("Open Native RViz", on_click=self._open_native).props("dense outline")
                ui.button("View Logs", on_click=self.show_logs).props("dense outline")
            self._message = ui.label().classes("text-sm text-grey mt-2")
            # The URL is read exclusively from gui_config.yaml. NiceGUI's
            # default DOMPurify policy strips iframe tags, so it must be
            # disabled for this fixed, localhost-only trusted markup.
            self._iframe = ui.html("", sanitize=False).classes("w-full mt-2").style("aspect-ratio: 16 / 9;")
        self._refresh()
        ui.timer(1.0, self._refresh)
        return self._refresh

    def start(self) -> None:
        asyncio.create_task(self._run_operation("start"))

    def stop(self) -> None:
        asyncio.create_task(self._run_operation("stop"))

    def restart(self) -> None:
        asyncio.create_task(self._run_operation("restart"))

    async def _run_operation(self, operation: str) -> None:
        # Prerequisite checks and orderly process shutdown contain short waits;
        # keep them out of NiceGUI's event loop so other buttons stay responsive.
        await asyncio.to_thread(getattr(self.manager, operation))
        self._refresh()

    def reload(self) -> None:
        """Reload only the iframe; never touch the ROS/noVNC process chain."""
        if self._iframe is not None:
            self._iframe.content = self._iframe_html()
            self._iframe.update()

    def show_logs(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-[900px] max-w-full"):
            ui.label("Embedded RViz logs").classes("text-h6")
            for name, lines in self.manager.get_logs().items():
                content = escape("\n".join(lines) or "(no output)")
                ui.markdown(f"### {name}\n```\n{content}\n```")
            ui.button("Close", on_click=dialog.close)
        dialog.open()

    def _open_native(self) -> None:
        if self.open_native_rviz is not None:
            self.open_native_rviz()

    def _refresh(self) -> None:
        if self._status is None:
            return
        state = self.manager.get_status()
        status = state["status"]
        self._status.text = status
        self._status.color = {"RUNNING": "positive", "ERROR": "negative", "STARTING": "warning", "STOPPED": "grey"}[status]
        if status == "STARTING":
            self._message.text = "Starting RViz and noVNC..."
        elif status == "ERROR":
            self._message.text = state["error"] or "Embedded RViz could not start. Retry or Open Native RViz."
        elif status == "RUNNING":
            self._message.text = ""
        else:
            self._message.text = "Embedded RViz is not running."
        self._status.update()
        self._message.update()
        if self._iframe is not None:
            content = self._iframe_html() if status == "RUNNING" else ""
            if self._iframe.content != content:
                self._iframe.content = content
                self._iframe.update()

    def _iframe_html(self) -> str:
        return f'<iframe src="{escape(self.iframe_url, quote=True)}" style="width:100%;height:100%;border:none;display:block"></iframe>'
