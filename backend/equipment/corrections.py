"""Literal, source-derived corrections in new native copies, followed by recheck.

The caller owns tenant authorization and local storage paths. There are no
model-supplied selectors, edit policies, file names or replacement values.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

import ezdxf
from openpyxl import load_workbook

from backend.drafting.preview import drawing_svg
from backend.drafting.schemas import BomCellEdit, CadAttributeEdit
from backend.drafting.service import (
    DraftingError,
    _bom_table,
    _checked_bytes,
    _edit_bom,
    _exact_insert,
    _safe_value,
    _single_attribute,
)

from .comparison import compare_packages
from .readers import HEADER_MAP, TAG_RE, _key, stable_id
from .rules import evaluate
from .schemas import EQUIPMENT_FIELDS, SourceInput


class CorrectionError(ValueError):
    def __init__(self, message, plan=None):
        super().__init__(message)
        self.plan = plan


def _inputs(values):
    return [
        value if isinstance(value, SourceInput) else SourceInput.model_validate(value)
        for value in values
    ]


def _manifest(sources):
    return [
        {
            key: getattr(source, key)
            for key in ("id", "name", "role", "revision", "sha256", "lifecycle")
        }
        for source in sources
    ]


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def _same(left, right):
    return left["value"] == right["value"] and left.get("unit") == right.get("unit")


def _target_tables(source):
    data = _checked_bytes(Path(source.path), source.sha256)
    if Path(source.name).suffix.casefold() == ".csv":
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        return {"BOM": rows}
    workbook = load_workbook(
        io.BytesIO(data), read_only=True, data_only=False, keep_links=False
    )
    try:
        return {sheet.title: [list(row) for row in sheet.values] for sheet in workbook}
    finally:
        workbook.close()


def _unsupported_requests(sources):
    from .design_requests import extract_requests

    compiled = extract_requests(sources)
    blocked = []
    pattern = re.compile(
        r"\b(?:move|relocate|resize|rewire|reroute|add|delete|remove|rename|rotate)\b[^.\n]{0,100}(?:\b(?:breakers?|devices?|bus|wires?|wiring|relays?|clearance|location|position|connections?|compartments?|feeders?|cabinets?)\b|\b[A-Z0-9]{1,10}[-_]\d+\b)|\b(?:change|revise|update|modify)\b[^.\n]{0,80}\b(?:wiring|topology|connection|clearance|dimensions|geometry|busbar)\b|\b(?:target|requested|desired)\s+(?:position|location|wiring|topology|clearance|geometry|dimensions|height|width)\s*[:=]",
        re.IGNORECASE,
    )
    for source in sources:
        if (
            source.role not in {"markup", "specification"}
            or Path(source.name).suffix.casefold() != ".pdf"
        ):
            continue
        import pymupdf

        try:
            data = _checked_bytes(Path(source.path), source.sha256)
            with pymupdf.open(stream=data, filetype="pdf") as document:
                for index, page in enumerate(document):
                    if index >= 80:
                        break
                    text = page.get_text()
                    native_page = any(
                        r["source_id"] == source.id and r["page"] == index + 1
                        for r in compiled["recognized_lines"]
                    )
                    geometry_text = text
                    display_headings = set()
                    for block in page.get_text("dict").get("blocks", []):
                        for line in block.get("lines", []):
                            spans = line.get("spans", [])
                            if (
                                spans
                                and min(span.get("size", 0) for span in spans) >= 14
                                and any(
                                    "bold" in span.get("font", "").lower()
                                    for span in spans
                                )
                            ):
                                display_headings.add(
                                    " ".join(
                                        span.get("text", "") for span in spans
                                    ).strip()
                                )
                    if source.role == "markup" and len(text.strip()) < 30:
                        blocked.append(
                            {
                                "code": "unreadable_requested_changes",
                                "source_id": source.id,
                                "page": index + 1,
                                "reason": "This returned-change page has insufficient readable text. Establish its requested changes before preparing automatic corrections.",
                            }
                        )
                    for recognized in compiled["recognized_lines"]:
                        if (
                            recognized["source_id"] == source.id
                            and recognized["page"] == index + 1
                        ):
                            text = text.replace(recognized["text"], "")
                            geometry_text = geometry_text.replace(
                                recognized["text"], ""
                            )
                    for requested in re.finditer(
                        r"\b(?:target|requested|desired)[ _]+([^:=\n]{1,80})\s*[:=]",
                        text,
                        re.IGNORECASE,
                    ):
                        field = HEADER_MAP.get(_key(requested[1]))
                        if field not in EQUIPMENT_FIELDS and _key(requested[1]) not in {
                            "revision",
                            "drawingrevision",
                            "jobnumber",
                            "ordernumber",
                            "sheet",
                            "output",
                        }:
                            blocked.append(
                                {
                                    "code": "unsupported_requested_field",
                                    "source_id": source.id,
                                    "page": index + 1,
                                    "reason": f"The explicitly requested field '{requested[1].strip()}' is outside the supported literal correction scope.",
                                }
                            )
                    match = None
                    for candidate in pattern.finditer(geometry_text):
                        start = geometry_text.rfind("\n", 0, candidate.start()) + 1
                        end = geometry_text.find("\n", candidate.end())
                        line = geometry_text[
                            start : end if end >= 0 else len(geometry_text)
                        ].strip()
                        # Unaddressed display headings are not a second action.
                        # Exact targets and sentences remain in executable scope.
                        if (
                            native_page
                            and line in display_headings
                            and not TAG_RE.search(line)
                            and not re.search(r"\bW\d+\b", line, re.IGNORECASE)
                            and not re.search(
                                r"\b(?:all|every|each)\b", line, re.IGNORECASE
                            )
                        ):
                            continue
                        match = candidate
                        break
                    if match:
                        blocked.append(
                            {
                                "code": "unsupported_geometry_request",
                                "source_id": source.id,
                                "page": index + 1,
                                "reason": "A layout/topology or device-addition/deletion request requires a drafter. No geometry or device inventory will be invented.",
                                "excerpt": match[0][:200],
                            }
                        )
        except Exception:  # noqa: BLE001 - unreadable requested changes must fail closed.
            blocked.append(
                {
                    "code": "unreadable_requested_changes",
                    "source_id": source.id,
                    "reason": "A requested-change source could not be inspected safely.",
                }
            )
    return [*blocked, *compiled["blockers"]]


def plan_corrections(
    baseline_sources,
    target_sources,
    requirement_sources=None,
    *,
    identity_profile=None,
    identity_source_catalog=None,
):
    baseline, target, requirements = (
        _inputs(baseline_sources),
        _inputs(target_sources),
        _inputs(requirement_sources or []),
    )
    native = [
        source
        for source in target
        if source.role == "drawing" and Path(source.name).suffix.casefold() == ".dxf"
    ]
    boms = [
        source
        for source in target
        if source.role == "bom"
        and Path(source.name).suffix.casefold() in {".xlsx", ".csv"}
    ]
    blockers, warnings = [], []
    if len(native) != 1 or len(boms) != 1:
        blockers.append(
            {
                "code": "editable_sources_required",
                "reason": "Use exactly one editable native DXF and one XLSX/CSV BOM. A PDF drawing cannot be edited as native CAD.",
            }
        )
    editable = [
        *native,
        *boms,
        *[source for source in target if source.role == "markup"],
    ]
    identity_options = (
        {
            "identity_profile": identity_profile,
            "identity_source_catalog": identity_source_catalog,
        }
        if identity_profile is not None
        else {}
    )
    comparison = compare_packages(baseline, editable, requirements, **identity_options)
    from .identity import IdentityContext, IdentityError

    try:
        identity = IdentityContext(identity_profile, identity_source_catalog)
    except IdentityError:
        identity = None
    blockers.extend(
        {"code": "company_identity_unresolved", "reason": reason}
        for reason in comparison.get("identity_issues", [])
    )
    if identity_profile is not None:
        blockers.append(
            {
                "code": "profile_correction_not_enabled",
                "reason": "Company alias evaluation is available, but alias-aware native/BOM correction is not enabled. Original tags remain unchanged.",
            }
        )
    blockers.extend(_unsupported_requests([*baseline, *target, *requirements]))
    if comparison["summary"]["source_issues"]:
        blockers.append(
            {
                "code": "source_coverage_incomplete",
                "reason": "Unreadable or partially interpreted baseline, native or requirement files prevent a complete bounded correction.",
            }
        )
    if not comparison["coverage"].get("baseline_comparison_available"):
        blockers.append(
            {
                "code": "baseline_required",
                "reason": "A readable baseline drawing and BOM are required for a closed before/after loop.",
            }
        )
    if any(
        source.role == "drawing" and Path(source.name).suffix.casefold() == ".pdf"
        for source in target
    ):
        warnings.append(
            "Original PDF snapshots remain unchanged. The corrected package gets a new preview rendered from the saved native DXF; old PDF copies are not mixed into the new revision."
        )
    edits = []
    native_plan = None
    from .design_requests import extract_requests

    native_requests = extract_requests([*target, *requirements])
    removed_tags = {
        identity.canonical(r["device_tag"]) if identity else r["device_tag"]
        for r in native_requests["requests"]
        if r["kind"] == "remove_device"
    }
    retained_facts = [
        fact
        for fact in comparison["requirements"]["facts"]
        if fact["role"] == "specification"
    ] + [
        fact
        for side in ("baseline", "target", "requirements")
        for fact in comparison[side]["requested_changes"]
    ]
    for tag in sorted(removed_tags):
        if any(fact["device_tag"] == tag for fact in retained_facts):
            blockers.append(
                {
                    "code": "removal_conflicts_with_requirement",
                    "device_tag": tag,
                    "reason": "A retained-device requirement or requested value conflicts with deleting this device. Clarify the governing request before editing.",
                }
            )
    if native_requests["requests"] and len(native) == 1:
        from .native_bom import verify_inventory
        from .native_edits import plan_native_changes
        from .native_model import NativeModelError

        try:
            native_plan = plan_native_changes(
                native[0].path,
                native_requests["requests"],
                expected_sha256=native[0].sha256,
                **identity_options,
            )
        except NativeModelError as exc:
            # A changed or unreadable original invalidates preparation, but the
            # order must still open so its earlier package can be shown stale.
            blockers.append({"code": "native_source_unavailable", "reason": str(exc)})
        if native_plan and native_plan["status"] != "ready":
            blockers.extend(
                {
                    "code": "native_design_check",
                    "reason": c.get(
                        "reason",
                        c.get("label", "The requested design cannot be verified."),
                    ),
                }
                for c in native_plan.get("checks", [])
                if c.get("status") != "pass"
            )
        if len(boms) == 1:
            try:
                inventory = verify_inventory(
                    native[0].path, boms[0].path, **identity_options
                )
                if inventory["status"] != "verified":
                    blockers.append(
                        {
                            "code": "native_bom_source_disagrees",
                            "reason": "The native device inventory and BOM disagree before the requested design change. Reconcile those sources before structural editing.",
                        }
                    )
            except DraftingError as exc:
                blockers.append(
                    {"code": "native_bom_source_unavailable", "reason": str(exc)}
                )
    if len(native) == 1 and len(boms) == 1:
        current_check = {}
        try:
            drawing = ezdxf.read(
                io.StringIO(
                    _checked_bytes(Path(native[0].path), native[0].sha256).decode(
                        "utf-8-sig"
                    )
                )
            )
            tables = _target_tables(boms[0])
            for check in comparison["checks"]:
                current_check = check
                if check["status"] in {"verified", "unchanged"}:
                    continue
                if (
                    check["status"] != "mismatch"
                    or check["field"] not in EQUIPMENT_FIELDS
                ):
                    blockers.append(
                        {
                            "code": "requires_engineering_review",
                            "device_tag": check["device_tag"],
                            "field": check["field"],
                            "reason": check["reason"],
                        }
                    )
                    continue
                governing = (
                    check.get("required", {}).get("evidence")
                    or check.get("requested", {}).get("evidence")
                    or []
                )
                if (
                    not governing
                    or len(
                        {
                            json.dumps([fact["value"], fact.get("unit")])
                            for fact in governing
                        }
                    )
                    != 1
                ):
                    blockers.append(
                        {
                            "code": "explicit_requirement_required",
                            "device_tag": check["device_tag"],
                            "field": check["field"],
                            "reason": "No single explicit governing value authorizes a literal draft correction.",
                        }
                    )
                    continue
                desired = governing[0]
                replacement = desired["value"]
                if desired.get("unit") in {"V AC", "V DC"}:
                    replacement = (
                        f"{replacement} {'VAC' if desired['unit'] == 'V AC' else 'VDC'}"
                    )
                _safe_value(str(replacement))
                targets, before = [], {}
                for role, source in (("drawing", native[0]), ("bom", boms[0])):
                    observed = check[f"target_{role}"]["evidence"]
                    if len(observed) != 1:
                        raise CorrectionError(
                            "A correction requires one unambiguous native attribute or BOM cell per device and field."
                        )
                    fact = observed[0]
                    before[role] = fact["raw_value"]
                    if _same(fact, desired):
                        continue
                    target_info = {
                        "source_id": source.id,
                        "expected_value": fact["raw_value"],
                        "replacement_value": str(replacement),
                    }
                    if role == "drawing":
                        loc = fact["evidence"]
                        target_info.update(
                            kind="cad_attribute",
                            entity_handle=loc["cad_handle"],
                            block_name=loc["block_name"],
                            equipment_tag=fact.get(
                                "original_device_tag", fact["device_tag"]
                            ),
                            attribute_tag=loc["attribute"],
                        )
                        selected = SimpleEdit(target_info)
                        attribute = _single_attribute(
                            _exact_insert(drawing, selected), selected.attribute_tag
                        )
                        if attribute.dxf.text != fact["raw_value"]:
                            raise CorrectionError(
                                "The native attribute changed after source inspection."
                            )
                    else:
                        loc = fact["evidence"]
                        rows = tables.get(loc["sheet"], [])
                        if not rows:
                            raise CorrectionError(
                                "The source BOM worksheet is unavailable."
                            )
                        headers = [str(value or "") for value in rows[0]]
                        tag_columns = [
                            index
                            for index, header in enumerate(headers)
                            if HEADER_MAP.get(_key(header)) == "device_tag"
                        ]
                        if len(tag_columns) != 1 or len(headers) != len(set(headers)):
                            raise CorrectionError(
                                "BOM correction requires a unique first-row device-tag header and unique column names."
                            )
                        column_index = int(loc["column"]) - 1
                        if (
                            column_index >= len(headers)
                            or HEADER_MAP.get(_key(headers[column_index]))
                            != check["field"]
                        ):
                            raise CorrectionError(
                                "BOM header/cell identity could not be verified."
                            )
                        matches = [
                            row
                            for row in rows[1:]
                            if len(row) > tag_columns[0]
                            and str(row[tag_columns[0]] or "").strip().upper()
                            == fact.get("original_device_tag", fact["device_tag"])
                        ]
                        if len(matches) != 1:
                            raise CorrectionError("The BOM device row is ambiguous.")
                        row_key = str(matches[0][tag_columns[0]])
                        target_info.update(
                            kind="bom_cell",
                            row_key_column=headers[tag_columns[0]],
                            row_key=row_key,
                            column=headers[column_index],
                            worksheet=loc["sheet"],
                            cell=loc["cell"],
                        )
                    targets.append(target_info)
                if targets:
                    edits.append(
                        {
                            "id": stable_id(
                                "correction", check["device_tag"], check["field"]
                            ),
                            "device_tag": check["device_tag"],
                            "field": check["field"],
                            "before": before,
                            "after": desired["value"],
                            "unit": desired.get("unit"),
                            "requirement_evidence": [
                                {
                                    "source_id": fact["source_id"],
                                    "fact_id": fact["id"],
                                    "sha256": fact["sha256"],
                                    "revision": fact["revision"],
                                    "raw_value": fact["raw_value"],
                                    "evidence": fact["evidence"],
                                }
                                for fact in governing
                            ],
                            "targets": targets,
                        }
                    )
        except Exception as exc:  # noqa: BLE001 - native readers fail into a blocked plan.
            blockers.append(
                {
                    "code": "native_edit_unavailable",
                    "device_tag": current_check.get("device_tag"),
                    "field": current_check.get("field"),
                    "reason": str(exc)[:400]
                    if isinstance(exc, (CorrectionError, DraftingError))
                    else "The native source could not be inspected safely for exact edits.",
                }
            )
    current_rules = evaluate(comparison["target"])
    predicted = deepcopy(comparison["target"])
    for edit in edits:
        for fact in predicted["facts"]:
            if (
                fact["device_tag"] == edit["device_tag"]
                and fact["field"] == edit["field"]
            ):
                fact["value"], fact["unit"] = edit["after"], edit["unit"]
    predicted_rules = evaluate(predicted)
    if predicted_rules["failed"] or predicted_rules["unknown"]:
        blockers.append(
            {
                "code": "equipment_data_constraint",
                "reason": "The proposed literal values fail or cannot establish a supported equipment-data constraint. An engineer must resolve the requirement.",
            }
        )
    native_edits = []
    if native_plan:
        for request in native_plan["requests"]:
            kind = request["kind"]
            native_edits.append(
                {
                    "id": request["id"],
                    "kind": kind,
                    "device_tag": request.get("device_tag", request.get("wire_id", "")),
                    "field": kind,
                    "label": kind.replace("_", " "),
                    "before": None,
                    "after": request.get(
                        "to",
                        request.get(
                            "at",
                            request.get(
                                "to_terminal",
                                request.get("points", "Requested native change"),
                            ),
                        ),
                    ),
                    "requirement_evidence": [
                        {**request["evidence"], "fact_id": request["id"]}
                    ],
                    "targets": [],
                }
            )
    status = (
        "blocked" if blockers else "ready" if edits or native_edits else "no_changes"
    )
    source_manifest = {
        "baseline": _manifest(baseline),
        "target": _manifest(target),
        "requirements": _manifest(requirements),
    }
    plan = {
        "schema_version": 1,
        "status": status,
        "edits": [*edits, *native_edits],
        "native_plan": native_plan,
        "blockers": blockers,
        "warnings": warnings,
        "source_manifest": source_manifest,
        "comparison": comparison,
        "engineering_approved": False,
        "production_released": False,
        "geometry_changed": bool(native_edits),
        "current_rules": current_rules,
        "predicted_rules": predicted_rules,
        "identity_resolution": comparison.get("identity_resolution"),
    }
    plan["plan_id"] = _digest(
        {
            "sources": source_manifest,
            "edits": [*edits, *native_edits],
            "blockers": blockers,
            "native_plan_id": native_plan["plan_id"] if native_plan else None,
            "identity_resolution": comparison.get("identity_resolution"),
        }
    )
    return plan


class SimpleEdit:
    """Internal selector generated from the source observation, never a model."""

    def __init__(self, values):
        self.__dict__.update(values)


def _native_preview(path, drawing_path, revision, *, geometry_changed=False):
    """Vector geometry plus labelled values, both read from the saved DXF."""
    import pymupdf
    from reportlab.pdfgen import canvas

    actual = ezdxf.readfile(drawing_path)
    svg = drawing_svg(actual, title=f"Native DXF / draft {revision}")
    # Flatten the adapter's nested display viewport before PDF conversion.
    # Its web header is not CAD geometry and nested SVG viewport support varies
    # across PDF renderers. The actual primitive group stays unchanged.
    root = ET.fromstring(svg)
    native = root.find("{http://www.w3.org/2000/svg}svg")
    if native is None:
        raise CorrectionError("The native drawing preview viewport is unavailable.")
    flat = ET.Element(
        "{http://www.w3.org/2000/svg}svg",
        {"width": "1120", "height": "720", "viewBox": native.attrib["viewBox"]},
    )
    for element in native:
        flat.append(element)
    with pymupdf.open(stream=ET.tostring(flat), filetype="svg") as image:
        geometry_pdf = image.convert_to_pdf()
    rows = []
    for entity in actual.modelspace().query("INSERT"):
        attrs = {attribute.dxf.tag: attribute.dxf.text for attribute in entity.attribs}
        if "EQUIPMENT_TAG" not in attrs:
            continue
        fields = [(HEADER_MAP.get(_key(tag)), value) for tag, value in attrs.items()]
        rows.append(
            (
                attrs["EQUIPMENT_TAG"],
                [
                    (field, value)
                    for field, value in fields
                    if field in EQUIPMENT_FIELDS
                ],
            )
        )
    if not rows or len(rows) > 24:
        raise CorrectionError(
            "The native preview supports one to 24 explicitly tagged equipment blocks."
        )
    labels = {
        "frame_a": "Frame",
        "trip_a": "Trip",
        "trip_unit": "Trip unit",
        "part_number": "Part number",
        "quantity": "Quantity",
        "nameplate": "Nameplate",
        "control_voltage_v": "Control voltage",
        "interrupting_ka": "Interrupting",
    }
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(1224, 1000), invariant=1)
    if geometry_changed:
        from math import ceil

        from reportlab.pdfbase.pdfmetrics import stringWidth

        # The native drawing gets its own sheet. The saved attributes follow as
        # labelled blocks so the independent PDF reader can locate exact facts.
        margin, page_width = 38, 1224
        inner_width = page_width - 2 * margin
        ordered = sorted(rows, key=lambda row: row[0])
        columns = 3
        column_width = (inner_width - 48) / columns
        if any(
            stringWidth(f"{labels[field]}: {value}", "Helvetica", 11) > column_width
            for _, fields in ordered
            for field, value in fields
        ):
            columns, column_width = 1, inner_width
        per_page = columns * 3
        page_count = 1 + ceil(len(ordered) / per_page)

        def line(text, x, y, *, width=inner_width, size=11, bold=False):
            font = "Helvetica-Bold" if bold else "Helvetica"
            measured = stringWidth(text, font, size)
            fitted = min(size, size * width / measured) if measured else size
            if fitted < 8:
                raise CorrectionError(
                    "A native component label is too long for a readable draft preview."
                )
            c.setFont(font, fitted)
            c.drawString(x, y, text)

        def heading(title, subtitle, number):
            c.setFillColorRGB(0.09, 0.11, 0.14)
            line(f"RIVET / DRAFT {revision} / {title}", margin, 960, size=20, bold=True)
            c.setFillColorRGB(0.30, 0.34, 0.39)
            line(subtitle, margin, 937, size=11)
            c.setStrokeColorRGB(0.80, 0.82, 0.85)
            c.setLineWidth(0.65)
            c.line(margin, 919, page_width - margin, 919)
            c.line(margin, 78, page_width - margin, 78)
            line(
                "DRAFT FOR ENGINEERING REVIEW - NOT FOR FABRICATION OR RELEASE",
                margin,
                56,
                size=9,
            )
            c.setFont("Helvetica", 9)
            c.drawRightString(
                page_width - margin, 56, f"Sheet {number} of {page_count}"
            )

        heading(
            "WIRING AND LAYOUT",
            "Vector preview read from the saved native DXF. Engineering approval required.",
            1,
        )
        # Keep one real, legible source anchor outside the bottom title strip.
        # A geometry-only page must not become uninspectable to the independent
        # document reader. This is a duplicate of the same saved native fact.
        reference_tag, reference_fields = next(
            ((tag, fields) for tag, fields in ordered if fields), (None, [])
        )
        if reference_tag is None:
            raise CorrectionError("The native drawing has no readable equipment facts.")
        c.setFillColorRGB(0.09, 0.11, 0.14)
        line(reference_tag, margin, 175, size=13, bold=True)
        for index, (field, value) in enumerate(reference_fields[:2]):
            line(f"{labels[field]}: {value}", margin, 153 - index * 21, size=10)
        c.setFillColorRGB(0.35, 0.39, 0.44)
        line(
            "Complete labelled equipment schedule on the following sheets.",
            635,
            175,
            width=550,
            size=10,
        )
        line(
            f"Editable source: {Path(drawing_path).name}", 635, 153, width=550, size=10
        )
        c.showPage()

        for offset in range(0, len(ordered), per_page):
            heading(
                "EQUIPMENT SCHEDULE",
                "Device values read from the same saved DXF. Use the Excel BOM for the editable bill of materials.",
                2 + offset // per_page,
            )
            for index, (tag, fields) in enumerate(ordered[offset : offset + per_page]):
                column, row = index % columns, index // columns
                x = margin + column * (column_width + 24)
                y = 878 - row * 253
                c.setFillColorRGB(0.09, 0.11, 0.14)
                line(tag, x, y, width=column_width, size=16, bold=True)
                c.setStrokeColorRGB(0.85, 0.87, 0.89)
                c.line(x, y - 15, x + column_width, y - 15)
                c.setFillColorRGB(0.18, 0.21, 0.25)
                for field_index, (field, value) in enumerate(fields):
                    line(
                        f"{labels[field]}: {value}",
                        x,
                        y - 39 - field_index * 22,
                        width=column_width,
                        size=11,
                    )
            c.showPage()
        c.save()
        with (
            pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as output,
            pymupdf.open(stream=geometry_pdf, filetype="pdf") as geometry,
        ):
            output[0].show_pdf_page(pymupdf.Rect(38, 100, 1186, 790), geometry, 0)
            output.save(path)
        return
    for offset in range(0, len(rows), 3):
        c.setFont("Helvetica-Bold", 20)
        c.drawString(38, 960, f"RIVET / DRAFT {revision} / NATIVE DRAWING COPY")
        c.setFont("Helvetica", 10)
        c.drawString(
            38,
            939,
            "Native wiring and layout changes in a new draft copy. Engineering approval required."
            if geometry_changed
            else "Literal attribute changes only. Original geometry and source sheet identifiers retained. Engineering approval required.",
        )
        for index, (tag, fields) in enumerate(rows[offset : offset + 3]):
            x = 42 + index * 395
            c.setFont("Helvetica-Bold", 16)
            c.drawString(x, 390, tag)
            c.setFont("Helvetica", 10)
            for row_index, (field, value) in enumerate(fields):
                c.drawString(x, 363 - row_index * 23, f"{labels[field]}: {value}")
        c.setFont("Helvetica", 9)
        c.drawString(
            38,
            45,
            "NOT FOR FABRICATION OR RELEASE. This preview shows supported native primitives; open the DXF for engineering review.",
        )
        c.showPage()
    c.save()
    with (
        pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as output,
        pymupdf.open(stream=geometry_pdf, filetype="pdf") as geometry,
    ):
        for page in output:
            page.show_pdf_page(pymupdf.Rect(35, 80, 1189, 555), geometry, 0)
        output.save(path)


def prepare_corrections(
    baseline_sources,
    target_sources,
    requirement_sources=None,
    *,
    destination,
    expected_plan_id=None,
    target_revision=None,
    identity_profile=None,
    identity_source_catalog=None,
):
    """Prepare and independently verify new draft copies; never mutate originals."""
    baseline, target, requirements = (
        _inputs(baseline_sources),
        _inputs(target_sources),
        _inputs(requirement_sources or []),
    )
    identity_options = (
        {
            "identity_profile": identity_profile,
            "identity_source_catalog": identity_source_catalog,
        }
        if identity_profile is not None
        else {}
    )
    plan = plan_corrections(baseline, target, requirements, **identity_options)
    if expected_plan_id and plan["plan_id"] != expected_plan_id:
        raise CorrectionError(
            "Sources or supported requirements changed. Inspect the new correction plan.",
            plan,
        )
    if plan["status"] != "ready":
        raise CorrectionError("No safely executable literal correction is ready.", plan)
    drawing_source = next(
        source
        for source in target
        if source.role == "drawing" and Path(source.name).suffix.casefold() == ".dxf"
    )
    bom_source = next(
        source
        for source in target
        if source.role == "bom"
        and Path(source.name).suffix.casefold() in {".csv", ".xlsx"}
    )
    revision = target_revision or f"{drawing_source.revision}-R1"
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}", revision)
        or revision == drawing_source.revision
    ):
        raise CorrectionError("Choose a distinct safe draft revision identifier.")
    destination = Path(destination).resolve()
    originals = [
        Path(source.path).resolve() for source in [*baseline, *target, *requirements]
    ]
    if destination.exists() or any(
        destination == path or destination in path.parents for path in originals
    ):
        raise CorrectionError(
            "The output destination must be new and cannot contain or replace an original."
        )
    snapshot = {
        source.id: _checked_bytes(Path(source.path), source.sha256)
        for source in [*baseline, *target, *requirements]
    }
    drawing = ezdxf.read(io.StringIO(snapshot[drawing_source.id].decode("utf-8-sig")))
    if plan.get("native_plan"):
        from .native_edits import NativeEditError, apply_native_changes

        with tempfile.TemporaryDirectory(prefix="rivet-native-edit-") as temporary:
            interim = Path(temporary) / "native-draft.dxf"
            try:
                applied = apply_native_changes(
                    drawing_source.path,
                    plan["native_plan"],
                    interim,
                    **identity_options,
                )
            except NativeEditError as exc:
                report = exc.report or {}
                failures = [
                    {
                        "code": "native_readback_failed",
                        "reason": check.get(
                            "reason",
                            "The saved drawing does not match the source-backed design plan.",
                        ),
                    }
                    for check in report.get("checks", [])
                    if check.get("status") != "pass"
                ]
                raise CorrectionError(
                    "The native drawing failed its saved-file recheck.",
                    {"blockers": failures},
                ) from exc
            if applied["status"] != "verified":
                raise CorrectionError(
                    "The native wiring/layout changes did not pass their independent checks.",
                    applied,
                )
            drawing = ezdxf.readfile(interim)
    flat = []
    for edit in plan["edits"]:
        for target_info in edit["targets"]:
            evidence = edit["requirement_evidence"][0]
            values = {
                key: value
                for key, value in target_info.items()
                if key not in {"source_id", "cell"}
            }
            values.update(
                id=stable_id(edit["id"], target_info["kind"]),
                evidence_id=evidence["fact_id"],
                reason="Apply the exact explicitly documented value to a new draft copy; engineering approval remains separate.",
                decision_status="confirmed_for_draft",
                decision_record_id=f"explicit-source:{evidence['source_id']}:{evidence['fact_id']}",
            )
            flat.append(
                CadAttributeEdit(**values)
                if target_info["kind"] == "cad_attribute"
                else BomCellEdit(**values)
            )
    bom_edits = [edit for edit in flat if isinstance(edit, BomCellEdit)]
    tables, bom_state = _bom_table(
        snapshot[bom_source.id],
        Path(bom_source.name).suffix.casefold(),
        {edit.worksheet for edit in bom_edits}
        or {
            "BOM"
            if Path(bom_source.name).suffix.casefold() == ".csv"
            else next(iter(_target_tables(bom_source)))
        },
    )
    for edit in flat:
        if isinstance(edit, CadAttributeEdit):
            attribute = _single_attribute(
                _exact_insert(drawing, edit), edit.attribute_tag
            )
            if attribute.dxf.text != edit.expected_value:
                raise CorrectionError("The native attribute changed before editing.")
            _safe_value(edit.replacement_value, cad=True)
            attribute.dxf.text = edit.replacement_value
        else:
            _edit_bom(tables, bom_state, edit)
    bom_changes = []
    if plan.get("native_plan"):
        from .native_bom import apply_inventory

        if bom_state[0] == "csv":
            from openpyxl import Workbook

            workbook = Workbook()
            workbook.active.title = "Equipment BOM"
            for row in bom_state[1]:
                workbook.active.append(row)
            bom_state = ("xlsx", workbook)
        bom_changes = apply_inventory(
            bom_state, plan["native_plan"]["requests"], drawing, **identity_options
        )
    review_layout = drawing.layouts.new(f"Rivet draft {revision}")
    for index, text in enumerate(
        (
            f"DRAFT {revision} - ENGINEERING APPROVAL REQUIRED",
            f"Derived from source revision {drawing_source.revision}; original source files retained.",
            "Source-cited native wiring/layout changes. Not for fabrication or production release."
            if plan.get("native_plan")
            else "Literal data/attribute corrections only. Not for fabrication or production release.",
        )
    ):
        review_layout.add_text(text, height=5 if index == 0 else 3).set_placement(
            (15, 180 - index * 12)
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".rivet-correction-", dir=destination.parent))
    reserved = False
    try:
        drawing_name, bom_name, preview_name = (
            f"Drawing-draft-{revision}.dxf",
            f"BOM-draft-{revision}{'.xlsx' if bom_state[0] == 'xlsx' else '.csv'}",
            f"Drawing-draft-{revision}.pdf",
        )
        drawing.saveas(stage / drawing_name)
        saved = ezdxf.readfile(stage / drawing_name)
        if saved.audit().has_errors:
            raise CorrectionError(
                "The saved native drawing failed structural validation."
            )
        if bom_state[0] == "xlsx":
            if plan.get("native_plan"):
                from .native_model import inspect_native
                from .native_schedules import add_native_schedules

                add_native_schedules(
                    bom_state[1], inspect_native(stage / drawing_name), revision
                )
            note = bom_state[1].create_sheet("Rivet draft")
            note.append(["Draft revision", revision])
            note.append(["Status", "Engineering approval required - not released"])
            note.append(["Derived from", bom_source.revision])
            note.append(["Plan fingerprint", plan["plan_id"]])
            for worksheet in bom_state[1]:
                worksheet.oddHeader.right.text = (
                    f"DRAFT {revision} - ENGINEERING REVIEW"
                )
            bom_state[1].save(stage / bom_name)
            bom_state[1].close()
        else:
            with (stage / bom_name).open("w", encoding="utf-8", newline="") as output:
                csv.writer(output).writerows(bom_state[1])
        if plan.get("native_plan"):
            _native_preview(
                stage / preview_name,
                stage / drawing_name,
                revision,
                geometry_changed=True,
            )
        else:
            _native_preview(stage / preview_name, stage / drawing_name, revision)
        output_sources = [
            SourceInput(
                id=f"draft:{plan['plan_id']}:{role}",
                name=name,
                path=str(stage / name),
                role="bom" if role == "bom" else "drawing",
                revision=revision,
                sha256=hashlib.sha256((stage / name).read_bytes()).hexdigest(),
                lifecycle="draft",
            )
            for name, role in (
                (drawing_name, "drawing"),
                (bom_name, "bom"),
                (preview_name, "preview"),
            )
        ]
        from .native_verification import recheck_package

        verification = recheck_package(
            baseline,
            output_sources,
            requirements
            + [source for source in [*baseline, *target] if source.role == "markup"],
            original_target=target,
            native_plan=plan.get("native_plan"),
            literal_edits=plan["edits"],
            **identity_options,
        )
        verification["data_rules"] = evaluate(verification["target"])
        if (
            verification["summary"]["outcome"] != "supported_fields_match"
            or verification["summary"]["remaining"]
            or verification["summary"]["cannot_verify"]
            or verification["data_rules"]["failed"]
            or verification["data_rules"]["unknown"]
        ):
            raise CorrectionError(
                "Saved native copies did not pass the independent drawing/BOM/preview recheck.",
                {"plan": plan, "verification": verification},
            )
        for source in [*baseline, *target, *requirements]:
            _checked_bytes(Path(source.path), source.sha256)
        artifacts = [
            {
                "name": name,
                "role": role,
                "sha256": hashlib.sha256((stage / name).read_bytes()).hexdigest(),
                "media_type": media,
            }
            for name, role, media in (
                (drawing_name, "drawing", "application/dxf"),
                (
                    bom_name,
                    "bom",
                    "text/csv"
                    if bom_name.endswith(".csv")
                    else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
                (preview_name, "preview", "application/pdf"),
            )
        ]
        result = {
            "schema_version": 1,
            "status": "prepared_draft",
            "plan_id": plan["plan_id"],
            "draft_revision": revision,
            "source_manifest": plan["source_manifest"],
            "changes": plan["edits"],
            "bom_changes": bom_changes,
            "artifacts": artifacts,
            "verification": verification,
            "attachable": True,
            "engineering_approved": False,
            "production_released": False,
            "geometry_changed": bool(plan.get("native_plan")),
            "native_verification": verification.get("native_verification"),
            "originals_unchanged": True,
            "warnings": plan["warnings"],
            "identity_resolution": plan.get("identity_resolution"),
        }
        manifest_name = "correction-manifest.json"
        (stage / manifest_name).write_text(json.dumps(result, indent=2) + "\n")
        result["artifacts"] = [
            *artifacts,
            {
                "name": manifest_name,
                "role": "manifest",
                "sha256": hashlib.sha256(
                    (stage / manifest_name).read_bytes()
                ).hexdigest(),
                "media_type": "application/json",
            },
        ]
        destination.mkdir(exist_ok=False)
        reserved = True
        for artifact in result["artifacts"]:
            shutil.move(str(stage / artifact["name"]), destination / artifact["name"])
        return result
    except Exception as exc:
        if reserved:
            shutil.rmtree(destination, ignore_errors=True)
        if isinstance(exc, CorrectionError):
            raise
        raise CorrectionError(
            "The draft copies could not be prepared or verified safely."
        ) from exc
    finally:
        shutil.rmtree(stage, ignore_errors=True)
