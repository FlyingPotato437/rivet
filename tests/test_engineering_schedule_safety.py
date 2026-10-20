"""Synthetic schedule safety regressions; no originals, keys or providers read.

Failing controls deliberately expose missing fail-closed boundaries while the
implementation is frozen. Invalid evidence may be rejected or left unknown.
"""

import json
from copy import deepcopy
from hashlib import sha256

import pymupdf
import pytest
from test_engineering_schedule_reader import regions, sample

from backend.engineering.readers import read_customer_drawings
from backend.engineering.requirements import extract_requirements
from backend.engineering.schedule_reader import project_schedule


@pytest.fixture(autouse=True)
def database():
    """Only pure projection and temporary source-only transport seams are used."""
    yield


def _value_or_rejected(reading, row, feature):
    try:
        projected = project_schedule(reading, regions())
    except ValueError:
        return None
    return next(
        p["value"]
        for p in projected["objects"][row]["properties"]
        if p["feature"] == feature
    )


@pytest.mark.parametrize("literal", ["", "250"])
def test_direct_numeric_value_requires_matching_printed_literal(literal):
    reading = sample()
    # This is an ordinary direct cell: no observed shared-value association.
    cell = reading["tables"][0]["rows"][2]["cells"][0]
    cell.update(raw_text=literal, value="400", shared_id=None)
    assert _value_or_rejected(reading, 2, "frame_a") is None


def test_shared_numeric_value_requires_a_printed_literal():
    reading = sample()
    reading["tables"][0]["shared_cells"][0]["raw_text"] = ""
    assert _value_or_rejected(reading, 1, "frame_a") is None


@pytest.mark.parametrize("location", ["literal_value", "arrow_scope"])
def test_shared_numeric_evidence_must_remain_in_named_column(location):
    reading = sample()
    shared = reading["tables"][0]["shared_cells"][0]
    # FRAME is x=.2–.4. These boxes lie entirely in the interrupting column.
    if location == "literal_value":
        shared["value_bbox"] = [0.61, 0.3, 0.79, 0.4]
    else:
        shared["bbox"] = [0.61, 0.3, 0.79, 0.56]
    assert _value_or_rejected(reading, 1, "frame_a") is None


def test_matching_direct_literals_preserve_current_and_header_unit_conversion():
    projected = project_schedule(sample(), regions())
    properties = {p["feature"]: p for p in projected["objects"][0]["properties"]}
    assert properties["frame_a"]["value"] == 400
    assert properties["rated_current_a"]["value"] == 100
    assert properties["interrupting_ka"]["value"] == 42
    assert properties["interrupting_ka"]["unit"] == "kA"
    assert "trip_a" not in properties


def test_matching_shared_column_keeps_literal_and_complete_scope_provenance():
    projected = project_schedule(sample(), regions())
    prop = projected["objects"][1]["properties"][0]
    assert prop["feature"] == "frame_a" and prop["value"] == 400
    provenance = prop["schedule_provenance"]
    assert provenance["column_key"] == "frame"
    assert provenance["shared_value"]["row_keys"] == ["r0", "r1"]
    assert provenance["shared_value"]["kind"] == "continuation_arrow"
    assert provenance["shared_value"]["value_bbox"][0] == pytest.approx(0.18)


def test_verify_values_withholds_new_schedule_values_and_graph_verifier_credit(
    tmp_path, monkeypatch
):
    """Exercise actual integration, with synthetic observations and no network."""
    import backend.engineering.drawing_verification as verification
    import backend.engineering.schedule_regions as locator
    from backend.equipment.evidence import drawing_reader

    path = tmp_path / "synthetic-source.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    source = {
        "id": "synthetic-source",
        "name": path.name,
        "path": str(path),
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "role": "customer_one_line",
        "pages": [1],
    }
    original_row = deepcopy(project_schedule(sample(), regions())["objects"][0])
    original_row["key"] = "original-table-depiction"
    graph = {
        "source_id": source["id"],
        "sha256": source["sha256"],
        "version": "synthetic-graph-v1",
        "prompt_sha256": "b" * 64,
        "pages": [
            {
                "page": 1,
                "reading": {
                    "objects": [original_row],
                    "conductors": [],
                    "ambiguities": [],
                    "unknown_regions": [],
                },
            }
        ],
    }
    checked = deepcopy(graph)
    checked["pages"][0]["reading"]["objects"][0]["properties"][0].update(
        value=None, confidence="unknown"
    )
    graph_verification_hash = "d" * 64
    checked["value_verification"] = {
        "version": "synthetic-blind-graph-verifier",
        "verification_report_sha256": graph_verification_hash,
        "engineering_approved": False,
    }
    monkeypatch.setattr(
        drawing_reader, "read_drawing_evidence", lambda *a, **kw: deepcopy(graph)
    )
    monkeypatch.setattr(
        verification,
        "verify_drawing_values",
        lambda *a, **kw: {
            "projected_report": deepcopy(checked),
            "verification_report": {
                "cells": [{"retained_original_value": False}],
                "deferred": [],
                "proposed_corrections": [],
            },
        },
    )
    monkeypatch.setattr(
        locator,
        "locate_schedule_regions",
        lambda *a, **kw: {"regions": regions(), "diagnostics": []},
    )
    schedule_calls = []

    def schedule_provider(images, *, model):
        schedule_calls.append(model)
        return sample(), {}

    def no_provider(*a, **kw):
        pytest.fail("No actual graph or verification provider is permitted")

    result, _ = read_customer_drawings(
        extract_requirements([]),
        [source],
        output_dir=tmp_path / "readings",
        enabled=True,
        provider=no_provider,
        verify_values=True,
        verification_provider=no_provider,
        schedule_provider=schedule_provider,
        read_notes=False,
    )
    assert len(schedule_calls) == 1
    schedule_frames = [
        item
        for item in result["items"]
        if item.get("representation_role") == "schedule_row"
        and item["key"] in {"frame_a", "trip_a"}
    ]
    assert schedule_frames
    assert all(item["value"] is None for item in schedule_frames)
    claim_ids = {cid for item in schedule_frames for cid in item["source_claim_ids"]}
    for claim in result["evidence_claims"]:
        if claim["id"] in claim_ids:
            assert graph_verification_hash not in json.dumps(claim["conditions"])
