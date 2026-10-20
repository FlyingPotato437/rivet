"""Synthetic transport controls; no provider, source-page or accuracy calls."""

import copy
from types import SimpleNamespace

import pytest

from backend.equipment.evidence import drawing_reader as reader


@pytest.fixture(autouse=True)
def database():
    """Transport controls do not rebuild the application database."""
    yield


@pytest.fixture
def calls(monkeypatch):
    captured = []
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-transport-control")

    class Client:
        def __init__(self, **kwargs):
            self.record = {"client": copy.deepcopy(kwargs)}
            captured.append(self.record)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, url, **kwargs):
            self.record.update(url=url, body=copy.deepcopy(kwargs["json"]))
            return SimpleNamespace(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {
                    "status": "completed",
                    "output": [{"content": [{"type": "output_text", "text": '{"synthetic_control":true}'}]}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

    monkeypatch.setattr(reader.httpx, "Client", Client)
    return captured


def test_general_caller_keeps_180_seconds_even_with_engineering_env(monkeypatch, calls):
    monkeypatch.setenv("RIVET_ENGINEERING_DRAWING_TIMEOUT_SECONDS", "1200")
    value, _usage = reader.transcribe_drawing([], model="gpt-6.1-sol")
    assert value == {"synthetic_control": True}
    assert calls[0]["client"]["timeout"] == 180


def test_engineering_default_is_explicit_900_seconds(monkeypatch, calls):
    monkeypatch.delenv("RIVET_ENGINEERING_DRAWING_TIMEOUT_SECONDS", raising=False)
    assert reader.engineering_drawing_timeout() == 900
    reader.transcribe_drawing([], model="gpt-6.1-sol", timeout_seconds=reader.engineering_drawing_timeout())
    assert calls[0]["client"]["timeout"] == 900


@pytest.mark.parametrize("seconds", [30, 900.5, 1800])
def test_explicit_validated_timeout_reaches_http_client(seconds, calls):
    reader.transcribe_drawing([], model="gpt-6.1-sol", timeout_seconds=seconds)
    assert calls[0]["client"]["timeout"] == seconds


@pytest.mark.parametrize("value", ["30", "900.5", "1800"])
def test_engineering_env_accepts_finite_bounded_seconds(value, monkeypatch):
    monkeypatch.setenv("RIVET_ENGINEERING_DRAWING_TIMEOUT_SECONDS", value)
    assert reader.engineering_drawing_timeout() == float(value)


@pytest.mark.parametrize("value", ["", "bad", "29.9", "1800.1", "nan", "inf", "-1"])
def test_invalid_engineering_env_is_not_silently_clamped(value, monkeypatch):
    monkeypatch.setenv("RIVET_ENGINEERING_DRAWING_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="between 30 and 1800"):
        reader.engineering_drawing_timeout()


@pytest.mark.parametrize("value", [None, False, True, 0, 29, 1801, float("nan"), float("inf")])
def test_invalid_explicit_timeout_fails_before_provider(value, calls):
    with pytest.raises(ValueError, match="between 30 and 1800"):
        reader.transcribe_drawing([], model="gpt-6.1-sol", timeout_seconds=value)
    assert not calls


def test_longer_wait_does_not_change_model_request_body(calls):
    images = [([.1, .2, .3, .4], b"synthetic-control-image")]
    reader.transcribe_drawing(images, model="gpt-6.1-sol")
    reader.transcribe_drawing(images, model="gpt-6.1-sol", timeout_seconds=900)
    assert calls[0]["body"] == calls[1]["body"]
    assert calls[0]["url"] == calls[1]["url"]
    assert calls[0]["client"]["timeout"] != calls[1]["client"]["timeout"]
