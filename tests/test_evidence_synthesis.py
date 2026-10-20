"""Deterministic safety controls, not real-document accuracy measurements."""

import hashlib
from copy import deepcopy
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image, ImageDraw

from backend.equipment.evidence.graph_diff import compare_reconciled_models
from backend.equipment.evidence.synthesis import (
    _ocr,
    _table_strips,
    synthesize_and_compare,
)


@pytest.fixture(autouse=True)
def database():
    """These pure file/graph controls must never rebuild a shared database."""
    yield


def node(identity, tag, *, kind="device", local=False):
    return {
        "id": identity,
        "kind": kind,
        "scope_key": "job-1/lineup-H3",
        "identifiers": {"raw_tag": tag},
        "metadata": {"source_local": local},
        "source_refs": [],
    }


def value(subject, number, *, review=False, feature="rated_current", unit="A"):
    return {
        "subject_id": subject,
        "feature": feature,
        "value": number,
        "unit": unit,
        "status": "reconciled",
        "needs_review": review,
        "supporting_claim_ids": [],
    }


def model(nodes, resolutions=(), edges=(), *, complete=False):
    return {
        "nodes": nodes,
        "resolutions": list(resolutions),
        "claims": [],
        "edges": list(edges),
        "coverage": {"identity_complete": complete, "connectivity_complete": complete},
    }


def test_partial_coverage_never_establishes_absence_or_addition():
    before = model([node("a", "52-12")])
    after = model([node("b", "52-13")])
    result = compare_reconciled_models(before, after)
    assert result["changes"] == []
    assert {u["kind"] for u in result["unknowns"]} == {
        "possible_device_added",
        "possible_device_removed",
    }
    result = compare_reconciled_models(before, model([], complete=True))
    assert [c["kind"] for c in result["changes"]] == ["device_removed"]


def test_ratings_require_resolved_values_and_preserve_units():
    before = model([node("a", "52-12")], [value("a", 600)])
    equivalent = model([node("b", "52-12")], [value("b", 0.6, unit="kA")])
    assert compare_reconciled_models(before, equivalent)["changes"] == []
    after = model([node("b", "52-12")], [value("b", 60)])
    result = compare_reconciled_models(before, after)
    assert [(c["kind"], c["before"], c["after"]) for c in result["changes"]] == [
        ("rating_changed", 600, 60)
    ]
    after["resolutions"][0]["needs_review"] = True
    result = compare_reconciled_models(before, after)
    assert result["changes"] == []
    assert result["unknowns"][0]["kind"] == "attribute_not_comparable"


def test_graph_diff_never_overwrites_observed_with_proposed_or_requirements():
    before = model(
        [node("a", "52-12")],
        [{**value("a", 600), "state": "observed", "side": "baseline"}],
    )
    after = model(
        [node("b", "52-12")],
        [
            {**value("b", 600), "state": "observed", "side": "target"},
            {**value("b", 800), "state": "proposed", "side": "target"},
            {**value("b", 900), "state": "observed", "side": "requirements"},
        ],
    )
    assert not compare_reconciled_models(before, after)["changes"]
    after["resolutions"].append(
        {**value("b", 1000), "state": "observed", "side": "baseline"}
    )
    result = compare_reconciled_models(before, after)
    assert not result["changes"]
    assert any(u["kind"] == "attribute_not_comparable" for u in result["unknowns"])
    assert not compare_reconciled_models(
        before, after, before_side="baseline", after_side="target"
    )["changes"]


def test_unique_provisional_delta_is_visible_separately_from_confirmed_changes():
    before = model(
        [node("a", "52-12")],
        [{**value("a", 600, review=True), "status": "provisional"}],
    )
    after = model(
        [node("b", "52-12")], [{**value("b", 60, review=True), "status": "provisional"}]
    )
    result = compare_reconciled_models(before, after)
    assert not result["changes"]
    assert len(result["candidate_changes"]) == 1
    assert result["candidate_changes"][0]["needs_review"] is True
    after["resolutions"][0]["conflicting_claim_ids"] = ["other-claim"]
    assert not compare_reconciled_models(before, after)["candidate_changes"]


def test_source_local_positions_and_alias_candidates_do_not_match():
    before = model(
        [node("position-1", None, local=True)],
        [value("position-1", 600)],
        complete=True,
    )
    after = model(
        [node("position-1", None, local=True)], [value("position-1", 60)], complete=True
    )
    assert (
        compare_reconciled_models(before, after, matches={"position-1": "position-1"})[
            "changes"
        ]
        == []
    )
    before = model([node("a", "52-12")], [value("a", 600)])
    after = model([node("b", "BKR-12")], [value("b", 60)])
    assert compare_reconciled_models(before, after, matches={"a": "b"})["changes"] == []


def test_duplicate_identity_never_becomes_phantom_added_or_removed():
    before = model([node("a", "52-12")], complete=True)
    after = model([node("b", "52-12"), node("c", "52-12")], complete=True)
    result = compare_reconciled_models(before, after)
    assert not result["changes"]
    assert result["unknowns"][0]["kind"] == "ambiguous_identity"


def test_unique_tag_in_different_lineup_is_never_a_rating_correspondence():
    before_node, after_node = node("a", "52-12"), node("b", "52-12")
    before_node["lineup_id"], after_node["lineup_id"] = "lineup-A", "lineup-B"
    result = compare_reconciled_models(
        model([before_node], [value("a", 600)]), model([after_node], [value("b", 60)])
    )
    assert not result["identity_matches"]
    assert not result["changes"]
    assert not result["candidate_changes"]
    after_node["lineup_id"] = "lineup-A"
    result = compare_reconciled_models(
        model([before_node], [value("a", 600)]), model([after_node], [value("b", 60)])
    )
    assert len(result["changes"]) == 1


def test_connection_diff_requires_complete_opposite_issue_and_endpoint_identity():
    before_nodes = [
        node("a", "52-12"),
        node("t1", "52-12:X1", kind="terminal"),
        node("n1", "N1", kind="connectivity_node"),
    ]
    after_nodes = [
        node("b", "52-12"),
        node("t2", "52-12:X1", kind="terminal"),
        node("n2", "N1", kind="connectivity_node"),
    ]
    edge = {
        "id": "wire-1",
        "kind": "terminal_connectivity_node",
        "from": "t2",
        "to": "n2",
    }
    before, after = (
        model(before_nodes, complete=False),
        model(after_nodes, edges=[edge], complete=True),
    )
    result = compare_reconciled_models(before, after)
    assert not result["changes"]
    assert any(u["kind"] == "possible_connection_added" for u in result["unknowns"])
    before["coverage"]["connectivity_complete"] = True
    result = compare_reconciled_models(before, after)
    assert [c["kind"] for c in result["changes"]] == ["connection_added"]


def pdf_case(tmp_path, claimed_value=600, *, bbox=None, review=False):
    path = tmp_path / "source.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 65), "600A", fontsize=18)
    page.insert_text((250, 180), "UNMODELLED 52-99", fontsize=15)
    doc.save(path)
    doc.close()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    source = {
        "source_id": "drawing",
        "sha256": digest,
        "page": 1,
        "bbox": bbox or [0.09, 0.13, 0.25, 0.24],
    }
    graph = model([node("a", "52-12")], [value("a", claimed_value, review=review)])
    graph["source_hashes"] = {"drawing": digest}
    graph["claims"] = [
        {
            "id": "claim-1",
            "subject_id": "a",
            "feature": "rated_current",
            "value": claimed_value,
            "raw_value": f"{claimed_value}A",
            "source": source,
        }
    ]
    graph["resolutions"][0]["supporting_claim_ids"] = ["claim-1"]
    return path, graph


def test_synthesis_explains_exact_value_but_preserves_unmodelled_ink(tmp_path):
    path, graph = pdf_case(tmp_path)
    result = synthesize_and_compare(
        graph, path, "drawing", 1, tmp_path / "evidence", ocr=False, max_dimension=800
    )
    assert 0 < result["spatial_ink_coverage"] < 0.7
    assert result["unexplained_regions"]
    assert result["equipment_extraction_recall"] is None
    assert result["independent_unexplained_region_recall"]["recall"] is None
    assert (tmp_path / "evidence/reconstructed.svg").is_file()
    assert (tmp_path / "evidence/canonical-model.png").is_file()


def test_wrong_60a_does_not_explain_source_600a(tmp_path):
    path, graph = pdf_case(tmp_path, claimed_value=60)
    result = synthesize_and_compare(
        graph, path, "drawing", 1, tmp_path / "wrong", ocr=False, max_dimension=800
    )
    assert result["matched_anchors"] == 0
    assert result["strictly_explained_ink_pixels"] == 0


def test_wrong_normalization_cannot_match_through_retained_raw_600a(tmp_path):
    path, graph = pdf_case(tmp_path, claimed_value=60)
    graph["claims"][0]["raw_value"] = "600A"
    result = synthesize_and_compare(
        graph,
        path,
        "drawing",
        1,
        tmp_path / "wrong-normalized",
        ocr=False,
        max_dimension=800,
    )
    assert result["matched_anchors"] == 0
    assert result["strictly_explained_ink_pixels"] == 0
    assert result["claim_anchor_diagnostics"]["raw_numeric_candidate_rejections"] == 1


def test_large_bbox_and_unresolved_values_cannot_erase_the_page(tmp_path):
    path, graph = pdf_case(tmp_path, bbox=[0, 0, 1, 1])
    result = synthesize_and_compare(
        graph, path, "drawing", 1, tmp_path / "large", ocr=False, max_dimension=800
    )
    assert result["strictly_explained_ink_pixels"] == 0
    assert result["tentative_text_ink_pixels"] > 0
    graph["resolutions"][0]["needs_review"] = True
    result = synthesize_and_compare(
        graph, path, "drawing", 1, tmp_path / "review", ocr=False, max_dimension=800
    )
    assert result["strictly_explained_ink_pixels"] == 0


def test_hash_binding_and_independent_key_requirement(tmp_path):
    path, graph = pdf_case(tmp_path)
    altered = deepcopy(graph)
    altered["source_hashes"]["drawing"] = "0" * 64
    with pytest.raises(ValueError, match="hash-bound"):
        synthesize_and_compare(altered, path, "drawing", 1, tmp_path / "bad", ocr=False)
    with pytest.raises(ValueError, match="independently labelled"):
        synthesize_and_compare(
            graph,
            path,
            "drawing",
            1,
            tmp_path / "unfrozen",
            ocr=False,
            region_key={"label_source": "model", "regions": []},
        )


def test_ruled_table_proposals_are_independent_of_model_boxes():
    ink = Image.new("L", (400, 300), 0)
    draw = ImageDraw.Draw(ink)
    for x in (70, 120, 170, 220, 270):
        draw.line((x, 40, x, 250), fill=255, width=1)
    for y in (40, 90, 140, 190, 250):
        draw.line((70, y, 270, y), fill=255, width=1)
    proposals, total = _table_strips(ink, ink)
    assert total >= 4
    assert proposals and all(
        p["equipment_identity_established"] is False for p in proposals
    )
    assert {p["proposal_method"] for p in proposals} == {
        "independent_parallel_source_lines"
    }


def test_ocr_orientation_maps_back_to_original_physical_coordinates(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        index = len(calls)
        calls.append(index)
        header = "level\tleft\ttop\twidth\theight\tconf\ttext\n"
        row = "5\t10\t20\t20\t30\t99\t600A\n" if index == 1 else "5\t0\t0\t1\t1\t0\tx\n"
        return SimpleNamespace(returncode=0, stdout=(header + row).encode())

    monkeypatch.setattr(
        "backend.equipment.evidence.synthesis.shutil.which", lambda _: "tesseract"
    )
    monkeypatch.setattr("backend.equipment.evidence.synthesis.subprocess.run", fake_run)
    words, warning = _ocr(Image.new("RGB", (200, 100), "white"), timeout=10)
    assert not warning and len(calls) == 4
    assert words[0]["rotation_ccw"] == 90
    assert words[0]["bbox"] == pytest.approx([0.75, 0.1, 0.9, 0.3])
