"""Stable loaders for robot-project configuration used by the GUI adapters."""

from __future__ import annotations

from typing import Any

import yaml


def load_grasping_config() -> dict[str, Any]:
    """Load grasping.yaml without depending on a demo module's private API."""
    from llm_skill_robot.utils import get_config_dir

    path = get_config_dir() / "grasping.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Grasping configuration not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    if not isinstance(document, dict):
        raise ValueError("grasping.yaml must contain a YAML mapping.")
    return document


def load_tabletop_safety_config() -> dict[str, Any]:
    """Load the calibrated tabletop policy shared with the supervised demo."""
    from llm_skill_robot.utils import get_config_dir

    path = get_config_dir() / "tabletop_safety.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Tabletop safety configuration not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    if not isinstance(document, dict):
        raise ValueError("tabletop_safety.yaml must contain a YAML mapping.")
    return document
