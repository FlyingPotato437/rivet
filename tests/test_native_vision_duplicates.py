"""Synthetic controls for repeated annotations; not real handwriting accuracy."""

from types import SimpleNamespace

import pytest

from backend.ingestion.vision import Finding, native_duplicate


@pytest.fixture(autouse=True)
def database():
    yield


def note(text="Add one spare breaker", *, page=1, kind="Text", bbox=None):
    return SimpleNamespace(
        text=text,
        id="native",
        location={
            "page": page,
            "annotation_id": "n1",
            "annotation_type": kind,
            "bbox": bbox or [0.4, 0.3, 0.42, 0.32],
        },
    )


def finding(text, *, kind="review_comment", bbox=None):
    return Finding(
        text=text,
        kind=kind,
        confidence="low" if kind == "ambiguous_mark" else "high",
        bbox=bbox or [0.4, 0.3, 0.42, 0.32],
    )


def test_zero_padded_printed_number_does_not_duplicate_native_instruction():
    native = note()
    assert native_duplicate(finding("02 Add one spare breaker"), [native], 1) is native


def test_equipment_rating_is_not_stripped_as_a_comment_number():
    assert native_duplicate(finding("30 HP motor"), [note("HP motor")], 1) is None


def test_same_instruction_far_from_the_native_note_stays_reviewable():
    native = note(kind="FreeText")
    repeated_elsewhere = finding(native.text, bbox=[0.7, 0.7, 0.9, 0.9])
    assert native_duplicate(repeated_elsewhere, [native], 1) is None


def test_unique_sticky_note_icon_uses_its_existing_readable_instruction():
    native = note()
    assert (
        native_duplicate(finding("Note icon", kind="ambiguous_mark"), [native], 1)
        is native
    )


def test_unreadable_handwriting_or_a_different_page_remains_reviewable():
    mark = finding("Illegible handwriting", kind="ambiguous_mark")
    assert native_duplicate(mark, [note(kind="FreeText")], 1) is None
    assert native_duplicate(mark, [note(page=2)], 1) is None
    assert native_duplicate(mark, [note(bbox=[0.41, 0.31, 0.43, 0.33])], 1) is None


def test_overlapping_or_repeated_native_notes_are_not_arbitrarily_selected():
    native = note()
    assert (
        native_duplicate(
            finding("Note icon", kind="ambiguous_mark"), [native, note()], 1
        )
        is None
    )
    assert native_duplicate(finding(native.text), [native, note()], 1) is None
