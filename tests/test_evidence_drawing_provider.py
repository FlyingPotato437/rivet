"""Synthetic HTTP/provider controls; no remote models or real drawing pages."""

import hashlib
import io
import json
import stat
from pathlib import Path

import httpx
import pymupdf
import pytest
from PIL import Image

from backend.equipment.evidence import drawing_reader as reader


@pytest.fixture(autouse=True)
def database():
    """Only HTTP stubs and temporary synthetic files are used."""
    yield


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-test-key-no-network")


def _http(monkeypatch, payload, *, status=200):
    calls = []
    client_type = httpx.Client

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    return calls


def _completed(text='{"synthetic_control":true}'):
    return {
        "id": "resp_synthetic",
        "model": "gpt-6.1-sol",
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 4, "output_tokens": 3},
    }


def test_generic_default_keeps_20000_and_engineering_cap_is_explicit(monkeypatch):
    monkeypatch.delenv("RIVET_ENGINEERING_DRAWING_OUTPUT_TOKENS", raising=False)
    calls = _http(monkeypatch, _completed())
    assert reader.engineering_drawing_output_tokens() == 60000
    first, generic_usage = reader.transcribe_drawing([], model="gpt-6.1-sol")
    second, engineering_usage = reader.transcribe_drawing(
        [],
        model="gpt-6.1-sol",
        max_output_tokens=reader.engineering_drawing_output_tokens(),
    )
    assert first == second == {"synthetic_control": True}
    assert [call["max_output_tokens"] for call in calls] == [20000, 60000]
    assert {**calls[0], "max_output_tokens": 60000} == calls[1]
    assert generic_usage["request_settings"]["max_output_tokens"] == 20000
    assert engineering_usage["request_settings"]["max_output_tokens"] == 60000
    assert (
        generic_usage["request_settings"]["request_sha256"]
        != engineering_usage["request_settings"]["request_sha256"]
    )


@pytest.mark.parametrize("value", [1024, 48000, 60000, 128000])
def test_bounded_explicit_cap_reaches_http(value, monkeypatch):
    calls = _http(monkeypatch, _completed())
    reader.transcribe_drawing([], model="gpt-6.1-sol", max_output_tokens=value)
    assert calls[0]["max_output_tokens"] == value


@pytest.mark.parametrize("value", [None, True, False, 1023, 128001, 60000.5, "60000"])
def test_invalid_explicit_cap_fails_before_http(value, monkeypatch):
    calls = _http(monkeypatch, _completed())
    with pytest.raises(ValueError, match="between 1024 and 128000"):
        reader.transcribe_drawing([], model="gpt-6.1-sol", max_output_tokens=value)
    assert calls == []


@pytest.mark.parametrize("value", ["", "abc", "60000.5", "1023", "128001"])
def test_invalid_engineering_cap_is_not_silently_clamped(value, monkeypatch):
    monkeypatch.setenv("RIVET_ENGINEERING_DRAWING_OUTPUT_TOKENS", value)
    with pytest.raises(ValueError, match="between 1024 and 128000"):
        reader.engineering_drawing_output_tokens()


def test_incomplete_response_preserves_reason_usage_model_and_bounded_output(
    monkeypatch,
):
    payload = _completed("x" * 70000)
    payload.update(
        status="incomplete", incomplete_details={"reason": "max_output_tokens"}
    )
    _http(monkeypatch, payload)

    with pytest.raises(
        reader.DrawingProviderError, match="max_output_tokens"
    ) as caught:
        reader.transcribe_drawing([], model="gpt-6.1-sol", max_output_tokens=60000)

    error = caught.value
    assert isinstance(error, ValueError)
    assert error.provider_metadata["status"] == "incomplete"
    assert error.provider_metadata["incomplete_details"] == {
        "reason": "max_output_tokens"
    }
    assert error.provider_metadata["usage"] == payload["usage"]
    assert error.provider_metadata["model"] == "gpt-6.1-sol"
    assert error.provider_metadata["id"] == "resp_synthetic"
    assert len(error.output_excerpt) == 64000 and error.output_excerpt_truncated
    assert error.diagnostic()["graph_accepted"] is False
    assert error.request_settings["max_output_tokens"] == 60000


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress"])
def test_noncompleted_even_valid_json_is_never_accepted(status, monkeypatch):
    payload = _completed()
    payload["status"] = status
    _http(monkeypatch, payload)
    with pytest.raises(reader.DrawingProviderError, match="no graph accepted"):
        reader.transcribe_drawing([], model="gpt-6.1-sol")


def test_http_failure_preserves_provider_error_without_accepting_output(monkeypatch):
    payload = {
        "id": "resp_error",
        "error": {"code": "synthetic_limit", "message": "Synthetic provider error"},
    }
    _http(monkeypatch, payload, status=429)
    with pytest.raises(reader.DrawingProviderError) as caught:
        reader.transcribe_drawing([], model="gpt-6.1-sol")
    assert caught.value.provider_metadata["http_status"] == 429
    assert caught.value.provider_metadata["error"]["code"] == "synthetic_limit"


def test_completed_but_invalid_json_remains_a_rejected_provider_result(monkeypatch):
    _http(monkeypatch, _completed('{"unfinished":'))
    with pytest.raises(reader.DrawingProviderError, match="invalid JSON") as caught:
        reader.transcribe_drawing([], model="gpt-6.1-sol")
    assert caught.value.provider_metadata["status"] == "completed"
    assert caught.value.output_excerpt == '{"unfinished":'


def test_timeout_is_a_typed_failure_with_no_transport_secret(monkeypatch):
    def fail(**_kwargs):
        raise httpx.ReadTimeout("synthetic transport timeout")

    monkeypatch.setattr(reader.httpx, "Client", fail)
    with pytest.raises(reader.DrawingProviderError, match="transport failed") as caught:
        reader.transcribe_drawing([], model="gpt-6.1-sol", timeout_seconds=900)
    assert caught.value.provider_metadata["status"] == "transport_error"
    assert caught.value.provider_metadata["error_type"] == "ReadTimeout"
    assert "synthetic-test-key" not in json.dumps(caught.value.diagnostic())


def test_failed_response_archive_is_private_bound_and_cannot_be_a_cached_graph(
    tmp_path, monkeypatch
):
    source_path = tmp_path / "synthetic.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=100, height=100)
        doc.save(source_path)
    source = {
        "id": "synthetic-source",
        "name": source_path.name,
        "path": str(source_path),
        "role": "drawing",
        "revision": "SYNTHETIC",
        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }
    image = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(image, format="PNG")
    monkeypatch.setattr(
        reader,
        "_drawing_images",
        lambda _page: (
            [([0, 0, 1, 1], image.getvalue())],
            0,
            None,
            {"status": "synthetic"},
        ),
    )
    payload = _completed('{"partial":true}')
    payload.update(
        status="incomplete", incomplete_details={"reason": "max_output_tokens"}
    )
    calls = _http(monkeypatch, payload)
    output = tmp_path / "failed-reading"

    for _attempt in range(2):
        with pytest.raises(reader.DrawingProviderError) as caught:
            reader.read_drawing_evidence(
                source, [1], output_dir=output, model="gpt-6.1-sol"
            )

    assert len(calls) == 2  # Rejected responses never become accepted response caches.
    path = Path(caught.value.diagnostic_path)
    assert path.is_file() and path.parent == output
    record = json.loads(path.read_text())
    assert record["source_id"] == source["id"] and record["sha256"] == source["sha256"]
    assert record["page"] == 1 and record["model"] == "gpt-6.1-sol"
    assert len(record["prompt_sha256"]) == 64
    assert len(record["request_settings"]["request_sha256"]) == 64
    assert record["input_images"][0]["whole_upright_bbox"] == [0, 0, 1, 1]
    assert len(record["input_images"][0]["sha256"]) == 64
    assert (
        record["provider_metadata"]["incomplete_details"]["reason"]
        == "max_output_tokens"
    )
    assert record["graph_accepted"] is False and "reading" not in record
    assert not list(output.glob("*-raw.json"))
    assert not list(output.glob("*drawing-report*"))
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
