"""Synthetic identity controls; locations, never expected ratings, resolve scope."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "schedule_scope_score",
    Path(__file__).parents[1] / "scripts/evaluate_switchboard_reconstruction.py",
)
score = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score)


@pytest.fixture(autouse=True)
def database():
    yield


def ref(box):
    return {"sha256": "a" * 64, "page": 1, "bbox": box}


def expected(value=400):
    return {
        "id": "e",
        "subject_id": "1",
        "source_identifier": "1",
        "key": "frame_a",
        "value": value,
        "source_refs": [
            {**ref([0.1, 0.1, 0.2, 0.2]), "source_scope_bbox": [0, 0, 0.4, 0.4]}
        ],
    }


def item(ident, box, value):
    return {
        "id": ident,
        "subject_id": ident,
        "display_tag": "1",
        "key": "frame_a",
        "value": value,
        "source_refs": [ref(box)],
    }


def test_physical_scope_resolves_repeated_label_without_expected_value_selection():
    rows = [
        item("inside", [0.1, 0.1, 0.2, 0.2], 400),
        item("outside", [0.6, 0.1, 0.7, 0.2], 900),
    ]
    for value in (400, 900, 123):
        found, ambiguous, _ = score._literal_candidates(
            expected(value), rows, identity_key="subject_id"
        )
        assert [r["id"] for r in found] == ["inside"]
        assert not ambiguous


def test_same_scope_duplicate_stays_ambiguous_even_one_rating_matches():
    rows = [item("a", [0.1, 0.1, 0.2, 0.2], 400), item("b", [0.2, 0.2, 0.3, 0.3], 900)]
    found, ambiguous, _ = score._literal_candidates(
        expected(), rows, identity_key="subject_id"
    )
    assert not found and ambiguous == ["a", "b"]


@pytest.mark.parametrize(
    "box", [None, [0.6, 0.1, 0.7, 0.2], [0, 0, 2, 1], [0.3, 0.3, 0.2, 0.2]]
)
def test_missing_foreign_or_invalid_actual_location_cannot_match(box):
    found, _, _ = score._literal_candidates(
        expected(), [item("a", box, 400)], identity_key="subject_id"
    )
    assert not found


def test_wrong_original_and_invalid_expected_scope_fail_closed():
    exp = expected()
    row = item("a", [0.1, 0.1, 0.2, 0.2], 400)
    row["source_refs"][0]["sha256"] = "b" * 64
    assert not score._literal_candidates(exp, [row], identity_key="subject_id")[0]
    row["source_refs"][0]["sha256"] = "a" * 64
    exp["source_refs"][0]["source_scope_bbox"] = [0, 0, 2, 1]
    assert not score._literal_candidates(exp, [row], identity_key="subject_id")[0]


def test_other_tables_are_unscored_not_false_alarms():
    rows = [
        item("inside", [0.1, 0.1, 0.2, 0.2], 400),
        item("outside", [0.6, 0.1, 0.7, 0.2], 900),
    ]
    result = score.score_requirements({"requirements": [expected()]}, {"items": rows})
    assert result["match"] == 1 and result["false_alarms"] == 0
    assert [r["id"] for r in result["out_of_scope_unmatched_items"]] == ["outside"]


def test_legacy_unscoped_key_keeps_page_only_ambiguity():
    exp = expected()
    exp["source_refs"][0].pop("source_scope_bbox")
    found, ambiguous, _ = score._literal_candidates(
        exp,
        [item("a", [0.1, 0.1, 0.2, 0.2], 400), item("b", [0.6, 0.1, 0.7, 0.2], 900)],
        identity_key="subject_id",
    )
    assert not found and ambiguous == ["a", "b"]
