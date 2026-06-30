from __future__ import annotations

import json
from types import SimpleNamespace

from app import assignment_suggestions
from app.assignment_suggestions import AssignmentSuggestionRequest


class FakeLlmClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "FakeLlmClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def build_request_payload(self, *args, **kwargs) -> dict:
        return {"messages": []}

    def chat_completion(self, request_payload: dict) -> tuple[str, dict]:
        return (
            json.dumps({
                "description": "Ethanol draft",
                "assignments": [
                    {
                        "centerWavenumber": 1050,
                        "tolerance": 35,
                        "name": "C-O stretch",
                        "color": "#2563EB",
                        "note": "literature candidate",
                    },
                    {
                        "centerWavenumber": 3300,
                        "tolerance": 80,
                        "name": "O-H stretch",
                        "color": "not-a-color",
                        "note": "",
                    },
                    {
                        "centerWavenumber": "bad",
                        "tolerance": 40,
                        "name": "invalid row",
                    },
                ],
            }),
            {},
        )


def test_suggest_assignment_library_normalises_llm_draft(monkeypatch) -> None:
    monkeypatch.setattr(
        assignment_suggestions,
        "LocalLlmClient",
        FakeLlmClient,
    )
    settings = SimpleNamespace(
        llm_base_url="http://127.0.0.1:8001",
        llm_model="test-model",
        llm_timeout_seconds=1,
        llm_temperature=0.1,
        llm_max_tokens=1200,
        llm_validate_model=False,
    )

    payload = assignment_suggestions.suggest_assignment_library(
        settings,
        AssignmentSuggestionRequest(
            experiment_code="FT-IR",
            material="ethanol",
        ),
    )

    library = payload["library"]
    assert library["id"] == "ethanol-ftir"
    assert library["name"] == "ethanol FT-IR"
    assert library["description"] == "Ethanol draft"
    assert library["assignmentCount"] == 2
    assert library["assignments"][0]["color"] == "#2563eb"
    assert library["assignments"][1]["color"] == "#64748b"
    assert "LLM 추천 초안" in payload["warning"]
