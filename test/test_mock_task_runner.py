from hitl_gui.mock.mock_task_runner import MockTaskRunner


def test_mock_runner_returns_static_result():
    assert MockTaskRunner().submit("inspect cube") == {"status": "mock", "task": "inspect cube"}
