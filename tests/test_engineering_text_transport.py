"""Synthetic semantic transport controls; no remote models or real sources."""

import json
import stat

import httpx
import pytest

from backend.engineering import text_reader as reader


@pytest.fixture(autouse=True)
def database():
    """Only stub HTTP and temporary source-bound diagnostic files are used."""
    yield


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-text-test-key-no-network")
    monkeypatch.delenv("RIVET_ENGINEERING_TEXT_OUTPUT_TOKENS", raising=False)
    monkeypatch.delenv("RIVET_ENGINEERING_TEXT_TIMEOUT_SECONDS", raising=False)


def _response(*, status="completed", text='{"items":[],"unhandled":[]}'):
    return {
        "id": "resp_synthetic_text",
        "model": "gpt-6.1-sol",
        "status": status,
        "incomplete_details": {"reason": "max_output_tokens"}
        if status == "incomplete"
        else None,
        "usage": {"input_tokens": 4, "output_tokens": 3},
        "output": [{"content": [{"type": "output_text", "text": text}]}],
    }


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


def _documents():
    return [
        {
            "id": "synthetic-specification",
            "sha256": "a" * 64,
            "role": "specification",
            "spans": [
                {
                    "id": "synthetic-span",
                    "text": "Synthetic source clause.",
                    "location": {"page": 1, "bbox": [0.1, 0.1, 0.8, 0.2]},
                }
            ],
        }
    ]


def _requirements():
    return {
        "items": [],
        "evidence_claims": [],
        "warnings": [],
        "unreadable_pages": [],
        "conflicts": [],
    }


def test_default_cap_and_timeout_are_recorded_without_prompt_or_model_changes(
    monkeypatch,
):
    calls = _http(monkeypatch, _response())
    value, usage = reader.provider([], model="gpt-6.1-sol")
    assert value == {"items": [], "unhandled": []}
    assert calls[0]["max_output_tokens"] == 32000
    assert (
        calls[0]["model"] == "gpt-6.1-sol" and calls[0]["instructions"] == reader.PROMPT
    )
    assert usage["request_settings"]["max_output_tokens"] == 32000
    assert usage["request_settings"]["timeout_seconds"] == 900
    assert len(usage["request_settings"]["request_sha256"]) == 64


@pytest.mark.parametrize("value", [1024, 60000, 128000])
def test_explicit_or_environment_cap_reaches_http(value, monkeypatch):
    calls = _http(monkeypatch, _response())
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_OUTPUT_TOKENS", str(value))
    assert reader.text_output_tokens() == value
    reader.provider([], model="gpt-6.1-sol")
    reader.provider([], model="gpt-6.1-sol", max_output_tokens=value)
    assert [call["max_output_tokens"] for call in calls] == [value, value]


@pytest.mark.parametrize(
    "value", ["", "abc", "32_000", "32000.5", "1023", "128001", "true"]
)
def test_invalid_environment_cap_fails_before_http(value, monkeypatch):
    calls = _http(monkeypatch, _response())
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_OUTPUT_TOKENS", value)
    with pytest.raises(ValueError, match="between 1024 and 128000"):
        reader.provider([], model="gpt-6.1-sol")
    assert calls == []


@pytest.mark.parametrize("value", [True, False, 1023, 128001, 32000.5, "32000"])
def test_noninteger_or_out_of_range_explicit_cap_is_rejected(value, monkeypatch):
    calls = _http(monkeypatch, _response())
    with pytest.raises(ValueError, match="between 1024 and 128000"):
        reader.provider([], model="gpt-6.1-sol", max_output_tokens=value)
    assert calls == []


@pytest.mark.parametrize("value", ["29", "1801", "nan", "inf"])
def test_timeout_remains_finite_and_bounded(value, monkeypatch):
    calls = _http(monkeypatch, _response())
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="30–1800"):
        reader.provider([], model="gpt-6.1-sol")
    assert calls == []


def test_cap_changes_request_fingerprint_without_changing_other_body_fields(
    monkeypatch,
):
    calls = _http(monkeypatch, _response())
    _, first = reader.provider([], model="gpt-6.1-sol", max_output_tokens=32000)
    _, second = reader.provider([], model="gpt-6.1-sol", max_output_tokens=60000)
    assert {**calls[0], "max_output_tokens": 60000} == calls[1]
    assert (
        first["request_settings"]["request_sha256"]
        != second["request_settings"]["request_sha256"]
    )


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress"])
def test_noncompleted_response_even_with_valid_schema_is_never_accepted(
    status, monkeypatch
):
    payload = _response(status=status)
    _http(monkeypatch, payload)
    with pytest.raises(reader.TextProviderError, match="no claims accepted") as caught:
        reader.provider([], model="gpt-6.1-sol")
    assert isinstance(caught.value, ValueError)
    assert caught.value.provider_metadata["status"] == status
    assert caught.value.provider_metadata["usage"] == payload["usage"]
    assert caught.value.provider_metadata["model"] == "gpt-6.1-sol"
    assert caught.value.provider_metadata["id"] == payload["id"]
    assert caught.value.diagnostic()["claims_accepted"] is False


def test_http_error_keeps_bounded_provider_metadata(monkeypatch):
    payload = {
        "id": "resp_synthetic_error",
        "error": {"code": "synthetic_rate_limit", "message": "x" * 6000},
    }
    _http(monkeypatch, payload, status=429)
    with pytest.raises(reader.TextProviderError) as caught:
        reader.provider([], model="gpt-6.1-sol")
    assert caught.value.provider_metadata["http_status"] == 429
    assert caught.value.provider_metadata["error"]["code"] == "synthetic_rate_limit"
    assert len(caught.value.provider_metadata["error"]["message"]) == 4000


@pytest.mark.parametrize(
    "text", ['{"unfinished":', '{"items":[{"span_id":"partial"}],"unhandled":[]}']
)
def test_invalid_json_or_partial_schema_is_typed_and_never_projected(text, monkeypatch):
    _http(monkeypatch, _response(text=text))
    with pytest.raises(
        reader.TextProviderError, match="invalid JSON or schema"
    ) as caught:
        reader.provider([], model="gpt-6.1-sol")
    assert caught.value.output_excerpt == text


def test_timeout_diagnostic_excludes_transport_secret(monkeypatch):
    def fail(**_kwargs):
        raise httpx.ReadTimeout("synthetic transport error")

    monkeypatch.setattr(reader.httpx, "Client", fail)
    with pytest.raises(reader.TextProviderError, match="transport failed") as caught:
        reader.provider([], model="gpt-6.1-sol")
    assert caught.value.provider_metadata["error_type"] == "ReadTimeout"
    assert "synthetic-text-test-key" not in json.dumps(caught.value.diagnostic())


def test_incomplete_response_archive_is_private_source_bound_and_not_a_success_cache(
    tmp_path, monkeypatch
):
    payload = _response(status="incomplete", text="x" * 70000)
    calls = _http(monkeypatch, payload)
    output = tmp_path / "semantic-failures"
    for _attempt in range(2):
        result, records = reader.read_customer_text(
            _requirements(),
            _documents(),
            output_dir=output,
            enabled=True,
            model="gpt-6.1-sol",
        )
        assert (
            records == [] and result["items"] == [] and result["evidence_claims"] == []
        )
        assert result["reader_coverage"][-1]["state"] == "partial"
    assert len(calls) == 2
    warning = result["warnings"][-1]
    assert (
        warning["verification_required"] and warning["provider_status"] == "incomplete"
    )
    path = output / warning["provider_diagnostic_file"]
    diagnostic = json.loads(path.read_text())
    assert diagnostic["claims_accepted"] is False and "reading" not in diagnostic
    assert (
        diagnostic["provider_metadata"]["incomplete_details"]["reason"]
        == "max_output_tokens"
    )
    assert (
        len(diagnostic["output_excerpt"]) == 64000
        and diagnostic["output_excerpt_truncated"]
    )
    assert len(diagnostic["fingerprint"]) == 64
    assert diagnostic["source_spans"][0]["source_id"] == "synthetic-specification"
    assert diagnostic["source_spans"][0]["sha256"] == "a" * 64
    assert diagnostic["source_spans"][0]["span_id"] == "synthetic-span"
    assert diagnostic["source_spans"][0]["location"]["page"] == 1
    assert len(diagnostic["source_spans"][0]["text_sha256"]) == 64
    assert (
        stat.S_IMODE(path.stat().st_mode) == 0o600
        and stat.S_IMODE(output.stat().st_mode) == 0o700
    )
    assert all(p.name.endswith("-failed-response.json") for p in output.iterdir())


def test_completed_semantic_cache_remains_usable_after_cap_change(
    tmp_path, monkeypatch
):
    calls = _http(monkeypatch, _response())
    first, first_records = reader.read_customer_text(
        _requirements(),
        _documents(),
        output_dir=tmp_path / "cache",
        enabled=True,
        model="gpt-6.1-sol",
    )
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_OUTPUT_TOKENS", "60000")
    second, second_records = reader.read_customer_text(
        _requirements(),
        _documents(),
        output_dir=tmp_path / "cache",
        enabled=True,
        model="gpt-6.1-sol",
    )
    assert len(calls) == 1
    assert first["items"] == second["items"]
    assert not first_records[0]["cache_hit"] and second_records[0]["cache_hit"]
    assert second_records[0]["usage"]["request_settings"]["max_output_tokens"] == 32000
