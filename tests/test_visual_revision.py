"""Synthetic invariants only; these tests are never real-document accuracy."""

import hashlib

import pymupdf
import pytest
from PIL import Image, ImageDraw

from backend.equipment.pdf_model import _BBoxIndex
from backend.equipment.schemas import SourceInput
from backend.equipment.visual_revision import (
    align_images,
    changed_regions,
    compare_revision_sources,
)


def source(tmp_path, name):
    p = tmp_path / name
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 40), "Scanned identity is deliberately unknown")
    doc.save(p)
    return SourceInput(
        id=name,
        name=name,
        path=str(p),
        role="drawing",
        revision="unlabelled",
        sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
    )


def identity(drawing="DRAW-1"):
    return {
        "drawing_id": drawing,
        "sheet_id": "1OF2",
        "title": "ONE LINE",
        "revision": None,
        "issue_date": None,
        "confidence": "high",
        "bbox": [0.5, 0.8, 0.9, 0.95],
    }


def test_visibility_index_retains_large_covers_and_intersecting_local_glyphs():
    covers = [
        {"bbox": [0, 0, 2000, 2000], "seqno": 20},
        {"bbox": [12, 12, 14, 14], "seqno": 3},
        {"bbox": [100, 100, 110, 110], "seqno": 5},
    ]
    index = _BBoxIndex(covers)
    assert index.intersecting([10, 10, 15, 15]) == covers[:2]
    assert index.intersecting([1990, 1990, 2000, 2000]) == covers[:1]


def test_scan_alignment_handles_opposite_rotation_and_preserves_frame_transform():
    image = Image.new("RGB", (400, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 25, 375, 575), outline="black", width=3)
    draw.rectangle((40, 60, 80, 200), fill="black")
    before, after, transform = align_images(image.rotate(180), image)
    assert transform["baseline_rotation_ccw"] == 180
    assert before.size == after.size
    regions, _ = changed_regions(before, after)
    assert regions == []


def test_pixel_regions_are_candidates_not_engineering_changes():
    image = Image.new("RGB", (500, 400), "white")
    after = image.copy()
    ImageDraw.Draw(after).line((100, 100, 300, 100), fill="black", width=4)
    regions, _ = changed_regions(image, after)
    assert regions
    assert all(not r["engineering_change_established"] for r in regions)


def test_unmatched_drawing_identity_does_not_become_structural_change(tmp_path):
    old, new = source(tmp_path, "old.pdf"), source(tmp_path, "new.pdf")

    def provider(*args, **kwargs):
        return {
            "baseline_identity": identity("A1"),
            "target_identity": identity("B9"),
            "changes": [
                {
                    "kind": "connection_added",
                    "description": "Visible added line",
                    "source_label": "Test connection",
                    "device_tag": None,
                    "before": None,
                    "after": "line",
                    "confidence": "high",
                    "bbox": [0.1, 0.1, 0.3, 0.3],
                }
            ],
            "uncertain_regions": [],
            "limitations": [],
        }, {}

    report = compare_revision_sources(old, new, tmp_path / "out", provider=provider)
    assert report["revision_checks"] == []
    assert report["pairs"][0]["sheet_identity_matched"] is False
    assert report["page_order_used_for_matching"] is False


def test_wrong_hash_rejected_before_provider(tmp_path):
    old, new = source(tmp_path, "old.pdf"), source(tmp_path, "new.pdf")
    old = old.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(ValueError, match="hash mismatch"):
        compare_revision_sources(
            old,
            new,
            tmp_path / "out",
            provider=lambda *args: pytest.fail("provider called"),
        )


def test_flat_titleblock_readings_are_aggregated_and_conflicts_not_guessed(tmp_path):
    from backend.equipment.visual_revision import _merge_visual_identities

    src = source(tmp_path, "source.pdf")
    pages = [
        {
            "page": 1,
            "sheet_identity": {
                "status": "unknown",
                "drawing_id": None,
                "sheet_id": None,
            },
        }
    ]
    records = [
        {
            "source_id": src.id,
            "page": 1,
            "field": "drawing_number",
            "text": "DW-42",
            "confidence": "high",
            "bbox": [0.8, 0.8, 0.9, 0.85],
        },
        {
            "source_id": src.id,
            "page": 1,
            "field": "sheet_number",
            "text": "3",
            "confidence": "medium",
            "bbox": [0.8, 0.9, 0.9, 0.95],
        },
        {
            "source_id": src.id,
            "page": 1,
            "field": "revision",
            "text": "B",
            "confidence": "low",
            "bbox": [0.8, 0.9, 0.9, 0.95],
        },
    ]
    obs = {"source_hashes": {src.id: src.sha256}, "sheet_identities": records}
    _merge_visual_identities(src, pages, obs)
    assert pages[0]["sheet_identity"]["drawing_id"] == "DW-42"
    assert pages[0]["sheet_identity"]["sheet_id"] == "3"
    assert pages[0]["sheet_identity"].get("revision") is None
    assert pages[0]["sheet_identity"]["unknown_identity_fields"]
    obs["sheet_identities"].append({**records[0], "text": "DW-43"})
    _merge_visual_identities(src, pages, obs)
    assert pages[0]["sheet_identity"]["status"] == "ambiguous"
    assert pages[0]["sheet_identity"]["visual_identity_conflicts"]["drawing_id"] == [
        "DW-42",
        "DW-43",
    ]
