"""Raw BOM row recovery is distinct from device facts and exact PDF captions."""

from backend.equipment.real_evaluation import (
    _bom_observations,
    _score,
    merge_bom_observations,
    validate_answer_key,
)


def test_pdf_rows_match_physical_page_not_an_invented_sheet_caption():
    label = {
        "scope": "complete",
        "source_ids": ["original"],
        "items": [
            {
                "id": "row-1",
                "source_id": "original",
                "page": 1,
                "sheet": "Friendly review caption",
                "row": 1,
                "fields": {"item_number": 1, "description": "Circuit-Breaker"},
            }
        ],
    }
    actual = [
        {
            "id": "actual-1",
            "source_id": "original",
            "page": 1,
            "sheet": "PDF page 1",
            "row": 1,
            "fields": {"item_number": 1, "description": "Circuit‐Breaker"},
        }
    ]
    score = _score(label, actual, "bom_rows")
    assert (score["tp"], score["fn"], score["fp"]) == (1, 0, 0)
    assert score["field_values"]["correct"] == 2
    assert _score(label, [{**actual[0], "page": 2}], "bom_rows")["fn"] == 1


def test_raw_rows_never_become_invented_equipment_facts_or_quantities():
    report = {
        "id": "original",
        "role": "bom",
        "observed_bom_rows": [
            {
                "id": "source-row",
                "source_id": "original",
                "status": "observed_row",
                "page": 1,
                "row": 1,
                "fields": {"item_number": 1},
                "quantity": None,
                "device_tag": None,
            }
        ],
    }
    rows = _bom_observations([report], [])
    assert len(rows) == 1 and rows[0]["engineering_verified"] is False
    assert rows[0]["device_tag"] is None and rows[0]["quantity"] is None
    assert _bom_observations([{**report, "role": "drawing"}], []) == []


def test_pdf_reference_uses_physical_page_without_fabricated_worksheet_name():
    key = {
        "schema_version": "rivet.real-answer-key.v1",
        "package_id": "p",
        "review": {
            "method": "assistant_visual_review",
            "reviewer": "Reviewer",
            "reviewed_at": "2026-10-18T20:00:00Z",
            "engineering_reviewed": False,
        },
        "source_hashes": {"s": "a" * 64},
        "labels": {
            "bom_rows": {
                "scope": "complete",
                "source_ids": ["s"],
                "items": [
                    {
                        "id": "r",
                        "source_id": "s",
                        "page": 1,
                        "row": 1,
                        "fields": {"trip_a": 100},
                    }
                ],
            }
        },
    }
    validate_answer_key(key, {"package_id": "p", "source_hashes": key["source_hashes"]})


def test_two_readers_corroborate_one_physical_row_but_disagreement_stays_unknown():
    native = {
        "id": "native",
        "source_id": "s",
        "page": 1,
        "row": 1,
        "table": 1,
        "bbox": [0.1, 0.2, 0.9, 0.25],
        "fields": {"trip_a": 100},
    }
    image = {**native, "id": "image", "fields": {"trip_a": 200, "frame_a": 250}}
    rows, merges = merge_bom_observations([native], [image])
    assert len(rows) == len(merges) == 1
    assert rows[0]["fields"] == {"trip_a": None, "frame_a": 250}
    assert rows[0]["field_conflicts"]["trip_a"] == [100, 200]
    assert (
        rows[0]["requires_human_verification"] and not rows[0]["engineering_verified"]
    )
    assert native["fields"] == {"trip_a": 100}  # raw reader output stays intact
    other_region = {**image, "bbox": [0.1, 0.5, 0.9, 0.55]}
    rows, merges = merge_bom_observations([native], [other_region])
    assert len(rows) == 2 and not merges
    rows, merges = merge_bom_observations([{**native, "bbox": None}], [image])
    assert len(rows) == 2 and not merges  # row numbering alone isn't evidence
