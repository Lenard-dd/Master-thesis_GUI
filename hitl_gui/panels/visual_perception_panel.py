"""Compact visual evidence panel beside the persistent RViz view."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from nicegui import ui
from llm_skill_robot.utils import resolve_project_path


class VisualPerceptionPanel:
    """Show semantic scene output and the latest SAM3 2D evidence.

    The panel reads only structured results produced by the existing skills.
    It does not turn an LLM candidate into a localized object or invoke any
    robot action.
    """

    def __init__(self, controller) -> None:
        self.controller = controller
        self._content = None
        self._fingerprint: tuple[Any, ...] | None = None

    def render(self):
        with ui.card().classes("w-full p-3 min-h-[170px]"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Scene & 2D Perception").classes("text-h6")
                ui.badge("READ-ONLY", color="positive")
            ui.label(
                "Unverified semantic candidates; localize selected objects with SAM3 + RGB-D before manipulation."
            ).classes("text-xs text-grey")
            self._content = ui.column().classes("w-full gap-2 mt-2")
        self.refresh()
        return self.refresh

    def refresh(self) -> None:
        if self._content is None:
            return
        description, image_paths = self._latest_evidence()
        fingerprint = (
            repr(description),
            tuple((str(path), path.stat().st_mtime_ns if path.exists() else None) for path in image_paths),
        )
        if fingerprint == self._fingerprint:
            return
        self._fingerprint = fingerprint
        self._content.clear()
        with self._content:
            # This card spans the workspace width. Separate the semantic
            # overview, candidate inventory, and visual evidence into three
            # predictable columns.
            with ui.row().classes("w-full items-start gap-4 flex-wrap"):
                with ui.column().classes("flex-1 min-w-[240px] gap-2"):
                    if isinstance(description, dict):
                        self._render_overview(description)
                    else:
                        ui.label("No scene description yet.").classes("text-sm text-grey")
                if isinstance(description, dict):
                    with ui.column().classes("flex-1 min-w-[240px] gap-2"):
                        self._render_candidates(description)
                if image_paths:
                    with ui.column().classes("w-56 max-w-full gap-1"):
                        ui.label("Camera / SAM3 evidence").classes("text-subtitle2")
                        with ui.row().classes("w-full gap-2 flex-wrap"):
                            for path in image_paths:
                                with ui.column().classes("w-52 max-w-full gap-1"):
                                    ui.image(_data_url(path)).classes("w-full rounded border")
                                    ui.label(path.name).classes("text-[10px] text-grey truncate")

    def _latest_evidence(self) -> tuple[dict[str, Any] | None, list[Path]]:
        cached_description = getattr(self.controller.state, "latest_scene_description", None)
        description = cached_description if isinstance(cached_description, dict) else None
        nodes = reversed(getattr(self.controller.state, "tool_nodes", []))
        for node in nodes:
            output = {**getattr(node, "output_summary", {}), **getattr(node, "output_data", {})}
            tool_name = getattr(node, "tool_name", "")
            if tool_name in {"detect_object", "detect_objects"}:
                # A localization result is more useful than the unannotated
                # RGB capture. Every detection shares one combined overlay,
                # so this normally returns exactly one image.
                overlays: list[Path] = []
                seen: set[Path] = set()
                for candidate in output.get("objects", []) or []:
                    metadata = candidate.get("metadata", {}) if isinstance(candidate, dict) else {}
                    self._append_image(metadata.get("overlay_path"), overlays, seen)
                scene = output.get("scene", {})
                for candidate in scene.get("objects", []) if isinstance(scene, dict) else []:
                    metadata = candidate.get("metadata", {}) if isinstance(candidate, dict) else {}
                    self._append_image(metadata.get("overlay_path"), overlays, seen)
                return description, overlays[:1]
            if tool_name == "describe_scene":
                value = output.get("scene_description")
                if description is None and isinstance(value, dict):
                    description = value
                image_paths: list[Path] = []
                self._append_image(output.get("image_path"), image_paths, set())
                return description, image_paths[:1]

        image_paths: list[Path] = []
        self._append_image(
            getattr(self.controller.state, "latest_scene_image_path", None),
            image_paths,
            set(),
        )
        return description, image_paths[:1]

    @staticmethod
    def _append_image(value: Any, paths: list[Path], seen: set[Path]) -> None:
        if not isinstance(value, str):
            return
        # D435i/SAM3 output paths are commonly project-relative while the GUI
        # is launched from ~/dev_ws. Resolve against the LLM_Ros project too.
        path = resolve_project_path(value)
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not path.is_file():
            return
        if path not in seen:
            seen.add(path)
            paths.append(path)

    @staticmethod
    def _render_overview(description: dict[str, Any]) -> None:
        ui.label("Scene overview (unverified)").classes("text-subtitle2")
        with ui.row().classes("w-full items-center gap-2"):
            ui.badge(str(description.get("scene_type", "unknown")), color="primary")
        ui.label(_short_text(str(description.get("summary", "No summary returned.")), 230)).classes("text-sm")
        hazards = description.get("hazards", [])
        if isinstance(hazards, list) and hazards:
            ui.label("Notes / hazards").classes("text-xs font-medium text-grey-8")
            with ui.row().classes("w-full gap-1 flex-wrap"):
                for hazard in hazards:
                    ui.badge(str(hazard), color="warning").props("outline")

    @staticmethod
    def _render_candidates(description: dict[str, Any]) -> None:
        ui.label("Candidate objects").classes("text-subtitle2")
        candidates = description.get("candidate_objects", [])
        if isinstance(candidates, list) and candidates:
            with ui.row().classes("w-full gap-1 flex-wrap"):
                for item in candidates:
                    if isinstance(item, dict):
                        ui.badge(
                            f"{item.get('query', 'unknown')} {float(item.get('confidence', 0.0)):.0%}",
                            color="positive" if item.get("manipulable") else "grey",
                        )
            with ui.expansion("Candidate details", icon="list").classes("w-full text-sm"):
                for item in candidates:
                    if not isinstance(item, dict):
                        continue
                    ui.label(
                        f"• {item.get('query', 'unknown')}: {item.get('description', '')}"
                    ).classes("text-sm")
        else:
            ui.label("No candidate objects returned.").classes("text-sm text-grey")


def _short_text(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[:limit - 1].rstrip()}…"


def _data_url(path: Path) -> str:
    """Render a local, skill-generated image without exposing a file URL."""
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"
