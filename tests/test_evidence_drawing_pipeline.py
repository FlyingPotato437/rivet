"""Runtime drawing-hook controls; synthetic fixtures do not measure accuracy."""

import hashlib
import json
import time
from pathlib import Path

import pymupdf
import pytest

from backend.equipment.evidence import pipeline
from backend.equipment.evidence.ontology import make_node
from backend.equipment.evidence.reconcile import reconcile_graph
from backend.equipment.evidence.schema import make_claim, new_graph, source_ref


@pytest.fixture(autouse=True)
def database():
    yield


def test_page_budget_covers_both_issues_instead_of_only_first_long_source():
    selected = [("old", p) for p in range(1, 151)] + [("new", p) for p in range(1, 151)]
    chosen, deferred = pipeline._select_drawing_pages(
        selected, {"old": {"role": "drawing"}, "new": {"role": "drawing"}}, 2
    )
    assert chosen == [("old", 1), ("new", 1)]
    assert len(deferred) == 298


def test_large_package_budget_has_explicit_deferred_tail_without_twenty_page_cap():
    selected = [("old", p) for p in range(1, 301)] + [("new", p) for p in range(1, 301)]
    chosen, deferred = pipeline._select_drawing_pages(
        selected, {"old": {"role": "drawing"}, "new": {"role": "drawing"}}, 500
    )
    assert len(chosen) == 500 and len(deferred) == 100
    assert sum(sid == "old" for sid, _page in chosen) == 250
    assert sum(sid == "new" for sid, _page in chosen) == 250


def test_first24_appends_drawing_evidence_and_keeps_candidates_pending(
    tmp_path, monkeypatch
):
    sources = []
    for sid, side in (("old", "baseline"), ("new", "target")):
        path = tmp_path / (sid + ".pdf")
        with pymupdf.open() as document:
            document.new_page(width=100, height=100)
            document.save(path)
        sources.append(
            {
                "id": sid,
                "name": path.name,
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "role": "drawing",
                "revision": side,
                "side": side,
            }
        )
    package = {
        "id": "drawing-hook-control",
        "kind": "real_public",
        "sources": sources,
        "revision_pairs": [{"baseline_source_id": "old", "target_source_id": "new"}],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    calls = []

    def drawing(source, pages, **kwargs):
        calls.append((source["id"], pages))
        graph = new_graph(kwargs["scope_id"])
        graph["source_hashes"] = {source["id"]: source["sha256"]}
        ref = source_ref(source, page=1, bbox=[0.1, 0.1, 0.9, 0.9])
        graph["nodes"] = [
            make_node(
                "cited-control-section",
                "section",
                kwargs["scope_id"],
                identifiers={"raw_tag": "SECTION A"},
                source_refs=[ref],
                metadata={"source_local": False},
            )
        ]
        signature = {
            "named_interfaces": []
            if source["id"] == "old"
            else [
                {
                    "label": "REMOTE ASSEMBLY",
                    "bbox": [0.2, 0.2, 0.3, 0.3],
                    "confidence": "high",
                }
            ],
            "rating_labels": [],
            "section_fully_visible": True,
            "enumeration_complete_estimate": True,
        }
        graph["claims"] = [
            make_claim(
                "cited-control-section",
                "connection",
                signature,
                source=ref,
                extractor={"name": "independent_drawing_page_image"},
                conditions={"side": kwargs["side"]},
            )
        ]
        graph = reconcile_graph(graph)
        return {
            "source_id": source["id"],
            "sha256": source["sha256"],
            "status": "completed_requires_human_verification",
            "graphs": [graph],
            "reports": [
                {"source_id": source["id"], "pages": [{"page": 1, "cache_hit": True}]}
            ],
            "errors": [],
            "deferred_pages": [],
        }

    monkeypatch.setattr(pipeline, "run_drawing_stage", drawing)
    monkeypatch.setattr(
        pipeline,
        "read_table_evidence",
        lambda source, pages, **_k: {
            "source_id": source["id"],
            "sha256": source["sha256"],
            "rows": [],
            "claims": [],
            "pages": [],
        },
    )
    monkeypatch.setattr(
        pipeline,
        "synthesize_and_compare",
        lambda *_a, **_k: {"unexplained_region_count": 0},
    )

    def local(function, *args, deadline, **kwargs):
        return {"status": "completed", "result": function(*args, **kwargs)}

    monkeypatch.setattr(pipeline, "_call_with_budget", local)
    result = pipeline.run_evidence_pipeline(
        package,
        manifest,
        comparison={
            side: {"facts": [], "sources": [], "requested_changes": []}
            for side in ("baseline", "target", "requirements")
        },
        source_observations={},
        predictions={},
        output_dir=tmp_path / "result",
        deadline=time.monotonic() + 30,
        drawing_vision=True,
    )
    assert calls == [("old", [1]), ("new", [1])]
    assert result["summary"]["drawing_reader"]["pages_processed"] == 2
    assert result["summary"]["drawing_reader"]["cached_model_pages"] == 2
    assert result["summary"]["drawing_reader"]["candidate_changes"] == 1
    assert result["summary"]["drawing_reader"]["confirmed_changes"] == 0
    assert result["summary"]["accepted_literal_or_calibrated_decisions"] == 0
    graph = json.loads(Path(result["artifacts"]["canonical_graph"]).read_text())
    assert len(graph["claims"]) == 2
    assert all(r["needs_review"] for r in graph["resolutions"])
    assert not graph["metadata"]["engineering_approved"]
