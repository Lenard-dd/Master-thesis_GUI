from pathlib import Path

import pytest

from hitl_gui.robot_config import load_grasping_config, load_tabletop_safety_config


def test_grasping_config_loads_without_demo_private_helper():
    document = load_grasping_config()
    grasping = document.get("grasping", document)
    assert isinstance(grasping, dict)
    assert "plan_only_preview" in grasping


def test_missing_grasping_config_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr("llm_skill_robot.utils.get_config_dir", lambda: Path(tmp_path))
    with pytest.raises(FileNotFoundError, match="grasping.yaml"):
        load_grasping_config()


def test_tabletop_safety_config_loads_shared_calibration():
    document = load_tabletop_safety_config()
    config = document["tabletop_safety"]
    assert config["enabled"] is True
    assert config["frame"] == "base_link"
    assert config["grasp_contact_backoff_m"] > 0.0
    assert config["finger_axial_close_sweep_m"] > 0.0


def test_missing_tabletop_safety_config_fails_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr("llm_skill_robot.utils.get_config_dir", lambda: Path(tmp_path))
    with pytest.raises(FileNotFoundError, match="tabletop_safety.yaml"):
        load_tabletop_safety_config()
