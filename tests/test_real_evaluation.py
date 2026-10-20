"""Reference-bound real-document scores never use extraction as their truth."""

import hashlib
import json
from copy import deepcopy

import pytest

from backend.equipment.real_evaluation import (
    EvaluationError,
    annotation_actionability,
    evaluate_package,
    merge_comment_predictions,
    score_predictions,
    summarize_reports,
    vector_page_failure,
)


def prediction(**kwargs):
    return {
        "schema_version": "rivet.real-predictions.v1",
        "package_id": "real-1",
        "source_hashes": {"drawing": "a" * 64},
        "comments": [],
        "devices": [],
        "bom_rows": [],
        "issues": [],
        "revision_checks": [],
        **kwargs,
    }


def key(**labels):
    return {
        "schema_version": "rivet.real-answer-key.v1",
        "package_id": "real-1",
        "review": {
            "reviewer": "Independent visual reviewer",
            "reviewed_at": "2026-10-18T22:00:00Z",
            "method": "assistant_visual_review",
            "engineering_reviewed": False,
        },
        "source_hashes": {"drawing": "a" * 64},
        "labels": labels,
    }


def note(item_id, text, page=1, **kwargs):
    return {
        "id": item_id,
        "source_id": "drawing",
        "page": page,
        "text": text,
        "kind": "printed_markup",
        "actionable": True,
        **kwargs,
    }


def page_scope(items, *, scope="complete", page=1):
    return {
        "scope": scope,
        "pages": [{"source_id": "drawing", "page": page}],
        "items": items,
    }


def comments(report):
    return report["extraction"]["actionable_comments"]


def test_private_reports_cannot_be_written_into_shareable_output(tmp_path):
    with pytest.raises(EvaluationError, match="ignored .data"):
        evaluate_package(
            {"id": "pilot-001", "kind": "real_private", "sources": []},
            tmp_path / "manifest.json",
            output_dir=tmp_path / "shareable-output",
        )


def test_missing_categories_are_null_not_empty_perfect_accuracy():
    report = score_predictions(key(), prediction())
    assert report["extraction"]["actionable_comments"] is None
    assert report["extraction"]["device_tags"] is None
    assert report["extraction"]["bom_rows"] is None
    assert report["checks"] == {"issues": None, "revision_checks": None}
    assert report["engineering_accuracy_validated"] is False
    assert report["reference_status"] == "preliminary_assistant_visual_reference"


def test_complete_reference_counts_tp_fn_fp_and_exact_page_scope():
    reference = key(
        comments=page_scope(
            [note("r1", "Confirm breaker trip"), note("r2", "Add label")]
        )
    )
    observed = prediction(
        comments=[
            note("p1", "Confirm breaker trip"),
            note("p2", "Wrong extra note"),
            note("outside", "An unreviewed page", page=79),
        ]
    )
    result = comments(score_predictions(reference, observed))
    assert (result["tp"], result["fn"], result["fp"]) == (1, 1, 1)
    assert result["precision"] == result["recall"] == 0.5
    assert [item["id"] for item in result["unscored_predictions"]] == ["outside"]


def test_partial_reference_never_establishes_precision_or_false_positives():
    reference = key(comments=page_scope([note("r1", "Confirm trip")], scope="partial"))
    result = comments(
        score_predictions(
            reference,
            prediction(
                comments=[
                    note("p1", "Confirm trip"),
                    note("p2", "Another real unlabelled comment"),
                ]
            ),
        )
    )
    assert result["tp"] == 1 and result["fn"] == 0
    assert result["recall"] == 1
    assert result["fp"] is None and result["precision"] is None
    assert [item["id"] for item in result["unscored_predictions"]] == ["p2"]


def test_one_prediction_cannot_satisfy_two_identical_reference_comments():
    reference = key(
        comments=page_scope(
            [note("r1", "Confirm dimension"), note("r2", "Confirm dimension")]
        )
    )
    result = comments(
        score_predictions(
            reference, prediction(comments=[note("p1", "Confirm dimension")])
        )
    )
    assert (result["tp"], result["fn"], result["fp"]) == (1, 1, 0)


def test_numbering_dash_typography_and_final_period_are_formatting_not_new_comments():
    reference = key(
        comments=page_scope(
            [note("r1", "Electrical panel - shall be 100A, 120/208V, 1PH.")]
        )
    )
    result = comments(
        score_predictions(
            reference,
            prediction(
                comments=[
                    note("p1", "1.) Electrical panel – shall be 100A, 120/208V, 1PH")
                ]
            ),
        )
    )
    assert result["tp"] == 1
    wrong = comments(
        score_predictions(
            reference,
            prediction(
                comments=[note("p1", "Electrical panel - shall be 200A, 120/208V, 1PH")]
            ),
        )
    )
    assert wrong["tp"] == 0


def test_location_detection_is_separate_from_transcription_accuracy():
    reference = key(
        comments=page_scope(
            [note("r1", "Incoming on right", bbox=[0.1, 0.2, 0.6, 0.3])]
        )
    )
    report = score_predictions(
        reference,
        prediction(
            comments=[note("p1", "Incoming on left", bbox=[0.1, 0.2, 0.6, 0.3])]
        ),
    )
    assert comments(report)["tp"] == 1
    assert report["extraction"]["comment_transcription"]["tp"] == 0
    assert report["extraction"]["comment_transcription"]["fn"] == 1


def test_grouped_block_content_does_not_fake_atomic_segmentation():
    reference = key(
        comments=page_scope([note("r1", "Confirm trip"), note("r2", "Add label")])
    )
    report = score_predictions(
        reference, prediction(comments=[note("block", "1. Confirm trip\n2. Add label")])
    )
    assert comments(report)["tp"] == 0
    assert (
        report["extraction"]["comment_content_coverage"]["covered_reference_texts"] == 2
    )
    assert (
        "not distinct comment segmentation"
        in report["extraction"]["comment_content_coverage"]["measurement"]
    )


def test_reviewed_zero_scope_never_means_100_percent_recall():
    result = comments(score_predictions(key(comments=page_scope([])), prediction()))
    assert result["tp"] == result["fn"] == result["fp"] == 0
    assert result["precision"] is None and result["recall"] is None
    with pytest.raises(EvaluationError, match="reviewed pages"):
        score_predictions(
            key(comments={"scope": "complete", "items": []}), prediction()
        )


def test_stamps_signatures_unknown_marks_are_not_actionable_recall_truth():
    reference = key(
        comments=page_scope(
            [
                note("r1", "Confirm trip"),
                note("stamp", "APPROVED", actionable=False, kind="stamp"),
            ]
        )
    )
    report = score_predictions(
        reference,
        prediction(
            comments=[
                note("p1", "Confirm trip"),
                note("p2", "APPROVED", actionable=False),
                note("unknown", "Unclear ink", actionable=None),
            ]
        ),
    )
    assert comments(report)["tp"] == 1 and comments(report)["fp"] == 0
    assert report["extraction"]["non_actionable_marks"]["tp"] == 1
    assert report["extraction"]["non_actionable_marks"]["precision"] is None
    assert (
        report["extraction"]["unclassified_comment_predictions"][0]["id"] == "unknown"
    )
    with pytest.raises(EvaluationError, match="actionable"):
        score_predictions(
            key(
                comments=page_scope(
                    [
                        {
                            k: v
                            for k, v in note("r", "Confirm").items()
                            if k != "actionable"
                        }
                    ]
                )
            ),
            prediction(),
        )


def test_references_need_matching_source_bytes_and_independent_review_provenance():
    changed = prediction(source_hashes={"drawing": "b" * 64})
    with pytest.raises(EvaluationError, match="hashes differ"):
        score_predictions(key(), changed)
    for edit in (
        lambda value: value["review"].update(method="extractor_output"),
        lambda value: value["review"].update(engineering_reviewed=True),
        lambda value: value["review"].update(reviewed_at="2026-10-18T12:00:00"),
    ):
        reference = key()
        edit(reference)
        with pytest.raises(EvaluationError):
            score_predictions(reference, prediction())


def test_alias_like_tags_are_exact_and_do_not_inherit_device_accuracy():
    labels = [{"id": "r1", "source_id": "drawing", "page": 1, "tag": "CB-12"}]
    observed = [{"id": "p1", "source_id": "drawing", "page": 1, "tag": "BKR12"}]
    result = score_predictions(
        key(devices=page_scope(labels)), prediction(devices=observed)
    )["extraction"]["device_tags"]
    assert (result["tp"], result["fn"], result["fp"]) == (0, 1, 1)


def test_bom_row_presence_does_not_fake_cell_value_accuracy():
    reference = {
        "scope": "complete",
        "source_ids": ["drawing"],
        "items": [
            {
                "id": "r1",
                "source_id": "drawing",
                "sheet": "BOM",
                "row": 4,
                "device_tag": "CB-12",
                "fields": {"part_number": "RIGHT", "quantity": 2},
            }
        ],
    }
    observed = [
        {
            "id": "p1",
            "source_id": "drawing",
            "sheet": "BOM",
            "row": 4,
            "device_tag": "CB-12",
            "fields": {"part_number": "WRONG"},
        }
    ]
    result = score_predictions(key(bom_rows=reference), prediction(bom_rows=observed))[
        "extraction"
    ]["bom_rows"]
    assert result["tp"] == 1
    assert result["field_values"]["correct"] == 0
    assert result["field_values"]["wrong"] == result["field_values"]["missing"] == 1
    assert result["field_values"]["accuracy_on_labelled_fields"] == 0


def test_missing_bom_rows_contribute_all_labelled_cells_to_accuracy_denominator():
    reference = {
        "scope": "complete",
        "source_ids": ["drawing"],
        "items": [
            {
                "id": "row1",
                "source_id": "drawing",
                "sheet": "BOM",
                "row": 1,
                "fields": {"part_number": "A", "quantity": 1},
            },
            {
                "id": "row2",
                "source_id": "drawing",
                "sheet": "BOM",
                "row": 2,
                "fields": {"part_number": "B", "quantity": 2},
            },
        ],
    }
    observed = [
        {
            "id": "p1",
            "source_id": "drawing",
            "sheet": "BOM",
            "row": 1,
            "fields": {"part_number": "A", "quantity": 1},
        }
    ]
    result = score_predictions(key(bom_rows=reference), prediction(bom_rows=observed))[
        "extraction"
    ]["bom_rows"]
    assert result["tp"] == result["fn"] == 1
    assert result["field_values"]["correct"] == result["field_values"]["missing"] == 2
    assert result["field_values"]["accuracy_on_labelled_fields"] == 0.5
    empty = score_predictions(key(bom_rows=reference), prediction())["extraction"][
        "bom_rows"
    ]
    assert empty["field_values"]["accuracy_on_labelled_fields"] == 0


def test_known_zero_precision_and_recall_have_zero_f1_not_missing_measurement():
    report = score_predictions(
        key(comments=page_scope([note("r", "Confirm trip")])),
        prediction(comments=[note("p", "Wrong note")]),
    )
    assert comments(report)["f1"] == 0


def test_explicit_vector_page_failure_surfaces_without_promoting_coverage_warnings():
    page = {
        "page": 43,
        "issues": [
            {"code": "page_complexity_or_coverage", "reason": "Work bound exceeded"}
        ],
    }
    failure = vector_page_failure("original", page)
    assert failure["page"] == 43 and failure["error_type"] == "PageInspectionFailed"
    assert (
        vector_page_failure(
            "original",
            {
                "page": 1,
                "issues": [
                    {"code": "unaccounted_ink", "reason": "Coverage remains unknown"}
                ],
            },
        )
        is None
    )


def test_extraction_and_check_metrics_do_not_share_the_same_truth():
    reference = key(
        devices=page_scope(
            [{"id": "tag", "source_id": "drawing", "page": 1, "tag": "CB-12"}]
        ),
        revision_checks={
            "scope": "complete",
            "items": [
                {
                    "id": "check",
                    "kind": "equipment_field",
                    "device_tag": "CB-12",
                    "field": "trip_unit",
                    "status": "remaining",
                }
            ],
        },
    )
    observed = prediction(
        devices=[{"id": "p", "source_id": "drawing", "page": 1, "tag": "CB-12"}],
        revision_checks=[
            {
                "id": "bad",
                "kind": "equipment_field",
                "device_tag": "CB-12",
                "field": "trip_unit",
                "status": "verified",
            }
        ],
    )
    report = score_predictions(reference, observed)
    assert report["extraction"]["device_tags"]["tp"] == 1
    assert report["checks"]["revision_checks"]["tp"] == 0
    assert (
        report["checks"]["revision_checks"]["fn"]
        == report["checks"]["revision_checks"]["fp"]
        == 1
    )


def test_source_bound_duplicate_merge_preserves_legitimate_repeated_notes():
    native = [
        note(
            "native-1",
            "Confirm trip",
            bbox=[0.1, 0.1, 0.5, 0.2],
            native_annotation_id="A1",
            origin="native_annotation",
        )
    ]
    external = [
        note("vision-1", "Confirm trip", bbox=[0.1, 0.1, 0.5, 0.2], origin="vision"),
        note("vision-2", "Confirm trip", bbox=[0.1, 0.7, 0.5, 0.8], origin="vision"),
    ]
    merged, diagnostics = merge_comment_predictions(native, external)
    assert len(merged) == 2
    assert len(diagnostics["merged_duplicates"]) == 1
    assert len(diagnostics["ambiguous_duplicates"]) == 1
    assert native[0]["id"] == "native-1"
    assert merged[0]["origins"] == ["native_annotation", "vision"]


def test_explicit_annotation_identity_can_correct_transcription_without_duplicate():
    merged, diagnostics = merge_comment_predictions(
        [note("native", "Broken text", native_annotation_id="A1")],
        [note("vision", "Corrected text", native_annotation_id="A1")],
    )
    assert len(merged) == 1 and merged[0]["text"] == "Corrected text"
    assert diagnostics["merged_duplicates"][0]["method"] == "annotation_id"


@pytest.mark.parametrize(
    "annotation,expected",
    [
        ({"text": "Confirm the breaker trip", "kind": "FreeText"}, True),
        ({"text": "APPROVED", "kind": "Stamp"}, False),
        ({"text": "10/18/2026", "kind": "Text"}, False),
        ({"text": "John Smith", "kind": "Text"}, False),
        ({"text": "Light fixture - no comment", "kind": "Text"}, False),
        ({"text": "", "kind": "Polygon"}, None),
    ],
)
def test_native_candidate_rules_are_content_based_not_reference_page_based(
    annotation, expected
):
    assert annotation_actionability(annotation)[0] is expected


def test_harness_uses_actual_shared_production_native_classifier():
    from backend.ingestion.markup_classification import (
        annotation_actionability as production,
    )

    assert annotation_actionability is production


def test_summary_does_not_mix_synthetic_controls_into_real_package_accuracy():
    metrics = score_predictions(
        key(comments=page_scope([note("r", "Confirm trip")])), prediction(comments=[])
    )
    reports = [
        {
            "kind": "real_public",
            "scores": metrics,
            "errors": [],
            "coverage": {"physical_pages": 1},
        },
        {
            "kind": "synthetic_control",
            "scores": None,
            "errors": [],
            "coverage": {"physical_pages": 10},
        },
    ]
    summary = summarize_reports(reports)
    assert summary["real_public"]["extraction"]["actionable_comments"]["fn"] == 1
    assert (
        summary["synthetic_control"]["extraction"]["actionable_comments"]["tp"] is None
    )


def test_actual_small_control_package_writes_model_log_checks_without_database(
    tmp_path,
):
    from reportlab.pdfgen import canvas

    drawing = tmp_path / "drawing.pdf"
    pdf = canvas.Canvas(str(drawing))
    pdf.drawString(50, 750, "CB-12")
    pdf.drawString(50, 730, "Trip unit: TU-2500")
    pdf.save()
    package = {
        "id": "control-only",
        "kind": "synthetic_control",
        "sources": [
            {
                "id": "drawing",
                "name": drawing.name,
                "path": drawing.name,
                "role": "drawing",
                "revision": "C",
                "side": "target",
                "sha256": hashlib.sha256(drawing.read_bytes()).hexdigest(),
            }
        ],
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema_version": "rivet.real-packages.v1", "packages": [package]})
    )
    report = evaluate_package(package, manifest, output_dir=tmp_path / "outputs")
    assert report["scores"] is None
    assert report["coverage"]["physical_pages"] == 1
    assert report["native_annotation_transport"]["recall"] is None
    assert report["comparison_summary"]["outcome"] == "cannot_verify"
    for artifact in (
        "equipment_model",
        "comment_log",
        "revision_checks",
        "predictions",
        "report",
    ):
        assert report["artifact_paths"][artifact]
    assert (
        json.loads(
            (tmp_path / "outputs/control-only/equipment-model.json").read_text()
        )["engineering_approved"]
        is False
    )
    assert (
        hashlib.sha256(drawing.read_bytes()).hexdigest()
        == package["sources"][0]["sha256"]
    )


def test_changed_original_is_a_failed_run_not_a_clean_zero(tmp_path):
    path = tmp_path / "drawing.pdf"
    path.write_bytes(b"changed")
    package = {
        "id": "changed",
        "sources": [
            {
                "id": "drawing",
                "path": path.name,
                "sha256": "a" * 64,
                "role": "drawing",
                "revision": "C",
                "side": "target",
            }
        ],
    }
    with pytest.raises(EvaluationError, match="changed from its pinned hash"):
        evaluate_package(package, tmp_path / "manifest.json")


def test_scoring_does_not_mutate_independently_authored_keys_or_predictions():
    reference = key(comments=page_scope([note("r", "Confirm trip")]))
    observed = prediction(comments=[note("p", "Confirm trip")])
    originals = deepcopy((reference, observed))
    score_predictions(reference, observed)
    assert (reference, observed) == originals
