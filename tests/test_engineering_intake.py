"""Explicit role/deadline bridge controls, not real design accuracy."""

from copy import deepcopy

import pytest

from backend.engineering.intake import customer_case, run_customer_intake


@pytest.fixture(autouse=True)
def database():
    yield


def package():
    return {
        "id": "SYNTHETIC-CONTROL",
        "kind": "real_private",
        "sources": [
            {
                "id": "requirements",
                "role": "specification",
                "path": "spec.pdf",
                "sha256": "a" * 64,
            },
            {
                "id": "consultant",
                "role": "drawing",
                "path": "one-line.pdf",
                "sha256": "b" * 64,
            },
            {
                "id": "approved",
                "role": "drawing",
                "path": "manufacturer-approved.pdf",
                "sha256": "c" * 64,
            },
        ],
    }


def test_no_engineering_source_role_is_guessed_from_name_or_legacy_role():
    assert customer_case(package()) is None


def test_only_explicit_customer_selection_enters_requirement_reader():
    source = package()
    before = deepcopy(source)
    source["engineering_inputs"] = [
        {"source_id": "consultant", "role": "customer_one_line", "pages": [3, 5]}
    ]
    case = customer_case(source)
    assert case["sources"] == [
        {**before["sources"][1], "role": "customer_one_line", "pages": [3, 5]}
    ]
    assert source["sources"] == before["sources"]
    assert not case["approved_reconstruction_eligible"]


@pytest.mark.parametrize(
    "selection",
    [
        [{"source_id": "approved", "role": "manufacturer_reference"}],
        [{"source_id": "foreign", "role": "specification"}],
        [{"source_id": "consultant", "role": "customer_one_line"}] * 2,
    ],
)
def test_reference_unknown_and_duplicate_sources_rejected(selection):
    source = package()
    source["engineering_inputs"] = selection
    with pytest.raises(ValueError):
        customer_case(source)


def test_native_cad_customer_inputs_request_pdf_export():
    source = package()
    source["sources"][1]["path"] = "original.dxf"
    source["engineering_inputs"] = [
        {"source_id": "consultant", "role": "customer_one_line"}
    ]
    with pytest.raises(ValueError, match="PDF export"):
        customer_case(source)


def test_local_intake_summary_has_no_accuracy_or_release_claim(tmp_path, monkeypatch):
    from scripts import evaluate_switchboard_reconstruction as runner

    seen = []

    def run(case, manifest, output, **options):
        seen.append((case, options))
        return {
            "requirements": {"items": [], "conflicts": [], "unreadable_pages": []},
            "design": {},
            "artifacts": None,
            "elapsed_seconds": 0.01,
        }

    monkeypatch.setattr(runner, "run_customer_case", run)
    source = package()
    source["engineering_inputs"] = [
        {"source_id": "requirements", "role": "specification"}
    ]
    result = run_customer_intake(
        source, tmp_path / "manifest.json", tmp_path / "first-run"
    )
    assert (
        result["accuracy"] is None
        and not result["engineering_approved"]
        and not result["customer_mail_sent"]
    )
    assert not seen[0][1]["vision"] and not seen[0][1]["read_text"]
    assert (tmp_path / "first-run/prediction-frozen.json").is_file()
    with pytest.raises(ValueError, match="fresh"):
        run_customer_intake(source, tmp_path / "manifest.json", tmp_path / "first-run")
