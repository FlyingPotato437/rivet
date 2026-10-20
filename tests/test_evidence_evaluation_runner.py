"""Synthetic runner plumbing controls; never real extraction accuracy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from backend.equipment.evidence.schema import make_claim
from scripts import evaluate_reconciled_equipment as runner


@pytest.fixture(autouse=True)
def database():
    """These file-only controls never rebuild a database."""
    yield


def _write(path, value):
    path.write_text(json.dumps(value))
    return path


def _case(tmp_path):
    pdf = tmp_path / "synthetic-control.pdf"
    doc = pymupdf.open(); page = doc.new_page(); page.insert_text((30, 40), "Synthetic correspondence plumbing control")
    doc.save(pdf); doc.close()
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    source = {"id": "schedule", "path": pdf.name, "name": pdf.name, "sha256": digest,
              "role": "bom", "revision": "control", "side": "target"}
    package = {"id": "synthetic-plumbing", "kind": "synthetic", "sources": [source]}
    manifest = _write(tmp_path / "manifest.json", {"packages": [package]})
    prediction = {"source_hashes": {"schedule": digest}, "bom_rows": [{
        "id": "vision-eight", "source_id": "schedule", "sha256": digest, "page": 1, "table": 9,
        "fields": {"circuit_no": 8, "trip_a": 60}, "cells": [
            {"header": "CKT NO", "field": "circuit_no", "raw_text": "8", "value": 8, "confidence": "high", "column": 1},
            {"header": "TRIP AMP", "field": "trip_a", "raw_text": "60A", "value": 60, "confidence": "high", "column": 2},
            {"header": "DEVICE/FRAME RATING", "field": "device_frame", "raw_text": "L", "value": "L", "confidence": "high", "column": 3},
        ]
    }]}
    _write(tmp_path / "predictions.json", prediction)
    ref = {"source_id": "schedule", "sha256": digest, "page": 1, "table": 2, "row": 4, "bbox": [.2, .3, .3, .4]}
    claim = make_claim("raw-source-row", "trip_a", 600, source=ref, raw_value="600A", unit="A",
                       raw_confidence=95, extractor={"name": "source_ruled_table_tesseract"})
    table = {"source_id": "schedule", "sha256": digest, "status": "candidate_tables", "claims": [claim],
             "pages": [{"page": 1, "orientation_established": True, "orientation_clockwise": 0, "tables": [{
                 "table": 2, "bbox": [.1, .1, .6, .7], "headers": [
                     {"raw_text": "CKT NO", "field": "circuit_no", "ocr_confidence": 95, "bbox": [.1, .1, .2, .2]},
                     {"raw_text": "TRIP AMP", "field": "trip_a", "ocr_confidence": 95, "bbox": [.2, .1, .3, .2]},
                     {"raw_text": "DEVICE/FRAME RATING", "field": "device_frame", "ocr_confidence": 95, "bbox": [.3, .1, .4, .2]},
                 ]}]}],
             "rows": [{"subject_id": "raw-source-row", "source_local_id": "source-cell-row", "page": 1, "table": 2,
                       "grid_row": 4, "bbox": [.1, .3, .6, .4], "cells": [{
                           "field": "circuit_no", "header": "CKT NO", "raw_text": "8", "grid_column": 1,
                           "geometry_valid": True, "bbox": [.1, .3, .2, .4], "word_bboxes": [
                               {"text": "8", "confidence": 95, "bbox": [.13, .32, .17, .36]}]}]}]}
    _write(tmp_path / "table.json", table)
    entry = {"id": "synthetic-control", "package_id": package["id"], "prediction_paths": ["predictions.json"],
             "table_report_paths": ["table.json"]}
    index = _write(tmp_path / "index.json", {"unscored": [entry]})
    return index, manifest, entry, package, table


def _vision_trip(graph):
    node = next(n for n in graph["nodes"] if n.get("metadata", {}).get("source_local_id") != "source-cell-row")
    return next(r for r in graph["resolutions"] if r["subject_id"] == node["id"] and r["feature"] == "trip_a")


def test_default_none_preserves_separate_readers_and_explicit_report_policy(tmp_path):
    index, manifest, *_ = _case(tmp_path)
    result = runner.evaluate(index, [manifest], tmp_path / "legacy")
    assert result["schema_version"] == "rivet.reconciled-equipment-evaluation.v1"
    assert result["correspondence_policy"] == "none"
    case = result["cases"][0]
    assert case["split"] == "unscored" and case["correspondence_policy"] == "none"
    graph = json.loads(Path(case["model_path"]).read_text())
    assert not graph.get("identity_correspondence") and _vision_trip(graph)["value"] == 60
    assert case["table_report_input_hashes"]


def test_explicit_v2_uses_source_cell_proof_without_key_based_merge(tmp_path):
    index, manifest, *_ = _case(tmp_path)
    result = runner.evaluate(index, [manifest], tmp_path / "v2", correspondence_policy="source-local-v2")
    assert result["schema_version"] == "rivet.reconciled-equipment-evaluation.v2"
    assert result["correspondence_policy"] == "source-local-v2"
    case = result["cases"][0]
    assert case["source_local_correspondence"]["counts"]["proven_source_local"] == 1
    graph = json.loads(Path(case["model_path"]).read_text())
    trip = _vision_trip(graph)
    assert trip["value"] is None and trip["status"] == "conflict" and trip["needs_review"]
    assert {a["value"] for a in trip["alternatives"]} == {60, 600}
    assert case["score_path"] is None and case["table_score_path"] is None


def test_case_construction_cannot_read_its_answer_key(tmp_path, monkeypatch):
    _, manifest, entry, package, _ = _case(tmp_path)
    entry["answer_key"] = "never-read.json"
    original = runner._read
    reads = []
    def read(path):
        assert Path(path).name != "never-read.json"
        reads.append(Path(path).name)
        return original(path)
    monkeypatch.setattr(runner, "_read", read)
    case = runner._load_case(entry, "unscored", {package["id"]: (package, manifest)}, tmp_path,
                             tmp_path / "out", correspondence_policy="source-local-v2")
    assert case["graph"]["identity_correspondence"][0]["verified"]
    assert set(reads) == {"predictions.json", "table.json"}


def test_fresh_table_reader_receives_sources_and_pages_only(tmp_path, monkeypatch):
    from backend.equipment.evidence import table_reader
    _, manifest, entry, package, table = _case(tmp_path)
    entry.pop("table_report_paths")
    entry["read_table_pages"] = {"schedule": [1]}
    entry["answer_key"] = "never-read.json"
    calls = []
    def reader(source, pages, *, output_dir):
        calls.append((source, pages, output_dir))
        assert "answer_key" not in source and "labels" not in source
        return table
    monkeypatch.setattr(table_reader, "read_table_evidence", reader)
    case = runner._load_case(entry, "unscored", {package["id"]: (package, manifest)}, tmp_path,
                             tmp_path / "out", correspondence_policy="source-local-v2")
    assert len(calls) == 1 and calls[0][1] == [1]
    assert case["graph"]["metadata"]["source_local_correspondence"]["counts"]["proven_source_local"] == 1


def test_unknown_policy_fails_before_opening_any_input(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_read", lambda path: pytest.fail("Invalid policy must fail before input reads"))
    with pytest.raises(ValueError, match="Unknown evidence correspondence policy"):
        runner.evaluate(tmp_path / "missing", [], tmp_path / "out", correspondence_policy="oracle")


def test_cli_forwards_explicit_correspondence_policy(tmp_path, monkeypatch, capsys):
    import sys
    captured = {}
    def evaluate(*args, **kwargs):
        captured.update(kwargs)
        return {"status": "completed", "cases": [], "elapsed_seconds": 0, "correspondence_policy": kwargs["correspondence_policy"]}
    monkeypatch.setattr(runner, "evaluate", evaluate)
    monkeypatch.setattr(sys, "argv", ["evaluate", "--index", "index.json", "--manifest", "manifest.json", "--output", str(tmp_path / "out"),
                                     "--correspondence-policy", "source-local-v2"])
    runner.main()
    assert captured["correspondence_policy"] == "source-local-v2"
    assert json.loads(capsys.readouterr().out)["correspondence_policy"] == "source-local-v2"


def test_v2_projection_includes_source_supported_feature_missing_from_visual_schema(tmp_path):
    from backend.equipment.evidence.evaluation import canonical_predictions, score_reconciliation
    index, manifest, entry, package, _ = _case(tmp_path)
    pred_path = tmp_path / "predictions.json"
    prediction = json.loads(pred_path.read_text())
    row = prediction["bom_rows"][0]
    row["fields"].pop("trip_a")
    row["cells"][1].update(field="unknown", raw_text="", value=None)
    _write(pred_path, prediction)
    case = runner._load_case(entry, "unscored", {package["id"]: (package, manifest)}, tmp_path,
                             tmp_path / "out", correspondence_policy="source-local-v2")
    graph = runner.reconcile_graph(case["graph"])
    output = canonical_predictions(graph, case["frozen"])
    assert output["projection_version"] == "rivet.canonical-row-projection.v2"
    assert output["bom_rows"][0]["fields"]["trip_a"] == 600
    assert "trip_a" not in case["frozen"]["bom_rows"][0]["fields"]
    assert canonical_predictions(graph, case["frozen"], accepted_only=True)["bom_rows"][0]["fields"]["trip_a"] is None
    key = {"schema_version": "rivet.real-answer-key.v1", "package_id": package["id"],
           "source_hashes": case["frozen"]["source_hashes"], "labels": {},
           "review": {"reviewer": "Synthetic control author", "method": "assistant_visual_review",
                      "reviewed_at": "2026-10-18T00:00:00+00:00", "engineering_reviewed": False}}
    assert score_reconciliation(key, case["frozen"], graph)["projection_version"] == "rivet.canonical-row-projection.v2"


def test_unproven_local_link_cannot_supply_missing_feature(tmp_path):
    from backend.equipment.evidence.evaluation import canonical_predictions
    _, manifest, entry, package, table = _case(tmp_path)
    prediction = json.loads((tmp_path / "predictions.json").read_text())
    prediction["bom_rows"][0]["fields"].pop("trip_a")
    prediction["bom_rows"][0]["cells"][1].update(field="unknown", raw_text="", value=None)
    _write(tmp_path / "predictions.json", prediction)
    table["rows"][0]["cells"][0]["word_bboxes"][0]["confidence"] = 49
    _write(tmp_path / "table.json", table)
    case = runner._load_case(entry, "unscored", {package["id"]: (package, manifest)}, tmp_path,
                             tmp_path / "out", correspondence_policy="source-local-v2")
    graph = runner.reconcile_graph(case["graph"])
    output = canonical_predictions(graph, case["frozen"])
    assert output["bom_rows"][0]["fields"].get("trip_a") is None
    assert all(link["status"] == "candidate" for link in graph["identity_correspondence"])


def test_v2_projection_never_borrows_cross_source_or_requirements_claims(tmp_path):
    from backend.equipment.evidence.evaluation import canonical_predictions
    _, manifest, entry, package, _ = _case(tmp_path)
    case = runner._load_case(entry, "unscored", {package["id"]: (package, manifest)}, tmp_path,
                             tmp_path / "out", correspondence_policy="source-local-v2")
    graph = runner.reconcile_graph(case["graph"])
    vision = next(n for n in graph["nodes"] if n.get("metadata", {}).get("source_local_id") != "source-cell-row")
    for feature, sid, side, role in [("voltage_v", "foreign-source", "target", "drawing"),
                                     ("frame_a", "schedule", "requirements", "specification"),
                                     ("frequency_hz", "schedule", "baseline", "drawing")]:
        graph["claims"].append(make_claim(vision["id"], feature, 480, source={"source_id": sid, "sha256": "b" * 64, "page": 1},
                                          extractor={"name": "control"}, conditions={"side": side, "role": role}))
        graph["resolutions"].append({"subject_id": vision["id"], "feature": feature, "value": 480, "state": "observed", "side": side, "needs_review": False})
    output = canonical_predictions(graph, case["frozen"])
    assert "voltage_v" not in output["bom_rows"][0]["fields"]
    assert "frame_a" not in output["bom_rows"][0]["fields"]
    assert "frequency_hz" not in output["bom_rows"][0]["fields"]
