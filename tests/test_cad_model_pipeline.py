"""Authentic vendor-sample fidelity is separate from manufacturer accuracy."""

import hashlib
import json
from pathlib import Path

import pytest

from backend.equipment.comparison import compare_packages
from backend.equipment.model import compile_model
from backend.equipment.readers import read_source
from backend.equipment.real_evaluation import evaluate_package
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    """These source/model adapters do not use or rebuild a database."""
    yield


def native_source():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "benchmarks/real-equipment-v2/cad-sources.json").read_text()
    )
    spec = manifest["sources"][0]
    path = (root / "benchmarks/real-equipment-v2" / spec["path"]).resolve()
    if not path.is_file():
        pytest.skip("Authentic ignored Autodesk source is not present locally.")
    return SourceInput(
        **{k: spec[k] for k in ("id", "name", "role", "revision", "sha256")},
        path=str(path),
    )


def test_authentic_vendor_xml_reaches_model_with_catalog_anchors():
    source = native_source()
    report, facts = read_source(source)
    assert report["cad_report"]["imported_counts"]["components"] == 13
    assert report["cad_report"]["imported_counts"]["connections"] == 22
    assert (
        report["cad_report"]["imported_counts"]["connections_with_unknown_endpoints"]
        == 4
    )
    assert len(facts) == 13
    comparison = compare_packages([], [source])
    model = compile_model(comparison, "a" * 64, "Unspecified", source.revision)
    assert model["observed_device_count"] == 13
    assert {d["tag"] for d in model["devices"]} == {
        "A17",
        "A18",
        "A19",
        "A20",
        "A21",
        "A22",
        "CS1",
        "SEL1",
        "TD-5",
        "TD-5:1",
        "TD-5:2",
        "TD-7",
        "XFMR1",
    }
    for device in model["devices"]:
        for value in device["fields"]["part_number"]["build"]:
            assert value["source"]["location"]["xml_path"].startswith(
                "/HarnessData/Harness[1]/Connector["
            )
            assert value["source"]["sha256"] == source.sha256
        assert device["connectivity"]["status"] == "unknown"
        assert device["dimensions"]["status"] == "unknown"
    assert model["coverage"]["connectivity_reconstructed"] is False
    assert model["coverage"]["geometry_validated"] is False
    assert model["production_released"] is False


def test_harness_keeps_declared_connections_but_does_not_claim_check_accuracy(tmp_path):
    source = native_source()
    spec = {
        k: getattr(source, k)
        for k in ("id", "name", "path", "role", "revision", "sha256")
    }
    result = evaluate_package(
        {
            "id": "authentic-vendor-xml",
            "kind": "vendor_sample",
            "sources": [{**spec, "side": "target"}],
        },
        tmp_path / "manifest.json",
        include_vector=False,
    )
    assert result["scores"] is None
    observed = result["equipment_model"]["source_observations"]["cad_reports"][0]
    assert len(observed["connections"]) == 22
    assert sum(x["status"] == "unknown_endpoint" for x in observed["connections"]) == 4
    assert all(x["geometry_verified"] is False for x in observed["connections"])
    assert result["equipment_model"]["engineering_approved"] is False


def test_synthetic_connection_declarations_never_become_native_geometry(tmp_path):
    data = b'<HarnessData><Harness><Connector RefDes="BKR-12"><Property Name="CAT" Value="P1"/><Pin Definition="1"/></Connector><Wire Name="W1"><Pin1 ConnectorRefDes="BKR-12" ConnectorPin="1"/><Pin2 ConnectorRefDes="BKR-12" ConnectorPin="2"/></Wire></Harness></HarnessData>'
    path = tmp_path / "synthetic.xml"
    path.write_bytes(data)
    source = SourceInput(
        id="control",
        name=path.name,
        path=str(path),
        role="drawing",
        revision="X",
        sha256=hashlib.sha256(data).hexdigest(),
    )
    comparison = compare_packages([], [source])
    model = compile_model(comparison, "b" * 64, "Unspecified", "X")
    assert (
        comparison["target"]["sources"][0]["cad_report"]["connections"][0]["status"]
        == "reported"
    )
    assert model["native"] is None
    assert model["coverage"]["connectivity_reconstructed"] is False
    assert model["devices"][0]["connectivity"]["status"] == "unknown"
