"""Independent source facts from saved generated files, not model projections."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import ezdxf
from openpyxl import load_workbook

from backend.equipment.readers import read_source
from backend.equipment.schemas import SourceInput

from .generation import SYSTEM_FIELDS


def read_generated_inventory(*, drawing_path, bom_path, model_id, model_version):
    result = {}
    for role, filename in (("drawing", drawing_path), ("bom", bom_path)):
        path = Path(filename)
        if not path.is_file() or path.is_symlink():
            raise ValueError("Generated source is unavailable for independent reading.")
        checksum = sha256(path.read_bytes()).hexdigest()
        source = SourceInput(
            id="generated-" + role + ":" + checksum[:16],
            name=path.name,
            role=role,
            revision=str(model_version),
            path=str(path),
            sha256=checksum,
        )
        report, facts = read_source(source)
        # Keep native-reader coverage and uncertainty; do not turn missing data
        # into invented facts just because it was present in the input design.
        scalar_facts = []
        if role == "drawing":
            drawing = ezdxf.readfile(path)
            for insert in drawing.modelspace().query("INSERT"):
                pairs = [(a.dxf.tag, a.dxf.text) for a in insert.attribs]
                if not any(k == "SYS_SCHEMA" for k, _ in pairs):
                    continue
                attrs = dict(pairs)
                if len(attrs) != len(pairs):
                    raise ValueError("Duplicate system metadata fields in saved DXF.")
                if (
                    attrs.get("SYS_SCHEMA") != "rivet.engineering-system.v1"
                    or attrs.get("SYS_MODEL_ID") != model_id
                    or attrs.get("SYS_MODEL_VERSION") != str(model_version)
                ):
                    raise ValueError(
                        "Saved DXF system metadata has a different model binding."
                    )
                for field in SYSTEM_FIELDS:
                    tag = "SYS_" + field.upper()
                    if tag not in attrs:
                        raise ValueError("Saved DXF system metadata is incomplete.")
                    scalar_facts.append(
                        (
                            field,
                            json.loads(attrs[tag]),
                            {"entity_handle": insert.dxf.handle, "attribute": tag},
                        )
                    )
        else:
            wb = load_workbook(path, read_only=True, data_only=False)
            try:
                if "System requirements" in wb.sheetnames:
                    sheet = wb["System requirements"]
                    rows = list(sheet.values)
                    expected = [
                        "Field",
                        "Value JSON",
                        "Unit",
                        "Model ID",
                        "Model version",
                        "Source references",
                    ]
                    if not rows or list(rows[0]) != expected:
                        raise ValueError("Saved system table headers changed.")
                    seen = set()
                    for row_index, row in enumerate(rows[1:], 2):
                        if not any(v is not None for v in row):
                            continue
                        field, raw, _, saved_id, saved_version, _ = row
                        if field not in SYSTEM_FIELDS or field in seen:
                            raise ValueError("Saved system table has ambiguous fields.")
                        seen.add(field)
                        if saved_id != model_id or str(saved_version) != str(
                            model_version
                        ):
                            raise ValueError(
                                "Saved BOM system metadata has a different model binding."
                            )
                        if (
                            not isinstance(raw, str)
                            or sheet.cell(row_index, 2).data_type == "f"
                        ):
                            raise ValueError(
                                "Saved system scalar must be literal JSON, never a formula."
                            )
                        scalar_facts.append(
                            (
                                field,
                                json.loads(raw),
                                {"sheet": sheet.title, "row": row_index, "column": 2},
                            )
                        )
                    if seen != set(SYSTEM_FIELDS):
                        raise ValueError("Saved system table omits fields.")
            finally:
                wb.close()
        for field, value, evidence in scalar_facts:
            facts.append(
                {
                    "id": checksum[:16] + ":" + field + ":" + str(len(facts)),
                    "scope": "system",
                    "field": field,
                    "value": value,
                    "source_id": source.id,
                    "sha256": checksum,
                    "role": role,
                    "model_id": model_id,
                    "model_version": str(model_version),
                    "evidence": evidence,
                }
            )
        result[role] = {"report": report, "facts": facts}
        if sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError("Generated source changed during independent reading.")
    return result
