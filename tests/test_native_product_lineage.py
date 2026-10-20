"""Synthetic product-label lineage controls; never engineering intent or ratings."""

import csv
import hashlib
from copy import deepcopy

import pytest

from backend.equipment.native_verification import _close_inherited_manufacturer
from backend.equipment.readers import normalize_value, read_source
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    yield


def _original(tmp_path, values=None):
    values = values or [
        ["KA-1", "SYNTHETIC-PART", "SYNTHETIC MAKER", "ORIGINAL 1"],
        ["KA-2", "SYNTHETIC-PART", "SYNTHETIC MAKER", "ORIGINAL 2"],
    ]
    path = tmp_path / "original-bom.csv"
    with path.open("w", newline="") as stream:
        csv.writer(stream).writerows(
            [["equipment_tag", "part_number", "manufacturer", "nameplate"], *values]
        )
    source = SourceInput(
        id="original-bom", name=path.name, path=str(path), role="bom",
        revision="SYNTHETIC ORIGINAL", sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    report, facts = read_source(source)
    assert report["status"] == "parsed"
    return {"sources": [report], "facts": facts}


def _inputs():
    maker, unit, error = normalize_value("manufacturer", "SYNTHETIC MAKER")
    assert not error
    maker_fact = {
        "id": "target-manufacturer", "device_tag": "KA-3", "role": "bom",
        "field": "manufacturer", "value": maker, "unit": unit,
        "raw_value": "SYNTHETIC MAKER", "source_id": "new-bom", "sha256": "2" * 64,
    }
    part_fact = {
        **maker_fact, "id": "target-part", "field": "part_number",
        "value": "SYNTHETIC-PART", "raw_value": "SYNTHETIC-PART", "unit": None,
    }
    check = {
        "device_tag": "KA-3", "field": "manufacturer", "status": "unknown",
        "reason": "No explicit maker request", "required": {"evidence": []},
        "requested": {"evidence": []}, "target_bom": {"evidence": [maker_fact]},
        "target_drawing": {"evidence": []},
    }
    comparison = {
        "checks": [check, {"device_tag": "KA-3", "field": "part_number", "status": "verified"}],
        "target": {"facts": [maker_fact, part_fact]},
    }
    requests = [{
        "kind": "add_device", "device_tag": "KA-3", "attributes": {"PART_NUMBER": "SYNTHETIC-PART"},
        "evidence": {"source_id": "synthetic-addition", "sha256": "1" * 64, "text": "Add KA-3, exact part SYNTHETIC-PART"},
    }]
    return comparison, requests


def test_exact_original_product_lineage_retains_full_inventory_and_never_creates_intent(tmp_path):
    original = _original(tmp_path)
    comparison, requests = _inputs()
    before = deepcopy((comparison, requests, original))
    result = _close_inherited_manufacturer(comparison, requests, original)
    check = result["checks"][0]
    assert check["status"] == "verified"
    lineage = check["source_product_identity_lineage"]
    assert len(lineage["original_rows"]) == 2
    assert {row["row"] for row in lineage["original_rows"]} == {2, 3}
    assert all(row["source_id"] == "original-bom" and row["sha256"] for row in lineage["original_rows"])
    assert all(row["cells"] for row in lineage["original_rows"])
    assert lineage["customer_intent_inferred"] is False
    assert lineage["drawing_manufacturer_inferred"] is False
    assert lineage["engineering_approved"] is False
    assert check["required"] == check["requested"] == {"evidence": []}
    assert not check["target_drawing"]["evidence"]
    assert (comparison, requests, original) == before


@pytest.mark.parametrize("mutation", ["different-part", "mixed-makers", "blank", "added-row", "foreign-hash", "partial"])
def test_original_lineage_rejects_missing_ambiguous_foreign_or_added_row_evidence(tmp_path, mutation):
    rows = [
        ["KA-1", "SYNTHETIC-PART", "SYNTHETIC MAKER", "ORIGINAL 1"],
        ["KA-2", "SYNTHETIC-PART", "SYNTHETIC MAKER", "ORIGINAL 2"],
    ]
    if mutation == "different-part":
        for row in rows:
            row[1] = "DIFFERENT-PART"
    elif mutation == "mixed-makers":
        rows[1][2] = "OTHER MAKER"
    elif mutation == "blank":
        rows[1][2] = ""
    elif mutation == "added-row":
        rows[1][0] = "KA-3"
    original = _original(tmp_path, rows)
    if mutation == "foreign-hash":
        original["sources"][0]["observed_bom_rows"][0]["sha256"] = "0" * 64
    elif mutation == "partial":
        original["sources"][0]["status"] = "partial"
    comparison, requests = _inputs()
    assert _close_inherited_manufacturer(comparison, requests, original)["checks"][0]["status"] == "unknown"


@pytest.mark.parametrize("mutation", ["target-maker", "target-part", "request", "required", "drawing-conflict", "part-unverified", "request-maker", "target-raw", "foreign-role"])
def test_target_or_governing_conflicts_are_not_closed(tmp_path, mutation):
    original = _original(tmp_path)
    comparison, requests = _inputs()
    check = comparison["checks"][0]
    if mutation == "target-maker":
        check["target_bom"]["evidence"][0]["value"] = "OTHER MAKER"
    elif mutation == "target-part":
        comparison["target"]["facts"][1]["raw_value"] = "DIFFERENT-PART"
    elif mutation == "request":
        check["requested"]["evidence"] = [{"value": "OTHER MAKER"}]
    elif mutation == "required":
        check["required"]["evidence"] = [{"value": "OTHER MAKER"}]
    elif mutation == "drawing-conflict":
        check["target_drawing"]["evidence"] = [{"value": "OTHER MAKER", "unit": None}]
    elif mutation == "part-unverified":
        comparison["checks"][1]["status"] = "unknown"
    elif mutation == "target-raw":
        check["target_bom"]["evidence"][0]["raw_value"] = "OTHER MAKER"
    elif mutation == "foreign-role":
        check["target_bom"]["evidence"][0]["role"] = "specification"
    else:
        requests[0]["attributes"]["MANUFACTURER"] = "OTHER MAKER"
    assert _close_inherited_manufacturer(comparison, requests, original)["checks"][0]["status"] == "unknown"


@pytest.mark.parametrize("field", ["frame_a", "trip_a", "pole_count", "product_family"])
def test_lineage_never_closes_other_or_safety_values(tmp_path, field):
    original = _original(tmp_path)
    comparison, requests = _inputs()
    comparison["checks"][0]["field"] = field
    assert _close_inherited_manufacturer(comparison, requests, original)["checks"][0]["status"] == "unknown"
