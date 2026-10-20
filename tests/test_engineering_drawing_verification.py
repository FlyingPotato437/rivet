"""Synthetic source-only corroboration controls; no real pages or model calls."""

import copy
import hashlib
import json

import pymupdf
import pytest

from backend.engineering import drawing_verification as verification
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    """Verification tests use temporary PDF bytes and provider stubs only."""
    yield


@pytest.fixture(autouse=True)
def inline_provider_budget(monkeypatch):
    """Keep stub observations local; timeout behavior has its own envelope control."""

    def run(function, *args, deadline, **kwargs):
        return {"status": "completed", "result": function(*args, **kwargs)}

    monkeypatch.setattr(verification, "_call_with_budget", run)


def _synthetic_source(tmp_path, *, pages=1, devices_per_page=1, circuit=True):
    path = tmp_path / "synthetic-source-only-control.pdf"
    source_id = "synthetic-source-only-control"
    report_pages = []
    with pymupdf.open() as document:
        for page_number in range(1, pages + 1):
            page = document.new_page(width=600, height=800)
            objects = []
            for index in range(devices_per_page):
                top = 0.12 + index * 0.32
                anchor = str(7 + index) if circuit else None
                frame_box = [0.30, top + 0.06, 0.47, top + 0.09]
                trip_box = [0.30, top + 0.11, 0.47, top + 0.14]
                page.insert_text((180, (top + 0.08) * 800), "654AF", fontsize=12)
                page.insert_text((180, (top + 0.13) * 800), "321AT", fontsize=12)
                if anchor:
                    page.insert_text((120, top * 800), anchor, fontsize=12)
                page.draw_rect(pymupdf.Rect(120, top * 800, 312, (top + 0.20) * 800))
                objects.append(
                    {
                        "key": f"synthetic-object-{page_number}-{index}",
                        "kind": "device_symbol",
                        "raw_tag": None,
                        "circuit_label": anchor,
                        "section_label": None,
                        "raw_label": "SYNTHETIC SOURCE CONTROL",
                        "position_state": "populated",
                        "properties": [
                            {
                                "feature": "frame_a",
                                "raw_text": "654AF",
                                "value": 654,
                                "unit": "A",
                                "confidence": "high",
                                "bbox": frame_box,
                            },
                            {
                                "feature": "trip_a",
                                "raw_text": "321AT",
                                "value": 321,
                                "unit": "A",
                                "confidence": "high",
                                "bbox": trip_box,
                            },
                        ],
                        "confidence": "high",
                        "bbox": [0.20, top - 0.02, 0.52, top + 0.20],
                    }
                )
            report_pages.append(
                {
                    "page": page_number,
                    "rotation_clockwise": 0,
                    "reading": {"objects": objects},
                }
            )
        document.save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    source = SourceInput(
        id=source_id,
        name=path.name,
        path=str(path),
        sha256=digest,
        role="drawing",
        revision="SYNTHETIC-1",
    )
    report = {"source_id": source_id, "sha256": digest, "pages": report_pages}
    return source, report


def _local_box(source_box, image):
    """Map a known synthetic original-page rectangle into the actual input crop."""
    left, top, right, bottom = image["source_bbox"]
    x0, y0, x1, y1 = source_box
    box = [
        (x0 - left) / (right - left),
        (y0 - top) / (bottom - top),
        (x1 - left) / (right - left),
        (y1 - top) / (bottom - top),
    ]
    x0, y0, x1, y1 = box
    rotation = image["rotation_clockwise"]
    if rotation == 90:
        return [1 - y1, x0, 1 - y0, x1]
    if rotation == 180:
        return [1 - x1, 1 - y1, 1 - x0, 1 - y0]
    if rotation == 270:
        return [y0, 1 - x1, y1, 1 - x0]
    assert rotation == 0
    return box


class SyntheticProvider:
    def __init__(self, change=None):
        self.calls = []
        self.change = change

    def __call__(self, batch, *, model, timeout_seconds):
        self.calls.append(
            {"batch": copy.deepcopy(batch), "model": model, "timeout": timeout_seconds}
        )
        devices = []
        for device in batch["devices"]:
            properties = []
            for prop in device["properties"]:
                image = next(
                    image
                    for image in device["images"]
                    if image["kind"] == "property"
                    and image["property_id"] == prop["property_id"]
                )
                value = {"frame_a": 654, "trip_a": 321}[prop["feature"]]
                properties.append(
                    {
                        "property_id": prop["property_id"],
                        "feature": prop["feature"],
                        "raw_text": f"{value}{'AF' if prop['feature'] == 'frame_a' else 'AT'}",
                        "value": value,
                        "unit": "A",
                        "confidence": "high",
                        "image_id": image["image_id"],
                        "bbox": _local_box(prop["source_bbox"], image),
                        "reason": "Literal synthetic source control, with clear symbol association.",
                    }
                )
            response_device = {
                "device_id": device["device_id"],
                "circuit_literal": device["circuit_anchor"],
                "association": "clear",
                "properties": properties,
            }
            if self.change:
                self.change(response_device, batch)
            devices.append(response_device)
        return {"devices": devices}, {"synthetic_provider_calls": 1}


def _properties(report):
    return [
        prop
        for page in report["pages"]
        for obj in page["reading"]["objects"]
        for prop in obj["properties"]
    ]


def _verify(tmp_path, source, report, provider, **kwargs):
    return verification.verify_drawing_values(
        source,
        report,
        output_dir=tmp_path / "verification",
        provider=provider,
        **kwargs,
    )


def test_two_agreeing_views_retain_original_and_share_one_truth_ancestry(tmp_path):
    source, report = _synthetic_source(tmp_path)
    original = copy.deepcopy(report)
    provider = SyntheticProvider()

    result = _verify(tmp_path, source, report, provider)

    assert {call["batch"]["view_id"] for call in provider.calls} == {
        "upright",
        "original",
    }
    assert [prop["value"] for prop in _properties(result["projected_report"])] == [
        654,
        321,
    ]
    cells = result["verification_report"]["cells"]
    assert len(cells) == 2
    assert all(
        cell["status"] == "corroborated_original" and cell["retained_original_value"]
        for cell in cells
    )
    ancestry = result["verification_report"]["ancestry"]
    assert ancestry["source_id"] == source.id and ancestry["sha256"] == source.sha256
    assert ancestry["independent_truths"] == 1
    assert ancestry["model_calls_are_independent_truths"] is False
    assert {cell["independence_group"] for cell in cells} == {
        ancestry["independence_group"]
    }
    assert report == original


def test_provider_metadata_excludes_initial_values_and_source_raw_ratings(tmp_path):
    source, report = _synthetic_source(tmp_path)
    provider = SyntheticProvider()

    _verify(tmp_path, source, report, provider)

    def metadata(value):
        if isinstance(value, bytes):
            return "<source-image-bytes>"
        if isinstance(value, dict):
            return {key: metadata(item) for key, item in value.items()}
        if isinstance(value, list):
            return [metadata(item) for item in value]
        return value

    for call in provider.calls:
        visible = json.dumps(metadata(call["batch"]))
        assert '"321"' not in visible and '"654"' not in visible
        assert '"321AT"' not in visible and '"654AF"' not in visible

        def numeric_leaves(value):
            if isinstance(value, dict):
                return [
                    leaf for item in value.values() for leaf in numeric_leaves(item)
                ]
            if isinstance(value, list):
                return [leaf for item in value for leaf in numeric_leaves(item)]
            return [value] if isinstance(value, (int, float)) else []

        assert not {321, 654}.intersection(numeric_leaves(call["batch"]))
        for device in call["batch"]["devices"]:
            assert device["circuit_anchor"] == "7"
            assert (
                device["device_id"]
                != report["pages"][0]["reading"]["objects"][0]["key"]
            )
            assert all(
                "value" not in prop and "raw_text" not in prop
                for prop in device["properties"]
            )


@pytest.mark.parametrize("changed", ["source", "report"])
def test_source_hash_or_report_hash_mismatch_fails_before_provider(tmp_path, changed):
    source, report = _synthetic_source(tmp_path)
    provider = SyntheticProvider()
    if changed == "source":
        source = source.model_copy(update={"sha256": "f" * 64})
    else:
        report["sha256"] = "f" * 64

    with pytest.raises(ValueError, match="(?i)(hash|sha|source)"):
        _verify(tmp_path, source, report, provider)

    assert provider.calls == []


def test_complete_cache_reuses_source_bound_views_without_more_provider_calls(tmp_path):
    source, report = _synthetic_source(tmp_path)
    provider = SyntheticProvider()
    first = _verify(tmp_path, source, report, provider)
    calls = len(provider.calls)

    second = _verify(tmp_path, source, report, provider)

    assert calls > 0 and len(provider.calls) == calls
    assert second["projected_report"]["pages"] == first["projected_report"]["pages"]
    assert (
        second["verification_report"]["ancestry"]
        == first["verification_report"]["ancestry"]
    )


@pytest.mark.parametrize(
    "case", ["low", "unknown", "ambiguous", "wrong-anchor", "bad-box", "mixed-raw"]
)
def test_unreliable_or_misassociated_reread_removes_only_affected_value(tmp_path, case):
    source, report = _synthetic_source(tmp_path)

    def change(device, batch):
        if batch["view_id"] != "original":
            return
        trip = next(
            prop for prop in device["properties"] if prop["feature"] == "trip_a"
        )
        if case in {"low", "unknown"}:
            trip["confidence"] = case
            if case == "unknown":
                trip.update(value=None, raw_text="", unit=None)
        elif case == "ambiguous":
            device["association"] = "ambiguous"
        elif case == "wrong-anchor":
            device["circuit_literal"] = "8"
        elif case == "bad-box":
            trip["bbox"] = [0, 0, 0.01, 0.01]
        else:
            trip["raw_text"] = "654AF 321AT"

    result = _verify(tmp_path, source, report, SyntheticProvider(change))

    props = {prop["feature"]: prop for prop in _properties(result["projected_report"])}
    assert (
        props["trip_a"]["value"] is None and props["trip_a"]["confidence"] == "unknown"
    )
    assert props["trip_a"]["raw_text"] == "321AT"
    if case in {"ambiguous", "wrong-anchor"}:
        assert props["frame_a"]["value"] is None
    else:
        assert props["frame_a"]["value"] == 654
    assert not result["verification_report"]["proposed_corrections"]


def test_agreed_alternative_is_a_candidate_correction_and_never_an_overwrite(tmp_path):
    source, report = _synthetic_source(tmp_path)
    original = copy.deepcopy(report)

    def change(device, _batch):
        trip = next(
            prop for prop in device["properties"] if prop["feature"] == "trip_a"
        )
        trip.update(raw_text="999AT", value=999)

    result = _verify(tmp_path, source, report, SyntheticProvider(change))

    props = {prop["feature"]: prop for prop in _properties(result["projected_report"])}
    assert props["trip_a"]["value"] is None and props["trip_a"]["raw_text"] == "321AT"
    assert props["frame_a"]["value"] == 654
    corrections = result["verification_report"]["proposed_corrections"]
    assert len(corrections) == 1 and corrections[0]["feature"] == "trip_a"
    assert corrections[0]["proposed_value"] == 999
    assert corrections[0]["engineering_approved"] is False
    assert (
        next(
            cell
            for cell in result["verification_report"]["cells"]
            if cell["feature"] == "trip_a"
        )["status"]
        == "discrepancy"
    )
    assert report == original


def test_disagreeing_views_clear_value_without_proposing_a_correction(tmp_path):
    source, report = _synthetic_source(tmp_path)

    def change(device, batch):
        if batch["view_id"] == "original":
            trip = next(
                prop for prop in device["properties"] if prop["feature"] == "trip_a"
            )
            trip.update(raw_text="999AT", value=999)

    result = _verify(tmp_path, source, report, SyntheticProvider(change))

    props = {prop["feature"]: prop for prop in _properties(result["projected_report"])}
    assert props["trip_a"]["value"] is None
    assert props["trip_a"]["confidence"] == "unknown"
    assert props["trip_a"]["raw_text"] == "321AT"
    assert props["frame_a"]["value"] == 654
    assert not result["verification_report"]["proposed_corrections"]


def test_device_and_page_limits_explicitly_defer_unverified_values(tmp_path):
    source, report = _synthetic_source(tmp_path, pages=2, devices_per_page=2)
    provider = SyntheticProvider()

    result = _verify(tmp_path, source, report, provider, max_devices=1, max_pages=1)

    cells = result["verification_report"]["cells"]
    assert sum(cell["status"] == "corroborated_original" for cell in cells) == 2
    assert sum(cell["status"] == "deferred" for cell in cells) == 6
    assert {item["reason"] for item in result["verification_report"]["deferred"]} == {
        "DeviceBudget",
        "PageBudget",
    }
    assert (
        sum(
            prop["value"] is not None
            for prop in _properties(result["projected_report"])
        )
        == 2
    )
    assert all(len(call["batch"]["devices"]) == 1 for call in provider.calls)


def test_expired_call_budget_defers_values_without_claiming_corroboration(
    tmp_path, monkeypatch
):
    source, report = _synthetic_source(tmp_path)
    provider = SyntheticProvider()
    monkeypatch.setattr(
        verification,
        "_call_with_budget",
        lambda *_args, **_kwargs: {
            "status": "timed_out",
            "error_type": "SyntheticTimeout",
        },
    )

    result = _verify(tmp_path, source, report, provider)

    assert provider.calls == []
    assert all(
        prop["value"] is None and prop["confidence"] == "unknown"
        for prop in _properties(result["projected_report"])
    )
    assert all(
        cell["status"] == "deferred" for cell in result["verification_report"]["cells"]
    )
    assert result["verification_report"]["deferred"]
    assert not result["verification_report"]["proposed_corrections"]


def test_unlabeled_main_can_use_clear_geometry_without_inventing_circuit_identity(
    tmp_path,
):
    source, report = _synthetic_source(tmp_path, circuit=False)

    result = _verify(tmp_path, source, report, SyntheticProvider())

    assert [prop["value"] for prop in _properties(result["projected_report"])] == [
        654,
        321,
    ]
    assert (
        result["projected_report"]["pages"][0]["reading"]["objects"][0]["circuit_label"]
        is None
    )


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_source_crop_boxes_survive_all_four_extra_orientations(tmp_path, rotation):
    source, report = _synthetic_source(tmp_path)
    report["pages"][0]["rotation_clockwise"] = rotation
    result = _verify(tmp_path, source, report, SyntheticProvider())
    assert all(
        c["status"] == "corroborated_original"
        for c in result["verification_report"]["cells"]
    )
    for cell in result["verification_report"]["cells"]:
        for read in cell["reads"]:
            assert read["source_bbox"] == pytest.approx(cell["original"]["bbox"])
            assert read["source_ref"]["sha256"] == source.sha256
            assert read["source_ref"]["page"] == 1


def test_oversized_crop_is_deferred_before_any_large_pdf_render(tmp_path, monkeypatch):
    source, report = _synthetic_source(tmp_path)
    report["pages"][0]["reading"]["objects"][0]["bbox"] = [0, 0, 1, 1]
    monkeypatch.setattr(
        verification,
        "_render_detail",
        lambda *_a, **_kw: pytest.fail("Oversized crop reached renderer"),
    )
    provider = SyntheticProvider()
    result = _verify(tmp_path, source, report, provider)
    assert not provider.calls
    assert result["verification_report"]["deferred"][0]["reason"] == "CropEdgeBudget"
    assert result["verification_report"]["render_pixels"] == 0


def test_pixel_budget_is_checked_before_pdf_render(tmp_path, monkeypatch):
    source, report = _synthetic_source(tmp_path)
    monkeypatch.setattr(verification, "MAX_RENDER_PIXELS", 1)
    monkeypatch.setattr(
        verification,
        "_render_detail",
        lambda *_a, **_kw: pytest.fail("Over-budget crop reached renderer"),
    )
    result = _verify(tmp_path, source, report, SyntheticProvider())
    assert result["verification_report"]["deferred"][0]["reason"] == "RenderPixelBudget"
    assert result["verification_report"]["render_pixels"] == 0


def test_cache_is_blind_to_initial_value_and_cannot_promote_agreed_alternative(
    tmp_path,
):
    source, report = _synthetic_source(tmp_path)
    provider = SyntheticProvider()
    _verify(tmp_path, source, report, provider)
    calls = len(provider.calls)
    changed = copy.deepcopy(report)
    changed["pages"][0]["reading"]["objects"][0]["properties"][0].update(
        value=111, raw_text="111AF"
    )
    result = _verify(tmp_path, source, changed, provider)
    assert len(provider.calls) == calls
    frame = _properties(result["projected_report"])[0]
    assert frame["value"] is None and frame["raw_text"] == "111AF"
    correction = result["verification_report"]["proposed_corrections"][0]
    assert correction["proposed_value"] == 654 and correction["original_value"] == 111
    assert len(correction["source_refs"]) == 2


def test_tiny_shared_deadline_defers_without_any_provider_call(tmp_path):
    source, report = _synthetic_source(tmp_path)
    provider = SyntheticProvider()
    result = _verify(tmp_path, source, report, provider, max_seconds=0.01)
    assert not provider.calls
    assert all(
        c["status"] == "deferred" for c in result["verification_report"]["cells"]
    )
