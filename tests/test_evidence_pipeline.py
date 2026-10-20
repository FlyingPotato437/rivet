"""First-day orchestration controls; no control counts as real accuracy."""

import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path

import pymupdf
import pytest

from backend.equipment.evidence import pipeline
from backend.equipment.evidence.schema import make_claim


def _source(tmp_path, sid="drawing", side="target"):
    path = tmp_path / (sid + ".pdf")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 50), "CB-12 trip 600A")
    document.save(path)
    document.close()
    return {
        "id": sid,
        "name": path.name,
        "path": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "role": "drawing",
        "revision": side,
        "side": side,
    }


def _package(tmp_path, sources):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    return {
        "id": "control-package",
        "kind": "real_public",
        "sources": sources,
    }, manifest


def _empty_comparison():
    return {
        side: {"facts": [], "sources": [], "requested_changes": []}
        for side in ("baseline", "target", "requirements")
    }


def test_all_source_hashes_are_checked_before_any_evidence_reader(
    tmp_path, monkeypatch
):
    first, second = _source(tmp_path, "first"), _source(tmp_path, "second")
    second["sha256"] = "a" * 64
    package, manifest = _package(tmp_path, [first, second])
    monkeypatch.setattr(
        pipeline,
        "_call_with_budget",
        lambda *a, **k: pytest.fail("Hash mismatch must stop all readers"),
    )
    with pytest.raises(ValueError, match="hash changed"):
        pipeline.run_evidence_pipeline(
            package,
            manifest,
            comparison=_empty_comparison(),
            source_observations={},
            predictions={},
            output_dir=tmp_path / "out",
            deadline=time.monotonic() + 5,
        )
    assert not (tmp_path / "out").exists()


def test_expired_deadline_keeps_observations_but_claims_no_acceptance(
    tmp_path, monkeypatch
):
    source = _source(tmp_path)
    package, manifest = _package(tmp_path, [source])
    monkeypatch.setattr(
        pipeline,
        "_call_with_budget",
        lambda *a, **k: pytest.fail("Expired work must not start"),
    )
    result = pipeline.run_evidence_pipeline(
        package,
        manifest,
        comparison=_empty_comparison(),
        source_observations={},
        predictions={},
        output_dir=tmp_path / "out",
        deadline=time.monotonic() - 1,
    )
    summary = result["summary"]
    assert summary["status"] == "partial_requires_human_verification"
    assert summary["accepted_literal_or_calibrated_decisions"] == 0
    assert summary["equipment_extraction_recall"] is None
    assert {e["stage"] for e in result["errors"]} == {
        "source_table_reading",
        "reconciliation",
        "analysis_by_synthesis",
    }
    assert Path(result["archive_receipt"]["run_path"]).is_file()
    assert (
        json.loads(Path(result["artifacts"]["canonical_graph"]).read_text())[
            "resolutions"
        ]
        == []
    )


def test_derivation_maps_same_original_page_to_one_source_ancestry(tmp_path):
    source = _source(tmp_path)
    source["derivation"] = {"parent_sha256": "c" * 64, "parent_physical_pages": [246]}
    package, manifest = _package(tmp_path, [source])
    sources, context, pages = pipeline.prepare_evidence_inputs(package, manifest)
    assert context[source["id"]]["page_map"] == {"1": 246}
    first = {"source_id": source["id"], "sha256": source["sha256"], "page": 1}
    other = {"source_id": "copy", "sha256": "b" * 64, "page": 1}
    assert pipeline._ancestry(first, context[source["id"]]) == pipeline._ancestry(
        other, context[source["id"]]
    )
    assert pages == {source["id"]: [1]} and sources[source["id"]]["path"]


def test_table_and_vision_rows_do_not_join_by_position_and_raw_claims_remain(tmp_path):
    source = _source(tmp_path)
    package, manifest = _package(tmp_path, [source])
    sources, contexts, _ = pipeline.prepare_evidence_inputs(package, manifest)
    ref = {
        "source_id": source["id"],
        "sha256": source["sha256"],
        "page": 1,
        "bbox": [0.1, 0.1, 0.2, 0.2],
        "cell": 1,
    }
    claim = make_claim(
        "table-row",
        "trip_a",
        600,
        source=ref,
        extractor={"name": "source_ruled_table_tesseract"},
        raw_value="600A",
        raw_confidence=96,
        unit="A",
    )
    table = {
        "version": "source-table-control",
        "source_id": source["id"],
        "sha256": source["sha256"],
        "pages": [{"page": 1, "status": "candidate_tables"}],
        "rows": [
            {
                "subject_id": "table-row",
                "source_local_id": "row-1",
                "page": 1,
                "bbox": [0.1, 0.1, 0.9, 0.2],
                "cells": [
                    {"grid_column": 1, "raw_text": "600A", "bbox": [0.1, 0.1, 0.2, 0.2]}
                ],
                "fields": {"trip_a": 600},
            }
        ],
        "claims": [claim],
    }
    vision = {
        "bom_rows": [
            {
                "id": "vision-row",
                "source_id": source["id"],
                "sha256": source["sha256"],
                "page": 1,
                "row": 1,
                "bbox": [0.1, 0.1, 0.9, 0.2],
                "cells": [
                    {
                        "column": 1,
                        "field": "trip_a",
                        "value": 60,
                        "raw_text": "60",
                        "confidence": "high",
                        "bbox": [0.1, 0.1, 0.2, 0.2],
                    }
                ],
            }
        ]
    }
    original = deepcopy(table)
    graph = pipeline.build_evidence_graph(
        sources=sources,
        source_context=contexts,
        scope_id="order-one",
        predictions=vision,
        table_reports=[table],
        reconcile=False,
    )
    typed = [c for c in graph["claims"] if c["feature"] == "trip_a"]
    assert {c["value"] for c in typed} == {60, 600}
    assert len({c["subject_id"] for c in typed}) == 2
    assert len({c["independence_group"] for c in typed}) == 1
    assert all(
        c["conditions"]["side"] == "target" and c["conditions"]["role"] == "drawing"
        for c in typed
    )
    assert table == original
    assert next(c for c in typed if c["value"] == 600)["id"] != claim["id"]
    assert any(c["raw_value"] == "600A" for c in graph["claims"])


def test_table_hash_mismatch_cannot_enter_graph(tmp_path):
    source = _source(tmp_path)
    package, manifest = _package(tmp_path, [source])
    sources, contexts, _ = pipeline.prepare_evidence_inputs(package, manifest)
    with pytest.raises(ValueError, match="pinned original"):
        pipeline.build_evidence_graph(
            sources=sources,
            source_context=contexts,
            scope_id="order",
            table_reports=[{"source_id": source["id"], "sha256": "b" * 64}],
        )


def test_one_shared_deadline_and_explicit_revision_pairs_only(tmp_path, monkeypatch):
    sources = [_source(tmp_path, "old", "baseline"), _source(tmp_path, "new", "target")]
    package, manifest = _package(tmp_path, sources)
    package["revision_pairs"] = [
        {"baseline_source_id": "old", "target_source_id": "new"}
    ]
    deadline = time.monotonic() + 20
    calls = []

    def bounded(function, *args, deadline, **kwargs):
        calls.append((function.__name__, deadline, kwargs))
        assert (
            "region_key" not in kwargs
            and "region_references" not in kwargs
            and "answer_key" not in kwargs
        )
        if function is pipeline.read_table_evidence:
            return {
                "status": "completed",
                "result": {
                    "source_id": args[0]["id"],
                    "sha256": args[0]["sha256"],
                    "pages": [{"page": p, "status": "unknown"} for p in args[1]],
                    "rows": [],
                    "claims": [],
                },
            }
        if function is pipeline.reconcile_graph:
            return {"status": "completed", "result": function(*args, **kwargs)}
        if function is pipeline.synthesize_and_compare:
            return {
                "status": "completed",
                "result": {
                    "source_id": args[2],
                    "page": args[3],
                    "unexplained_region_count": 2,
                },
            }
        return {"status": "completed", "result": {"changes": [], "unknowns": []}}

    monkeypatch.setattr(pipeline, "_call_with_budget", bounded)
    result = pipeline.run_evidence_pipeline(
        package,
        manifest,
        comparison=_empty_comparison(),
        source_observations={},
        predictions={},
        output_dir=tmp_path / "out",
        deadline=deadline,
        correspondence_policy=None,
    )
    assert all(call[1] == deadline for call in calls)
    assert result["summary"]["synthesis_pages_processed"] == 2
    assert result["summary"]["unexplained_regions"] == 4
    assert result["summary"]["graph_revision_pairs_attempted"] == 1
    assert len([c for c in calls if c[0] == "compare_reconciled_models"]) == 1
    assert result["summary"]["requires_human_verification"]
