import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import peak_recommendations as recommendations
from app.auth import project_for_path
from app.errors import ApiException
from app.llm_client import LlmError


def payload(**changes):
    return recommendations.PeakRecommendationRequest.model_validate({
        "material": "탄소 코팅 LFP", "sample": "시료 1",
        "candidates": [{"id": 4, "x": 1350, "intensity": 0.7}, {"id": 8, "x": 1580, "intensity": 0.4}],
        **changes,
    })


@pytest.fixture
def llm(monkeypatch):
    state = {"response": {"peaks": [{"id": 4, "name": "D band (추정)", "reason": "표준 대조 필요"}]}}

    class Client:
        def __init__(self, *args):
            state["settings"] = args

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def build_request_payload(self, system, user, **kwargs):
            state["system"], state["input"] = system, json.loads(user)
            return kwargs

        def chat_completion(self, request):
            if "error" in state:
                raise state["error"]
            return state.get("text", json.dumps(state["response"])), {}

    monkeypatch.setattr(recommendations, "LocalLlmClient", Client)
    return state


def settings():
    return SimpleNamespace(llm_base_url="http://127.0.0.1:8001", llm_model="local-test", llm_timeout_seconds=120,
                           llm_temperature=0.1, llm_max_tokens=4000, llm_validate_model=True)


def test_only_grounded_candidates_returned(llm):
    llm["response"] = {"peaks": [
        {"id": 4, "name": "D band", "reason": "추정", "x": 9999, "intensity": 999},
        {"id": 4, "name": "duplicate"}, {"id": 999, "name": "invented"},
        {"id": "8", "name": "bad type"}, {"id": 8, "name": "G band"},
    ]}
    result = recommendations.recommend_peaks(settings(), "Raman", payload())
    assert [p["id"] for p in result["peaks"]] == [4, 8]
    assert result["peaks"][0]["x"] == 1350
    assert result["peaks"][0]["intensity"] == 0.7
    assert "검토용" in result["warning"]
    assert llm["input"]["material"] == "탄소 코팅 LFP"
    assert llm["settings"][2] == 60
    assert llm["settings"][4] == 1800


@pytest.mark.parametrize("changes", [
    {"material": "  "}, {"material": "a" * 1001}, {"candidates": []},
    {"candidates": [{"id": 1, "x": 3, "intensity": 4}] * 121},
    {"candidates": [{"id": 1, "x": 3, "intensity": 4}] * 2},
    {"candidates": [{"id": 1, "x": float("nan"), "intensity": 4}]},
    {"candidates": [{"id": 1, "x": 2, "intensity": float("inf")}]},
    {"endpoint": "https://untrusted.invalid"},
])
def test_input_bounds(changes):
    with pytest.raises(ValidationError):
        payload(**changes)


@pytest.mark.parametrize("response", [{}, {"peaks": None}, {"peaks": [{"id": 999, "name": "unknown"}]}])
def test_invalid_output_does_not_silently_apply(llm, response):
    llm["response"] = response
    with pytest.raises(ApiException):
        recommendations.recommend_peaks(settings(), "FT-IR", payload())


def test_empty_recommendations_are_valid(llm):
    llm["response"] = {"peaks": []}
    assert recommendations.recommend_peaks(settings(), "FT-IR", payload())["peaks"] == []


def test_model_failure_and_invalid_json_release_slot(llm):
    llm["error"] = LlmError("LLM_TIMEOUT", "timeout", retryable=True)
    for _ in range(3):
        with pytest.raises(ApiException):
            recommendations.recommend_peaks(settings(), "FT-IR", payload())
    del llm["error"]
    llm["text"] = "invalid json"
    with pytest.raises(ApiException):
        recommendations.recommend_peaks(settings(), "FT-IR", payload())
    del llm["text"]
    assert recommendations.recommend_peaks(settings(), "FT-IR", payload())["peaks"]


def test_busy_limit(llm):
    recommendations._slots.acquire()
    recommendations._slots.acquire()
    try:
        with pytest.raises(ApiException) as exc:
            recommendations.recommend_peaks(settings(), "FT-IR", payload())
        assert exc.value.status_code == 429
    finally:
        recommendations._slots.release()
        recommendations._slots.release()


@pytest.mark.parametrize("kind", ["ftir", "raman"])
def test_api_and_project_permission_mapping(llm, kind):
    from app.ftir_web import router as ftir_router
    from app.raman_web import router as raman_router
    app = FastAPI()
    app.state.settings = settings()
    app.include_router(ftir_router if kind == "ftir" else raman_router)
    route = f"/api/v1/{kind}/peaks/recommend"
    assert project_for_path(route) == kind.upper()
    with TestClient(app) as client:
        result = client.post(route, json=payload().model_dump())
        assert result.status_code == 200
        assert result.json()["peaks"][0]["x"] == 1350
        assert client.post(route, json={"material": "x", "candidates": []}).status_code == 422


@pytest.mark.parametrize("kind", ["ftir", "raman"])
def test_recommendations_enforce_login_and_project_permission(llm, kind, tmp_path):
    from unittest.mock import Mock
    from app.auth import install_auth
    from app.config import Settings
    from app.ftir_web import router as ftir_router
    from app.raman_web import router as raman_router
    app = FastAPI()
    install_auth(app, Settings(storage_root=tmp_path, auth_enabled=True), Mock())
    app.include_router(ftir_router if kind == "ftir" else raman_router)
    service = app.state.auth_service
    service.context_from_token = Mock(return_value=None)
    route = f"/api/v1/{kind}/peaks/recommend"
    with TestClient(app) as client:
        assert client.post(route, json=payload().model_dump()).status_code == 401
        service.context_from_token.return_value = SimpleNamespace(status="ACTIVE", is_admin=False, projects=set())
        assert client.post(route, json=payload().model_dump()).status_code == 403
        assert "input" not in llm
        service.context_from_token.return_value.projects = {kind.upper()}
        assert client.post(route, json=payload().model_dump()).status_code == 200
