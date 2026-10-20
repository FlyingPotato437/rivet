"""Independent PDF cases/evaluation math; no database, model call or network."""

from copy import deepcopy
from hashlib import sha256
from urllib.parse import urlsplit

import pymupdf
import pytest

from backend.equipment.pdf_corpus import build_vector_pdf_corpus
from backend.equipment.pdf_evaluation import evaluate_pdf_graph
from scripts.build_pdf_drawing_corpus import SOURCES


@pytest.fixture(scope="module")
def vector_corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("independent-pdf-source-corpus")
    return root, build_vector_pdf_corpus(root)


def test_pdf_fixtures_contain_only_real_visible_text_vectors_and_external_truth(
    vector_corpus,
):
    root, manifest = vector_corpus
    assert len(manifest["fixtures"]) == 10
    for fixture in manifest["fixtures"]:
        path = root / fixture["path"]
        assert fixture["sha256"] == sha256(path.read_bytes()).hexdigest()
        with pymupdf.open(path) as pdf:
            assert pdf.embfile_count() == 0 and not pdf.get_xml_metadata()
            assert len(pdf) == 1 and not pdf[0].get_images()
            assert len(pdf[0].get_drawings()) >= 9
            text = pdf[0].get_text()
            if fixture["id"].startswith("10-"):
                assert text.strip() == ""
                assert fixture["ground_truth"]["unknown_required"]
            else:
                assert all(
                    tag in text for tag in fixture["ground_truth"]["expected_tags"]
                )
            assert fixture["ground_truth"]["physical_dimensions_verified"] is False


def test_rotation_crop_and_crossing_truth_are_independently_explicit(vector_corpus):
    root, manifest = vector_corpus
    fixtures = {fixture["id"]: fixture for fixture in manifest["fixtures"]}
    assert (
        len(fixtures["02-crossing-without-junction"]["ground_truth"]["expected_nets"])
        == 2
    )
    assert (
        len(fixtures["03-crossing-with-junction"]["ground_truth"]["expected_nets"]) == 1
    )
    assert fixtures["05-ambiguous-undotted-branch"]["ground_truth"]["unknown_required"]
    assert fixtures["06-missing-terminal-label"]["ground_truth"]["expected_ports"] == [
        "KA-1:A1",
        "TB-1:1",
        "TB-1:2",
    ]
    with pymupdf.open(root / "08-rotated-page.pdf") as pdf:
        assert pdf[0].rotation == 90 and pdf[0].rect.width == 550
    with pymupdf.open(root / "09-cropped-page.pdf") as pdf:
        assert list(pdf[0].cropbox) == [40, 140, 570, 320]
        assert pdf[0].rect.width == 530 and pdf[0].rect.height == 180


def _crossing_report(joined):
    terminals = ["TB-1:1", "CB-12:TC1", "KA-1:13", "PL-1:X1"]
    return {
        "devices": [
            {
                "tag": terminal.split(":")[0],
                "ports": [{"id": terminal, "label": terminal.split(":")[1]}],
            }
            for terminal in terminals
        ],
        "connections": [{"terminal_ids": terminals}]
        if joined
        else [{"terminal_ids": terminals[:2]}, {"terminal_ids": terminals[2:]}],
        "coverage": {"status": "bounded"},
        "issues": [],
    }


def test_evaluation_exposes_a_falsely_joined_crossing_instead_of_counting_it_correct(
    vector_corpus,
):
    _, manifest = vector_corpus
    truth = manifest["fixtures"][1]["ground_truth"]
    correct = evaluate_pdf_graph(_crossing_report(False), truth)
    assert correct["tags"]["recall"] == 1 and correct["connections"]["precision"] == 1
    mistaken = evaluate_pdf_graph(_crossing_report(True), truth)
    assert mistaken["connections"]["matched"] == 2
    assert len(mistaken["connections"]["unexpected"]) == 4
    assert mistaken["connections"]["recall"] == 1
    assert mistaken["connections"]["precision"] == pytest.approx(1 / 3)


def test_partial_public_annotation_never_claims_whole_sheet_precision():
    truth = {
        "scope": "partial_subset",
        "expected_tags": ["52T1", "52T2"],
        "expected_ports": [],
        "expected_nets": [],
        "unknown_required": True,
        "annotation_page": 19,
    }
    report = {
        "devices": [
            {"tag": "52T1", "page": 19},
            {"tag": "OTHER-1", "page": 19},
            {"tag": "52T2", "page": 2},
        ],
        "connections": [],
        "coverage": {"status": "unknown"},
    }
    result = evaluate_pdf_graph(report, truth)
    assert result["tags"]["recall"] == 0.5
    assert result["tags"]["precision"] is None
    assert result["missed_tag_count"] == 1 and result["tags"]["missed"] == ["52T2"]
    assert result["unscored_prediction_tags"] == ["OTHER-1"]
    assert result["unknown_preserved"]
    false_claim = deepcopy(report)
    false_claim["coverage"]["status"] = "complete"
    assert evaluate_pdf_graph(false_claim, truth)["false_complete_claim"]


def test_geometric_contact_identity_is_unscored_instead_of_a_fictional_pin():
    report = _crossing_report(False)
    report["devices"][1]["ports"][0].update(id="CB-12:contact-123", label=None)
    report["connections"][0]["terminal_ids"][1] = "CB-12:contact-123"
    truth = {
        "expected_tags": ["TB-1", "CB-12", "KA-1", "PL-1"],
        "expected_ports": ["TB-1:1", "KA-1:13", "PL-1:X1"],
        "expected_nets": [["KA-1:13", "PL-1:X1"]],
    }
    result = evaluate_pdf_graph(report, truth)
    assert result["connections"]["precision"] == 1
    assert result["unscored_geometric_connection_pairs"] == [
        ("CB-12:contact-123", "TB-1:1")
    ]


def test_subset_accepts_actual_segment_geometry_and_excludes_outside_segments():
    report = _crossing_report(False)
    report["connections"][0]["segments"] = [{"points": [[10, 20], [80, 20]]}]
    report["connections"][1]["segments"] = [{"points": [[10, 20], [150, 20]]}]
    for device in report["devices"]:
        device["bbox"] = [10, 10, 20, 20]
    truth = {
        "scope": "partial_subset",
        "evaluation_bbox": [0, 0, 100, 100],
        "expected_tags": [],
        "expected_ports": ["TB-1:1", "CB-12:TC1", "KA-1:13", "PL-1:X1"],
        "expected_nets": [["TB-1:1", "CB-12:TC1"]],
    }
    result = evaluate_pdf_graph(report, truth)
    assert result["connections"]["recall"] == 1
    assert result["connections"]["observed"] == 1


def test_public_terminal_label_subset_does_not_create_a_customer_device():
    report = {
        "devices": [],
        "unassigned_terminals": [
            {
                "id": "p1:terminal:13:abc",
                "label": "13",
                "page": 1,
                "position": [10, 20],
            },
            {
                "id": "p1:terminal:15:def",
                "label": "15",
                "page": 1,
                "position": [80, 20],
            },
        ],
        "connections": [
            {
                "page": 1,
                "terminal_ids": ["p1:terminal:13:abc", "p1:terminal:15:def"],
                "segments": [{"points": [[10, 20], [80, 20]]}],
            }
        ],
        "coverage": {"status": "unknown"},
    }
    truth = {
        "scope": "partial_subset",
        "annotation_page": 1,
        "evaluation_bbox": [0, 0, 100, 100],
        "unassigned_terminal_scope": {"namespace": "FIG10", "labels": ["13", "15"]},
        "expected_ports": ["FIG10:13", "FIG10:15"],
        "expected_nets": [["FIG10:13", "FIG10:15"]],
        "unknown_required": True,
    }
    result = evaluate_pdf_graph(report, truth)
    assert result["ports"]["recall"] == result["connections"]["recall"] == 1
    assert result["ports"]["precision"] is None
    assert result["tags"]["observed"] == 0 and result["unknown_preserved"]
    report["unassigned_terminals"].append(
        {"id": "p1:terminal:13:ghi", "label": "13", "page": 1, "position": [90, 20]}
    )
    result = evaluate_pdf_graph(report, truth)
    assert result["ports"]["recall"] == 0.5
    assert result["connections"]["recall"] == 0


def test_official_sources_are_primary_documents_with_explicit_annotation_limits():
    assert len(SOURCES) == 5
    assert {source["publisher"] for source in SOURCES} == {
        "Minnesota Department of Transportation",
        "Schweitzer Engineering Laboratories",
    }
    for source in SOURCES:
        assert urlsplit(source["url"]).hostname in {
            "selinc.com",
            "www.dot.state.mn.us",
            "edocs-public.dot.state.mn.us",
        }
        assert source["ground_truth"]["notes"]
        assert source["subset"] and source["pages"]
