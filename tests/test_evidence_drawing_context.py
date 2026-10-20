"""Source-evidence viewing controls, excluded from real accuracy metrics."""

import hashlib
from copy import deepcopy

import pymupdf
import pytest

from backend.equipment.evidence.drawing_context import add_context_crops


@pytest.fixture(autouse=True)
def database():
    yield


def _fixture(tmp_path):
    path = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=512, height=256)
        page.draw_line((100, 50), (100, 200))
        page.insert_text((120, 60), "SOURCE CONNECTION")
        document.save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    sources = {sid: {"path": str(path), "sha256": sha} for sid in ("old", "new")}

    def model(sid):
        return {
            "source_hashes": {sid: sha},
            "nodes": [
                {
                    "id": "section",
                    "source_refs": [
                        {
                            "source_id": sid,
                            "sha256": sha,
                            "page": 1,
                            "bbox": [0.1, 0.1, 0.9, 0.9],
                        }
                    ],
                }
            ],
            "claims": [],
        }

    report = {
        "candidate_changes": [
            {
                "id": "candidate",
                "graph_subject_id": "section",
                "baseline_source_id": "old",
                "baseline_page": 1,
                "source_id": "new",
                "page": 1,
                "baseline_bbox": [0.49, 0.49, 0.5, 0.5],
                "target_bbox": [0.49, 0.49, 0.5, 0.5],
                "supporting_claim_ids": [],
            }
        ]
    }
    return path, sources, model("old"), model("new"), report


def test_tiny_symbol_gets_full_cited_section_context_without_changing_scored_box(
    tmp_path,
):
    _path, sources, before, after, report = _fixture(tmp_path)
    original = deepcopy(report)
    result = add_context_crops(
        before,
        after,
        report,
        sources=sources,
        output_dir=tmp_path / "crops",
        page_reports=[
            {"source_id": "new", "pages": [{"page": 1, "rotation_clockwise": 90}]}
        ],
    )
    assert report == original
    item = result["candidate_changes"][0]
    assert item["baseline_bbox"] == original["candidate_changes"][0]["baseline_bbox"]
    assert item["target_bbox"] == original["candidate_changes"][0]["target_bbox"]
    view = item["source_context_crops"]["after"]
    assert view["status"] == "readable_context"
    assert min(view["pixel_size"]) >= 320
    assert max(view["pixel_size"]) <= 2201
    assert view["pixel_size"][1] > view["pixel_size"][0]
    assert view["view_rotation_clockwise"] == 90
    assert view["view_context_bbox"][0] < 0.1 and view["view_context_bbox"][2] > 0.9
    assert view["cited_context_refs"][0]["bbox"] == [0.1, 0.1, 0.9, 0.9]


def test_context_never_reads_changed_source_bytes(tmp_path):
    path, sources, before, after, report = _fixture(tmp_path)
    path.write_bytes(b"different bytes")
    with pytest.raises(ValueError, match="hash mismatch"):
        add_context_crops(
            before, after, report, sources=sources, output_dir=tmp_path / "crops"
        )


def test_missing_context_stays_bounded_and_explicit(tmp_path):
    _path, sources, before, after, report = _fixture(tmp_path)
    before["nodes"] = []
    after["nodes"] = []
    result = add_context_crops(
        before, after, report, sources=sources, output_dir=tmp_path / "crops"
    )
    view = result["candidate_changes"][0]["source_context_crops"]["after"]
    assert view["cited_context_refs"] == []
    assert 0 <= view["view_context_bbox"][0] < view["view_context_bbox"][2] <= 1
    assert view["original_scored_bbox"] == [0.49, 0.49, 0.5, 0.5]
