"""Verify recorded responses and native drafter output before resolution counts."""

from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path

from backend.domain.service import get
from backend.storage import db
from backend.storage.models import Document, Span


def _revision(value):
    return str(value).removeprefix("Rev ").casefold()


def _native_documents(s, order, source_ids, revision, suffixes):
    seen = set()
    for source_id in source_ids:
        document = get(s, Document, get(s, Span, source_id).document_id)
        if document.id in seen or document.project_id != order.project_id:
            continue
        seen.add(document.id)
        if _revision(document.meta.get("revision_label")) != _revision(revision):
            continue
        path = db.BLOBS / document.blob
        if (
            Path(document.blob).name != document.blob
            or path.suffix.lower() not in suffixes
            or not path.is_file()
        ):
            continue
        contents = path.read_bytes()
        if sha256(contents).hexdigest() != document.sha256:
            continue
        yield document, path.suffix.lower(), contents


def _entity(contents, expected):
    import ezdxf

    document = ezdxf.read(StringIO(contents.decode("utf-8-sig")))
    entity = document.entitydb.get(expected["entity_handle"])
    if (
        not entity
        or entity.dxftype() != "INSERT"
        or entity.dxf.name != expected["block_name"]
    ):
        return None
    tags = [a.dxf.text for a in entity.attribs if a.dxf.tag == "EQUIPMENT_TAG"]
    if tags != [expected["equipment_tag"]]:
        return None
    matching = [
        candidate
        for candidate in document.modelspace().query("INSERT")
        if any(
            a.dxf.tag == "EQUIPMENT_TAG" and a.dxf.text == expected["equipment_tag"]
            for a in candidate.attribs
        )
    ]
    if len(matching) != 1:
        return None
    return entity


def _bom_value(contents, suffix, change):
    import csv

    from openpyxl import load_workbook

    if suffix == ".csv":
        rows = list(csv.DictReader(StringIO(contents.decode("utf-8-sig"))))
        if len(rows) > 100_000:
            return None
    else:
        workbook = load_workbook(
            BytesIO(contents), data_only=False, read_only=True, keep_links=False
        )
        if change["worksheet"] not in workbook:
            return None
        sheet = workbook[change["worksheet"]]
        if sheet.max_row > 100_000 or sheet.max_column > 500:
            workbook.close()
            return None
        values = iter(sheet.values)
        headers = [str(v) if v is not None else "" for v in next(values)]
        rows = [dict(zip(headers, row)) for row in values]
        workbook.close()
    matches = [
        row
        for row in rows
        if str(row.get(change["row_key_column"])) == change["row_key"]
    ]
    return str(matches[0].get(change["column"])) if len(matches) == 1 else None


def resolution_failure(s, order, plan, resolution, revision, recorded_comment):
    """Empty string means observable completion, never technical certification."""
    if plan["kind"] == "answer":
        if (
            not recorded_comment
            or recorded_comment.get("status") not in {"responded", "closed"}
            or not recorded_comment.get("reviewed")
            or not recorded_comment.get("response", "").strip()
            or not recorded_comment.get("responder", "").strip()
        ):
            return "The response has not been recorded as reviewed and answered."
        return ""
    bundle = plan["draft_bundle"]
    try:
        if plan["kind"] == "layout_task":
            task = next(
                t for t in bundle["layout_tasks"] if t["id"] == plan["layout_task_id"]
            )
            for _, _, contents in _native_documents(
                s, order, resolution.source_ids, revision, {".dxf"}
            ):
                entity = _entity(contents, task)
                if entity and any(
                    abs(float(current) - float(old)) > 1e-6
                    for current, old in zip(
                        entity.dxf.insert, task["anchor_xyz"], strict=True
                    )
                ):
                    return ""
            return "The revised native drawing does not show the drafter completing the located layout change."
        change = next(
            c for c in bundle["changes"] if c["id"] == plan["draft_change_id"]
        )
        if change["kind"] == "cad_attribute":
            for _, _, contents in _native_documents(
                s, order, resolution.source_ids, revision, {".dxf"}
            ):
                entity = _entity(contents, change)
                if entity and [
                    a.dxf.text
                    for a in entity.attribs
                    if a.dxf.tag == change["attribute_tag"]
                ] == [change["after"]]:
                    return ""
        else:
            for _, suffix, contents in _native_documents(
                s, order, resolution.source_ids, revision, {".xlsx", ".csv"}
            ):
                if _bom_value(contents, suffix, change) == change["after"]:
                    return ""
        return "The revised native CAD/BOM file does not establish this requested edit."
    except (ValueError, KeyError, StopIteration, UnicodeError):
        return "The revised native artifact could not be verified without guessing."
