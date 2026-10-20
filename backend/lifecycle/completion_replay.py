"""Complete a fictional Rev D replay only in an isolated rivet_test_* database.

This is an integration scenario, not a production auto-approval tool. Its named
fictional PM/engineer/buyer decisions are explicit simulations using the normal
approval service. It refuses the live database before creating any records.
"""

import argparse
import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID

from pydantic import TypeAdapter

from backend.company_context import customers
from backend.company_context.brief import order_brief
from backend.company_context.schemas import CustomerBindingWrite, CustomerWrite
from backend.domain.service import get
from backend.identity import Identity, current_org, identity_scope
from backend.orders.service import lock_order
from backend.records.schemas import CommentWrite
from backend.records.service import change_record, ensure
from backend.storage import db
from backend.storage.db import Session, engine
from backend.storage.models import Document, Order, Span

from . import service
from .replay import _source, build
from .schemas import DecisionWrite, Prepare


def _prepare(order_id, values):
    with Session.begin() as s:
        order = lock_order(s, order_id)
        return service.prepare(
            s,
            order,
            TypeAdapter(Prepare).validate_python(
                {"expected_version": order.version, **values}
            ),
        )


def _approve(order_id, operation, source_id, role):
    with Session() as s:
        order = get(s, Order, order_id)
        organization_id = order.organization_id
        package = next(
            p
            for p in service.view(s, order)["packages"]
            if p["operation"] == operation and p["status"] != "superseded"
        )
    with identity_scope(
        Identity(
            organization_id, f"Fictional {role} - isolated simulation", "org:admin"
        )
    ):
        for gate in package["pending_gates"]:
            with Session.begin() as s:
                order = lock_order(s, order_id)
                service.decide(
                    s,
                    order,
                    package["id"],
                    DecisionWrite(
                        expected_version=order.version,
                        gate=gate,
                        decision="approve",
                        reason="Explicit isolated fictional human decision after checking the exact artifact and source hashes; no production approval.",
                        source_ids=[source_id],
                    ),
                )


def _native(s, order, path, kind, revision, release):
    document, span = _source(
        s,
        order,
        path.name,
        "Fictional drafter-produced native Rev D, checked in this isolated replay only.",
        kind=kind,
        release=release,
        file=path,
    )
    document.meta = {**document.meta, "revision_label": revision}
    return document, span


def complete(*, number=None):
    if engine.url.database != "rivet_test" and not (
        engine.url.database or ""
    ).startswith("rivet_test_"):
        raise RuntimeError(
            "Completion simulation refuses a live database. Use an isolated rivet_test_* database and separate RIVET_DATA_DIR."
        )
    result = build(number=number)
    order_id, organization_id = result["order_id"], result["organization_id"]
    with Session.begin() as s:
        order = lock_order(s, order_id)
        customer = customers.create_customer(
            s,
            CustomerWrite(
                key="meridian-" + order_id[:12],
                name="Meridian Data Center - fictional replay",
                description="Explicit isolated customer-history demonstration; not a real client.",
            ),
        )
        customers.bind_order(
            s,
            order_id,
            CustomerBindingWrite(
                customer_id=customer["id"],
                expected_version=0,
                reason="The isolated fixture explicitly identifies this fictional customer.",
            ),
        )
        view = service.view(s, order)
        quote = next(p for p in view["packages"] if p["operation"] == "quote")
        review = next(
            p for p in view["packages"] if p["operation"] == "revision_review"
        )
        price_id = quote["payload"]["lines"][0]["source_ids"][0]
        engineer_source = review["payload"]["comments"][7]["source_ids"][1]
    _approve(order_id, "quote", price_id, "sales reviewer")
    with Session.begin() as s:
        order = lock_order(s, order_id)
        _, corrected_po = _source(
            s,
            order,
            "Corrected-customer-PO.json",
            {
                "tag": "CB-12",
                "quantity": "1",
                "unit_price": "1600.00",
                "purchase_order": "PO-FICTIONAL-CORRECTED",
            },
            kind="purchase_order",
        )
        po_id = corrected_po.id
    _prepare(
        order_id,
        {
            "operation": "kickoff",
            "purchase_order_number": "PO-FICTIONAL-CORRECTED",
            "lines": [
                {
                    "tag": "CB-12",
                    "quantity": "1",
                    "unit_price": "1600.00",
                    "source_ids": [po_id],
                }
            ],
        },
    )
    _approve(order_id, "kickoff", po_id, "order engineer")
    _approve(
        order_id,
        "revision_review",
        engineer_source,
        "engineering and commercial reviewers",
    )
    # Record the PM's seven actual answers through the existing comment-writing
    # service. A prepared response by itself deliberately does not satisfy this.
    with (
        identity_scope(
            Identity(organization_id, "Fictional PM - isolated simulation", "org:admin")
        ),
        Session.begin() as s,
    ):
        order = lock_order(s, order_id)
        record = ensure(s, order)
        for plan in review["payload"]["comments"][:7]:
            comment = next(
                c for c in record.data["comments"] if c["id"] == plan["comment_id"]
            )
            body = CommentWrite(
                expected_version=record.version,
                actor="Fictional PM - isolated simulation",
                reason="The fictional PM has reviewed and issued this answer within the isolated integration scenario.",
                text=comment["text"],
                number=comment["number"],
                revision="C",
                author=comment["author"],
                authored_at=comment["authored_at"],
                status="responded",
                response=plan["response"],
                responder="Fictional PM - isolated simulation",
                target_source_id=comment["target_source_id"],
                reviewed=True,
            )
            change_record(s, order, record, body, "comment", comment["id"])
    # A real drafter-produced native file completes geometry; the agent's source
    # draft is preserved. The test makes an unchanged copy first to prove failure.
    import ezdxf
    from openpyxl import load_workbook

    bundle_directory = Path(result["draft_report"]).parent
    manifest = json.loads(Path(result["draft_report"]).read_text())
    draft_dxf = bundle_directory / next(
        a["name"] for a in manifest["artifacts"] if a["role"] == "draft_drawing"
    )
    draft_bom = bundle_directory / next(
        a["name"] for a in manifest["artifacts"] if a["role"] == "draft_bom"
    )
    completed_dir = db.DATA / "completion-replay" / organization_id / order_id
    completed_dir.mkdir(parents=True, exist_ok=False)
    unchanged = completed_dir / "E-10-Rev-D-layout-not-completed.dxf"
    unchanged.write_bytes(draft_dxf.read_bytes())
    document = ezdxf.readfile(draft_dxf)
    layout = manifest["layout_tasks"][0]
    original_entity = document.entitydb.get(layout["entity_handle"])
    original_entity.translate(0, -16, 0)
    extra = document.modelspace().add_blockref("RIVET_BREAKER", (95, 45))
    extra.add_attrib(
        "EQUIPMENT_TAG", "CB-EXTRA", insert=(145, 55), dxfattribs={"height": 4}
    )
    extra.add_attrib(
        "TRIP_UNIT", "CB-EXTRA-DEMO", insert=(145, 41), dxfattribs={"height": 4}
    )
    native_dxf = completed_dir / "E-10-Rev-D-drafter-completed.dxf"
    # The simulated drafter updates the native title block as well as geometry.
    # Original Rev C and Rivet's draft copies remain untouched.
    revisions = {
        "RIVET / SWITCHBOARD APPROVAL": "RIVET / SWITCHBOARD AS BUILT",
        "SOURCE REV C - NOT RELEASED": "REV D / FICTIONAL AS-BUILT RECORD",
        "E-10 / SOURCE REV C": "E-10 / REV D",
        "APPROVAL REVIEW": "LOCAL AS-BUILT RECORD",
        "NOT RELEASED": "FICTIONAL RECORD ONLY",
    }
    for entity in document.modelspace().query("TEXT"):
        entity.dxf.text = revisions.get(entity.dxf.text, entity.dxf.text)
    document.saveas(native_dxf)
    workbook = load_workbook(draft_bom)
    workbook["BOM"].append(
        [
            "30",
            "CB-EXTRA",
            "CB-EXTRA-DEMO",
            "Commercially approved fictional additional breaker",
            1,
        ]
    )
    native_bom = completed_dir / "BOM-Rev-D-drafter-completed.xlsx"
    workbook.save(native_bom)
    with Session.begin() as s:
        order = lock_order(s, order_id)
        false_doc, false_span = _native(
            s, order, unchanged, "drawing", "D", "approved_for_customer"
        )
        actual_doc, actual_span = _native(
            s, order, native_dxf, "drawing", "D", "approved_for_customer"
        )
        bom_doc, bom_span = _native(s, order, native_bom, "bom", "D", "approved")
        bom_rows = []
        for tag, part in (
            ("10", "TU-3000-DEMO"),
            ("20", "CB-12-DEMO"),
            ("CB-EXTRA", "CB-EXTRA-DEMO"),
        ):
            span = Span(
                document_id=bom_doc.id,
                text=json.dumps({"tag": tag, "quantity": "1", "part_number": part}),
                location={"sheet": "BOM", "local_demo": True},
            )
            s.add(span)
            s.flush()
            bom_rows.append(
                {
                    "tag": tag,
                    "quantity": "1",
                    "part_number": part,
                    "source_ids": [span.id],
                }
            )
        old_id, actual_id, actual_source, bom_source = (
            false_doc.id,
            actual_doc.id,
            actual_span.id,
            bom_span.id,
        )
        unchanged_source = false_span.id
        actual_sha = actual_doc.sha256
        old_sha = false_doc.sha256
    resolutions = []
    for plan in review["payload"]["comments"]:
        if plan["kind"] == "answer":
            disposition, ids = "answered", plan["source_ids"]
        elif plan["kind"] == "text_edit":
            disposition, ids = (
                "edit_verified",
                [
                    bom_source
                    if plan["draft_change_id"] == "edit-bom-line"
                    else actual_source
                ],
            )
        else:
            disposition, ids = "layout_verified", [unchanged_source]
        resolutions.append(
            {
                "comment_id": plan["comment_id"],
                "disposition": disposition,
                "source_ids": ids,
                "note": "Actual recorded answer or exact native drafter output checked in this isolated simulation.",
            }
        )
    check_body = {
        "operation": "revision_check",
        "revision": "D",
        "resolutions": resolutions,
        "approved_bom": bom_rows,
        "revision_bom": bom_rows,
        "outbound_document_ids": [old_id],
    }
    failed = _prepare(order_id, check_body)
    failing_check = failed["packages"][0]
    if (
        failing_check["status"] != "blocked"
        or not failing_check["payload"]["resolution_failures"]
    ):
        raise AssertionError("Unfinished layout unexpectedly passed the revision check")
    check_body = deepcopy(check_body)
    check_body["resolutions"][-1]["source_ids"] = [actual_source]
    check_body["outbound_document_ids"] = [actual_id]
    checked = _prepare(order_id, check_body)
    if checked["packages"][0]["payload"]["documented_resolutions"] != 11:
        raise AssertionError("All eleven actual resolutions were not verified")
    _approve(order_id, "revision_check", actual_source, "release engineer")
    with Session.begin() as s:
        order = lock_order(s, order_id)
        _, approval = _source(
            s,
            order,
            "Customer-approval-stale-Rev-D.txt",
            "Fictional customer approval referencing the obsolete pre-layout file.",
            kind="customer_approval",
        )
        stale_doc = get(s, Document, approval.document_id)
        stale_doc.meta = {
            **stale_doc.meta,
            "approved_revision": "D",
            "approved_document_sha256": [old_sha],
        }
        stale_approval_id = approval.id
        _, engineer = _source(
            s,
            order,
            "Engineer-approval-Rev-D.txt",
            "Fictional engineer checked the completed Rev D and exact native drawing hash.",
            kind="engineering_approval",
        )
        engineer_doc = get(s, Document, engineer.document_id)
        engineer_doc.meta = {
            **engineer_doc.meta,
            "approved_revision": "D",
            "approved_document_sha256": [actual_sha],
        }
        current_engineer_id = engineer.id
        lead_time = next(
            p
            for p in service.view(s, order)["packages"]
            if p["operation"] == "purchasing"
        )["payload"]["source_ids"][0]
    _approve(order_id, "purchasing", lead_time, "buyer and customer-schedule reviewer")
    from fastapi import HTTPException

    try:
        _prepare(
            order_id,
            {
                "operation": "release",
                "customer_approval_source_ids": [stale_approval_id],
                "engineering_approval_source_ids": [current_engineer_id],
            },
        )
    except HTTPException as exc:
        stale_rejected = exc.status_code == 409
    else:
        stale_rejected = False
    if not stale_rejected:
        raise AssertionError("Stale customer approval unexpectedly authorized release")
    with Session.begin() as s:
        order = lock_order(s, order_id)
        _, approval = _source(
            s,
            order,
            "Customer-approval-current-Rev-D.txt",
            "Fictional customer approves the completed Rev D exact native drawing hash.",
            kind="customer_approval",
        )
        doc = get(s, Document, approval.document_id)
        doc.meta = {
            **doc.meta,
            "approved_revision": "D",
            "approved_document_sha256": [actual_sha],
        }
        current_customer_id = approval.id
    _prepare(
        order_id,
        {
            "operation": "release",
            "customer_approval_source_ids": [current_customer_id],
            "engineering_approval_source_ids": [current_engineer_id],
        },
    )
    _approve(order_id, "release", current_engineer_id, "production-release reviewers")
    from .fixture_closeout import write_test_report

    test_report_pdf = write_test_report(
        completed_dir / "FTR-17-functional-test-report.pdf",
        job=result["job"],
        drawing_path=native_dxf,
    )
    with Session.begin() as s:
        order = lock_order(s, order_id)
        report, _ = _source(
            s,
            order,
            "FTR-17-functional-test-report.pdf",
            "FICTIONAL ISOLATED TEST REPORT FTR-17: routine functional sequence recorded for this simulation; signed by fictional quality reviewer. No real test or certification is claimed.",
            kind="test_report",
            release="approved",
            file=test_report_pdf,
        )
        report.meta = {**report.meta, "revision_label": "0"}
        as_built, _ = _native(s, order, native_dxf, "as_built", "D", "released")
        report_id, as_built_id = report.id, as_built.id
    closeout_result = _prepare(
        order_id,
        {
            "operation": "closeout",
            "test_report_document_ids": [report_id],
            "as_built_document_ids": [as_built_id],
            "other_document_ids": [bom_doc.id],
        },
    )
    closeout_source_id = next(
        item for item in closeout_result["packages"] if item["operation"] == "closeout"
    )["payload"]["source_ids"][0]
    _approve(order_id, "closeout", closeout_source_id, "quality and handover reviewers")
    with Session() as s:
        order = get(s, Order, order_id)
        final = service.view(s, order)
        history = order_brief(s, order_id)
        check = next(
            p
            for p in final["packages"]
            if p["operation"] == "revision_check" and p["status"] != "superseded"
        )
    return {
        "mode": "isolated_local_simulation",
        "database": engine.url.database,
        "order_id": order_id,
        "organization_id": organization_id,
        "job": result["job"],
        "layout_failure_proven": True,
        "stale_signature_rejected": stale_rejected,
        "verified_comments": check["payload"]["addressed_comments"],
        "total_comments": check["payload"]["total_comments"],
        "stages": final["completed_work"],
        "customer_history_candidates": history["withheld"],
        "automatically_reusable_customer_notes": len(history["preferences"])
        + len(history["history"]),
        "actual_drafter_dxf": str(native_dxf),
        "actual_drafter_bom": str(native_bom),
        "notice": "Fictional isolated simulation, using explicit simulated human decisions. No live database, mailbox or external enterprise system was changed.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=UUID)
    parser.add_argument("--job")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--simulate-human-decisions", action="store_true", required=True
    )
    args = parser.parse_args()
    with identity_scope(
        Identity(
            str(args.organization_id) if args.organization_id else current_org(),
            "Isolated replay operator",
        )
    ):
        result = complete(number=args.job)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
