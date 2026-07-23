"""HTTP boundary tests for the standalone planner service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from nai_prompt_planner.api import create_app


def test_health_does_not_expose_key_or_base_url(monkeypatch) -> None:
    """Expose only readiness and model metadata."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://private.example/v1")

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "ready": True,
        "model": "deepseek-v4-flash",
        "error": None,
    }
    assert "secret-key" not in response.text
    assert "private.example" not in response.text


def test_plan_requires_configured_service_token(monkeypatch) -> None:
    """Protect planning requests when a service token is configured."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")
    monkeypatch.setenv("PLANNER_SERVICE_TOKEN", "service-secret")
    result = {
        "ok": True,
        "prompt": "1girl, solo, layered dress, gentle smile",
        "character_prompts": {},
        "error": None,
    }

    with patch(
        "nai_prompt_planner.api.DeepSeekPromptPlanner.plan",
        new=AsyncMock(return_value=result),
    ):
        with TestClient(create_app()) as client:
            unauthorized = client.post(
                "/v1/plan",
                json={"description": "可爱的女孩"},
            )
            authorized = client.post(
                "/v1/plan",
                headers={"Authorization": "Bearer service-secret"},
                json={"description": "可爱的女孩"},
            )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json() == result


def test_plan_rejects_protocol_extra_fields(monkeypatch) -> None:
    """Keep the public request schema deliberately small and strict."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-key")

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/plan",
            json={"description": "可爱的女孩", "api_key": "must-not-be-accepted"},
        )

    assert response.status_code == 422
