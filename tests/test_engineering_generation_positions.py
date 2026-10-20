"""Actual file controls for synthetic source-local and prepared positions."""

import copy

import ezdxf
import pytest
from openpyxl import load_workbook

from backend.engineering.generation import generate_package
from backend.equipment.native_model import inspect_native


@pytest.fixture(autouse=True)
def database():
    """These export controls use temporary files only, without a database."""
    yield


@pytest.fixture
def synthetic_position_design():
    devices = [
        {
            "id": f"drawing:{section}:7",
            "tag": f"drawing:{section}:7",
            "display_tag": "7",
            "section_label": section,
            "source_local_identity": True,
            "position_state": state,
            "role": "feeder",
            "frame_a": 100,
            "trip_a": 60,
            "pole_count": 3,
            "section_id": f"S-{section}",
            "position_id": f"P-{section}",
            "supplied": None,
        }
        for section, state in (("A", "prepared"), ("B", "future"))
    ]
    devices.append(
        {
            "id": "synthetic-unknown-position",
            "tag": "SYNTHETIC-UNKNOWN",
            "role": "feeder",
            "section_id": "S-C",
            "position_id": "P-C",
            "supplied": None,
        }
    )
    return {
        "model_id": "SYNTHETIC-POSITION-EXPORT-CONTROL",
        "model_version": "1",
        "order_name": "Synthetic drawing position fixture",
        "sections": [
            {
                "id": f"S-{section}",
                "kind": "distribution",
                "positions": [{"id": f"P-{section}", "device_id": device["id"]}],
            }
            for section, device in zip(("A", "B", "C"), devices, strict=True)
        ],
        "devices": devices,
    }


def _drawing_devices(path):
    document = ezdxf.readfile(path)
    inserts = {
        insert.get_attrib_text("EQUIPMENT_TAG"): insert
        for insert in document.modelspace().query("INSERT")
        if insert.has_attrib("EQUIPMENT_TAG")
    }
    assert len(inserts) == 3
    return document, inserts


def _has_dashed_symbol(document, insert):
    for entity in document.blocks[insert.dxf.name]:
        if entity.dxftype() not in {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE"}:
            continue
        linetype = entity.dxf.get("linetype", "BYLAYER").upper()
        if linetype == "BYBLOCK":
            linetype = insert.dxf.get("linetype", "BYLAYER").upper()
        if linetype == "BYLAYER":
            linetype = document.layers.get(entity.dxf.layer).dxf.linetype.upper()
        if "DASH" in linetype or "HIDDEN" in linetype:
            return True
    return False


def _visible_text(document):
    text = [entity.dxf.text for entity in document.modelspace().query("TEXT")]
    text += [
        attribute.dxf.text
        for insert in document.modelspace().query("INSERT")
        for attribute in insert.attribs
        if not attribute.is_invisible
    ]
    return " ".join(text)


def test_dxf_preserves_internal_identity_and_displays_literal_source_positions(
    tmp_path, synthetic_position_design
):
    before = copy.deepcopy(synthetic_position_design)
    package = tmp_path / "source-positions"

    generate_package(synthetic_position_design, output_dir=package)

    for filename in ("one-line.dxf", "front-elevation.dxf"):
        document, inserts = _drawing_devices(package / filename)
        assert set(inserts) == {"drawing:A:7", "drawing:B:7", "SYNTHETIC-UNKNOWN"}
        for section, state in (("A", "prepared"), ("B", "future")):
            insert = inserts[f"drawing:{section}:7"]
            assert insert.get_attrib_text("SOURCE_LABEL") == "7"
            assert insert.get_attrib_text("SECTION_LABEL") == section
            assert insert.get_attrib_text("IDENTITY_SCOPE") == "source_local"
            assert insert.get_attrib_text("POSITION_STATE") == state
            assert insert.get_attrib("EQUIPMENT_TAG").is_invisible
            assert not insert.get_attrib("SOURCE_LABEL").is_invisible
            assert _has_dashed_symbol(document, insert)
        visible = _visible_text(document)
        assert "P.S." in visible and "FUTURE" in visible
        assert "drawing:A:7" not in visible and "drawing:B:7" not in visible
        assert (
            inserts["SYNTHETIC-UNKNOWN"].get_attrib_text("POSITION_STATE").casefold()
            == "unknown"
        )
        native = inspect_native(package / filename)
        assert {device["tag"] for device in native["devices"]} == set(inserts)
    assert synthetic_position_design == before


def test_bom_retains_source_labels_scope_and_position_states_without_supply_guess(
    tmp_path, synthetic_position_design
):
    package = tmp_path / "position-bom"

    generate_package(synthetic_position_design, output_dir=package)

    workbook = load_workbook(package / "bom.xlsx", read_only=True, data_only=True)
    try:
        records = workbook["Equipment BOM"].iter_rows(values_only=True)
        headers = next(records)
        rows = {row[0]: dict(zip(headers, row, strict=True)) for row in records}
    finally:
        workbook.close()
    assert len(rows) == 3
    for section, state in (("A", "prepared"), ("B", "future")):
        row = rows[f"drawing:{section}:7"]
        assert row["source_label"] == "7"
        assert row["section_label"] == section
        assert row["identity_scope"] == "source_local"
        assert row["position_state"] == state
        assert row["quantity"] == 1
        assert row["supplied"] == "unknown"
    assert rows["SYNTHETIC-UNKNOWN"]["position_state"].casefold() == "unknown"
