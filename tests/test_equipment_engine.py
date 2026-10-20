"""Pure document tests with independent expected values; no mailbox/database."""

import csv
import hashlib

import pytest
from pydantic import ValidationError
from reportlab.pdfgen import canvas

from backend.equipment import SourceInput, analyze_package, compare_packages
from backend.equipment.demo_documents import build_demo_corpus
from backend.equipment.readers import normalize_value


@pytest.fixture(autouse=True)
def database():
    # Override the repository's database-rebuilding autouse fixture. These
    # tests only read temporary files and must not touch any test/live DB.
    yield


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("equipment-corpus")
    manifest = build_demo_corpus(root)
    sources = {
        item["id"]: {**item, "path": str(root / item["path"])}
        for item in manifest["sources"]
    }
    cases = {item["id"]: item for item in manifest["cases"]}
    return sources, cases


def run_case(corpus, name):
    sources, cases = corpus
    case = cases[name]
    return compare_packages(
        [sources[s] for s in case["baseline"]],
        [sources[s] for s in case["target"]],
        [sources[s] for s in case["requirements"]],
    )


def check(result, tag, field):
    return next(
        item
        for item in result["checks"]
        if item["device_tag"] == tag and item["field"] == field
    )


def source(path, role, revision="D", source_id=None):
    return {
        "id": source_id or path.name,
        "name": path.name,
        "path": str(path),
        "role": role,
        "revision": revision,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def write_csv(path, rows):
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    return path


@pytest.mark.parametrize("name", ["complete-E", "native-DXF-complete", "csv-complete"])
def test_complete_revision_verifies_actual_drawings_and_bom(corpus, name):
    result = run_case(corpus, name)
    assert result["summary"]["outcome"] == "supported_fields_match"
    assert result["summary"]["remaining"] == 0
    assert result["coverage"]["target_observed_device_count"] == 3
    assert not result["coverage"]["complete_inventory_verified"]
    assert not result["summary"]["production_release_verified"]
    trip = check(result, "CB-12", "trip_unit")
    assert trip["status"] == "verified"
    assert trip["baseline_drawing"]["value"] == "TU-2500"
    assert trip["target_drawing"]["value"] == "TU-3000"
    assert trip["target_bom"]["value"] == "TU-3000"
    assert trip["required"]["value"] == "TU-3000"
    assert check(result, "CB-12", "nameplate")["status"] == "verified"
    assert check(result, "CB-12", "frame_a")["status"] == "unchanged"


@pytest.mark.parametrize("name", ["partial-D", "native-DXF-partial"])
def test_drawing_only_edit_does_not_hide_stale_bom_or_deleted_device(corpus, name):
    result = run_case(corpus, name)
    trip = check(result, "CB-12", "trip_unit")
    assert trip["status"] == "mismatch"
    assert trip["target_drawing"]["value"] == "TU-3000"
    assert trip["target_bom"]["value"] == "TU-2500"
    missing = check(result, "CB-14", "device_presence")
    assert missing["status"] == "missing"
    assert missing["missing_roles"] == ["drawing"]
    assert result["summary"]["outcome"] == "changes_remaining"
    assert result["summary"]["remaining"] == 2
    assert not any(
        item["device_tag"] == "CB-14" and item["field"] != "device_presence"
        for item in result["checks"]
    )


@pytest.mark.parametrize("name", ["titleblock-only", "comments-only"])
def test_revision_label_and_resolved_comment_never_prove_change(corpus, name):
    result = run_case(corpus, name)
    assert check(result, "CB-12", "trip_unit")["status"] == "mismatch"
    assert check(result, "CB-12", "trip_unit")["target_drawing"]["value"] == "TU-2500"
    assert check(result, "CB-12", "nameplate")["status"] == "mismatch"
    assert all(fact["role"] != "markup" for fact in result["target"]["facts"])


def test_duplicate_tag_values_fail_instead_of_majority_vote(corpus):
    result = run_case(corpus, "conflicting-tags")
    trip = check(result, "CB-12", "trip_unit")
    assert trip["status"] == "mismatch"
    assert {item["value"] for item in trip["target_drawing"]["values"]} == {
        "TU-2500",
        "TU-3000",
    }


def test_formula_cells_are_unknown_even_if_excel_cached_value_looks_valid(corpus):
    result = run_case(corpus, "formula-unknown")
    assert check(result, "CB-12", "trip_unit")["status"] == "unknown"
    assert any(
        warning["code"] == "formula_not_evaluated" for warning in result["warnings"]
    )


def test_scanned_drawing_is_unknown_not_zero_item_success(corpus):
    result = run_case(corpus, "unreadable-scan")
    assert result["summary"]["outcome"] == "cannot_verify"
    assert check(result, "CB-12", "device_presence")["status"] == "unknown"
    assert any(warning["code"] == "unreadable_page" for warning in result["warnings"])
    assert not any(fact["role"] == "drawing" for fact in result["target"]["facts"])


def test_unrequested_change_is_visible_and_not_auto_verified(corpus):
    result = run_case(corpus, "unrelated-change")
    assert check(result, "CB-16", "control_voltage_v")["status"] == "unknown"
    assert result["summary"]["outcome"] == "cannot_verify"


def test_no_fuzzy_device_aliasing(corpus):
    result = run_case(corpus, "unapproved-rename")
    assert check(result, "CB-14", "device_presence")["status"] == "missing"
    assert check(result, "CB-114", "trip_unit")["status"] == "unknown"


def test_each_fact_has_exact_byte_and_location_evidence(corpus):
    result = run_case(corpus, "complete-E")
    for fact in result["target"]["facts"]:
        assert len(fact["sha256"]) == 64
        assert fact["revision"] == "E"
        assert fact["source_id"]
        evidence = fact["evidence"]
        if fact["source_name"].endswith(".pdf"):
            assert evidence["page"] == 1
            assert all(0 <= coordinate <= 1 for coordinate in evidence["bbox"])
            assert evidence["bbox"][0] < evidence["bbox"][2]
            assert evidence["bbox"][1] < evidence["bbox"][3]
        else:
            assert evidence["sheet"] == "Equipment BOM"
            assert evidence["row"] >= 2
            assert evidence["cell"]
        assert "path" not in fact


@pytest.mark.parametrize(
    ("field", "raw", "expected", "unit"),
    [
        ("frame_a", "2.5 kA", 2500, "A"),
        ("trip_a", "2,000 A", 2000, "A"),
        ("interrupting_ka", "65000 A", 65, "kA"),
        ("control_voltage_v", "0.12 kV", 120, "V"),
        ("quantity", "2 ea", 2, "each"),
    ],
)
def test_explicit_units_normalize_without_false_revision_drift(
    field, raw, expected, unit
):
    assert normalize_value(field, raw) == (expected, unit, None)


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("frame_a", "2500 V"),
        ("interrupting_ka", "65 kV"),
        ("trip_a", "2.000,5 A"),
        ("quantity", "0"),
        ("quantity", "1.5"),
        ("trip_a", "2000/2500 A"),
    ],
)
def test_wrong_or_ambiguous_numeric_units_are_not_compared_as_numbers(field, raw):
    assert normalize_value(field, raw)[0] is None


def test_hash_mismatch_never_reuses_previous_facts(tmp_path):
    path = write_csv(
        tmp_path / "actual.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    original = source(path, "bom")
    path.write_text("tag,trip_unit\nCB-12,TU-2500\n")
    result = analyze_package([original])
    assert result["facts"] == []
    assert result["sources"][0]["status"] == "unreadable"
    assert any(w["code"] == "source_hash_mismatch" for w in result["warnings"])


def test_missing_file_never_establishes_missing_equipment(tmp_path):
    path = write_csv(
        tmp_path / "actual.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    base = source(path, "bom", "C")
    missing = {
        **base,
        "id": "missing",
        "path": str(tmp_path / "not-there.csv"),
        "revision": "D",
    }
    result = compare_packages([base], [missing])
    assert check(result, "CB-12", "device_presence")["status"] == "unknown"
    assert result["summary"]["outcome"] == "cannot_verify"


def test_explicit_requests_checked_separately_from_actual_facts(tmp_path):
    baseline = write_csv(
        tmp_path / "baseline.csv", [["tag", "trip_unit"], ["CB-12", "TU-2500"]]
    )
    target = write_csv(
        tmp_path / "target.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    request = write_csv(
        tmp_path / "request.csv", [["tag", "target_trip_unit"], ["CB-12", "TU-3000"]]
    )
    result = compare_packages(
        [source(baseline, "drawing", "C"), source(baseline, "bom", "C", "base-bom")],
        [source(target, "drawing", "D"), source(target, "bom", "D", "target-bom")],
        [source(request, "markup")],
    )
    assert result["requirements"]["facts"] == []
    assert len(result["requirements"]["requested_changes"]) == 1
    assert check(result, "CB-12", "trip_unit")["status"] == "verified"
    assert check(result, "CB-12", "trip_unit")["requested"]["value"] == "TU-3000"


def test_requested_change_does_not_override_governing_specification(tmp_path):
    actual = write_csv(
        tmp_path / "actual.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    spec = write_csv(
        tmp_path / "spec.csv", [["tag", "trip_unit"], ["CB-12", "TU-2500"]]
    )
    request = write_csv(
        tmp_path / "request.csv", [["tag", "requested_trip_unit"], ["CB-12", "TU-3000"]]
    )
    result = compare_packages(
        [source(actual, "drawing", "C")],
        [source(actual, "drawing", "D")],
        [source(spec, "specification"), source(request, "markup")],
    )
    assert check(result, "CB-12", "trip_unit")["status"] == "unknown"
    assert "conflict" in check(result, "CB-12", "trip_unit")["reason"]


def test_plain_comment_response_not_parsed_as_target_request(tmp_path):
    path = tmp_path / "comments.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(50, 750, "CB-12")
    c.drawString(50, 725, "Trip unit: TU-3000")
    c.drawString(50, 700, "We fixed all comments and updated the drawing.")
    c.save()
    result = analyze_package([source(path, "markup")])
    assert result["facts"] == []
    assert result["requested_changes"] == []


def test_duplicate_source_ids_excluded_and_immutable_source(tmp_path):
    path = write_csv(
        tmp_path / "actual.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    item = SourceInput(**source(path, "bom"))
    with pytest.raises(ValidationError):
        item.revision = "E"
    result = analyze_package([item, item])
    assert not result["facts"]
    assert any(w["code"] == "duplicate_source_id" for w in result["warnings"])


def test_empty_comparison_is_never_green():
    result = compare_packages([], [])
    assert result["summary"]["outcome"] == "cannot_verify"
    assert not result["coverage"]["complete_inventory_verified"]


@pytest.mark.parametrize("provided_role", ["drawing", "bom"])
def test_whole_missing_source_role_cannot_vanish_from_verification(
    tmp_path, provided_role
):
    actual = write_csv(
        tmp_path / "actual.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    spec = write_csv(
        tmp_path / "spec.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    result = compare_packages(
        [], [source(actual, provided_role)], [source(spec, "specification")]
    )
    verdict = check(result, "CB-12", "device_presence")
    assert verdict["status"] == "missing"
    assert verdict["missing_roles"] == [
        "bom" if provided_role == "drawing" else "drawing"
    ]
    assert result["summary"]["outcome"] != "supported_fields_match"
    assert not result["summary"]["revision_changes_verified"]


def test_missing_device_aggregation_preserves_distinct_incorrect_bom_value(tmp_path):
    original = write_csv(
        tmp_path / "original.csv", [["tag", "trip_unit"], ["CB-12", "TU-2500"]]
    )
    drawing = write_csv(
        tmp_path / "drawing.csv", [["tag", "trip_unit"], ["CB-14", "TU-600"]]
    )
    spec = write_csv(
        tmp_path / "spec.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    result = compare_packages(
        [
            source(original, "drawing", "C"),
            source(original, "bom", "C", "baseline-bom"),
        ],
        [source(drawing, "drawing", "D"), source(original, "bom", "D", "target-bom")],
        [source(spec, "specification")],
    )
    assert check(result, "CB-12", "device_presence")["status"] == "missing"
    assert check(result, "CB-12", "trip_unit")["status"] == "mismatch"
    assert check(result, "CB-12", "trip_unit")["target_bom"]["value"] == "TU-2500"


def test_target_only_matching_requirements_is_not_a_closed_revision_loop(tmp_path):
    actual = write_csv(
        tmp_path / "actual.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    spec = write_csv(
        tmp_path / "spec.csv", [["tag", "trip_unit"], ["CB-12", "TU-3000"]]
    )
    result = compare_packages(
        [],
        [source(actual, "drawing"), source(actual, "bom", source_id="bom")],
        [source(spec, "specification")],
    )
    assert check(result, "CB-12", "trip_unit")["status"] == "verified"
    assert result["summary"]["outcome"] == "cannot_verify"
    assert not result["coverage"]["baseline_comparison_available"]
    assert any(
        warning["code"] == "baseline_incomplete" for warning in result["warnings"]
    )


def test_review_notes_cannot_substitute_for_actual_values(tmp_path):
    path = tmp_path / "review-notes.pdf"
    c = canvas.Canvas(str(path))
    for y, text in [
        (750, "REVIEW NOTES"),
        (710, "CB-12"),
        (670, "Trip unit: TU-3000"),
        (630, "All comments fixed"),
    ]:
        c.drawString(50, y, text)
    c.save()
    result = analyze_package([source(path, "drawing")])
    assert result["facts"] == []


def test_bottom_titleblock_device_reference_is_not_equipment_data(tmp_path):
    path = tmp_path / "titleblock.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(50, 60, "CB-12")
    c.drawString(50, 45, "Trip unit: TU-3000")
    c.save()
    result = analyze_package([source(path, "drawing")])
    assert result["facts"] == []


@pytest.mark.parametrize(
    ("revised_value", "expected_status"),
    [("TU-3000", "verified"), ("TU-2500", "mismatch")],
)
def test_returned_baseline_markup_is_checked_against_next_actual_revision(
    tmp_path, revised_value, expected_status
):
    original = write_csv(
        tmp_path / "baseline.csv", [["tag", "trip_unit"], ["CB-12", "TU-2500"]]
    )
    revised = write_csv(
        tmp_path / "revised.csv", [["tag", "trip_unit"], ["CB-12", revised_value]]
    )
    returned = write_csv(
        tmp_path / "returned.csv", [["tag", "target_trip_unit"], ["CB-12", "TU-3000"]]
    )
    baseline = [
        source(original, "drawing", "C"),
        source(original, "bom", "C", "base-bom"),
        source(returned, "markup", "C"),
    ]
    target = [
        source(revised, "drawing", "D"),
        source(revised, "bom", "D", "target-bom"),
    ]
    result = compare_packages(baseline, target)
    assert check(result, "CB-12", "trip_unit")["status"] == expected_status
    assert result["coverage"]["explicit_requested_change_count"] == 1
    assert all(fact["role"] != "markup" for fact in result["baseline"]["facts"])
    assert check(result, "CB-12", "trip_unit")["requested"]["value"] == "TU-3000"


def test_rotated_cropped_page_evidence_uses_visible_viewer_coordinates(tmp_path):
    import pymupdf

    from backend.equipment.readers import _pdf_bbox

    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((100, 150), "CB-12", fontsize=12)
    page.insert_text((100, 190), "Trip unit: TU-3000", fontsize=12)
    page.insert_text((100, 220), "Nameplate: DATA HALL A", fontsize=12)
    page.set_cropbox(pymupdf.Rect(50, 100, 550, 700))
    page.set_rotation(90)
    # The visible crop is 600 wide x 500 high after clockwise rotation.
    # An unrotated crop-relative rectangle (50,80,150,100) is visibly
    # (500,50,520,150), not (50,80,150,100).
    assert _pdf_bbox((50, 80, 150, 100), page) == [0.833333, 0.1, 0.866667, 0.3]
    path = tmp_path / "rotated-cropped.pdf"
    document.save(path)
    document.close()
    result = analyze_package([source(path, "drawing")])
    trip = next(fact for fact in result["facts"] if fact["field"] == "trip_unit")
    x0, y0, x1, y1 = trip["evidence"]["bbox"]
    assert 0.8 < x0 < x1 < 0.9
    assert 0.09 < y0 < 0.11
    assert 0.2 < y1 < 0.4
    assert trip["value"] == "TU-3000"


def test_stable_results_for_identical_immutable_sources(corpus):
    first = run_case(corpus, "partial-D")
    second = run_case(corpus, "partial-D")
    assert first == second
