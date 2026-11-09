from __future__ import annotations

from typing import Any

import httpx
import pytest

import app.modules.provider_transport_evidence as evidence_module
from app.execution.research_start_provider_adapter import (
    PreparedPlannerAttempt,
    execute_prepared_attempt,
)


class _Attempt:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    def mark_io_started(self) -> None:  # pragma: no cover - recorder stub
        return None


class _Repository:
    def __init__(self) -> None:
        self.identities: list[str] = []
        self.attempt_numbers: list[int] = []

    def begin(self, **kwargs: Any) -> _Attempt:
        self.identities.append(str(kwargs["operation_identity"]))
        self.attempt_numbers.append(int(kwargs["attempt_number"]))
        return _Attempt()

    def mark_io_started(self, _attempt: _Attempt) -> None:
        return None

    def finish(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Transport:
    def post(self, *_args: Any, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "choices": [{"message": {"content": "{}"}}],
                "usage": {},
            },
            headers={"x-request-id": "req_test"},
            request=request,
        )


def _attempt(ordinal: int) -> PreparedPlannerAttempt[dict[str, Any]]:
    return PreparedPlannerAttempt(
        phase="research_planner",
        ordinal=ordinal,
        provider="huiyan_chat_completions",
        model="deepseek-v4.1-flash",
        endpoint="https://example.invalid/v1/chat/completions",
        payload={"model": "deepseek-v4.1-flash", "messages": []},
        response_format=None,
        timeout_seconds=5.0,
        decode=lambda body: dict(body),
    )


def test_retry_ordinals_get_distinct_operation_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER_API_KEY", "test-key")
    repository = _Repository()
    monkeypatch.setattr(evidence_module, "ProviderHttpAttemptRepository", lambda: repository)
    monkeypatch.setattr(
        evidence_module,
        "current_provider_execution_parent",
        lambda: object(),
    )

    execute_prepared_attempt(_attempt(1), transport=_Transport())
    execute_prepared_attempt(_attempt(2), transport=_Transport())

    assert repository.identities == [
        "research_start:research_planner:1",
        "research_start:research_planner:2",
    ]
    assert repository.attempt_numbers == [1, 2]
