"""Generated source-local inventory binding controls; no source accuracy claims."""

import csv
import shutil
from pathlib import Path

import ezdxf
import pytest
from openpyxl import load_workbook

from backend.drafting.service import DraftingError
from backend.engineering.generation import generate_package
from backend.equipment.native_bom import _attributes, _index, verify_inventory
from backend.equipment.readers import clean_tag


@pytest.fixture(autouse=True)
def database():
    yield


def _model():
    devices = []
    for index, (letter, section) in enumerate(zip("ab", "AB", strict=True), 1):
        tag = "drawing:" + letter * 32
        devices.append(
            {
                "id": tag,
                "tag": tag,
                "display_tag": "7",
                "section_label": section,
                "source_local_identity": True,
                "position_state": "populated_breaker",
                "role": "feeder",
                "section_id": "S1",
                "position_id": f"P{index}",
                "manufacturer": "SYNTHETIC",
                "family": "TEST",
                "frame": "J",
                "frame_a": 250,
                "trip_a": 225,
                "trip_type": "LI",
                "pole_count": 3,
                "interrupting_ka": 65,
                "catalog_sccr_ka": 65,
                "part_number": f"SYNTHETIC-{index}",
                "nameplate": f"SYNTHETIC FEEDER {index}",
                "supplied": None,
            }
        )
    return {
        "model_id": "synthetic-inventory-binding",
        "model_version": "1",
        "sections": [{"id": "S1", "positions": []}],
        "devices": devices,
    }


@pytest.fixture(scope="module")
def generated_originals(tmp_path_factory):
    result = generate_package(
        _model(), output_dir=tmp_path_factory.mktemp("source-local-native-originals")
    )
    paths = {item["kind"]: Path(item["path"]) for item in result["artifacts"]}
    return paths["one_line_dxf"], paths["bom_xlsx"]


@pytest.fixture
def generated(generated_originals, tmp_path):
    paths = tuple(tmp_path / path.name for path in generated_originals)
    for source, destination in zip(generated_originals, paths, strict=True):
        shutil.copyfile(source, destination)
    return paths


def _device_inserts(path):
    doc = ezdxf.readfile(path)
    inserts = [
        insert for insert in doc.modelspace().query("INSERT")
        if insert.has_attrib("EQUIPMENT_TAG")
    ]
    return doc, inserts


def _rows(path):
    book = load_workbook(path)
    try:
        return [list(row) for row in book.worksheets[0].values]
    finally:
        book.close()


def test_actual_generated_source_local_dxf_bom_inventory_is_verified(generated):
    drawing, bom = generated
    result = verify_inventory(drawing, bom)
    assert result["status"] == "verified"
    inventory = result["checks"][0]
    expected = sorted(d["tag"] for d in _model()["devices"])
    assert inventory["drawing_tags"] == inventory["bom_tags"] == expected
    assert len(expected) == 2  # Both literal circuit 7 positions survive independently.
    assert all(check["status"] == "pass" for check in result["checks"])
    assert clean_tag(expected[0]) is None


@pytest.mark.parametrize(
    "attribute", ["IDENTITY_SCOPE", "MODEL_ID", "MODEL_VERSION", "MODEL_DEVICE_ID"]
)
def test_each_missing_dxf_binding_is_rejected(generated, attribute):
    drawing, bom = generated
    doc, inserts = _device_inserts(drawing)
    inserts[0].delete_attrib(attribute)
    doc.saveas(drawing)
    with pytest.raises(DraftingError, match="source-local inventory ID"):
        verify_inventory(drawing, bom)


@pytest.mark.parametrize(
    "column", ["identity_scope", "model_id", "model_version", "model_device_id"]
)
def test_each_missing_bom_row_binding_is_rejected_without_forward_fill(generated, column):
    drawing, bom = generated
    book = load_workbook(bom)
    sheet = book.worksheets[0]
    headers = [cell.value for cell in sheet[1]]
    sheet.cell(3, headers.index(column) + 1, "")
    book.save(bom)
    book.close()
    with pytest.raises(DraftingError, match="source-local inventory ID"):
        verify_inventory(drawing, bom)


@pytest.mark.parametrize(
    "mutation", ["duplicate-model", "duplicate-tag", "alias-tag", "wrong-device", "wrong-scope"]
)
def test_duplicate_or_conflicting_dxf_binding_is_rejected(generated, mutation):
    drawing, bom = generated
    doc, inserts = _device_inserts(drawing)
    insert = inserts[0]
    if mutation == "duplicate-model":
        insert.add_attrib("MODEL_ID", "another-model")
    elif mutation == "duplicate-tag":
        insert.add_attrib("EQUIPMENT_TAG", insert.get_attrib_text("EQUIPMENT_TAG"))
    elif mutation == "alias-tag":
        insert.add_attrib("TAG", "CB-1")
    elif mutation == "wrong-device":
        insert.get_attrib("MODEL_DEVICE_ID").dxf.text = "drawing:" + "c" * 32
    else:
        insert.get_attrib("IDENTITY_SCOPE").dxf.text = "enterprise"
    doc.saveas(drawing)
    with pytest.raises(DraftingError, match="source-local inventory ID"):
        verify_inventory(drawing, bom)


@pytest.mark.parametrize("column", ["model_id", "model_version"])
def test_drawing_bom_model_binding_must_agree(generated, column):
    drawing, bom = generated
    book = load_workbook(bom)
    sheet = book.worksheets[0]
    headers = [cell.value for cell in sheet[1]]
    sheet.cell(2, headers.index(column) + 1, "different")
    book.save(bom)
    book.close()
    result = verify_inventory(drawing, bom)
    assert result["status"] == "blocked"
    assert any(
        check["status"] == "fail" and check["id"].endswith(column.replace("_", ""))
        for check in result["checks"]
    )


def test_duplicate_bom_identity_column_and_duplicate_rows_are_rejected(generated):
    _, bom = generated
    rows = _rows(bom)
    model_index = rows[0].index("model_id")
    duplicate_columns = [row + [row[model_index]] for row in rows]
    with pytest.raises(DraftingError, match="unique, nonempty column"):
        _index(duplicate_columns)
    with pytest.raises(DraftingError, match="exact unique device tag"):
        _index(rows + [rows[1]])


def test_duplicate_dxf_position_is_rejected(generated):
    drawing, _ = generated
    doc, inserts = _device_inserts(drawing)
    doc.modelspace().add_entity(inserts[0].copy())
    with pytest.raises(DraftingError, match="Duplicate native equipment tags"):
        _attributes(doc)


def test_csv_rows_use_the_same_exact_model_binding(generated, tmp_path):
    drawing, bom = generated
    rows = _rows(bom)
    path = tmp_path / "inventory.csv"
    with path.open("w", newline="") as stream:
        csv.writer(stream).writerows(rows)
    assert verify_inventory(drawing, path)["status"] == "verified"
    rows[2][rows[0].index("model_id")] = ""
    with path.open("w", newline="") as stream:
        csv.writer(stream).writerows(rows)
    with pytest.raises(DraftingError, match="source-local inventory ID"):
        verify_inventory(drawing, path)


def test_enterprise_tags_keep_existing_rules():
    assert clean_tag("cb-1") == "CB-1"
    mapped, index = _index([["equipment_tag", "part_number"], ["cb-1", "SYNTHETIC"]])
    assert mapped == ["device_tag", "part_number"]
    assert index == {"CB-1": 2}
    with pytest.raises(DraftingError, match="source-local inventory ID"):
        _index([["equipment_tag", "part_number"], ["drawing:" + "a" * 32, "SYNTHETIC"]])


def _add_device_without_native_field(rows, *, missing="manufacturer", part="SYNTHETIC-PART"):
    from backend.equipment.native_bom import apply_inventory

    drawing = ezdxf.new("R2010")
    drawing.blocks.new("SYNTHETIC_ADDITION")
    insert = drawing.modelspace().add_blockref("SYNTHETIC_ADDITION", (0, 0))
    values = {
        "EQUIPMENT_TAG": "KA-3",
        "PART_NUMBER": part,
        "QUANTITY": "1",
        "TRIP_A": "225",
        "FRAME_A": "250",
        "POLE_COUNT": "3",
        "MANUFACTURER": "SYNTHETIC",
        "PRODUCT_FAMILY": "TEST",
        "MODEL_ID": "explicit-new-model",
    }
    field_attribute = {
        "manufacturer": "MANUFACTURER", "product_family": "PRODUCT_FAMILY",
        "trip_a": "TRIP_A", "frame_a": "FRAME_A", "pole_count": "POLE_COUNT",
        "model_id": "MODEL_ID",
    }
    values.pop(field_attribute[missing])
    for key, value in values.items():
        insert.add_attrib(key, value)
    state = ("csv", rows)
    changes = apply_inventory(
        state, [{"kind": "add_device", "device_tag": "KA-3", "evidence": {}}], drawing
    )
    return state, changes


@pytest.mark.parametrize("field", ["manufacturer", "product_family"])
def test_nonempty_agreeing_original_same_part_identity_can_be_retained(field):
    rows = [
        ["equipment_tag", "part_number", field],
        ["KA-1", "SYNTHETIC-PART", "SYNTHETIC IDENTITY"],
        ["KA-2", "SYNTHETIC-PART", "SYNTHETIC IDENTITY"],
    ]
    state, changes = _add_device_without_native_field(rows, missing=field)
    assert state[1][-1] == ["KA-3", "SYNTHETIC-PART", "SYNTHETIC IDENTITY"]
    assert changes[0]["after"][field] == "SYNTHETIC IDENTITY"


def test_ambiguous_original_same_part_manufacturers_are_rejected():
    rows = [
        ["equipment_tag", "part_number", "manufacturer"],
        ["KA-1", "SYNTHETIC-PART", "SYNTHETIC A"],
        ["KA-2", "SYNTHETIC-PART", "SYNTHETIC B"],
    ]
    with pytest.raises(DraftingError, match="BOM column manufacturer"):
        _add_device_without_native_field(rows)


@pytest.mark.parametrize("part", ["", " ", "unknown", "DIFFERENT-PART"])
def test_no_manufacturer_inheritance_without_exact_nonempty_known_part(part):
    rows = [
        ["equipment_tag", "part_number", "manufacturer"],
        ["KA-1", "SYNTHETIC-PART", "SYNTHETIC"],
    ]
    with pytest.raises(DraftingError, match="BOM column manufacturer"):
        _add_device_without_native_field(rows, part=part)


@pytest.mark.parametrize("empty", [None, "", " "])
def test_empty_candidate_manufacturer_is_not_inherited(empty):
    rows = [
        ["equipment_tag", "part_number", "manufacturer"],
        ["KA-1", "SYNTHETIC-PART", "SYNTHETIC"],
        ["KA-2", "SYNTHETIC-PART", empty],
    ]
    with pytest.raises(DraftingError, match="BOM column manufacturer"):
        _add_device_without_native_field(rows)


@pytest.mark.parametrize("field", ["trip_a", "frame_a", "pole_count", "model_id"])
def test_safety_values_and_model_binding_cannot_inherit_from_same_part(field):
    rows = [
        ["equipment_tag", "part_number", field],
        ["KA-1", "SYNTHETIC-PART", "225" if field != "model_id" else "foreign-model"],
    ]
    with pytest.raises(DraftingError, match="BOM column"):
        _add_device_without_native_field(rows, missing=field)


@pytest.mark.parametrize("operation", ["apply", "verify"])
@pytest.mark.parametrize("argument", ["identity_profile", "identity_source_catalog"])
def test_direct_profile_inventory_paths_fail_with_explicit_guard(operation, argument):
    from backend.equipment.native_bom import apply_inventory

    with pytest.raises(DraftingError, match="profile-based alias resolution is not enabled"):
        if operation == "apply":
            apply_inventory(("csv", []), [], None, **{argument: {}})
        else:
            verify_inventory("not-read.dxf", "not-read.xlsx", **{argument: {}})
