"""Drawing reader integrity controls. These fixtures do not measure real accuracy."""

import hashlib
import io
from copy import deepcopy
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from backend.equipment.evidence.drawing_reader import (
    DrawingReading,
    _drawing_images,
    _original,
    _source_orientation,
    build_drawing_graph,
    drawing_graph_candidates,
    endpoint_association,
    path_ink_evidence,
    read_drawing_evidence,
    run_drawing_stage,
)


@pytest.fixture(autouse=True)
def database():
    """Pure source tests must not rebuild the shared application database."""
    yield


def reading(*, interface=True, complete=True):
    objects = [
        {
            "key": "bus",
            "kind": "busbar",
            "raw_tag": None,
            "circuit_label": None,
            "section_label": "SECTION 7",
            "raw_label": "MAIN BUS",
            "position_state": "populated",
            "properties": [],
            "confidence": "high",
            "bbox": [0.1, 0.1, 0.2, 0.3],
        }
    ]
    wires = []
    if interface:
        objects.append(
            {
                "key": "port",
                "kind": "external_interface",
                "raw_tag": None,
                "circuit_label": None,
                "section_label": "SECTION 7",
                "raw_label": "TO REMOTE ASSEMBLY",
                "position_state": "future",
                "properties": [],
                "confidence": "high",
                "bbox": [0.65, 0.1, 0.8, 0.3],
            }
        )
        wires.append(
            {
                "key": "path-1",
                "from_object": "bus",
                "to_object": "port",
                "section_label": "SECTION 7",
                "raw_label": None,
                "kind": "conductor",
                "path": [{"x": 0.2, "y": 0.2}, {"x": 0.65, "y": 0.2}],
                "connected_visible": True,
                "confidence": "high",
                "bbox": [0.19, 0.19, 0.66, 0.21],
            }
        )
    return {
        "identity": {
            "lineup_name": "UNIT TEST ASSEMBLY",
            "drawing_number": "TEST-1",
            "sheet_number": "1",
            "issue_date": None,
            "status_wording": "DRAFT",
            "confidence": "high",
            "bbox": [0.8, 0.8, 0.99, 0.99],
        },
        "body_bbox": [0.05, 0.05, 0.8, 0.7],
        "sections": [
            {
                "label": "SECTION 7",
                "label_bbox": [0.1, 0.5, 0.3, 0.55],
                "fully_visible": True,
                "enumeration_complete": complete,
                "confidence": "high",
                "bbox": [0.05, 0.05, 0.8, 0.7],
            }
        ],
        "objects": objects,
        "conductors": wires,
        "labels": [],
        "unknown_regions": [],
    }


def report(source="a", *, interface=True, complete=True, supported=True):
    raw = reading(interface=interface, complete=complete)
    for wire in raw["conductors"]:
        wire["pixel_evidence"] = {
            "supported": supported,
            "electrical_connection_verified": False,
        }
        wire["endpoint_evidence"] = endpoint_association(
            wire, {o["key"]: o for o in raw["objects"]}
        )
    return {
        "source_id": source,
        "sha256": ("a" if source == "a" else "b") * 64,
        "prompt_sha256": "c" * 64,
        "pages": [{"page": 1, "reading": raw}],
    }


def lineage():
    return {
        "verified": True,
        "lineup_identity": "independently-cited-test-lineage",
        "source_hashes": {"a": "a" * 64, "b": "b" * 64},
        "source_refs": [
            {
                "source_id": value,
                "sha256": value * 64,
                "page": 1,
                "bbox": [0.8, 0.8, 0.99, 0.99],
            }
            for value in ("a", "b")
        ],
    }


def test_source_paths_have_independent_ink_and_no_blank_shortcuts():
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(100, 100), (900, 100)], fill="black", width=3)
    supported = path_ink_evidence(image, [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}])
    shortcut = path_ink_evidence(image, [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.9}])
    assert supported["supported"] is True
    assert shortcut["supported"] is False
    assert supported["electrical_connection_verified"] is False


def test_endpoint_ownership_cannot_use_wrong_or_giant_symbol():
    raw = reading()
    wire = raw["conductors"][0]
    objects = {o["key"]: o for o in raw["objects"]}
    assert endpoint_association(wire, objects)["supported"]
    objects["port"]["bbox"] = [0.8, 0.8, 0.9, 0.9]
    assert not endpoint_association(wire, objects)["supported"]
    objects["port"]["bbox"] = [0, 0, 1, 1]
    assert not endpoint_association(wire, objects)["supported"]


def test_rotation_maps_all_source_boxes_and_conductor_points():
    transformed = _original(
        {"label_bbox": [0.1, 0.2, 0.4, 0.5], "path": [{"x": 0.1, "y": 0.2}]}, 90
    )
    assert transformed["label_bbox"] == pytest.approx([0.2, 0.6, 0.5, 0.9])
    assert transformed["path"][0] == pytest.approx({"x": 0.2, "y": 0.9})


def test_invalid_boxes_and_duplicate_source_keys_are_rejected():
    bad = reading()
    bad["sections"][0]["label_bbox"] = [0, 0.2, 1, 0.1]
    with pytest.raises(ValidationError):
        DrawingReading.model_validate(bad)


def test_straight_conductor_zero_thickness_is_valid_not_point_geometry():
    raw = reading()
    raw["conductors"][0]["bbox"] = [0.2, 0.2, 0.65, 0.2]
    DrawingReading.model_validate(raw)
    raw["conductors"][0]["bbox"] = [0.2, 0.2, 0.2, 0.2]
    with pytest.raises(ValidationError):
        DrawingReading.model_validate(raw)
    bad = reading()
    bad["objects"].append(deepcopy(bad["objects"][0]))
    with pytest.raises(ValidationError):
        DrawingReading.model_validate(bad)


def test_observed_path_graph_is_provisional_and_never_installed_or_released():
    graph = build_drawing_graph(report(), scope_id="test", lineup_lineage=lineage())
    assert len([n for n in graph["nodes"] if n["kind"] == "terminal"]) == 2
    assert len([n for n in graph["nodes"] if n["kind"] == "connectivity_node"]) == 1
    assert all(
        n["metadata"].get("printed_pin") is None
        for n in graph["nodes"]
        if n["kind"] == "terminal"
    )
    assert all(r["needs_review"] for r in graph["resolutions"])
    assert not graph["coverage"]["connectivity_complete"]
    assert not graph["metadata"]["physical_topology_verified"]
    interface = next(
        n
        for n in graph["nodes"]
        if n["metadata"].get("source_symbol_kind") == "external_interface"
    )
    assert not interface["metadata"]["installation_state_verified"]
    assert graph["metadata"]["topology_issues"] == []
    assert not any(c["feature"] == "present" for c in graph["claims"])


def test_unsupported_path_is_retained_unknown_not_network():
    graph = build_drawing_graph(report(supported=False), scope_id="test")
    assert not any(n["kind"] == "connectivity_node" for n in graph["nodes"])
    assert len(graph["metadata"]["unresolved_conductors"]) == 1


def test_cited_lineage_is_required_for_source_local_section_correspondence():
    before = build_drawing_graph(report(interface=False), scope_id="test")
    after = build_drawing_graph(report("b"), scope_id="test")
    result = drawing_graph_candidates(before, after)
    assert result["candidate_changes"] == []
    assert len(result["unknowns"]) == 2
    invalid = lineage()
    invalid["source_refs"][0]["sha256"] = "f" * 64
    bad_graph = build_drawing_graph(report(), scope_id="test", lineup_lineage=invalid)
    assert next(n for n in bad_graph["nodes"] if n["kind"] == "lineup")["metadata"][
        "source_local"
    ]


def test_generic_named_connection_delta_is_candidate_only():
    before = build_drawing_graph(
        report(interface=False),
        scope_id="test",
        lineup_lineage=lineage(),
        side="baseline",
    )
    after = build_drawing_graph(report("b"), scope_id="test", lineup_lineage=lineage())
    result = drawing_graph_candidates(before, after)
    assert result["confirmed_changes"] == []
    assert len(result["candidate_changes"]) == 1
    item = result["candidate_changes"][0]
    assert item["kind"] == "drawing_connection_added"
    assert item["source_label"] == "TO REMOTE ASSEMBLY"
    assert item["needs_review"] and not item["engineering_approved"]


def test_partial_section_estimate_does_not_emit_addition():
    before = build_drawing_graph(
        report(interface=False, complete=False),
        scope_id="test",
        lineup_lineage=lineage(),
    )
    after = build_drawing_graph(report("b"), scope_id="test", lineup_lineage=lineage())
    result = drawing_graph_candidates(before, after)
    assert result["candidate_changes"] == []
    assert len(result["unknowns"]) == 1


def test_assembly_fault_rating_never_becomes_device_interrupting_rating():
    raw = report()
    raw["pages"][0]["reading"]["objects"][0]["properties"] = [
        {
            "feature": "assembly_sccr_ka",
            "value": 65,
            "unit": "kA",
            "raw_text": "SCCR 65 kA",
            "bbox": [0.1, 0.1, 0.15, 0.15],
            "confidence": "high",
        }
    ]
    graph = build_drawing_graph(raw, scope_id="test")
    claim = next(c for c in graph["claims"] if c["feature"] == "assembly_sccr_ka")
    assert claim["raw_value"] == "SCCR 65 kA"
    assert claim["value"] is None and not claim["normalization"]["valid"]
    assert not any(c["feature"] == "interrupting_ka" for c in graph["claims"])


def test_source_hash_is_checked_before_provider(tmp_path):
    path = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(path)
    source = {
        "id": "a",
        "name": "source.pdf",
        "path": str(path),
        "role": "drawing",
        "revision": "draft",
        "sha256": "0" * 64,
    }
    with pytest.raises(ValueError, match="hash mismatch"):
        read_drawing_evidence(
            source,
            [1],
            output_dir=tmp_path / "report",
            provider=lambda *_a, **_k: pytest.fail("Must not call provider."),
        )
    source["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = []

    def provider(*_a, **_k):
        calls.append(True)
        return reading(), {}

    first = read_drawing_evidence(
        source, [1], output_dir=tmp_path / "report", provider=provider
    )
    second = read_drawing_evidence(
        source, [1], output_dir=tmp_path / "report", provider=provider
    )
    assert len(calls) == 1
    assert not first["pages"][0]["cache_hit"] and second["pages"][0]["cache_hit"]


def test_first_day_remote_policy_and_native_source_are_explicit(tmp_path):
    source = {
        "id": "a",
        "name": "source.pdf",
        "path": str(tmp_path / "source.pdf"),
        "role": "drawing",
        "revision": "draft",
        "sha256": "a" * 64,
    }
    result = run_drawing_stage(
        source, [1], scope_id="test", output_dir=tmp_path / "report", deadline=0
    )
    assert result["status"] == "not_processed_remote_vision_not_authorized"
    source["path"] = str(tmp_path / "source.dwg")
    result = run_drawing_stage(
        source, [1], scope_id="test", output_dir=tmp_path / "report", deadline=0
    )
    assert result["status"] == "unsupported_source_type_use_native_reader"


def test_first_day_timeout_does_not_silently_lose_pages(tmp_path, monkeypatch):
    import time

    from backend.equipment import package_reading

    monkeypatch.setenv("OPENAI_API_KEY", "test-not-an-api-key")
    monkeypatch.setattr(
        package_reading,
        "_call_with_budget",
        lambda *_a, **_k: {"status": "timed_out", "error_type": "DeadlineExceeded"},
    )
    source = {
        "id": "a",
        "name": "source.pdf",
        "path": str(tmp_path / "source.pdf"),
        "role": "drawing",
        "revision": "draft",
        "sha256": "a" * 64,
    }
    result = run_drawing_stage(
        source,
        [1, 2, 3],
        scope_id="test",
        output_dir=tmp_path / "report",
        deadline=time.monotonic() + 10,
        allow_remote=True,
        max_pages=1,
    )
    assert result["status"] == "partial_requires_human_verification"
    assert result["deferred_pages"] == [2, 3]
    assert result["errors"] == [
        {"page": 1, "status": "timed_out", "error_type": "DeadlineExceeded"}
    ]
    assert result["graphs"] == []


def test_four_way_orientation_is_source_ocr_ranked_without_expected_text(monkeypatch):
    from backend.equipment.evidence import drawing_reader

    lengths = iter((1, 20, 2, 3))
    monkeypatch.setattr(drawing_reader.shutil, "which", lambda _name: "/test/tesseract")

    def run(*_a, **_k):
        words = "\n".join("90\tSOURCE" for _ in range(next(lengths)))
        return SimpleNamespace(stdout=("conf\ttext\n" + words).encode())

    monkeypatch.setattr(drawing_reader.subprocess, "run", run)
    rotation, evidence = _source_orientation(Image.new("RGB", (200, 100), "white"))
    assert rotation == 90
    assert evidence["status"] == "selected_source_ocr_orientation"
    assert len(evidence["scores"]) == 4


def test_drawing_claims_share_original_page_ancestry_with_other_readers():
    from backend.equipment.evidence.schema import stable_id

    graph = build_drawing_graph(
        report(),
        scope_id="test",
        source_context={"original_sha256": "f" * 64, "page_map": {"1": 194}},
    )
    expected = stable_id("original-page-ancestry", "f" * 64, 194, None, None)
    assert {c["independence_group"] for c in graph["claims"]} == {expected}


def test_fallback_raster_uses_zoom_not_matrix_rotation(monkeypatch):
    from backend.equipment.evidence import drawing_reader

    monkeypatch.setattr(
        drawing_reader, "_images", lambda *_a: ([], 0, "orientation_not_established")
    )
    monkeypatch.setattr(
        drawing_reader,
        "_source_orientation",
        lambda *_a: (90, {"status": "selected_source_ocr_orientation"}),
    )
    with pymupdf.open() as document:
        page = document.new_page(width=200, height=100)
        images, rotation, warning, _evidence = _drawing_images(page)
    with Image.open(io.BytesIO(images[0][1])) as overview:
        assert overview.size == (300, 600)
    assert rotation == 90 and warning is None
