"""Reproducible, explicitly fictional eleven-comment local order replay.

Usage: .venv/bin/python -m backend.lifecycle.replay --organization-id UUID
Creates a NEW synthetic order and actual native DXF/XLSX draft copies. It never
approves its own work, changes external services, or sends email. The resulting
order remains ready for a person's money, scope and engineering decisions.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from backend.domain.service import scoped
from backend.drafting import DraftRevisionRequest, prepare_revision
from backend.identity import Identity, current_org, identity_scope
from backend.ingestion import parser as document_parser
from backend.orders.service import new_order
from backend.records.service import ensure
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Span, now, uid
from scripts.build_drafting_demo import make_request, make_sources

from . import service
from .fixture_documents import ANSWERS, CLAUSES, COMMENTS
from .schemas import Prepare


def _source(s, order, title, value, *, kind="specification", release="", file=None):
    content = (
        file.read_bytes()
        if file
        else (
            json.dumps(value, sort_keys=True) if isinstance(value, dict) else str(value)
        ).encode()
    )
    suffix = file.suffix if file else ".json" if isinstance(value, dict) else ".txt"
    blob = uid() + suffix
    db.BLOBS.mkdir(parents=True, exist_ok=True)
    (db.BLOBS / blob).write_bytes(content)
    document = Document(
        project_id=order.project_id,
        name=title,
        kind=kind,
        blob=blob,
        sha256=sha256(content).hexdigest(),
        state="ready",
        meta={
            "order_role": kind,
            "release_status": release,
            "revision_label": "C",
            "intake_order_id": order.id,
            "synthetic": True,
        },
        coverage=[{"label": "Local structured fixture", "state": "complete"}],
    )
    s.add(document)
    s.flush()
    if kind in {"erp_price", "erp_order", "accepted_exception"}:
        document.meta = {
            **document.meta,
            "lifecycle_trust": {
                "origin": "backend_local_demo",
                "organization_id": order.organization_id,
                "order_id": order.id,
                "document_sha256": document.sha256,
                "purposes": ["commercial_scope"]
                if kind == "accepted_exception"
                else ["pricing", "lead_time"],
            },
        }
    span = Span(
        document_id=document.id,
        text=json.dumps(value) if isinstance(value, dict) else str(value),
        location={"page": 1, "local_demo": True},
    )
    s.add(span)
    s.flush()
    return document, span


def _pdf_source(s, order, path, kind, release=""):
    document, unused = _source(
        s, order, path.name, "PDF source", kind=kind, release=release, file=path
    )
    s.delete(unused)
    s.flush()
    document_parser.parse(s, document)
    spans = list(s.scalars(scoped(Span).where(Span.document_id == document.id)))
    return document, spans


def _make_pdfs(destination, job):
    executable = os.getenv("RIVET_PDF_PYTHON")
    if (
        not executable
        and importlib.util.find_spec("reportlab")
        and importlib.util.find_spec("pypdf")
    ):
        executable = sys.executable
    if not executable:
        raise RuntimeError(
            "Set RIVET_PDF_PYTHON to the artifact Python runtime with ReportLab and pypdf before running the local replay."
        )
    result = subprocess.run(
        [
            executable,
            "-m",
            "backend.lifecycle.fixture_documents",
            "--output",
            str(destination),
            "--job",
            job,
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _prepare(s, order, values):
    return service.prepare(
        s,
        order,
        TypeAdapter(Prepare).validate_python(
            {"expected_version": order.version, **values}
        ),
    )


def build(*, number=None):
    number = number or "DEMO-LIFECYCLE-" + now().strftime("%m%d-%H%M%S")
    with Session.begin() as s:
        order = new_order(
            s,
            {
                "title": "Data Hall A · switchboard approval",
                "customer": "Meridian Data Center · fictional demo",
                "number": number,
                "synthetic": True,
                "category": "Low-voltage switchgear",
            },
        )
        organization_id, order_id = order.organization_id, order.id
        originals = db.DATA / "lifecycle" / organization_id / order_id / "originals"
        drawing, bom, handle = make_sources(originals, job_number=number)
        pdfs = _make_pdfs(originals, number)
        returned_document, returned_spans = _pdf_source(
            s, order, Path(pdfs["returned_pdf"]), "markup"
        )
        _, specification_spans = _pdf_source(
            s, order, Path(pdfs["specification_pdf"]), "specification", "approved"
        )
        clause_ids = []
        for clause, _, _ in CLAUSES[:7]:
            rows = [
                span
                for span in specification_spans
                if span.text.startswith(clause + " ")
            ]
            if not rows:
                raise ValueError(
                    "The generated PDF clause could not be parsed: " + clause
                )
            clause_ids.append([row.id for row in rows])
        _, cad_source = _source(
            s,
            order,
            drawing.name,
            "Native DXF source E-10 Rev C; fictional, not released.",
            kind="drawing",
            file=drawing,
        )
        _, bom_source = _source(
            s,
            order,
            bom.name,
            "Native BOM source Rev C; fictional.",
            kind="bom",
            file=bom,
        )
        _, price = _source(
            s,
            order,
            "ERP-price-CB-12.json",
            {
                "tag": "CB-12",
                "unit_cost": "1200.00",
                "currency": "USD",
                "gross_margin_percent": "25.00",
            },
            kind="erp_price",
        )
        _, extra_price = _source(
            s,
            order,
            "ERP-price-CB-EXTRA.json",
            {
                "tag": "CB-EXTRA",
                "unit_cost": "900.00",
                "currency": "USD",
                "gross_margin_percent": "25.00",
                "part_number": "CB-EXTRA-DEMO",
                "lead_time_days": 42,
            },
            kind="erp_price",
        )
        _, scope = _source(
            s,
            order,
            "Accepted-scope.json",
            {
                "scope_tag": "CB-EXTRA",
                "baseline_quantity": "0",
                "billable_scope_tag": "CB-EXTRA",
                "billable": True,
                "notice": "Fictional contract states extra breaker is excluded; new customer scope requires approval.",
            },
            kind="accepted_exception",
            release="approved",
        )
        _, po_source = _source(
            s,
            order,
            "Customer-PO.json",
            {
                "tag": "CB-12",
                "quantity": "1",
                "unit_price": "1500.00",
                "purchase_order": "PO-FICTIONAL",
            },
            kind="purchase_order",
        )
        comments, sources = [], []
        annotation_by_number = {
            int(span.location["annotation_id"].rsplit("-", 1)[-1]): span
            for span in returned_spans
            if span.location.get("annotation_id")
        }
        if len(annotation_by_number) != 11:
            raise ValueError(
                "The actual PDF parser did not recover all eleven native annotations"
            )
        for index, text in enumerate(COMMENTS, 1):
            document, span = returned_document, annotation_by_number[index]
            if span.text != text:
                raise ValueError(
                    "The extracted annotation differs from the generated review comment"
                )
            sources.append(span)
            comments.append(
                {
                    "id": uid(),
                    "number": str(index),
                    "revision": "C",
                    "text": text,
                    "original_text": text,
                    "author": "Fictional customer reviewer",
                    "authored_at": now().isoformat(),
                    "document_id": document.id,
                    "source_ids": [span.id],
                    "source_page": 1,
                    "target_source_id": cad_source.id,
                    "linked_email_id": "",
                    "status": "open",
                    "response": "",
                    "responder": "",
                    "reviewed": False,
                    "confidence": "Local demo",
                    "flags": [],
                    "created_at": now().isoformat(),
                    "updated_at": now().isoformat(),
                    "origin": "local_demo",
                }
            )
        # Make engineering's value selection explicit in the fixture. The bundle
        # itself still needs approval; this is not a customer or production signoff.
        _, engineer = _source(
            s,
            order,
            "Fictional-engineer-draft-selection.txt",
            "FICTIONAL LOCAL REPLAY: use TU-3000-DEMO for a draft copy only; do not release it. Nameplate text is CB-12 / DATA HALL A.",
            kind="engineering_approval",
        )
        request = make_request(
            drawing,
            bom,
            handle,
            organization_id=organization_id,
            order_id=order_id,
            job_number=number,
        )
        raw = request.model_dump(mode="json")
        for index, edit in enumerate(raw["edits"]):
            edit["evidence_id"] = sources[index + 7].id
            edit["decision_record_id"] = engineer.id
        raw["layout_tasks"][0]["evidence_id"] = sources[10].id
        raw["layout_tasks"][0]["decision_record_id"] = engineer.id
        request = DraftRevisionRequest.model_validate(raw)
        bundle = prepare_revision(
            request,
            drawing_path=drawing,
            bom_path=bom,
            destination=db.DATA
            / "drafting"
            / organization_id
            / order_id
            / str(request.bundle_id),
        )
        # Use the existing record model and mark fixture inputs as already ingested.
        record = ensure(s, order)
        record.data = {**record.data, "comments": comments}
        record.version += 1
        _prepare(
            s,
            order,
            {
                "operation": "quote",
                "lines": [
                    {
                        "tag": "CB-12",
                        "description": "Fictional switchgear breaker package",
                        "quantity": "1",
                        "unit_cost": "1200.00",
                        "gross_margin_percent": "25.00",
                        "source_ids": [price.id],
                    }
                ],
                "exceptions": [
                    {
                        "text": "Extra breaker is excluded from the original scope.",
                        "source_ids": [scope.id],
                    }
                ],
            },
        )
        _prepare(
            s,
            order,
            {
                "operation": "kickoff",
                "purchase_order_number": "PO-FICTIONAL",
                "lines": [
                    {
                        "tag": "CB-12",
                        "quantity": "1",
                        "unit_price": "1500.00",
                        "source_ids": [po_source.id],
                    }
                ],
            },
        )
        responses = ANSWERS
        plans = [
            {
                "comment_id": comments[i]["id"],
                "kind": "answer",
                "source_ids": [sources[i].id, *clause_ids[i]],
                "response": responses[i],
            }
            for i in range(7)
        ]
        for i, change in enumerate(bundle.changes):
            plans.append(
                {
                    "comment_id": comments[i + 7]["id"],
                    "kind": "text_edit",
                    "source_ids": [
                        sources[i + 7].id,
                        engineer.id,
                        cad_source.id,
                        bom_source.id,
                    ],
                    "draft_bundle_id": str(bundle.bundle_id),
                    "draft_change_id": change["id"],
                    "response": "The requested text/data edit is prepared in draft Rev D and awaits engineering approval.",
                }
            )
        task = bundle.layout_tasks[0]
        plans.append(
            {
                "comment_id": comments[10]["id"],
                "kind": "layout_task",
                "draft_bundle_id": str(bundle.bundle_id),
                "layout_task_id": task["id"],
                "source_ids": [sources[10].id, cad_source.id],
                "layout_location": {
                    "sheet": "E-10",
                    "x": task["anchor_xyz"][0],
                    "y": task["anchor_xyz"][1],
                    "entity_handle": task["entity_handle"],
                },
                "response": "The breaker is located for drafting. An extra breaker is a proposed scope addition requiring commercial approval.",
            }
        )
        _prepare(
            s,
            order,
            {
                "operation": "revision_review",
                "revision": "C",
                "comments": plans,
                "scope_additions": [
                    {
                        "tag": "CB-EXTRA",
                        "description": "Additional feeder breaker · CB-EXTRA",
                        "baseline_quantity": "0",
                        "requested_quantity": "1",
                        "unit_cost": "900.00",
                        "gross_margin_percent": "25.00",
                        "source_ids": [extra_price.id, sources[10].id],
                        "billability": "contract_confirmed",
                        "billability_source_ids": [scope.id],
                    }
                ],
            },
        )
        result = _prepare(
            s,
            order,
            {
                "operation": "purchasing",
                "part_number": "CB-EXTRA-DEMO",
                "lead_time_days": 42,
                "order_date": now().date().isoformat(),
                "current_ship_date": (now().date() + timedelta(days=20)).isoformat(),
                "source_ids": [extra_price.id],
            },
        )
    return {
        "organization_id": organization_id,
        "order_id": order_id,
        "job": number,
        "url": f"https://localhost:5179/#order/{order_id}",
        "mode": "local_demo",
        "bundle_id": str(bundle.bundle_id),
        "native_drawing": str(originals / drawing.name),
        "draft_report": bundle.report_path,
        "returned_pdf": pdfs["returned_pdf"],
        "specification_pdf": pdfs["specification_pdf"],
        "native_annotations_recovered": len(annotation_by_number),
        "prepared_work": result["completed_work"],
        "notice": "Fictional local replay. No external system was changed, no approval was fabricated, and no email was sent.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=UUID)
    parser.add_argument("--job")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    organization_id = (
        str(args.organization_id) if args.organization_id else current_org()
    )
    with identity_scope(Identity(organization_id, "Local replay operator")):
        result = build(number=args.job)
    content = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n")
    print(content)


if __name__ == "__main__":
    main()
