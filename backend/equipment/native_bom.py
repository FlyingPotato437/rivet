"""Real BOM inventory mutations and a second saved-file inventory check."""

from __future__ import annotations

import csv
import io
from copy import copy
from pathlib import Path
from zipfile import ZipFile

from openpyxl import load_workbook

from backend.drafting.service import DraftingError, _safe_value

from .readers import (
    HEADER_MAP,
    _key,
    _model_local_identity,
    _source_local_identity,
    clean_tag,
    normalize_value,
)


def _metadata(pairs):
    result = {}
    for key, value in pairs:
        result.setdefault(_key(key), []).append(value)
    return result


def _bound_tag(raw, metadata):
    """Accept opaque IDs only with one complete, local model binding."""
    declared_tags = [
        value
        for key, values in metadata.items()
        if HEADER_MAP.get(key) == "device_tag"
        for value in values
    ]
    is_local = any(
        str(value or "").casefold().startswith("drawing:") for value in declared_tags
    ) or "source_local" in metadata.get("identityscope", [])
    is_model_local = any(
        str(value or "").casefold().startswith("model:") for value in declared_tags
    ) or "model_local" in metadata.get("identityscope", [])
    if not is_local and not is_model_local:
        return clean_tag(raw)
    identity = (
        _model_local_identity(raw, metadata)
        if is_model_local
        else _source_local_identity(raw, metadata)
    )
    if (
        identity is None
        or declared_tags != [raw]
        or any(len(values) != 1 for values in metadata.values())
    ):
        raise DraftingError(
            (
                "A model-local inventory ID"
                if is_model_local
                else "A source-local inventory ID"
            )
            + " needs one exact equipment tag, scope, model, version and device binding on its own block or row."
        )
    return raw


def _attributes(drawing):
    result = {}
    for insert in drawing.modelspace().query("INSERT"):
        pairs = [(a.dxf.tag, a.dxf.text) for a in insert.attribs]
        tag = _bound_tag(insert.get_attrib_text("EQUIPMENT_TAG"), _metadata(pairs))
        if not tag:
            continue
        if tag in result:
            raise DraftingError(
                "Duplicate native equipment tags prevent BOM reconciliation."
            )
        result[tag] = {key.upper(): value for key, value in pairs}
    return result


def _proposed_model_binding(raw, metadata):
    """A saved-file identity binding, never an engineering-value assertion."""

    def one(key):
        values = metadata.get(key, [])
        return values[0] if len(values) == 1 else None

    if one("proposalstatus") != "PROPOSED":
        return None
    scope = one("identityscope")
    if scope == "model_local":
        identity = _model_local_identity(raw, metadata)
    elif scope == "source_local":
        identity = _source_local_identity(raw, metadata)
    elif scope == "declared_model":
        declared_tags = [
            value
            for key, values in metadata.items()
            if HEADER_MAP.get(key) == "device_tag"
            for value in values
        ]
        model_id, version, device_id = (
            one("modelid"),
            one("modelversion"),
            one("modeldeviceid"),
        )
        if (
            declared_tags != [raw]
            or one("equipmenttag") != raw
            or clean_tag(raw) is None
            or not isinstance(model_id, str)
            or not model_id.strip()
            or model_id.strip().casefold() in {"unknown", "none", "null"}
            or not isinstance(device_id, str)
            or not device_id.strip()
            or not isinstance(version, (str, int))
            or isinstance(version, bool)
            or not str(version).strip()
            or str(version).strip().casefold() in {"unknown", "none", "null"}
        ):
            return None
        identity = {
            "identity_scope": scope,
            "model_id": model_id,
            "model_version": str(version),
            "model_device_id": device_id,
        }
    else:
        return None
    return (
        (
            identity["identity_scope"],
            identity["model_id"],
            identity["model_version"],
            identity["model_device_id"],
        )
        if identity
        else None
    )


def _unknown_representation(value):
    return (
        value is None
        or isinstance(value, str)
        and value.strip().casefold() in {"", "unknown"}
    )


# Only fields emitted by the proposed-model exporter may use its blank/UNKNOWN
# representation contract. Unknowns never enter the known-fact reader output.
PROPOSED_UNKNOWN_FIELDS = frozenset(
    {
        "sourcelabel",
        "sectionlabel",
        "device_frame",
        "frame_a",
        "trip_a",
        "trip_unit",
        "pole_count",
        "interrupting_ka",
        "catalogsccrka",
        "part_number",
        "nameplate",
        "sectionid",
        "positionid",
        "manufacturer",
        "product_family",
    }
)


def _table(state):
    def eligible(rows):
        if not rows:
            return False
        return sum(
            HEADER_MAP.get(_key(h)) == "device_tag" for h in rows[0]
        ) == 1 and any(HEADER_MAP.get(_key(h)) == "part_number" for h in rows[0])

    if state[0] == "csv":
        rows = state[1]
        if not eligible(rows):
            raise DraftingError(
                "The BOM requires unique equipment-tag and part-number columns."
            )
        return None, rows
    if any(s.max_row > 10000 or s.max_column > 100 for s in state[1]):
        raise DraftingError(
            "The native BOM exceeds the supported complete table dimensions."
        )
    options = [(s, [list(row) for row in s.values]) for s in state[1]]
    options = [(s, rows) for s, rows in options if eligible(rows)]
    if len(options) != 1:
        raise DraftingError(
            "Select one unambiguous equipment BOM table for inventory edits."
        )
    sheet, rows = options[0]
    return sheet, rows


def _index(rows):
    headers = rows[0]
    mapped = [HEADER_MAP.get(_key(h), _key(h)) for h in headers]
    if len(set(mapped)) != len(mapped) or any(
        not str(h or "").strip() for h in headers
    ):
        raise DraftingError(
            "The equipment BOM needs unique, nonempty column identities."
        )
    tag_column = mapped.index("device_tag")
    indices = {}
    for i, row in enumerate(rows[1:], 2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        if len(row) != len(headers):
            raise DraftingError("The BOM contains an incomplete or irregular item row.")
        tag = _bound_tag(row[tag_column], _metadata(zip(headers, row, strict=True)))
        if not tag or tag in indices:
            raise DraftingError("Every BOM item needs one exact unique device tag.")
        indices[tag] = i
    return mapped, indices


def _require_literal_inventory_identity(identity_profile, identity_source_catalog):
    if identity_profile is not None or identity_source_catalog is not None:
        raise DraftingError(
            "Native inventory edits and verification require exact literal tags; profile-based alias resolution is not enabled for this path."
        )


def apply_inventory(
    state, requests, drawing, *, identity_profile=None, identity_source_catalog=None
):
    """Change only explicitly added/deleted device rows, using actual CAD attrs."""
    _require_literal_inventory_identity(identity_profile, identity_source_catalog)
    operations = [r for r in requests if r["kind"] in {"add_device", "remove_device"}]
    if not operations:
        return []
    sheet, rows = _table(state)
    mapped, index = _index(rows)
    original_rows = [list(row) for row in rows[1:]]
    if sheet is not None and any(
        c.data_type == "f" for s in state[1] for row in s for c in row
    ):
        raise DraftingError(
            "Structural BOM edits require review when the workbook contains formulas."
        )
    attributes = _attributes(drawing)
    changes = []
    # Remove descending so all other source row contents stay byte-for-value.
    removals = [r for r in operations if r["kind"] == "remove_device"]
    for request in sorted(
        removals, key=lambda r: index.get(r["device_tag"], -1), reverse=True
    ):
        tag = request["device_tag"]
        if tag not in index or tag in attributes:
            raise DraftingError(
                "A removed device must exist in the original BOM and be absent from the actual corrected DXF."
            )
        position = index[tag]
        before = rows[position - 1]
        if sheet is None:
            del state[1][position - 1]
        else:
            sheet.delete_rows(position)
        changes.append(
            {
                "kind": "bom_remove_device",
                "device_tag": tag,
                "before": before,
                "after": None,
                "evidence": request["evidence"],
            }
        )
    for request in [r for r in operations if r["kind"] == "add_device"]:
        tag = request["device_tag"]
        if tag in index or tag not in attributes:
            raise DraftingError(
                "An added BOM item must be a new device actually present in the corrected DXF."
            )
        attrs = attributes[tag]
        values = {HEADER_MAP.get(_key(k), _key(k)): v for k, v in attrs.items()}
        part_column = mapped.index("part_number")
        part = values.get("part_number")
        _, _, part_error = normalize_value("part_number", part)
        templates = [
            row
            for row in original_rows
            if not part_error
            and len(row) == len(mapped)
            and str(row[part_column] or "").strip()
            and str(row[part_column]) == str(part)
        ]
        row = []
        for col, field in enumerate(mapped):
            if field not in values:
                candidates = {str(r[col] or "") for r in templates}
                same_part_identity = (
                    field in {"manufacturer", "product_family"}
                    and len(candidates) == 1
                    and all(str(r[col] or "").strip() for r in templates)
                )
                explicit_binding = field in {
                    "identityscope",
                    "modelid",
                    "modelversion",
                    "modeldeviceid",
                    "sourcelabel",
                    "sectionlabel",
                    "sourcereferences",
                    "sourceid",
                    "sourcesha256",
                    "sourcepage",
                }
                if (
                    len(candidates) != 1
                    or explicit_binding
                    or field in HEADER_MAP.values()
                    and not same_part_identity
                ):
                    raise DraftingError(
                        f"The new device has no explicit value or unambiguous same-part template for BOM column {field}."
                    )
                value = next(iter(candidates))
            else:
                value = str(values[field])
            _safe_value(value)
            row.append(value)
        if sheet is None:
            state[1].append(row)
        else:
            position = sheet.max_row + 1
            for col, value in enumerate(row, 1):
                cell = sheet.cell(position, col, value)
                if position > 2:
                    previous = sheet.cell(position - 1, col)
                    cell._style = copy(previous._style)
                    cell.number_format = previous.number_format
        changes.append(
            {
                "kind": "bom_add_device",
                "device_tag": tag,
                "before": None,
                "after": dict(zip(mapped, row)),
                "evidence": request["evidence"],
            }
        )
    return changes


def _verify_inventory(drawing_path, bom_path):
    import ezdxf

    if not 0 < Path(bom_path).stat().st_size <= 30 * 1024 * 1024:
        raise DraftingError(
            "The native BOM source is empty or exceeds the supported file size."
        )
    drawing = ezdxf.readfile(drawing_path)
    attrs = _attributes(drawing)
    native_bindings = {}
    for insert in drawing.modelspace().query("INSERT"):
        pairs = [
            (attribute.dxf.tag, attribute.dxf.text) for attribute in insert.attribs
        ]
        metadata = _metadata(pairs)
        raw_tag = insert.get_attrib_text("EQUIPMENT_TAG")
        bound_tag = _bound_tag(raw_tag, metadata)
        if bound_tag:
            native_bindings[bound_tag] = _proposed_model_binding(raw_tag, metadata)
    if Path(bom_path).suffix.lower() == ".csv":
        rows = []
        for row in csv.reader(
            io.StringIO(Path(bom_path).read_text(encoding="utf-8-sig"))
        ):
            if len(rows) >= 10000 or len(row) > 100:
                raise DraftingError(
                    "The native BOM exceeds the supported complete table dimensions."
                )
            rows.append(row)
        state = ("csv", rows)
    else:
        with ZipFile(bom_path) as archive:
            if (
                len(archive.infolist()) > 2000
                or sum(i.file_size for i in archive.infolist()) > 64 * 1024 * 1024
            ):
                raise DraftingError(
                    "The BOM workbook exceeds the supported expansion limit."
                )
        state = ("xlsx", load_workbook(bom_path, data_only=False, keep_links=False))
    try:
        _, rows = _table(state)
        mapped, index = _index(rows)
        checks = [
            {
                "id": "native_bom_inventory",
                "label": "Drawing and BOM device inventory",
                "status": "pass" if set(attrs) == set(index) else "fail",
                "reason": "Exact device tags in saved drawing and saved BOM must agree.",
                "drawing_tags": sorted(attrs),
                "bom_tags": sorted(index),
            }
        ]
        for tag in sorted(set(attrs) & set(index)):
            native = {
                HEADER_MAP.get(_key(k), _key(k)): v for k, v in attrs[tag].items()
            }
            row = rows[index[tag] - 1]
            row_metadata = _metadata(zip(rows[0], row, strict=True))
            row_tag = row[mapped.index("device_tag")]
            row_binding = _proposed_model_binding(row_tag, row_metadata)
            same_model = bool(row_binding) and row_binding == native_bindings.get(tag)
            for col, field in enumerate(mapped):
                if field == "device_tag" or field not in native:
                    continue
                unknown_pair = (
                    same_model
                    and field in PROPOSED_UNKNOWN_FIELDS
                    and _unknown_representation(row[col])
                    and _unknown_representation(native[field])
                )
                if unknown_pair:
                    matches = True
                elif field in {
                    "part_number",
                    "quantity",
                    "frame_a",
                    "trip_a",
                    "trip_unit",
                    "nameplate",
                    "control_voltage_v",
                    "interrupting_ka",
                }:
                    actual, unit, error = normalize_value(field, row[col])
                    expected, expected_unit, expected_error = normalize_value(
                        field, native[field]
                    )
                    matches = (
                        not error
                        and not expected_error
                        and (actual, unit) == (expected, expected_unit)
                    )
                else:
                    matches = str(row[col]) == native[field]
                checks.append(
                    {
                        "id": f"native_bom:{tag}:{field}",
                        "label": f"{tag} · {field.replace('_', ' ')}",
                        "status": "pass" if matches else "fail",
                        "reason": "Both saved files explicitly retain an unknown value using the proposed-model blank/UNKNOWN representation; no engineering value is verified."
                        if unknown_pair
                        else "Read independently from the saved DXF attribute and BOM cell.",
                        "knowledge_status": "unknown"
                        if unknown_pair
                        else "compared_literal_values",
                        "engineering_value_known": False if unknown_pair else None,
                        "scope": "saved_file_representation_consistency"
                        if unknown_pair
                        else "saved_file_literal_consistency",
                    }
                )
        return {
            "status": "verified"
            if all(c["status"] == "pass" for c in checks)
            else "blocked",
            "checks": checks,
            "unknown_fields": [
                check["id"]
                for check in checks
                if check.get("knowledge_status") == "unknown"
            ],
            "scope": "Saved drawing/BOM representation consistency only; matching unknowns do not establish known values or engineering conformance.",
            "engineering_approved": False,
        }
    finally:
        if state[0] == "xlsx":
            state[1].close()


def verify_inventory(
    drawing_path, bom_path, *, identity_profile=None, identity_source_catalog=None
):
    _require_literal_inventory_identity(identity_profile, identity_source_catalog)
    try:
        return _verify_inventory(drawing_path, bom_path)
    except DraftingError:
        raise
    except Exception as exc:
        raise DraftingError(
            "The original drawing or BOM could not be read completely for native inventory reconciliation."
        ) from exc
