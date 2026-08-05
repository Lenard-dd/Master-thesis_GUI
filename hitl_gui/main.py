"""Executable entry point for the static NiceGUI HITL prototype."""

from __future__ import annotations

import argparse

from nicegui import app, ui

from hitl_gui.gui_controller import GuiController


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Static LLM Robot HITL GUI prototype")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mode", choices=["MOCK", "ROS"], help="Override gui_config.yaml monitoring mode.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    controller = GuiController()
    if args.mode:
        controller.set_gui_mode(args.mode)
    app.on_shutdown(controller.shutdown)
    ui.page("/")(controller.build_page)
    ui.run(host=args.host, port=args.port, reload=False, title="Milo Robot Collaboration Studio")


if __name__ == "__main__":
    main()
