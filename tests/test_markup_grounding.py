"""Regression checks for native annotation IDs and visible PDF coordinates."""

from collections import Counter
from hashlib import sha256
from types import SimpleNamespace

import pymupdf
import pytest

from backend.ingestion.markup_grounding import (
    _ground_document,
    annotation_coverage,
    stored_visual_redesign_items,
)
from backend.ingestion.vision import candidate_pages
from backend.orders.extraction import extract_order_document, parse_pdf_annotations


@pytest.fixture(autouse=True)
def database():
    yield


def test_legacy_xref_joins_rotated_native_callout_by_exact_geometry(tmp_path):
    path = tmp_path / "marked.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=800, height=600)
        page.set_rotation(90)
        note = page.add_freetext_annot(
            (280, 200, 400, 240),
            "Confirm cable entry",
            callout=[(190, 90), (240, 210), (280, 210)],
        )
        pdf.xref_set_key(note.xref, "NM", "null")
        pdf.save(path)

    digest = sha256(path.read_bytes()).hexdigest()
    with pymupdf.open(path) as pdf:
        page = pdf[0]
        annotation = next(page.annots())
        row = {
            "page": 1,
            "annotation_id": str(annotation.xref),
            "bbox": list(annotation.rect),
            "text": annotation.info["content"],
        }
    parser_note = parse_pdf_annotations(path)[0]
    assert parser_note["id"] != row["annotation_id"]
    span = {
        "id": "saved-span",
        "text": parser_note["text"],
        "location": {
            "page": 1,
            "annotation_id": parser_note["id"],
            "annotation_type": "FreeText",
            "original_sha256": digest,
            "coordinate_system": "visible-page-v1",
            "bbox": parser_note["bbox"],
        },
    }
    mismatched = {**row, "bbox": [10, 20, 30, 40]}
    _ground_document(path, digest, [span], {1: [row, mismatched]})
    assert row["source_id"] == "saved-span"
    assert row["grounding_status"] == "exact"
    assert row["source_location"]["bbox"] == parser_note["bbox"]
    assert row["source_location"]["text_bbox"] != parser_note["bbox"]
    assert "source_id" not in mismatched


def test_annotation_coverage_preserves_support_and_unknown_mark_anchors():
    annotations = {"line": "Line", "highlight": "Highlight", "square": "Square"}
    spans = [
        {
            "id": name,
            "text": "",
            "document_id": "original",
            "location": {
                "page": 2,
                "annotation_id": name,
                "annotation_type": kind,
                "bbox": [0.1, 0.2, 0.3, 0.4],
            },
        }
        for name, kind in annotations.items()
    ]
    coverage = [
        {
            "label": "Page 2 annotation",
            "state": "unsupported",
            "annotation_id": name,
            "detail": "A graphical annotation has no extractable text. Manual interpretation is required.",
        }
        for name in annotations
    ]
    group, square = annotation_coverage(coverage, spans, document_id="original")
    assert group["page"] == 2
    assert group["state"] == square["state"] == "unsupported"
    assert Counter(mark["source_id"] for mark in group["marks"]) == {
        "line": 1,
        "highlight": 1,
    }
    assert square["source_id"] == "square"
    assert "Unidentified square mark" in square["detail"]


def test_vision_prioritizes_native_graphics_on_otherwise_readable_page(tmp_path):
    path = tmp_path / "packet.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        page = pdf.new_page()
        page.insert_text(
            (72, 90),
            "A long enough ordinary specification line with no images and no red strokes.",
        )
        page.add_rect_annot((70, 60, 200, 120))
        pdf.save(path)
    assert candidate_pages(path) == [2, 1]


def test_graphical_vision_context_does_not_create_an_order_request():
    result = extract_order_document(
        {"kind": "markup"},
        [
            {
                "id": "vision-region",
                "text": "Leader from an existing callout",
                "location": {
                    "annotation_type": "supporting_markup",
                    "actionable": False,
                },
            }
        ],
    )
    assert result["comments"] == []


def test_visual_read_view_keeps_new_uncertainty_and_filters_support_or_duplicates():
    bbox = [0.1, 0.2, 0.3, 0.4]
    loc = {
        "page": 1,
        "bbox": bbox,
        "coordinate_system": "visible-page-v1",
        "original_sha256": "expected",
    }

    def span(identifier, text, **location):
        return SimpleNamespace(
            id=identifier,
            document_id="source",
            text=text,
            location={**loc, "annotation_id": identifier, **location},
        )

    native = span("native", "Verify rating", annotation_type="FreeText")
    findings = [
        span("duplicate", "Verify rating", extraction_method="vision", actionable=True),
        span("leader", "", extraction_method="vision", actionable=False),
        span(
            "stale",
            "Stale reading",
            extraction_method="vision",
            original_sha256="other",
        ),
        span(
            "unknown",
            "Unreadable area",
            extraction_method="vision",
            bbox=[0.6, 0.6, 0.7, 0.7],
        ),
    ]
    session = SimpleNamespace(scalars=lambda _: [native, *findings])
    result = stored_visual_redesign_items(
        session,
        SimpleNamespace(project_id="project"),
        [
            {
                "id": "source",
                "path": "returned.pdf",
                "sha256": "expected",
                "name": "Returned drawing",
            },
        ],
    )
    assert [item["source_id"] for item in result] == ["unknown"]
    assert result[0]["grounding_status"] == "approximate"
    assert result[0]["status"] == "needs_information"
    assert result[0]["change_ids"] == []
