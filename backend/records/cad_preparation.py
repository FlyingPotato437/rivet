"""Tenant/source-bound native draft preparation for the ordinary record agent.

Only copies are changed. Exact requested values must occur in the selected
returned-comment source; output remains pending engineering approval. Location
requests produce an anchored task and never move CAD geometry.
"""

import csv
import io
import json
import re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID
from zipfile import ZipFile

import ezdxf
from fastapi import HTTPException
from openpyxl import load_workbook
from pydantic import Field, ValidationError

from backend.domain.service import digest, fail, get, scoped
from backend.drafting import DraftingError, DraftRevisionRequest, prepare_revision
from backend.identity import current_actor
from backend.lifecycle import service as lifecycle
from backend.orders.schemas import StrictModel
from backend.storage import db
from backend.storage.models import Document, OrderEvent, Project, Span

MAX_SOURCE = 20 * 1024 * 1024
MAX_CONTEXT = 24000
CAD_ATTRIBUTES = ("TRIP_UNIT", "NAMEPLATE")
BOM_COLUMNS = ("part_number", "description", "quantity")


class EvidencedSelection(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    comment_id: str = Field(min_length=1, max_length=100)
    evidence_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=8, max_length=1200)


class AttributeSelection(EvidencedSelection):
    kind: Literal["cad_attribute"]
    entity_handle: str = Field(pattern=r"^[0-9A-Fa-f]+$", max_length=32)
    block_name: str = Field(min_length=1, max_length=120)
    equipment_tag: str = Field(min_length=1, max_length=120)
    attribute_tag: Literal["TRIP_UNIT", "NAMEPLATE"]
    expected_value: str = Field(max_length=500)
    replacement_value: str = Field(min_length=1, max_length=500)


class BomSelection(EvidencedSelection):
    kind: Literal["bom_cell"]
    row_key_column: str = Field(min_length=1, max_length=120)
    row_key: str = Field(min_length=1, max_length=240)
    column: Literal["part_number", "description", "quantity"]
    expected_value: str = Field(max_length=500)
    replacement_value: str = Field(min_length=1, max_length=500)
    worksheet: str = Field(default="BOM", min_length=1, max_length=120)


class LayoutSelection(EvidencedSelection):
    entity_handle: str = Field(pattern=r"^[0-9A-Fa-f]+$", max_length=32)
    block_name: str = Field(min_length=1, max_length=120)
    equipment_tag: str = Field(min_length=1, max_length=120)


class PrepareNative(StrictModel):
    drawing_document_id: UUID
    bom_document_id: UUID
    drawing_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bom_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    drawing_number: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    source_revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
    target_revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
    returned_revision: str = Field(min_length=1, max_length=40)
    edits: list[
        Annotated[AttributeSelection | BomSelection, Field(discriminator="kind")]
    ] = Field(min_length=1, max_length=20)
    layout_tasks: list[LayoutSelection] = Field(default_factory=list, max_length=20)


def _revision(value):
    return re.sub(
        r"^rev(?:ision)?\.?\s*", "", str(value or "").strip(), flags=re.IGNORECASE
    ).casefold()


def _native_document(s, order, document_id, role, expected_hash=None):
    document = get(s, Document, str(document_id))
    allowed = {".dxf"} if role == "drawing" else {".csv", ".xlsx"}
    path = db.BLOBS / document.blob
    if document.project_id != order.project_id:
        fail("Choose native sources belonging to this order.", 404)
    if (
        document.state not in {"ready", "parsed", "mapped"}
        or document.meta.get("active") is False
        or (document.meta.get("order_role") or document.kind) != role
        or Path(document.blob).name != document.blob
        or path.suffix.lower() not in allowed
        or not path.resolve().is_relative_to(db.BLOBS.resolve())
        or not path.is_file()
        or not 0 < path.stat().st_size <= MAX_SOURCE
    ):
        fail(
            "Use a current readable native DXF drawing and structured BOM registered to the order.",
            409,
        )
    content = path.read_bytes()
    if (
        sha256(content).hexdigest() != document.sha256
        or expected_hash is not None
        and document.sha256 != expected_hash
    ):
        fail(
            "The native source changed after inspection. Read the current source again.",
            409,
        )
    revision = document.meta.get("revision_label")
    if not revision or _revision(revision) == "unspecified":
        fail(
            "The native source revision must be explicitly recorded before preparing a copy.",
            409,
        )
    sources = list(
        s.scalars(
            scoped(Span)
            .where(Span.document_id == document.id)
            .order_by(Span.created_at, Span.id)
        )
    )
    if not sources:
        fail("The native source has no persisted evidence record.", 409)
    return document, path, content, [source.id for source in sources]


def _drawing_label(document):
    number = document.meta.get("drawing_number")
    if number and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", str(number)):
        return str(number)
    match = re.fullmatch(
        r"(.+?)[ _-]+Rev(?:ision)?[ _.-]+([A-Za-z0-9_.-]+)",
        Path(document.name).stem,
        re.IGNORECASE,
    )
    if match and _revision(match[2]) == _revision(document.meta.get("revision_label")):
        return match[1]
    return ""


def _drawing_inventory(content):
    try:
        drawing = ezdxf.read(io.StringIO(content.decode("utf-8-sig")))
        if len(drawing.modelspace()) > 20000:
            raise DraftingError("Drawing exceeds the bounded native inspection size.")
        items = []
        for entity in drawing.modelspace().query("INSERT"):
            attributes = {
                attribute.dxf.tag: attribute.dxf.text for attribute in entity.attribs
            }
            if "EQUIPMENT_TAG" not in attributes:
                continue
            items.append(
                {
                    "entity_handle": entity.dxf.handle,
                    "block_name": entity.dxf.name,
                    "equipment_tag": attributes["EQUIPMENT_TAG"],
                    "attributes": {
                        key: value
                        for key, value in attributes.items()
                        if key in CAD_ATTRIBUTES
                    },
                    "anchor_xyz": list(entity.dxf.insert),
                }
            )
            if len(items) >= 50:
                break
        return items
    except Exception:  # noqa: BLE001 - a native-file parser failure must not create a draft
        fail(
            "The native DXF could not be inspected without recovery; a drafter must check it.",
            409,
        )


def _bom_inventory(content, suffix):
    try:
        if suffix == ".csv":
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
            headers = reader.fieldnames or []
            rows = []
            for index, row in enumerate(reader):
                if index >= 50:
                    break
                rows.append(
                    {key: str(value or "")[:500] for key, value in row.items() if key}
                )
            return [{"worksheet": "BOM", "headers": headers, "rows": rows}]
        with ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 2000
                or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024
            ):
                fail("The native BOM exceeds the supported expanded file size.", 409)
        workbook = load_workbook(
            io.BytesIO(content), read_only=True, data_only=False, keep_links=False
        )
        tables = []
        try:
            for sheet in workbook:
                if sheet.title.casefold() != "bom":
                    continue
                if sheet.max_row > 100000 or sheet.max_column > 500:
                    fail("The native BOM exceeds supported dimensions.", 409)
                values = iter(sheet.values)
                headers = [str(value or "") for value in next(values)]
                rows = []
                for index, row in enumerate(values):
                    if index >= 50:
                        break
                    rows.append(
                        {
                            key: str(value if value is not None else "")[:500]
                            for key, value in zip(headers, row)
                            if key
                        }
                    )
                tables.append(
                    {"worksheet": sheet.title, "headers": headers, "rows": rows}
                )
        finally:
            workbook.close()
        return tables
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - malformed native spreadsheet formats must fail closed
        fail("The structured native BOM could not be inspected safely.", 409)


def inspect(s, order, w):
    drawings, boms, sources, issues = [], [], [], []
    for document in s.scalars(
        scoped(Document)
        .where(Document.project_id == order.project_id)
        .order_by(Document.created_at.desc())
    ):
        role = document.meta.get("order_role") or document.kind
        if role not in {"drawing", "bom"} or Path(document.blob).suffix.lower() not in {
            ".dxf",
            ".csv",
            ".xlsx",
        }:
            continue
        try:
            document, path, content, ids = _native_document(s, order, document.id, role)
        except HTTPException as exc:
            issues.append({"document_id": document.id, "message": str(exc.detail)})
            continue
        item = {
            "document_id": document.id,
            "name": document.name,
            "sha256": document.sha256,
            "revision": document.meta["revision_label"],
            "source_ids": ids[:20],
        }
        if role == "drawing":
            item.update(
                {
                    "drawing_number": _drawing_label(document),
                    "entities": _drawing_inventory(content),
                }
            )
            drawings.append(item)
        else:
            item["tables"] = _bom_inventory(content, path.suffix.lower())
            boms.append(item)
        if len(json.dumps([drawings, boms])) > MAX_CONTEXT:
            (drawings if role == "drawing" else boms).pop()
            issues.append(
                {
                    "message": "The native inventory is bounded; narrow this order's current sources."
                }
            )
            break
        sources.extend(ids[:20])
        if len(drawings) + len(boms) >= 6:
            break
    return {
        "order_version": order.version,
        "drawings": drawings,
        "boms": boms,
        "source_ids": list(dict.fromkeys(sources)),
        "issues": issues,
        "schema": PrepareNative.model_json_schema(),
        "saved_bundles": saved_bundles(s, order),
        "summary": f"Inspected {len(drawings)} native drawings and {len(boms)} structured bills of materials.",
        "limits": "DXF INSERT attributes TRIP_UNIT/NAMEPLATE and selected BOM literals only. Replacements must occur in a read current returned-comment source. Copies require engineering review; layout tasks do not move geometry.",
    }


def saved_bundles(s, order):
    result = []
    for event in s.scalars(
        scoped(OrderEvent)
        .where(OrderEvent.order_id == order.id, OrderEvent.kind == "cad_draft")
        .order_by(OrderEvent.created_at.desc())
        .limit(20)
    ):
        value = event.data
        try:
            for role, saved in value["native_sources"].items():
                document, _, _, _ = _native_document(
                    s, order, saved["id"], role, saved["sha256"]
                )
                if digest(document.meta) != saved["metadata_sha256"]:
                    raise DraftingError("Native source classification changed.")
            if (
                lifecycle._sources(s, order, list(value["evidence"]))
                != value["evidence"]
            ):
                continue
            bundle = lifecycle._draft_bundle(s, order, value["bundle_id"])
            if bundle["report_sha256"] != value["report_sha256"]:
                continue
            result.append({**value, "draft_bundle": bundle, "current": True})
        except (DraftingError, OSError):
            continue
        except HTTPException:
            continue
    return result


def prepare(s, order, run, w, payload_json, *, read_source_ids, automatic=False):
    if not run.state.get("cad_context_read"):
        fail(
            "Inspect the actual native CAD and BOM inventory before preparing a copy.",
            409,
        )
    if run.state.get("cad_order_version") != order.version:
        fail("The order changed after native inspection. Inspect it again.", 409)
    if isinstance(payload_json, str):
        if len(payload_json) > MAX_CONTEXT:
            fail("Keep native preparation within the bounded input size.", 413)
        try:
            payload_json = json.loads(payload_json)
        except ValueError:
            fail("Native preparation requires the inspected typed JSON schema.", 422)
    try:
        body = PrepareNative.model_validate(payload_json)
    except ValidationError as exc:
        fail(
            {
                "message": "The native draft input is invalid.",
                "errors": exc.errors(include_url=False, include_context=False),
            },
            422,
        )
    drawing, drawing_path, _, drawing_sources = _native_document(
        s, order, body.drawing_document_id, "drawing", body.drawing_sha256
    )
    bom, bom_path, _, bom_sources = _native_document(
        s, order, body.bom_document_id, "bom", body.bom_sha256
    )
    if drawing.meta.get("engineering_generated") or bom.meta.get(
        "engineering_generated"
    ):
        fail(
            "Apply this comment to the versioned equipment model in Review. Generated files must regenerate together; they cannot be edited as unrelated copies.",
            409,
        )
    if (
        _revision(drawing.meta["revision_label"]) != _revision(body.source_revision)
        or _revision(bom.meta["revision_label"]) != _revision(body.source_revision)
        or _revision(body.target_revision) == _revision(body.source_revision)
        or _drawing_label(drawing) != body.drawing_number
    ):
        fail(
            "The native drawing identity or source revision does not match its record.",
            409,
        )
    known = lifecycle._comment_map(s, order)
    evidence_ids = list(
        dict.fromkeys([item.evidence_id for item in [*body.edits, *body.layout_tasks]])
    )
    required_ids = set(evidence_ids) | {drawing_sources[0], bom_sources[0]}
    if required_ids - set(read_source_ids):
        fail(
            "Read the exact returned comments and native source evidence before preparing a copy.",
            409,
        )
    evidence = lifecycle._sources(s, order, list(required_ids))
    edits, tasks = [], []
    for item in [*body.edits, *body.layout_tasks]:
        comment = known.get(item.comment_id)
        span = get(s, Span, item.evidence_id)
        if (
            not comment
            or item.evidence_id not in comment.get("source_ids", [])
            or _revision(comment.get("revision")) != _revision(body.returned_revision)
        ):
            fail("Each native task must cite its exact current returned comment.", 409)
        document = get(s, Document, span.document_id)
        if document.meta.get("active") is False:
            fail("The selected returned source is no longer current.", 409)
        selected = item.model_dump(exclude={"comment_id"})
        selected.update(
            {
                "decision_status": "confirmed_for_draft",
                "decision_record_id": item.evidence_id,
            }
        )
        if isinstance(item, LayoutSelection):
            selected.update({"kind": "layout_task", "instruction": span.text[:1600]})
            tasks.append(selected)
        else:
            literal = re.sub(r"\s+", " ", item.replacement_value).strip()
            source_text = re.sub(r"\s+", " ", span.text)
            if not literal or not re.search(
                r"(?<![\w-])" + re.escape(literal) + r"(?![\w-])", source_text
            ):
                fail(
                    "The requested replacement is not explicitly present in the cited returned comment. Ask for the missing selection instead of guessing.",
                    409,
                )
            edits.append(selected)
    request_hash = digest(body.model_dump(mode="json"))
    for saved in saved_bundles(s, order):
        if saved["request_hash"] == request_hash:
            return {
                "summary": "Reused the existing current native draft bundle.",
                "bundle_id": saved["bundle_id"],
                "draft_bundle": saved["draft_bundle"],
                "comment_plans": saved["comment_plans"],
                "replayed": True,
            }, list(required_ids)
    if len(run.state.get("cad_preparations", [])) >= 4:
        fail("This run has reached its four-bundle native preparation limit.", 409)
    try:
        request = DraftRevisionRequest(
            organization_id=order.organization_id,
            order_id=order.id,
            job_number=order.number,
            drawing_number=body.drawing_number,
            source_revision=body.source_revision,
            target_revision=body.target_revision,
            source_lifecycle=str(drawing.meta.get("release_status", "draft"))
            if drawing.meta.get("release_status") in {"released", "approved"}
            else "draft",
            drawing_sha256=drawing.sha256,
            bom_sha256=bom.sha256,
            provenance="local_demo"
            if get(s, Project, order.project_id).synthetic
            else "authorized_upload",
            allowed_cad_attributes=CAD_ATTRIBUTES,
            allowed_bom_columns=BOM_COLUMNS,
            edits=tuple(edits),
            layout_tasks=tuple(tasks),
        )
        bundle = prepare_revision(
            request,
            drawing_path=drawing_path,
            bom_path=bom_path,
            destination=db.DATA
            / "drafting"
            / order.organization_id
            / order.id
            / str(request.bundle_id),
        )
    except (DraftingError, ValidationError) as exc:
        fail(str(exc), 409)
    plans = [
        {
            "comment_id": item.comment_id,
            "kind": "text_edit"
            if not isinstance(item, LayoutSelection)
            else "layout_task",
            "source_ids": [item.evidence_id, drawing_sources[0], bom_sources[0]],
            "response": "A native draft copy is prepared for engineering review; it is not released.",
            "draft_bundle_id": str(bundle.bundle_id),
            **(
                {"draft_change_id": item.id}
                if not isinstance(item, LayoutSelection)
                else {"layout_task_id": item.id}
            ),
        }
        for item in [*body.edits, *body.layout_tasks]
    ]
    value = {
        "bundle_id": str(bundle.bundle_id),
        "request_hash": request_hash,
        "report_sha256": bundle.report_sha256,
        "status": "pending_engineering_approval",
        "geometry_changed": False,
        "evidence": evidence,
        "native_sources": {
            "drawing": {
                "id": drawing.id,
                "sha256": drawing.sha256,
                "metadata_sha256": digest(drawing.meta),
            },
            "bom": {
                "id": bom.id,
                "sha256": bom.sha256,
                "metadata_sha256": digest(bom.meta),
            },
        },
        "comment_plans": plans,
        "requested_by": current_actor(),
        "origin": "event" if automatic else "requested",
    }
    s.add(
        OrderEvent(
            order_id=order.id,
            version=order.version,
            kind="cad_draft",
            summary=f"Rivet prepared {len(edits)} native field edits and {len(tasks)} located drafting tasks",
            actor="AI · Rivet",
            data=value,
        )
    )
    run.state = {
        **deepcopy(run.state),
        "cad_preparations": [
            *run.state.get("cad_preparations", []),
            {"bundle_id": str(bundle.bundle_id), "request_hash": request_hash},
        ],
    }
    s.flush()
    saved = lifecycle._draft_bundle(s, order, str(bundle.bundle_id))
    return {
        "summary": f"Prepared {len(edits)} actual native field edits and {len(tasks)} located drafting tasks; engineering review is required.",
        "bundle_id": str(bundle.bundle_id),
        "draft_bundle": saved,
        "comment_plans": plans,
        "replayed": False,
        "engineering_approved": False,
        "geometry_changed": False,
    }, list(required_ids)
