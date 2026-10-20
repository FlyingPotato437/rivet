from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

import ezdxf
from ezdxf import bbox
from openpyxl import load_workbook

from .preview import drawing_svg, report_html
from .schemas import (
    ArtifactRef,
    BomCellEdit,
    CadAttributeEdit,
    DraftRevisionBundle,
    DraftRevisionRequest,
)


class DraftingError(ValueError):
    """An exact, policy-authorized draft could not be prepared safely."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _checked_bytes(path: Path, expected_hash: str) -> bytes:
    if not path.is_file():
        raise DraftingError(f"Source file is missing: {path.name}")
    if path.stat().st_size > 50_000_000:
        raise DraftingError("Source file exceeds the 50 MB draft preparation limit")
    contents = path.read_bytes()
    if hashlib.sha256(contents).hexdigest() != expected_hash:
        raise DraftingError(f"Source hash changed: {path.name}")
    return contents


def _single_attribute(entity, name: str):
    found = [item for item in entity.attribs if item.dxf.tag == name]
    if len(found) != 1:
        raise DraftingError(
            f"Expected one {name} attribute on entity {entity.dxf.handle}; found {len(found)}"
        )
    attribute = found[0]
    if attribute.is_const or attribute.has_embedded_mtext_entity:
        raise DraftingError("Constant and multiline CAD attributes require a drafter")
    return attribute


def _exact_insert(document, change):
    handle = change.entity_handle.upper()
    entity = document.entitydb.get(handle)
    if (
        entity is None
        or entity.dxftype() != "INSERT"
        or entity not in document.modelspace()
    ):
        raise DraftingError(f"Modelspace block reference {handle} was not found")
    if entity.dxf.name != change.block_name:
        raise DraftingError(f"Block identity changed for entity {handle}")
    if entity.mcount != 1:
        raise DraftingError("Repeated block arrays require a drafter")
    block = document.blocks.get(entity.dxf.name)
    if block.block.is_xref or block.block.is_xref_overlay:
        raise DraftingError("Externally referenced CAD blocks require a drafter")
    tag = _single_attribute(entity, "EQUIPMENT_TAG")
    if tag.dxf.text != change.equipment_tag:
        raise DraftingError(f"Equipment identity changed for entity {handle}")
    matches = []
    for candidate in document.modelspace().query("INSERT"):
        for attribute in candidate.attribs:
            if (
                attribute.dxf.tag == "EQUIPMENT_TAG"
                and attribute.dxf.text == change.equipment_tag
            ):
                matches.append(candidate)
    if len(matches) != 1:
        raise DraftingError(f"Equipment tag {change.equipment_tag} is ambiguous")
    return entity


def _safe_value(value: str, *, cad=False):
    if any(ord(char) < 32 for char in value):
        raise DraftingError("Edit values must be a single printable line")
    if not cad and value.lstrip().startswith(("=", "+", "-", "@")):
        raise DraftingError("BOM replacement cannot contain a spreadsheet formula")


def _bom_table(contents: bytes, suffix: str, sheet_names: set[str]):
    if suffix == ".csv":
        if len(sheet_names) > 1:
            raise DraftingError("CSV has only one table; worksheet selectors disagree")
        try:
            rows = list(csv.reader(io.StringIO(contents.decode("utf-8-sig"))))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise DraftingError("BOM CSV must be valid UTF-8") from exc
        if len(rows) < 2:
            raise DraftingError("BOM has no data rows")
        headers = rows[0]
        if any(len(row) != len(headers) for row in rows[1:]):
            raise DraftingError("BOM CSV contains an irregular row")
        tables = {name: (headers, rows[1:]) for name in (sheet_names or {"BOM"})}
        return tables, ("csv", rows)
    try:
        workbook = load_workbook(
            io.BytesIO(contents), data_only=False, keep_links=False
        )
    except Exception as exc:
        raise DraftingError("BOM must be a valid .xlsx workbook") from exc
    tables = {}
    for name in sheet_names:
        if name not in workbook.sheetnames:
            raise DraftingError(f"BOM worksheet {name} was not found")
        sheet = workbook[name]
        if sheet.max_row > 100_000 or sheet.max_column > 500:
            raise DraftingError("BOM worksheet exceeds supported dimensions")
        rows = [list(row) for row in sheet.iter_rows()]
        if len(rows) < 2:
            raise DraftingError("BOM worksheet has no data rows")
        headers = [
            str(cell.value) if cell.value is not None else "" for cell in rows[0]
        ]
        tables[name] = (headers, rows[1:])
    return tables, ("xlsx", workbook)


def _edit_bom(tables, state, edit: BomCellEdit) -> dict:
    headers, rows = tables[edit.worksheet]
    if not all(isinstance(item, str) and item for item in headers) or len(
        headers
    ) != len(set(headers)):
        raise DraftingError("BOM column headers must be nonempty and unique")
    if edit.column not in headers or edit.row_key_column not in headers:
        raise DraftingError(f"BOM column {edit.column} or row key column was not found")
    key_index, column_index = (
        headers.index(edit.row_key_column),
        headers.index(edit.column),
    )

    def value(cell):
        raw = cell.value if state[0] == "xlsx" else cell
        return "" if raw is None else str(raw)

    found = [
        (index, row)
        for index, row in enumerate(rows, 2)
        if value(row[key_index]) == edit.row_key
    ]
    if len(found) != 1:
        raise DraftingError(f"Expected one BOM row {edit.row_key}; found {len(found)}")
    row_index, row = found[0]
    if state[0] == "xlsx" and row[column_index].data_type == "f":
        raise DraftingError(
            "A formula cell requires a drafter; it cannot be replaced with a literal"
        )
    before = value(row[column_index])
    if before != edit.expected_value:
        raise DraftingError(f"BOM value changed for {edit.row_key}.{edit.column}")
    _safe_value(edit.replacement_value)
    if state[0] == "xlsx":
        row[column_index].value = edit.replacement_value
        cell_address = row[column_index].coordinate
    else:
        row[column_index] = edit.replacement_value
        cell_address = f"row {row_index}, column {column_index + 1}"
    return {
        **edit.model_dump(),
        "target": f"BOM {edit.row_key} / {edit.column}",
        "before": before,
        "after": edit.replacement_value,
        "cell_address": cell_address,
        "status": "pending_engineering_approval",
    }


def _bundle_from_report(report_path: Path, report: dict) -> DraftRevisionBundle:
    return DraftRevisionBundle(
        bundle_id=report["bundle_id"],
        organization_id=report["organization_id"],
        order_id=report["order_id"],
        source_revision=report["source_revision"],
        target_revision=report["target_revision"],
        report_path=str(report_path.resolve()),
        report_sha256=sha256_file(report_path),
        artifacts=tuple(ArtifactRef.model_validate(a) for a in report["artifacts"]),
        changes=tuple(report["changes"]),
        layout_tasks=tuple(report["layout_tasks"]),
    )


def prepare_revision(
    request: DraftRevisionRequest,
    *,
    drawing_path: Path,
    bom_path: Path,
    destination: Path,
) -> DraftRevisionBundle:
    """Prepare an all-or-nothing, auditable copy from an exact typed edit plan.

    Callers establish authorization and tenant ownership before invoking this
    pure file adapter. This function does not interpret email instructions,
    establish engineering approval, access external CAD, or publish a release.
    """
    drawing_path, bom_path, destination = (
        Path(p).resolve() for p in (drawing_path, bom_path, destination)
    )
    if drawing_path.suffix.lower() != ".dxf":
        raise DraftingError(
            "Only editable DXF is supported; DWG and PDF are not editable CAD inputs"
        )
    if bom_path.suffix.lower() not in {".csv", ".xlsx"}:
        raise DraftingError("BOM input must be .csv or .xlsx")
    if destination.exists() or destination in (drawing_path, bom_path):
        raise DraftingError("Draft destination must be new and cannot replace a source")
    for source in (drawing_path, bom_path):
        if destination in source.parents:
            raise DraftingError("Source files cannot be inside the output destination")
    source_cad = _checked_bytes(drawing_path, request.drawing_sha256)
    source_bom = _checked_bytes(bom_path, request.bom_sha256)
    # Parsing the verified snapshot avoids a readfile race against the original.
    try:
        document = ezdxf.read(io.StringIO(source_cad.decode("utf-8-sig")))
    except Exception as exc:
        raise DraftingError(
            "DXF could not be parsed without recovery; no changes prepared"
        ) from exc
    audit = document.audit()
    if audit.has_errors or audit.has_fixes:
        raise DraftingError("DXF has structural errors and requires a drafter")
    highlight_handles = {
        _single_attribute(_exact_insert(document, edit), edit.attribute_tag).dxf.handle
        for edit in request.edits
        if isinstance(edit, CadAttributeEdit)
    }
    before_svg = drawing_svg(
        document,
        title=f"{request.drawing_number} / source Rev {request.source_revision}",
        highlight_handles=highlight_handles,
        highlight_color="#f8e5e3",
    )
    bom_edits = [edit for edit in request.edits if isinstance(edit, BomCellEdit)]
    tables, bom_state = _bom_table(
        source_bom, bom_path.suffix.lower(), {edit.worksheet for edit in bom_edits}
    )
    changes = []
    for edit in request.edits:
        if isinstance(edit, CadAttributeEdit):
            entity = _exact_insert(document, edit)
            attribute = _single_attribute(entity, edit.attribute_tag)
            if attribute.dxf.text != edit.expected_value:
                raise DraftingError(
                    f"CAD value changed for {edit.equipment_tag}.{edit.attribute_tag}"
                )
            _safe_value(edit.replacement_value, cad=True)
            attribute.dxf.text = edit.replacement_value
            changes.append(
                {
                    **edit.model_dump(),
                    "target": f"{edit.equipment_tag} / {edit.attribute_tag}",
                    "before": edit.expected_value,
                    "after": edit.replacement_value,
                    "attribute_handle": attribute.dxf.handle,
                    "status": "pending_engineering_approval",
                }
            )
        else:
            changes.append(_edit_bom(tables, bom_state, edit))
    tasks = []
    for task in request.layout_tasks:
        entity = _exact_insert(document, task)
        position = [float(value) for value in entity.dxf.insert]
        bounds = bbox.extents([entity], fast=False)
        if not bounds.has_data or not all(
            math.isfinite(value)
            for value in (*position, *bounds.extmin, *bounds.extmax)
        ):
            raise DraftingError("Layout task has no reliable finite CAD location")
        tasks.append(
            {
                **task.model_dump(),
                "anchor_xyz": position,
                "bbox": {"min": list(bounds.extmin), "max": list(bounds.extmax)},
                "coordinate_system": "DXF modelspace / source drawing units",
                "status": "requires_drafter",
                "geometry_changed": False,
            }
        )
    after_svg = drawing_svg(
        document,
        title=f"{request.drawing_number} / draft Rev {request.target_revision}",
        tasks=tasks,
        highlight_handles=highlight_handles,
    )
    # The new review sheet makes draft state visible without overwriting the
    # source titleblock or interpreting an unknown company's release scheme.
    review = document.layouts.new(f"Rivet draft {request.target_revision}")
    for i, text in enumerate(
        [
            f"DRAFT REV {request.target_revision} - ENGINEERING APPROVAL REQUIRED",
            f"Job {request.job_number} / drawing {request.drawing_number}",
            f"Derived from {request.source_lifecycle} revision {request.source_revision}; original unchanged.",
            "Text/data edits only. Layout changes require a drafter. Not released to production.",
            "FICTIONAL LOCAL DEMO"
            if request.provenance == "local_demo"
            else "PROPOSED EDITS - REVIEW BEFORE USE",
        ]
    ):
        review.add_text(text, height=5 if i == 0 else 3).set_placement(
            (15, 180 - i * 12)
        )
    report = {
        "schema_version": 1,
        "bundle_id": str(request.bundle_id),
        "organization_id": str(request.organization_id),
        "order_id": str(request.order_id),
        "job_number": request.job_number,
        "drawing_number": request.drawing_number,
        "source_revision": request.source_revision,
        "target_revision": request.target_revision,
        "source_lifecycle": request.source_lifecycle,
        "provenance": request.provenance,
        "status": "pending_engineering_approval",
        "engineering_approved": False,
        "production_released": False,
        "external_systems_modified": False,
        "sources": [
            {
                "role": "source_drawing",
                "name": drawing_path.name,
                "sha256": request.drawing_sha256,
            },
            {"role": "source_bom", "name": bom_path.name, "sha256": request.bom_sha256},
        ],
        "changes": changes,
        "layout_tasks": tasks,
        "artifacts": [],
        "limitations": [
            "Editable DXF INSERT attributes only; no DWG or proprietary CAD connection.",
            "CAD geometry is not moved; layout tasks preserve exact source coordinates.",
            "The source titleblock is unchanged; the new Rivet draft review sheet identifies the draft revision.",
            "Approval must be recorded by the authorized engineering workflow before release.",
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".rivet-draft-", dir=destination.parent))
    try:
        drawing_name = (
            f"{request.drawing_number}-draft-Rev-{request.target_revision}.dxf"
        )
        bom_name = f"BOM-draft-Rev-{request.target_revision}{bom_path.suffix.lower()}"
        document.saveas(stage / drawing_name)
        if bom_state[0] == "xlsx":
            bom_state[1].save(stage / bom_name)
        else:
            with (stage / bom_name).open("w", encoding="utf-8", newline="") as output:
                csv.writer(output).writerows(bom_state[1])
        # Reopen serialized native outputs before publishing any artifact.
        saved = ezdxf.readfile(stage / drawing_name)
        if saved.audit().has_errors:
            raise DraftingError("Serialized draft DXF failed structural validation")
        check_tables, check_state = _bom_table(
            (stage / bom_name).read_bytes(),
            bom_path.suffix.lower(),
            {edit.worksheet for edit in bom_edits},
        )
        for edit in request.edits:
            if isinstance(edit, CadAttributeEdit):
                actual = _single_attribute(
                    _exact_insert(saved, edit), edit.attribute_tag
                ).dxf.text
                if actual != edit.replacement_value:
                    raise DraftingError(
                        "Serialized CAD edit did not match its planned value"
                    )
            else:
                headers, rows = check_tables[edit.worksheet]
                key_index, column_index = (
                    headers.index(edit.row_key_column),
                    headers.index(edit.column),
                )

                def value(cell):
                    raw = cell.value if check_state[0] == "xlsx" else cell
                    return "" if raw is None else str(raw)

                matching = [
                    row for row in rows if value(row[key_index]) == edit.row_key
                ]
                if (
                    len(matching) != 1
                    or value(matching[0][column_index]) != edit.replacement_value
                ):
                    raise DraftingError(
                        "Serialized BOM edit did not match its planned value"
                    )
        (stage / "before.svg").write_text(before_svg, encoding="utf-8")
        (stage / "after.svg").write_text(after_svg, encoding="utf-8")
        for name, role, media in [
            (drawing_name, "draft_drawing", "application/dxf"),
            (
                bom_name,
                "draft_bom",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if bom_state[0] == "xlsx"
                else "text/csv",
            ),
            ("before.svg", "before_preview", "image/svg+xml"),
            ("after.svg", "after_preview", "image/svg+xml"),
        ]:
            report["artifacts"].append(
                {
                    "name": name,
                    "role": role,
                    "media_type": media,
                    "sha256": sha256_file(stage / name),
                }
            )
        (stage / "review.html").write_text(report_html(report), encoding="utf-8")
        report["artifacts"].append(
            {
                "name": "review.html",
                "role": "before_after_report",
                "media_type": "text/html",
                "sha256": sha256_file(stage / "review.html"),
            }
        )
        (stage / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        # Guard against source edits during the preparation interval.
        _checked_bytes(drawing_path, request.drawing_sha256)
        _checked_bytes(bom_path, request.bom_sha256)
        try:
            destination.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise DraftingError(
                "Draft destination already exists; nothing was overwritten"
            ) from exc
        try:
            # The exclusive directory creation reserves this new bundle. The
            # report is moved last so readers never see a partial valid bundle.
            names = [path for path in stage.iterdir() if path.name != "report.json"]
            for artifact in [*names, stage / "report.json"]:
                os.rename(artifact, destination / artifact.name)
        except Exception:
            shutil.rmtree(destination)
            raise
        return _bundle_from_report(destination / "report.json", report)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def load_bundle(
    report_path: Path,
    *,
    organization_id: UUID | str,
    order_id: UUID | str,
    expected_report_sha256: str | None = None,
) -> DraftRevisionBundle:
    """Load only a server-resolved report, checking tenancy and every artifact."""
    report_path = Path(report_path).resolve()
    if expected_report_sha256 and sha256_file(report_path) != expected_report_sha256:
        raise DraftingError("Draft report hash changed")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if (
            report["schema_version"] != 1
            or report["organization_id"] != str(organization_id)
            or report["order_id"] != str(order_id)
        ):
            raise DraftingError(
                "Draft bundle does not belong to this organization and order"
            )
        if (
            report["status"] != "pending_engineering_approval"
            or report["engineering_approved"] is not False
            or report["production_released"] is not False
            or report["external_systems_modified"] is not False
        ):
            raise DraftingError("Draft bundle has an invalid approval or release state")
        for artifact in report["artifacts"]:
            name = artifact["name"]
            candidate = report_path.parent / name
            if (
                Path(name).name != name
                or candidate.is_symlink()
                or candidate.resolve().parent != report_path.parent
            ):
                raise DraftingError("Draft artifact path is invalid")
            if not candidate.is_file() or sha256_file(candidate) != artifact["sha256"]:
                raise DraftingError(f"Draft artifact changed: {name}")
        return _bundle_from_report(report_path, report)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise DraftingError("Draft report is malformed") from exc


def read_bundle_artifact(
    report_path: Path,
    *,
    artifact_name: str,
    organization_id: UUID | str,
    order_id: UUID | str,
    expected_report_sha256: str | None = None,
) -> tuple[bytes, ArtifactRef]:
    """Resolve a named manifest member without accepting a client file path."""
    bundle = load_bundle(
        report_path,
        organization_id=organization_id,
        order_id=order_id,
        expected_report_sha256=expected_report_sha256,
    )
    matches = [item for item in bundle.artifacts if item.name == artifact_name]
    if len(matches) != 1:
        raise DraftingError("Draft artifact is not a unique member of this bundle")
    artifact = matches[0]
    contents = (Path(bundle.report_path).parent / artifact.name).read_bytes()
    if hashlib.sha256(contents).hexdigest() != artifact.sha256:
        raise DraftingError("Draft artifact changed during download")
    return contents, artifact
