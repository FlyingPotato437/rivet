"""Typed model keeps desired, proposed and actual equipment state separate."""

from copy import deepcopy

import pytest

from backend.equipment.comparison import compare_packages
from backend.equipment.demo_documents import build_demo_corpus
from backend.equipment.model import EquipmentModel, compile_model


@pytest.fixture(scope="module")
def comparison(tmp_path_factory):
    root = tmp_path_factory.mktemp("equipment-model")
    manifest = build_demo_corpus(root)
    sources = {
        item["id"]: {**item, "path": str(root / item["path"])}
        for item in manifest["sources"]
    }
    return compare_packages(
        [sources["01-baseline-C.drawing.dxf"], sources["01-baseline-C.bom.xlsx"]],
        [sources["02-partial-D.drawing.dxf"], sources["02-partial-D.bom.xlsx"]],
        [sources["requirements.pdf"]],
    )


def device(model, tag="CB-12"):
    return next(item for item in model["devices"] if item["tag"] == tag)


def test_model_separates_specified_from_actual_and_preserves_conflicts(comparison):
    model = compile_model(comparison, "a" * 64, "C", "D")
    validated = EquipmentModel.model_validate(model)
    assert validated.version == "a" * 64
    trip = device(model)["fields"]["trip_unit"]
    assert {item["value"] for item in trip["specified"]} == {"TU-3000"}
    assert {item["value"] for item in trip["build"]} == {"TU-2500", "TU-3000"}
    assert trip["status"] == "mismatch" and trip["agreement"] == "conflicting"
    assert {item["source"]["role"] for item in trip["build"]} == {"drawing", "bom"}
    assert all(
        item["source"]["location"] and len(item["source"]["sha256"]) == 64
        for item in trip["build"]
    )
    assert model["revisionId"] == "D"
    assert model["observed_device_count"] == 3


def test_native_coordinates_do_not_invent_parent_connections_or_dimensions(comparison):
    model = compile_model(comparison, "b" * 64, "C", "D")
    for item in model["devices"]:
        for name in ("parent", "connectivity", "dimensions"):
            assert item[name]["status"] == "unknown"
            assert item[name]["observations"] == []
    assert model["coverage"]["connectivity_reconstructed"] is False
    assert model["coverage"]["complete_inventory_verified"] is False
    assert (
        model["engineering_approved"] is False and model["production_released"] is False
    )


def test_explicit_native_relationship_is_observation_not_an_inferred_graph(comparison):
    raw = deepcopy(comparison)
    fact = deepcopy(
        next(
            item
            for item in raw["target"]["facts"]
            if item["role"] == "drawing" and item["device_tag"] == "CB-12"
        )
    )
    fact.update(
        id="explicit-parent", field="parent_tag", value="MSB-1", raw_value="MSB-1"
    )
    fact["evidence"]["attribute"] = "PARENT_TAG"
    raw["target"]["facts"].append(fact)
    model = compile_model(raw, "c" * 64, "C", "D")
    assert device(model)["parent"]["status"] == "observed"
    assert device(model)["parent"]["observations"][0]["value"] == "MSB-1"
    assert device(model)["connectivity"]["status"] == "unknown"
    assert model["coverage"]["connectivity_reconstructed"] is False


def test_requests_never_become_specified_or_build_and_version_is_bound(comparison):
    raw = deepcopy(comparison)
    requested = deepcopy(raw["requirements"]["facts"][0])
    requested.update(
        id="requested-not-actual",
        role="markup",
        value="UNAPPROVED-OPTION",
        raw_value="UNAPPROVED-OPTION",
    )
    raw["baseline"]["requested_changes"] = [requested]
    first = compile_model(raw, "d" * 64, "C", "D")
    second = compile_model(raw, "e" * 64, "C", "D-R1")
    field = device(first)["fields"][requested["field"]]
    assert field["requested"][0]["value"] == "UNAPPROVED-OPTION"
    assert all(
        item["value"] != "UNAPPROVED-OPTION"
        for item in [*field["specified"], *field["build"]]
    )
    assert first["version"] != second["version"]
    assert first["revisionId"] != second["revisionId"]


def test_missing_drawing_device_retains_baseline_and_bom_observations(comparison):
    model = compile_model(comparison, "f" * 64, "C", "D")
    trip = device(model, "CB-14")["fields"]["trip_unit"]
    assert trip["status"] == "missing"
    assert {item["source"]["role"] for item in trip["baseline"]} == {"drawing", "bom"}
    assert {item["source"]["role"] for item in trip["build"]} == {"bom"}


def test_internal_paths_not_projected_into_source_anchors(comparison):
    raw = deepcopy(comparison)
    for side in ("baseline", "target", "requirements"):
        for fact in raw[side]["facts"]:
            fact["path"] = "/private/secret.dxf"
            fact["evidence"]["path"] = "/private/secret.dxf"
    model = compile_model(raw, "a" * 64, "C", "D")
    assert "/private/secret.dxf" not in str(model)
