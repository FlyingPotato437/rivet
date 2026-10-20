"""Synthetic correspondence controls; none are real extraction accuracy claims."""
from copy import deepcopy

import pytest

from backend.equipment.evidence.correspondence import (
    append_correspondence_claims, match_source_local_rows,
)
from backend.equipment.evidence.ontology import make_node
from backend.equipment.evidence.schema import make_claim, new_graph, stable_id

HASH = "a" * 64


def _inputs():
    cells = [
        {"header": "CKT NO", "field": "circuit_no", "text": "8", "raw_text": "8", "value": 8,
         "confidence": "high", "value_confidence": "high", "bbox": [.8, .8, .9, .9]},
        {"header": "TRIP AMP", "field": "trip_a", "text": "60A", "value": 60, "confidence": "high"},
        {"header": "DEVICE/FRAME RATING", "field": "device_frame", "text": "L", "value": "L", "confidence": "high"},
    ]
    visual = {"source_hashes": {"schedule": HASH}, "bom_rows": [
        {"id": "vision-eight", "source_id": "schedule", "sha256": HASH, "page": 1, "table": 7,
         "row": 99, "cells": cells, "fields": {"circuit_no": 8, "trip_a": 60}},
    ]}
    headers = [
        {"raw_text": "CKT\nNO", "field": "circuit_no", "ocr_confidence": 95, "bbox": [.1, .1, .2, .2]},
        {"raw_text": "TRIP AMP", "field": "trip_a", "ocr_confidence": 95, "bbox": [.2, .1, .3, .2]},
        {"raw_text": "DEVICE/FRAME RATING", "field": "device_frame", "ocr_confidence": 95, "bbox": [.3, .1, .4, .2]},
    ]
    row = {"subject_id": "ocr-eight", "source_local_id": "native-cell-row", "page": 1, "table": 3,
           "grid_row": 6, "bbox": [.1, .3, .6, .4], "cells": [
               {"grid_column": 1, "header": "CKT NO", "field": "circuit_no", "raw_text": "8",
                "geometry_valid": True, "row_span": 1, "bbox": [.1, .3, .2, .4],
                "word_bboxes": [{"text": "8", "confidence": 95, "bbox": [.13, .32, .17, .36]}]},
           ]}
    source = {"source_id": "schedule", "sha256": HASH, "page": 1, "table": 3, "row": 6,
              "bbox": [.2, .3, .3, .4], "text": "600A"}
    claim = make_claim("ocr-eight", "trip_a", 600, source=source, unit="A", raw_value="600A",
                       raw_confidence=95, extractor={"name": "source_ruled_table_tesseract"})
    table = {"source_id": "schedule", "sha256": HASH, "pages": [
        {"page": 1, "orientation_established": True, "orientation_clockwise": 0,
         "tables": [{"table": 3, "bbox": [.1, .1, .6, .7], "headers": headers}]}
    ], "rows": [row], "claims": [claim]}
    return visual, table


def _match(visual, table, **kwargs):
    return match_source_local_rows(visual, [table], scope_id="org:order", **kwargs)


def test_exact_circuit_and_actual_source_geometry_link_without_row_or_value_matching():
    visual, table = _inputs()
    result = _match(visual, table)
    link = result["links"][0]
    assert link["status"] == "proven_source_local"
    assert link["circuit_literal"] == "8" and link["visual_local_id"] == "vision-eight"
    assert link["table_local_id"] == "native-cell-row"
    assert link["basis"]["ratings_or_row_ordinals_used"] is False
    assert link["basis"]["visual_geometry_used_as_precise_cell"] is False
    assert not link["physical_identity_verified"] and not link["engineering_approved"]
    assert any(r.get("text") == "8" and r["bbox"] == [.13, .32, .17, .36] for r in link["source_refs"])


@pytest.mark.parametrize("kind", ["hash", "page", "source_id"])
def test_different_original_source_or_page_never_matches(kind):
    visual, table = _inputs()
    if kind == "hash":
        table["sha256"] = "b" * 64
    elif kind == "page":
        table["pages"][0]["page"] = table["rows"][0]["page"] = 2
    else:
        table["source_id"] = "another-job"
    assert _match(visual, table)["links"] == []


def test_duplicate_circuit_labels_remain_candidates_even_at_same_ordinal():
    visual, table = _inputs()
    duplicate = deepcopy(table["rows"][0])
    duplicate.update(subject_id="duplicate", source_local_id="another-row", grid_row=99)
    table["rows"].append(duplicate)
    result = _match(visual, table)
    assert len(result["links"]) == 2
    assert all(l["status"] == "candidate" and "duplicate_circuit_literal" in l["unresolved"] for l in result["links"])


def test_duplicate_visual_labels_never_reuse_one_source_cell():
    visual, table = _inputs()
    duplicate = deepcopy(visual["bom_rows"][0]); duplicate["id"] = "duplicate"
    visual["bom_rows"].append(duplicate)
    assert all(l["status"] == "candidate" for l in _match(visual, table)["links"])


def test_multiple_same_schema_tables_are_not_joined_by_table_index():
    visual, table = _inputs()
    duplicate = deepcopy(table["pages"][0]["tables"][0]); duplicate["table"] = 7
    table["pages"][0]["tables"].append(duplicate)
    link = _match(visual, table)["links"][0]
    assert link["status"] == "candidate" and "multiple_matching_ruled_tables" in link["unresolved"]


@pytest.mark.parametrize("kind,reason", [
    ("word", "identifier_word_not_uniquely_located_in_cell"),
    ("low", "source_identifier_word_requires_verification"),
    ("header", "identifier_cell_not_aligned_with_printed_header_column"),
    ("orientation", "source_column_orientation_not_established"),
    ("merged", "identifier_cell_geometry_not_unique"),
])
def test_source_cell_word_header_and_orientation_are_required(kind, reason):
    visual, table = _inputs()
    cell = table["rows"][0]["cells"][0]
    if kind == "word": cell["word_bboxes"][0]["bbox"] = [.3, .32, .37, .36]
    elif kind == "low": cell["word_bboxes"][0]["confidence"] = 49
    elif kind == "header": table["pages"][0]["tables"][0]["headers"][0]["bbox"] = [.3, .1, .4, .2]
    elif kind == "orientation": table["pages"][0]["orientation_established"] = False
    else: cell["row_span"] = 2
    link = _match(visual, table)["links"][0]
    assert link["status"] == "candidate" and reason in link["unresolved"]


def test_normalized_circuit_cannot_overwrite_different_printed_identifier():
    visual, table = _inputs()
    visual["bom_rows"][0]["cells"][0]["raw_text"] = "7"
    assert _match(visual, table)["links"] == []
    visual["bom_rows"][0]["cells"][0]["raw_text"] = "08"
    assert _match(visual, table)["links"] == []


def test_wrong_typed_identifier_stays_candidate_despite_matching_printed_literal():
    visual, table = _inputs()
    visual["bom_rows"][0]["cells"][0]["value"] = 7
    link = _match(visual, table)["links"][0]
    assert link["status"] == "candidate" and "visual_literal_normalization_disagrees" in link["unresolved"]


def _graph(table, ancestry=None):
    graph = new_graph("org:order")
    graph["nodes"] = [
        make_node("v", "unclassified_position", "org:order", metadata={"source_id": "schedule", "source_local_id": "vision-eight", "source_local": True}),
        make_node("t", "unclassified_position", "org:order", metadata={"source_id": "schedule", "source_local_id": "native-cell-row", "source_local": True}),
    ]
    original = table["claims"][0]
    ocr = make_claim("t", "trip_a", 600, source=original["source"], unit="A", raw_value="600A",
                     raw_confidence=95, extractor=original["extractor"], independence_group=ancestry,
                     conditions={"side": "target", "role": "bom"})
    vision = make_claim("v", "trip_a", 60, source=original["source"], unit="A", raw_value="60A",
                        extractor={"name": "visual_reader"}, independence_group=ancestry,
                        conditions={"side": "target", "role": "bom"})
    graph["claims"] = [ocr, vision]
    return graph


def test_projections_preserve_every_original_claim_and_same_page_ancestry():
    visual, table = _inputs()
    links = _match(visual, table)
    graph = _graph(table)
    before = deepcopy(graph)
    projected = append_correspondence_claims(graph, links)
    assert graph == before
    assert projected["claims"][:2] == before["claims"]
    assert len(projected["claims"]) == 3
    extra = projected["claims"][-1]
    assert extra["subject_id"] == "v" and extra["value"] == 600 and extra["raw_value"] == "600A"
    assert extra["derived_from"] == [before["claims"][0]["id"]]
    assert extra["independence_group"] == projected["claims"][1]["independence_group"]
    assert extra["conditions"]["side"] == "target" and extra["state"] == "observed"
    assert not projected["aliases"]
    assert append_correspondence_claims(projected, links) == projected


def test_copied_original_page_ancestry_remains_one_group():
    visual, table = _inputs()
    context = {"schedule": {"original_sha256": "c" * 64, "page_map": {"1": 246}}}
    links = _match(visual, table, source_context=context)
    group = stable_id("original-page-ancestry", "c" * 64, 246, None, None)
    result = append_correspondence_claims(_graph(table, group), links)
    assert {c["independence_group"] for c in result["claims"]} == {group}


def test_candidates_never_project_and_foreign_scope_is_rejected():
    visual, table = _inputs()
    table["rows"][0]["cells"][0]["word_bboxes"][0]["confidence"] = 49
    links = _match(visual, table)
    graph = _graph(table)
    assert append_correspondence_claims(graph, links)["claims"] == graph["claims"]
    links["scope_id"] = "another-order"
    with pytest.raises(ValueError, match="different order"):
        append_correspondence_claims(graph, links)


def test_proven_local_projection_reconciles_real_conflict_without_auto_acceptance():
    from backend.equipment.evidence.reconcile import reconcile_graph
    visual, table = _inputs()
    graph = append_correspondence_claims(_graph(table), _match(visual, table))
    result = reconcile_graph(graph)
    trip = next(r for r in result["resolutions"] if r["subject_id"] == "v" and r["feature"] == "trip_a")
    assert trip["value"] is None and trip["status"] == "conflict" and trip["needs_review"]
    assert {a["value"] for a in trip["alternatives"]} == {60, 600}
    assert all(a["score"] == 1 for a in trip["alternatives"])
    assert not trip["engineering_approved"] and not trip["production_released"]


@pytest.mark.parametrize("tamper", ["link", "value", "ancestry", "side", "feature"])
def test_projection_cannot_change_source_evidence_or_borrow_identity(tamper):
    from backend.equipment.evidence.reconcile import reconcile_graph
    visual, table = _inputs()
    graph = append_correspondence_claims(_graph(table), _match(visual, table))
    extra = graph["claims"][-1]
    if tamper == "link": graph["identity_correspondence"][0]["verified"] = False
    elif tamper == "value": extra["value"] = 800
    elif tamper == "ancestry": extra["independence_group"] = "borrowed"
    elif tamper == "side": extra["conditions"]["side"] = "baseline"
    else: extra["feature"] = "frame_a"
    with pytest.raises(ValueError, match="borrow another equipment identity"):
        reconcile_graph(graph)


def test_versioned_pipeline_hook_preserves_v1_and_links_only_explicit_v2():
    from backend.equipment.evidence.pipeline import build_evidence_graph
    visual, table = _inputs()
    source = {"id": "schedule", "sha256": HASH, "role": "bom"}
    arguments = {"sources": [source], "source_context": {"schedule": {"side": "target", "role": "bom"}},
                 "scope_id": "org:order", "predictions": visual, "table_reports": [table]}
    legacy = build_evidence_graph(**arguments, correspondence_policy=None)
    updated = build_evidence_graph(**arguments, correspondence_policy="source-local-v2")
    assert not legacy.get("identity_correspondence")
    assert legacy["metadata"]["pipeline_version"] == "rivet.first24-evidence.v1"
    assert updated["metadata"]["source_local_correspondence"]["counts"]["proven_source_local"] == 1
    assert updated["metadata"]["pipeline_version"] == "rivet.first24-evidence.v2"
    for claim in legacy["claims"]:
        assert claim in updated["claims"]
    vision = next(n for n in updated["nodes"] if n.get("metadata", {}).get("source_local_id") == "vision-eight")
    trip = next(r for r in updated["resolutions"] if r["subject_id"] == vision["id"] and r["feature"] == "trip_a")
    assert trip["value"] is None and trip["status"] == "conflict"
    with pytest.raises(ValueError, match="Unknown evidence correspondence policy"):
        build_evidence_graph(**arguments, correspondence_policy="row-order")
