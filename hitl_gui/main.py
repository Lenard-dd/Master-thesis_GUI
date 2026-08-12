"""Executable entry point for the static NiceGUI HITL prototype."""

from __future__ import annotations

import argparse

from nicegui import app, ui

from hitl_gui.gui_controller import GuiController


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static LLM Robot HITL GUI prototype")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--mode", choices=["MOCK", "ROS"], help="Override gui_config.yaml monitoring mode.")
    parser.add_argument("--agent-enabled", type=_as_bool)
    parser.add_argument("--perception-enabled", type=_as_bool)
    parser.add_argument("--grasp-enabled", type=_as_bool)
    parser.add_argument("--simulation", type=_as_bool)
    return parser.parse_args()


def _as_bool(value: str) -> bool:
    value = value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected a boolean, got {value!r}")


def main() -> None:
    args = parse_args()
    overrides = {
        key: value for key, value in {
            "agent_enabled": args.agent_enabled,
            "perception_enabled": args.perception_enabled,
            "grasp_enabled": args.grasp_enabled,
            "simulation": args.simulation,
            "gui_mode": args.mode,
        }.items() if value is not None
    }
    controller = GuiController(config_overrides=overrides)
    app.on_shutdown(controller.shutdown)
    ui.page("/")(controller.build_page)
    ui.run(
        host=args.host or controller.gui_config.get("host", "127.0.0.1"),
        port=args.port or int(controller.gui_config.get("port", 8080)),
        reload=False,
        title="Milo Robot Collaboration Studio",
    )


if __name__ == "__main__":
    main()
