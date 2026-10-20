"""Synthetic export controls; no real sources or engineering accuracy claims."""

import copy

import ezdxf
import pytest
from openpyxl import load_workbook

from backend.engineering.generation import generate_package


@pytest.fixture(autouse=True)
def database():
    """Package generation uses only temporary files, without a database."""
    yield


@pytest.fixture
def synthetic_design():
    return {
        "model_id": "SYNTHETIC-REUSED-MAIN-EXPORT",
        "model_version": "1",
        "order_name": "Synthetic reuse identity export control",
        "manufacturer": "Synthetic proposed lineup manufacturer",
        "family": "Synthetic proposed lineup family",
        "sections": [
            {
                "id": "SYNTHETIC-S1",
                "kind": "main",
                "positions": [{"id": "SYNTHETIC-P1", "device_id": "MAIN"}],
            }
        ],
        "devices": [
            {
                "id": "MAIN",
                "tag": "SYNTHETIC-EXISTING-MAIN",
                "role": "main",
                "section_id": "SYNTHETIC-S1",
                "position_id": "SYNTHETIC-P1",
                "frame_a": 1200,
                "trip_a": 1000,
                "pole_count": 3,
                "supplied": False,
            }
        ],
    }


def _exported_identity(package_dir):
    """Read the actual CAD attributes and workbook cells written for the main."""
    drawings = {}
    for filename in ("one-line.dxf", "front-elevation.dxf"):
        drawing = ezdxf.readfile(package_dir / filename)
        devices = [
            insert
            for insert in drawing.modelspace().query("INSERT")
            if insert.has_attrib("EQUIPMENT_TAG")
            and insert.get_attrib_text("EQUIPMENT_TAG") == "SYNTHETIC-EXISTING-MAIN"
        ]
        assert len(devices) == 1
        drawings[filename] = {
            "manufacturer": devices[0].get_attrib_text("MANUFACTURER"),
            "family": devices[0].get_attrib_text("PRODUCT_FAMILY"),
        }
    workbook = load_workbook(package_dir / "bom.xlsx", read_only=True, data_only=True)
    try:
        rows = workbook["Equipment BOM"].iter_rows(values_only=True)
        headers = next(rows)
        matching = [
            dict(zip(headers, row, strict=True))
            for row in rows
            if row[headers.index("equipment_tag")] == "SYNTHETIC-EXISTING-MAIN"
        ]
        assert len(matching) == 1
        bom = {
            "manufacturer": matching[0]["mfr"],
            "family": matching[0]["product_family"],
        }
    finally:
        workbook.close()
    return drawings, bom


@pytest.mark.parametrize(
    "reuse_marker",
    [{"reuse_existing": True}, {"product_origin": "existing_source_equipment"}],
    ids=["reuse-existing-flag", "existing-source-origin"],
)
@pytest.mark.parametrize(
    "source_identity",
    [{}, {"manufacturer": None, "family": None}, {"manufacturer": "", "family": ""}],
    ids=["missing", "null", "empty"],
)
def test_reused_main_unknown_identity_stays_unknown_in_dxf_and_bom(
    tmp_path, synthetic_design, reuse_marker, source_identity
):
    main = synthetic_design["devices"][0]
    main.update(reuse_marker)
    main.update(source_identity)
    before = copy.deepcopy(synthetic_design)
    package = tmp_path / "unknown-source-identity"

    generate_package(synthetic_design, output_dir=package)

    drawings, bom = _exported_identity(package)
    assert all(
        identity == {"manufacturer": "UNKNOWN", "family": "UNKNOWN"}
        for identity in drawings.values()
    )
    assert bom == {"manufacturer": None, "family": None}
    assert synthetic_design == before


@pytest.mark.parametrize(
    "reuse_marker",
    [{"reuse_existing": True}, {"product_origin": "existing_source_equipment"}],
    ids=["reuse-existing-flag", "existing-source-origin"],
)
@pytest.mark.parametrize(
    "source_identity",
    [
        {
            "manufacturer": "Synthetic original main manufacturer",
            "family": "Synthetic original main family",
        },
        {"manufacturer": "Synthetic original main manufacturer", "family": None},
        {"manufacturer": None, "family": "Synthetic original main family"},
    ],
    ids=["complete-source-identity", "source-manufacturer-only", "source-family-only"],
)
def test_reused_main_preserves_each_known_source_identity_field_in_dxf_and_bom(
    tmp_path, synthetic_design, reuse_marker, source_identity
):
    synthetic_design["devices"][0].update({**reuse_marker, **source_identity})
    package = tmp_path / "known-source-identity"

    generate_package(synthetic_design, output_dir=package)

    drawings, bom = _exported_identity(package)
    expected_drawing = {
        key: value if value else "UNKNOWN" for key, value in source_identity.items()
    }
    assert all(identity == expected_drawing for identity in drawings.values())
    assert bom == source_identity
