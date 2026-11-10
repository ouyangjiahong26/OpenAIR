"""Research-start session status must expose real token usage for UI disclosure."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import research_start as route


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _install_session(monkeypatch: pytest.MonkeyPatch, session: dict[str, Any]) -> None:
    monkeypatch.setattr(
        route.research_start_repository,
        "get_scoped_session",
        lambda *_args, **_kwargs: session,
    )


def test_session_includes_reserved_task_token_usage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_session(
        monkeypatch,
        {
            "sessionId": "intent_123456789abc",
            "status": "running",
            "currentPhase": "planning",
            "reservedTaskId": "task_usage",
        },
    )

    class Ledger:
        def sum_task_usage(self, *_args: Any, **_kwargs: Any) -> int:
            return 1310

    monkeypatch.setattr(route, "get_token_ledger_repository", lambda: Ledger())

    response = client.get(
        "/api/research-start/sessions/intent_123456789abc",
        headers={"X-OpenAIR-Owner-Id": "owner", "X-OpenAIR-Workspace-Id": "workspace"},
    )

    assert response.status_code == 200
    assert response.json()["tokenUsage"] == {"totalTokens": 1310}


def test_session_without_reserved_task_omits_token_usage(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_session(
        monkeypatch,
        {
            "sessionId": "intent_123456789abc",
            "currentPhase": "understanding",
            "reservedTaskId": None,
            "status": "running",
        },
    )
    monkeypatch.setattr(
        route,
        "get_token_ledger_repository",
        lambda: pytest.fail("ledger must not be read without a task"),
    )

    response = client.get(
        "/api/research-start/sessions/intent_123456789abc",
        headers={"X-OpenAIR-Owner-Id": "owner", "X-OpenAIR-Workspace-Id": "workspace"},
    )

    assert response.status_code == 200
    assert response.json()["tokenUsage"] is None
