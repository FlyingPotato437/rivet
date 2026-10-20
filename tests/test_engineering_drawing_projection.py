"""Synthetic projection controls; these do not measure real drawing accuracy."""

from copy import deepcopy
from hashlib import sha256

import pymupdf
import pytest

from backend.engineering.requirements import (
    _proposed_main_roles,
    drawing_note_documents,
    extract_requirements,
    merge_drawing_readings,
    to_config_inputs,
)
from backend.engineering.text_reader import read_customer_text, text_timeout_seconds


@pytest.fixture(autouse=True)
def database():
    yield


def prop(feature="frame_a", value=2000, raw="2000AF"):
    return {
        "feature": feature,
        "value": value,
        "raw_text": raw,
        "unit": "A",
        "bbox": [0.2, 0.2, 0.3, 0.3],
        "confidence": "high",
    }


def obj(key, kind="device_symbol", label=None, properties=None):
    return {
        "key": key,
        "kind": kind,
        "raw_label": label,
        "raw_tag": None,
        "circuit_label": None,
        "section_label": None,
        "position_state": "populated",
        "properties": properties or [],
    }


def wire(a, b, supported=True):
    return {
        "from_object": a,
        "to_object": b,
        "kind": "conductor",
        "connected_visible": True,
        "pixel_evidence": {"supported": supported},
        "endpoint_evidence": {"supported": supported},
    }


def reading():
    return {
        "objects": [
            obj("source", "connection_interface", "UTILITY"),
            obj("breaker", properties=[prop(), prop("trip_a", 1600, "1600AT")]),
            obj("bus", "busbar", properties=[prop("bus_current_a", 2000, "2000A BUS")]),
        ],
        "conductors": [wire("source", "breaker"), wire("breaker", "bus")],
        "labels": [],
        "unknown_regions": [],
    }


def report(raw):
    return {
        "source_id": "drawing",
        "sha256": "a" * 64,
        "version": "fixture",
        "prompt_sha256": "b" * 64,
        "pages": [{"page": 3, "reading": raw}],
    }


def test_main_role_requires_unique_source_paths_not_object_key():
    raw = reading()
    assert _proposed_main_roles(raw) == {"breaker"}
    raw["conductors"][0]["endpoint_evidence"]["supported"] = False
    assert not _proposed_main_roles(raw)
    raw["objects"][1]["key"] = "main"
    assert not _proposed_main_roles(raw)
    raw = reading()
    raw["objects"].append(obj("other", properties=[prop()]))
    raw["conductors"] += [wire("source", "other"), wire("other", "bus")]
    assert not _proposed_main_roles(raw)


def test_unique_main_is_proposed_and_compound_voltage_not_fabricated():
    raw = reading()
    raw["objects"][-1]["properties"].append(prop("voltage_v", "208Y/120", "208Y/120V"))
    result = merge_drawing_readings(extract_requirements([]), [report(raw)])
    inputs = to_config_inputs(result)
    assert inputs["main"]["frame_a"]["value"] == 2000
    assert inputs["main"]["trip_a"]["value"] == 1600
    assert not inputs["main"]["frame_a"]["confirmed"]
    assert "voltage_v" not in inputs
    assert any(c["value"] == "208Y/120" for c in result["evidence_claims"])


def test_multiple_printed_mains_are_not_merged_or_supplied_as_feeders():
    raw = reading()
    raw["objects"][1]["raw_label"] = "MAIN A"
    raw["objects"].append(
        obj("other", label="MAIN B", properties=[prop(value=1000, raw="1000AF")])
    )
    result = merge_drawing_readings(extract_requirements([]), [report(raw)])
    assert len([i for i in result["items"] if i.get("unresolved_role")]) == 3
    assert "main" not in to_config_inputs(result)
    assert not to_config_inputs(result)["feeders"]
    assert any(w.get("verification_required") for w in result["warnings"])


def test_main_depictions_on_distinct_sheets_need_explicit_identity_review():
    first, second = report(reading()), report(reading())
    second["pages"][0]["page"] = 4
    result = merge_drawing_readings(extract_requirements([]), [first, second])
    inputs = to_config_inputs(result)
    assert "main" not in inputs and not inputs["feeders"]
    assert len([i for i in result["items"] if i.get("unresolved_role")]) == 4


def test_downstream_load_and_unknown_regions_remain_visible():
    raw = reading()
    raw["objects"].append(
        obj("panel", label="PANEL L", properties=[prop("rated_current_a", 600, "600A")])
    )
    raw["unknown_regions"] = [
        {
            "description": "Unreadable feeder",
            "reason": "Blurred digits",
            "bbox": [0.4, 0.4, 0.5, 0.5],
        }
    ]
    result = merge_drawing_readings(extract_requirements([]), [report(raw)])
    assert result["reference_observations"][0]["object"]["raw_label"] == "PANEL L"
    assert result["warnings"][-1]["region"]["bbox"] == [0.4, 0.4, 0.5, 0.5]
    assert result["warnings"][-1]["verification_required"]
    assert not to_config_inputs(result)["feeders"]


def test_identical_circuit_labels_in_different_sections_keep_distinct_positions():
    raw = reading()
    raw["objects"] = [
        obj("left", properties=[prop()]),
        obj("right", "prepared_device", properties=[prop()]),
    ]
    raw["conductors"] = []
    for device, section in zip(raw["objects"], ["LEFT", "RIGHT"]):
        device.update(circuit_label="7", section_label=section)
    raw["objects"][1]["position_state"] = "prepared"
    result = merge_drawing_readings(extract_requirements([]), [report(raw)])
    feeders = to_config_inputs(result)["feeders"]
    assert len(feeders) == 2 and len({f["id"] for f in feeders}) == 2
    assert {f["display_tag"] for f in feeders} == {"7"}
    assert {f["section_label"] for f in feeders} == {"LEFT", "RIGHT"}
    assert {f["position_state"] for f in feeders} == {"populated", "prepared"}
    assert all(
        f["source_local_identity"] and f["tag"].startswith("drawing:") for f in feeders
    )


def test_transcribed_note_retains_parent_claim_and_original_location(tmp_path):
    raw = reading()
    raw["labels"] = [
        {
            "text": "SPD 100 kA",
            "bbox": [0.1, 0.1, 0.2, 0.2],
            "confidence": "medium",
            "owner_key": None,
        }
    ]
    original = extract_requirements([])
    snapshot = deepcopy(original)
    result, docs = drawing_note_documents(original, [report(raw)])
    assert original == snapshot
    span = docs[0]["spans"][0]
    assert span["source_context"]["transcription_only"]
    result, _ = read_customer_text(
        result,
        docs,
        enabled=True,
        model="gpt-6.1-sol",
        output_dir=tmp_path,
        provider_fn=lambda *_args, **_kwargs: (
            {
                "items": [
                    {
                        "span_id": span["id"],
                        "key": "spd_rating_ka",
                        "subject_id": "order",
                        "value": 100.0,
                        "unit": "kA",
                        "quote": "100 kA",
                        "excluded": False,
                        "confidence": "medium",
                    }
                ],
                "unhandled": [],
            },
            {},
        ),
    )
    derived = result["evidence_claims"][-1]
    assert derived["derived_from"] == span["parent_claim_ids"]
    assert (
        derived["source"]["page"] == 3 and derived["conditions"]["vision_transcription"]
    )
    assert not result["items"][-1]["confirmed"]


@pytest.mark.parametrize("failed", [False, True])
def test_live_reader_uses_withheld_projection_not_unchecked_raw(
    tmp_path, monkeypatch, failed
):
    from backend.engineering import drawing_verification, readers
    from backend.equipment.evidence import drawing_reader

    path = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    doc = {
        "id": "drawing",
        "name": path.name,
        "role": "customer_one_line",
        "path": str(path),
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }
    raw = report(reading())
    raw["pages"][0]["page"] = 1
    raw["sha256"] = doc["sha256"]
    monkeypatch.setattr(
        drawing_reader, "read_drawing_evidence", lambda *_args, **_kwargs: deepcopy(raw)
    )

    def verification(*_args, **_kwargs):
        if failed:
            raise ValueError("Synthetic verifier failure")
        projected = deepcopy(raw)
        projected["pages"][0]["reading"]["objects"][1]["properties"][0].update(
            value=None, confidence="unknown"
        )
        return {
            "projected_report": projected,
            "verification_report": {
                "cells": [{"retained_original_value": False}],
                "proposed_corrections": [],
                "deferred": [],
            },
        }

    monkeypatch.setattr(drawing_verification, "verify_drawing_values", verification)
    result, _ = readers.read_customer_drawings(
        extract_requirements([]),
        [doc],
        output_dir=tmp_path / "readings",
        enabled=True,
        verify_values=True,
    )
    assert "frame_a" not in to_config_inputs(result).get("main", {})
    assert any(w.get("verification_required") for w in result["warnings"])
    assert raw["pages"][0]["reading"]["objects"][1]["properties"][0]["value"] == 2000


@pytest.mark.parametrize("value", ["NaN", "Infinity", "0", "29", "1801"])
def test_text_transport_timeout_is_bounded(monkeypatch, value):
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError):
        text_timeout_seconds()


def test_page_failure_keeps_successful_other_page_and_its_provenance(
    tmp_path, monkeypatch
):
    from backend.engineering import readers
    from backend.equipment.evidence import drawing_reader

    path = tmp_path / "two-pages.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.new_page()
        pdf.save(path)
    doc = {
        "id": "drawing",
        "name": path.name,
        "role": "customer_one_line",
        "path": str(path),
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }
    calls = []

    def read(source, pages, **kwargs):
        calls.append((pages, kwargs["output_dir"]))
        if pages == [1]:
            error = ValueError("Synthetic incomplete first page")
            error.provider_metadata = {"status": "incomplete"}
            raise error
        raw = report(reading())
        raw["sha256"] = source["sha256"]
        raw["pages"][0]["page"] = pages[0]
        return raw

    monkeypatch.setattr(drawing_reader, "read_drawing_evidence", read)
    result, reports = readers.read_customer_drawings(
        extract_requirements([]),
        [doc],
        output_dir=tmp_path / "reads",
        enabled=True,
        provider=lambda *_args, **_kwargs: pytest.fail("No model call"),
    )
    assert [pages for pages, _ in calls] == [[1], [2]]
    assert calls[0][1] != calls[1][1]
    assert len(reports) == 1 and reports[0]["pages"][0]["page"] == 2
    assert result["unreadable_pages"] == [
        {
            "document_id": "drawing",
            "pages": [1],
            "state": "failed",
            "verification_required": True,
        }
    ]
    assert result["warnings"][0]["page"] == 1
    assert result["warnings"][0]["provider_metadata"] == {"status": "incomplete"}
    claims = [
        claim for claim in result["evidence_claims"] if claim["feature"] == "main.frame_a"
    ]
    assert claims and all(claim["source"]["page"] == 2 for claim in claims)
    assert to_config_inputs(result)["main"]["frame_a"]["value"] == 2000


def test_engineering_wrapper_passes_its_configured_output_cap(tmp_path, monkeypatch):
    from backend.engineering import readers
    from backend.equipment.evidence import drawing_reader

    monkeypatch.setenv("RIVET_ENGINEERING_DRAWING_OUTPUT_TOKENS", "60000")
    monkeypatch.setenv("RIVET_ENGINEERING_DRAWING_TIMEOUT_SECONDS", "900")
    path = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    doc = {
        "id": "drawing",
        "name": path.name,
        "role": "customer_one_line",
        "path": str(path),
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }
    received = []

    def transcribe(images, **kwargs):
        received.append(kwargs)
        return {}, {}

    def read(_source, pages, *, provider, model, **_kwargs):
        provider([], model=model)
        raw = report(reading())
        raw["pages"][0]["page"] = pages[0]
        return raw

    monkeypatch.setattr(drawing_reader, "transcribe_drawing", transcribe)
    monkeypatch.setattr(drawing_reader, "read_drawing_evidence", read)
    readers.read_customer_drawings(
        extract_requirements([]),
        [doc],
        output_dir=tmp_path / "reads",
        enabled=True,
        model="gpt-6.1-sol",
    )
    assert received == [
        {"model": "gpt-6.1-sol", "timeout_seconds": 900.0, "max_output_tokens": 60000}
    ]
