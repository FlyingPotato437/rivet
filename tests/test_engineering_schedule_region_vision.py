"""Source-only localization controls: generated PDFs and mocked HTTP only."""

import base64
import hashlib
import io
import json
import stat
from copy import deepcopy
from pathlib import Path

import httpx
import numpy as np
import pymupdf
import pytest
from PIL import Image

from backend.engineering import schedule_region_vision as regions

_HTTP_CLIENT = httpx.Client
MODEL = "gpt-6.1-sol"
PRIMARY_BOX = [0.1, 0.1, 0.7, 0.65]
CONTEXT_BOX = [0.72, 0.1, 0.98, 0.4]


@pytest.fixture(autouse=True)
def database():
    """Override the database fixture: no order or engineering design is used."""
    yield


@pytest.fixture(autouse=True)
def no_remote_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-localization-test-no-network")

    def forbidden(*args, **kwargs):
        pytest.fail("Real provider HTTP must never run in localization controls")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(httpx, "post", forbidden)
    monkeypatch.setattr(httpx, "request", forbidden)


def _pdf(path, *, width=720, height=540, pages=1, label="SYNTHETIC SOURCE"):
    with pymupdf.open() as document:
        for number in range(pages):
            page = document.new_page(width=width, height=height)
            page.insert_text((width * 0.12, height * 0.15), label, fontsize=12)
            page.insert_text(
                (width * 0.12, height * 0.22),
                f"BREAKER SCHEDULE {number + 1}",
                fontsize=12,
            )
            page.draw_rect(
                pymupdf.Rect(
                    width * PRIMARY_BOX[0],
                    height * PRIMARY_BOX[1],
                    width * PRIMARY_BOX[2],
                    height * PRIMARY_BOX[3],
                ),
                color=(0, 0, 0),
                width=1,
            )
            # Four source-ink corners detect clipping, whitening, or table-only input.
            for x in (width * 0.01, width * 0.98):
                for y in (height * 0.01, height * 0.98):
                    page.draw_rect(
                        pymupdf.Rect(x, y, x + width * 0.005, y + height * 0.005),
                        color=(1, 0, 0),
                        fill=(1, 0, 0),
                    )
        document.save(path)
    return {
        "id": "synthetic-vision-source",
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _region(*, key="schedule-1", kind="schedule_table", bbox=None, **changes):
    return {
        "key": key,
        "kind": kind,
        "title": "SYNTHETIC BREAKER SCHEDULE",
        "bbox": list(PRIMARY_BOX if bbox is None else bbox),
        "related_schedule_keys": [] if kind == "schedule_table" else ["schedule-1"],
        "complete_extents": True,
        "confidence": "high",
        **changes,
    }


def _reading(*items):
    return {"regions": list(items or [_region()]), "warnings": []}


def _provider(reading=None):
    calls = []

    def provide(images, *, model):
        calls.append({"images": images, "model": model})
        return deepcopy(_reading() if reading is None else reading), {
            "input_tokens": 7,
            "output_tokens": 5,
        }

    return provide, calls


def _locate(source, output, provider, **kwargs):
    return regions.locate_schedule_regions_vision(
        source, 1, output_dir=output, provider=provider, model=MODEL, **kwargs
    )


def _files(directory):
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _response(*, status="completed", reading=None, text=None):
    return {
        "id": "resp_synthetic_localization",
        "model": MODEL,
        "status": status,
        "incomplete_details": {"reason": "max_output_tokens"}
        if status == "incomplete"
        else None,
        "usage": {"input_tokens": 7, "output_tokens": 5},
        "output": [
            {
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(_reading() if reading is None else reading)
                        if text is None
                        else text,
                    }
                ]
            }
        ],
    }


def _http(monkeypatch, payload, *, status=200):
    calls, settings = [], []

    def respond(request):
        calls.append(
            {
                "url": str(request.url),
                "method": request.method,
                "body": json.loads(request.content),
            }
        )
        return httpx.Response(status, json=payload)

    def client(**kwargs):
        settings.append(kwargs)
        return _HTTP_CLIENT(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(regions.httpx, "Client", client)
    return calls, settings


def _png():
    output = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(output, format="PNG")
    return output.getvalue()


def test_source_hash_is_checked_before_pdf_or_provider(tmp_path):
    path = tmp_path / "not-a-pdf.pdf"
    path.write_bytes(b"synthetic hash guard")
    provider, calls = _provider()
    source = {"id": "synthetic", "path": str(path), "sha256": "f" * 64}
    with pytest.raises(ValueError, match="(?i)hash"):
        _locate(source, tmp_path / "output", provider)
    assert not calls


@pytest.mark.parametrize("page", [True, False, 0, -1, 1.5, "1", 3])
def test_invalid_physical_page_is_checked_before_provider(tmp_path, page):
    source = _pdf(tmp_path / "source.pdf", pages=2)
    provider, calls = _provider()
    with pytest.raises(ValueError, match="(?i)page"):
        regions.locate_schedule_regions_vision(
            source, page, output_dir=tmp_path / "output", provider=provider, model=MODEL
        )
    assert not calls


def test_whole_overview_preserves_all_source_ink_and_bounded_size(tmp_path):
    source = _pdf(tmp_path / "source.pdf", width=1440, height=1080)
    provider, calls = _provider({"regions": [], "warnings": ["No confident schedule"]})
    result = _locate(source, tmp_path / "output", provider)
    assert result["regions"] == []
    assert len(calls) == 1 and calls[0]["model"] == MODEL
    assert len(calls[0]["images"]) == 1
    bbox, png = calls[0]["images"][0]
    assert bbox == [0, 0, 1, 1]
    image = Image.open(io.BytesIO(png)).convert("RGB")
    assert max(image.size) <= 2500
    assert result["overview"]["pixel_size"] == list(image.size)
    assert result["overview"]["sha256"] == hashlib.sha256(png).hexdigest()
    assert result["overview"]["orientation"] == "original_page"
    assert Path(result["overview"]["path"]).read_bytes() == png
    assert result["provenance"]["sha256"] == source["sha256"]
    assert result["provenance"]["page"] == 1
    pixels = np.asarray(image)
    red = (pixels[:, :, 0] > 180) & (pixels[:, :, 1] < 100) & (pixels[:, :, 2] < 100)
    rows, columns = np.where(red)
    assert len(rows) > 0
    assert columns.min() < image.width * 0.03 and columns.max() > image.width * 0.97
    assert rows.min() < image.height * 0.03 and rows.max() > image.height * 0.97


def test_primary_and_linked_context_keep_original_page_bounds_and_exact_crop(tmp_path):
    source = _pdf(tmp_path / "source.pdf")
    reading = _reading(
        _region(), _region(key="notes-1", kind="context_notes", bbox=CONTEXT_BOX)
    )
    provider, calls = _provider(reading)
    result = _locate(source, tmp_path / "output", provider)
    assert not result["cache_hit"] and len(calls) == 1
    assert len(result["regions"]) == 2
    for region, expected in zip(result["regions"], reading["regions"], strict=True):
        assert region["kind"] == expected["kind"]
        assert region["bbox"] == expected["bbox"]
        assert region["sha256"] == source["sha256"] and region["page"] == 1
        assert region["related_schedule_keys"] == expected["related_schedule_keys"]
        crop = region["crop"]
        assert 250 <= crop["render_dpi"] <= 360
        assert crop["rendered_directly_from_source"] is True
        image = Image.open(io.BytesIO(region["png"]))
        assert list(image.size) == crop["pixel_size"]
        assert max(image.size) <= 3000
        assert Path(crop["path"]).read_bytes() == region["png"]
        assert crop["sha256"] == hashlib.sha256(region["png"]).hexdigest()
        # Direct original-page rendering must match; resized overview cannot.
        with pymupdf.open(source["path"]) as document:
            page = document[0]
            box = region["bbox"]
            clip = pymupdf.Rect(
                box[0] * page.rect.width,
                box[1] * page.rect.height,
                box[2] * page.rect.width,
                box[3] * page.rect.height,
            )
            expected_png = page.get_pixmap(
                matrix=pymupdf.Matrix(crop["render_dpi"] / 72, crop["render_dpi"] / 72),
                clip=clip,
                alpha=False,
            ).tobytes("png")
        assert region["png"] == expected_png


@pytest.mark.parametrize(
    "confidence,complete",
    [("low", True), ("unknown", True), ("high", False), ("medium", False)],
)
def test_weak_or_incomplete_primary_and_its_context_are_omitted(
    tmp_path, confidence, complete
):
    provider, _ = _provider(
        _reading(
            _region(confidence=confidence, complete_extents=complete),
            _region(key="notes-1", kind="context_notes", bbox=CONTEXT_BOX),
        )
    )
    result = _locate(_pdf(tmp_path / "source.pdf"), tmp_path / "output", provider)
    assert not result["regions"]
    assert result["diagnostics"]


def test_medium_complete_region_is_retained_as_localization_evidence(tmp_path):
    provider, _ = _provider(_reading(_region(confidence="medium")))
    result = _locate(_pdf(tmp_path / "source.pdf"), tmp_path / "output", provider)
    assert len(result["regions"]) == 1
    assert result["regions"][0]["evidence"]["confidence"] == "medium"


@pytest.mark.parametrize("bbox", [[0, 0, 1, 1], [0.1, 0.1, 0.9, 0.9]])
def test_crop_declines_when_original_source_cannot_meet_250_dpi(tmp_path, bbox):
    source = _pdf(tmp_path / "source.pdf", width=2592, height=1728)
    provider, _ = _provider(_reading(_region(bbox=bbox)))
    result = _locate(source, tmp_path / "output", provider)
    assert not result["regions"]
    assert result["diagnostics"]


def test_crop_adapts_dpi_without_exceeding_pixel_limit(tmp_path):
    source = _pdf(tmp_path / "source.pdf", width=792, height=612)
    provider, _ = _provider(_reading(_region(bbox=[0, 0, 1, 1])))
    result = _locate(source, tmp_path / "output", provider)
    crop = result["regions"][0]["crop"]
    assert 250 <= crop["render_dpi"] < 360
    assert max(crop["pixel_size"]) <= 3000


def test_cache_hit_reuses_exact_private_source_bound_bytes_without_provider(tmp_path):
    source = _pdf(tmp_path / "source.pdf")
    output = tmp_path / "output"
    provider, calls = _provider()
    first = _locate(source, output, provider)
    frozen = _files(output)
    second = _locate(source, output, provider)
    assert not first["cache_hit"] and second["cache_hit"]
    assert len(calls) == 1
    assert first["regions"] == second["regions"]
    assert _files(output) == frozen
    assert not (stat.S_IMODE(output.stat().st_mode) & 0o077)
    for path in output.rglob("*"):
        assert not (stat.S_IMODE(path.stat().st_mode) & 0o077)
    records = [
        json.loads(data) for name, data in frozen.items() if name.endswith(".json")
    ]
    assert any(
        record.get("provenance", {}).get("sha256") == source["sha256"]
        for record in records
    )


@pytest.mark.parametrize("change", ["provenance", "reading"])
def test_tampered_cache_is_rejected_without_rewrite_or_provider(tmp_path, change):
    source = _pdf(tmp_path / "source.pdf")
    output = tmp_path / "output"
    provider, calls = _provider()
    result = _locate(source, output, provider)
    cache = Path(result["cache_path"])
    record = json.loads(cache.read_text())
    if change == "provenance":
        record["provenance"]["page"] = 9
    else:
        record["reading"]["regions"][0]["bbox"] = [0, 0, 1, 1]
    cache.write_text(json.dumps(record))
    frozen = _files(output)
    with pytest.raises(ValueError, match="(?i)cache.*(provenance|hash)"):
        _locate(source, output, provider)
    assert len(calls) == 1
    assert _files(output) == frozen


def test_original_changed_during_provider_is_not_cached_or_cropped(tmp_path):
    source = _pdf(tmp_path / "source.pdf")
    output = tmp_path / "output"

    def provider(images, *, model):
        Path(source["path"]).write_bytes(b"changed synthetic original")
        return _reading(), {}

    with pytest.raises(ValueError, match="(?i)original changed"):
        _locate(source, output, provider)
    assert not list(output.glob("*-raw.json"))
    assert not list(output.glob("page-*.png"))


def test_typed_provider_failure_saves_private_source_bound_diagnostic(tmp_path):
    source = _pdf(tmp_path / "source.pdf")
    output = tmp_path / "output"
    error = regions.DrawingProviderError(
        "Synthetic provider failure; no regions accepted",
        provider_metadata={"status": "incomplete", "usage": {"output_tokens": 2}},
        request_settings={
            "request_sha256": "a" * 64,
            "timeout_seconds": 300,
            "max_output_tokens": 2500,
        },
    )

    def provider(images, *, model):
        raise error

    with pytest.raises(regions.DrawingProviderError) as caught:
        _locate(source, output, provider)
    assert caught.value is error
    diagnostic = Path(error.diagnostic_path)
    record = json.loads(diagnostic.read_text())
    assert record["provenance"]["sha256"] == source["sha256"]
    assert record["provenance"]["page"] == 1
    assert not (stat.S_IMODE(diagnostic.stat().st_mode) & 0o077)
    assert not list(output.glob("*-raw.json"))
    assert not list(output.glob("page-*.png"))


@pytest.mark.parametrize(
    "change", ["source_hash", "source_id", "page", "model", "prompt", "overview"]
)
def test_cache_changes_when_any_source_or_request_provenance_changes(
    tmp_path, monkeypatch, change
):
    source = _pdf(tmp_path / "source.pdf", pages=2)
    output = tmp_path / "output"
    provider, calls = _provider()
    _locate(source, output, provider)
    frozen = _files(output)
    kwargs = {"model": MODEL}
    page = 1
    if change == "source_hash":
        source = _pdf(
            tmp_path / "other.pdf", pages=2, label="DIFFERENT SYNTHETIC SOURCE"
        )
    elif change == "source_id":
        source = {**source, "id": "different-synthetic-source"}
    elif change == "page":
        page = 2
    elif change == "model":
        kwargs["model"] = "gpt-6-sol"
    elif change == "prompt":
        monkeypatch.setattr(
            regions, "PROMPT", regions.PROMPT + "\nSynthetic provenance variation."
        )
    elif change == "overview":
        kwargs["overview_max_dimension"] = 1800
    result = regions.locate_schedule_regions_vision(
        source, page, output_dir=output, provider=provider, **kwargs
    )
    assert not result["cache_hit"] and len(calls) == 2
    assert all(_files(output)[name] == data for name, data in frozen.items())


@pytest.mark.parametrize(
    "bbox",
    [
        None,
        [None, None, None, None],
        [0, 0, 0, 1],
        [0.5, 0, 0.2, 1],
        [-0.1, 0, 1, 1],
        [0, 0, 1.1, 1],
        [0, 0, True, 1],
        [0, 0, "unknown", 1],
        [0, 0, 1],
        [0, 0, float("nan"), 1],
    ],
)
def test_unknown_or_invalid_provider_coordinates_fail_closed_with_saved_diagnostics(
    tmp_path, bbox
):
    item = _region()
    item["bbox"] = bbox
    provider, _ = _provider(_reading(item))
    output = tmp_path / "output"
    with pytest.raises(regions.DrawingProviderError) as caught:
        _locate(_pdf(tmp_path / "source.pdf"), output, provider)
    diagnostic = Path(caught.value.diagnostic_path)
    assert diagnostic.is_relative_to(output)
    assert diagnostic.exists()
    assert not list(output.glob("*-raw.json"))


@pytest.mark.parametrize(
    "bad_reading",
    [
        {
            "regions": [_region(key="notes-1", kind="context_notes", bbox=CONTEXT_BOX)],
            "warnings": [],
        },
        _reading(
            _region(),
            _region(
                key="notes-1",
                kind="context_notes",
                bbox=CONTEXT_BOX,
                related_schedule_keys=["missing-schedule"],
            ),
        ),
        _reading(_region(related_schedule_keys=["schedule-1"])),
        _reading(_region(), _region()),
        _reading(*[_region(key=f"schedule-{index}") for index in range(9)]),
        _reading(_region(complete_extents="true")),
        _reading(_region(trip_a=225)),
    ],
)
def test_invalid_region_bindings_or_budget_fail_closed(tmp_path, bad_reading):
    provider, _ = _provider(bad_reading)
    with pytest.raises(regions.DrawingProviderError):
        _locate(_pdf(tmp_path / "source.pdf"), tmp_path / "output", provider)


def test_transport_uses_official_endpoint_strict_schema_store_false_and_bounded_request(
    monkeypatch,
):
    calls, settings = _http(monkeypatch, _response())
    reading, usage = regions.transcribe_schedule_regions(
        [([0, 0, 1, 1], _png())], model=MODEL
    )
    assert reading == _reading()
    assert len(calls) == 1 and calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://api.openai.com/v1/responses"
    body = calls[0]["body"]
    assert body["store"] is False
    assert body["model"] == MODEL and body["max_output_tokens"] == 2500
    assert settings[0]["timeout"] == 300
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["instructions"] == regions.PROMPT
    content = body["input"][0]["content"]
    image = next(item for item in content if item["type"] == "input_image")
    assert base64.b64decode(image["image_url"].split(",", 1)[1]) == _png()
    assert usage["request_settings"]["timeout_seconds"] == 300
    assert usage["request_settings"]["max_output_tokens"] == 2500


@pytest.mark.parametrize(
    "images",
    [
        [],
        [([0, 0, 1, 1], _png()), ([0, 0, 1, 1], _png())],
        [([0.1, 0.1, 0.9, 0.9], _png())],
        [([0, 0, 1, 1], "not bytes")],
        [([0, 0, 1, 1], b"")],
    ],
)
def test_transport_rejects_missing_tiled_or_partial_page_inputs_before_http(images):
    with pytest.raises(ValueError, match="(?i)one complete original-page image"):
        regions.transcribe_schedule_regions(images, model=MODEL)


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "in_progress"])
def test_transport_never_accepts_noncompleted_output_even_with_valid_regions(
    monkeypatch, status
):
    calls, _ = _http(monkeypatch, _response(status=status))
    with pytest.raises(regions.DrawingProviderError):
        regions.transcribe_schedule_regions([([0, 0, 1, 1], _png())], model=MODEL)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "payload,http_status",
    [
        (_response(text='{"regions":['), 200),
        (
            _response(
                reading={"regions": [_region()], "warnings": [], "unexpected": "reject"}
            ),
            200,
        ),
        (_response(reading={"regions": [{"key": "partial"}], "warnings": []}), 200),
        (_response(), 500),
        ({"status": "completed", "output": []}, 200),
        ({"status": "completed", "output": None}, 200),
        ({"status": "completed", "output": "invalid output array"}, 200),
        ({"status": "completed", "output": {}}, 200),
        ({"status": "completed", "output": [{"content": None}]}, 200),
        ({"status": "completed", "output": [{"content": "invalid content"}]}, 200),
        (["invalid top-level response"], 200),
    ],
)
def test_transport_rejects_malformed_partial_extra_or_http_error_output(
    monkeypatch, payload, http_status
):
    _http(monkeypatch, payload, status=http_status)
    with pytest.raises(regions.DrawingProviderError):
        regions.transcribe_schedule_regions([([0, 0, 1, 1], _png())], model=MODEL)
