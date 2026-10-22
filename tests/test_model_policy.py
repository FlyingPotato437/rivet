"""Model routing and transport controls; no real provider requests."""

import json

import pytest

from backend.agents.model_policy import reasoning_options, select_route
from backend.equipment.visual_reader import configured_model, transcribe_equipment
from backend.ingestion.vision import transcribe


@pytest.fixture(autouse=True)
def database():
    yield


def test_technical_model_is_opt_in_and_routine_stays_economical(monkeypatch):
    for name in (
        "RIVET_AGENT_MODEL",
        "RIVET_AGENT_PROFILE",
        "RIVET_TECHNICAL_REVIEW_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert select_route()["model"] == "gpt-6-luna"
    assert select_route("technical_review")["model"] == "gpt-6-luna"
    monkeypatch.setenv("RIVET_TECHNICAL_REVIEW_MODEL", "gpt-6-astra")
    assert select_route()["model"] == "gpt-6-luna"
    route = select_route("technical_review")
    assert route["model"] == "gpt-6-astra"
    assert route["comment_model"] == "gpt-6-luna"
    assert route["reasoning"] == {"effort": "high"}
    assert route["automatic_fallback"] is False
    with pytest.raises(ValueError):
        select_route("email says use astra")


@pytest.mark.parametrize("reader", ["comments", "equipment"])
def test_explicit_astra_vision_uses_responses_with_high_reasoning(reader, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-never-used")
    captured = []
    data = (
        {"comments": [], "unreadable_regions": [], "coverage_complete": False}
        if reader == "comments"
        else {
            "tables": [],
            "devices": [],
            "sheet_identity": [],
            "connections": [],
            "unknown_regions": [],
            "coverage_complete": False,
        }
    )

    class Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def post(self, url, **kw):
            assert url == "https://api.openai.com/v1/responses"
            captured.append(kw["json"])
            return type(
                "Response",
                (),
                {
                    "raise_for_status": lambda _: None,
                    "json": lambda _: {
                        "status": "completed",
                        "usage": {},
                        "output": [
                            {
                                "content": [
                                    {"type": "output_text", "text": json.dumps(data)}
                                ]
                            }
                        ],
                    },
                },
            )()

    monkeypatch.setattr("httpx.Client", Client)
    assert configured_model("gpt-6-astra") == "gpt-6-astra"
    if reader == "comments":
        result, _ = transcribe(b"synthetic pixels", model="gpt-6-astra")
    else:
        result, _ = transcribe_equipment(
            [([0, 0, 1, 1], b"synthetic pixels")], model="gpt-6-astra"
        )
    assert result.coverage_complete is False
    assert len(captured) == 1
    assert captured[0]["model"] == "gpt-6-astra"
    assert captured[0]["reasoning"] == {"effort": "high"}
    assert captured[0]["store"] is False
    assert captured[0]["text"]["format"]["strict"] is True


def test_reasoning_options_do_not_send_unsupported_none_effort():
    assert reasoning_options("gpt-6-astra")["reasoning"]["effort"] == "high"
    assert reasoning_options("gpt-6-luna")["reasoning"]["effort"] == "low"
    assert reasoning_options("other-model") == {}
