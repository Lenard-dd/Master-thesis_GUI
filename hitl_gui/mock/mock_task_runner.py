"""Placeholder for a future mock task runner; intentionally has no side effects."""


class MockTaskRunner:
    """Represents the future mock integration boundary for this prototype."""

    def submit(self, task: str) -> dict[str, str]:
        return {"status": "mock", "task": task}
