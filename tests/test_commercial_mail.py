"""Actual email/PDF inputs -> trusted quote -> PO comparison -> unsent Outlook."""

from email.message import EmailMessage
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_autopilot import FakeMailbox
from test_lifecycle import decision
from test_lifecycle import fixture as _lifecycle_fixture
from test_lifecycle import prepare as prepare_package
from test_lifecycle_agent import enable
from test_record_scenarios import drain
from test_reply_assistant import receipt

from backend.autopilot import service as autopilot
from backend.lifecycle import service as lifecycle
from backend.lifecycle.mail_context import selected_message
from backend.records import agent_service, commercial_intake, lifecycle_preparation
from backend.records import service as records
from backend.records.email_intake import import_email
from backend.reply_assistant import automatic
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import Order, RecordIntake
from backend.worker import claim, process
from scripts.make_order_replay import write_pdf

fixture = _lifecycle_fixture
CATALOG = {
    "equipment_category": "low_voltage_circuit_breaker",
    "tag": "CB-12",
    "model": "LV-1200-3P",
    "description": "Fictional 1200 A breaker, three poles",
    "unit_cost": "1200.00",
    "gross_margin_percent": "25",
    "currency": "USD",
    "trip_unit": "TU-2500",
    "remote_indication": "not included",
    "exceptions": ["Tax and freight require a separate written quotation."],
}
RFQ = [
    "Equipment category: Low-voltage circuit breaker",
    "Equipment tag: CB-12",
    "Catalog model: LV-1200-3P",
    "Quantity: 2 each",
    "Trip unit: TU-2500",
    "Remote indication: required",
]
RFQ_COVER = """Hi Rivet team,

Please quote the two CB-12 circuit-breaker packages specified in the
attached RFQ for DEMO-QUOTE-1015-092316. Please identify any exceptions,
including remote indication, and prepare the quotation for review.

This is a fictional integration rehearsal between my own accounts. No real
customer order, pricing commitment or engineering approval is represented.

Thanks,
Rivet demo reviewer
"""


def incoming(
    oid,
    tmp_path,
    *,
    po=None,
    price="1500.00",
    revision=1,
    extra=(),
    rfq=RFQ,
    body="Please review the attached commercial document. Fictional product test.",
):
    title = "Request for quotation" if po is None else "Purchase order"
    lines = list(rfq)
    if po:
        lines += [
            f"Purchase order: {po}",
            f"PO revision: {revision}",
            f"Unit price: USD {price}",
        ]
    lines += list(extra)
    path = tmp_path / (str(uuid4()) + ".pdf")
    write_pdf(path, title, lines)
    message = EmailMessage()
    message["From"], message["To"] = "customer@example.com", "pm@example.com"
    message["Subject"] = title
    message["Message-ID"] = f"<{uuid4()}@example.com>"
    message.set_content(body)
    message.add_attachment(
        path.read_bytes(), maintype="application", subtype="pdf", filename=path.name
    )
    with Session.begin() as s:
        order = s.get(Order, oid)
        imported = import_email(
            s, order, "customer.eml", message.as_bytes(), "Unspecified", forwarded=True
        )
        mail = receipt(s, oid, imported["document_id"], message["Message-ID"])
        mail.status = "imported"
        intake = s.get(RecordIntake, mail.intake_id)
        intake.status = "matched"
        rid = mail.id
    drain()
    return rid


def prepare_worker(oid):
    enable(oid)
    assert agent_service.scheduled_pass() == 1
    job = claim(kinds=("record_agent",))
    assert job
    process(job)
    with Session() as s:
        return lifecycle.view(s, s.get(Order, oid))["packages"]


def stage(rid):
    from backend.mailbox.models import MailboxReceipt

    with Session.begin() as s:
        return automatic.reconcile_receipt(s, s.get(MailboxReceipt, rid))


def test_actual_rfq_pdf_to_approved_native_quote_then_corrected_po(
    fixture, tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    _, price_sid = source(CATALOG, kind="erp_price")
    rid = incoming(oid, tmp_path, body=RFQ_COVER)
    assert stage(rid)["status"] == "queued"
    with Session() as s:
        assert not list(s.scalars(select(ReplyDraft)))
    packages = prepare_worker(oid)
    quote = next(p for p in packages if p["operation"] == "quote")
    assert quote["payload"]["total"] == "3200.00"
    assert len(quote["payload"]["exceptions"]) == 2
    assert "remote indication required" in quote["payload"]["exceptions"][1]["text"]
    assert len(quote["sources"]) > 3
    assert stage(rid)["status"] == "waiting_for_approval"
    for gate in quote["gates"]:
        decision(oid, quote["id"], gate, price_sid)
    graph = FakeMailbox()
    result = autopilot.deliver(client_factory=graph)
    assert result[0]["status"] == "created"
    assert graph.draft()["attachments"] == ["quote.pdf"]
    assert "3200.00" in graph.draft()["body"]["content"]
    assert "1200.00" not in graph.draft()["body"]["content"]
    with Session() as s:
        from backend.mailbox.models import MailboxReceipt
        from backend.reply_assistant import service as replies

        mail = s.get(MailboxReceipt, rid)
        context = replies.outlook_context(s, mail.internet_message_id)
        assert context["preparation"]["status"] != "blocked"
        assert "Confirm the equipment category" not in context["preparation"]["detail"]
    po_receipt = incoming(oid, tmp_path, po="PO-442")
    assert stage(po_receipt)["status"] == "queued"
    mismatch = next(p for p in prepare_worker(oid) if p["operation"] == "kickoff")
    assert (
        mismatch["status"] == "blocked" and len(mismatch["payload"]["mismatches"]) == 1
    )
    draft = stage(po_receipt)
    assert draft["status"] == "ready"
    with Session() as s:
        row = s.get(ReplyDraft, draft["draft_id"])
        assert "USD 1500.00" in row.body and "USD 1600.00" in row.body
        assert "before order acceptance" in row.body
        assert "1200.00" not in row.body
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "created"
    corrected_receipt = incoming(
        oid, tmp_path, po="PO-442", price="1600.00", revision=2
    )
    assert stage(corrected_receipt)["status"] == "queued"
    corrected = next(p for p in prepare_worker(oid) if p["operation"] == "kickoff")
    assert corrected["id"] != mismatch["id"]
    assert corrected["payload"]["mismatches"] == []
    assert corrected["status"] == "blocked" and not corrected["decisions"]
    assert corrected["payload"]["scope_mismatches"][0]["field"] == "remote_indication"
    assert "not included" in corrected["payload"]["scope_mismatches"][0]["detail"]
    assert stage(corrected_receipt)["status"] == "ready"
    with Session() as s:
        row = s.scalar(select(ReplyDraft).order_by(ReplyDraft.created_at.desc()))
        assert "unit prices match, but equipment scope does not" in row.body
        assert "remote indication required" in row.body
        assert "acceptance" in row.body
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "created"
    scope_corrected_receipt = incoming(
        oid,
        tmp_path,
        po="PO-442",
        price="1600.00",
        revision=3,
        rfq=[
            "Remote indication: not included"
            if line.startswith("Remote indication:")
            else line
            for line in RFQ
        ],
    )
    assert stage(scope_corrected_receipt)["status"] == "queued"
    scope_corrected = next(
        p for p in prepare_worker(oid) if p["operation"] == "kickoff"
    )
    assert scope_corrected["id"] != corrected["id"]
    assert scope_corrected["payload"]["mismatches"] == []
    assert scope_corrected["payload"]["scope_mismatches"] == []
    assert scope_corrected["payload"]["quoted_exceptions"]
    assert scope_corrected["status"] == "awaiting_decision"
    assert not scope_corrected["decisions"]
    assert stage(scope_corrected_receipt)["status"] == "ready"
    with Session() as s:
        from backend.mailbox.models import MailboxReceipt

        mail = s.get(MailboxReceipt, scope_corrected_receipt)
        context = selected_message(s, mail.internet_message_id)
        assert {p["id"] for p in context["packages"]} == {scope_corrected["id"]}
        assert context["packages"][0]["allowed_gates"] == ["scope", "engineering"]
        assert all(source["current"] for source in context["sources"])
    assert autopilot.deliver(client_factory=graph)[0]["status"] == "created"
    assert sum(path.endswith("createReply") for _, path in graph.calls) == 4


@pytest.mark.parametrize(
    "change",
    ["untrusted_price", "unknown_model", "missing_quantity", "ambiguous_quantity"],
)
def test_unsupported_commercial_inputs_never_invent_quote(
    fixture, tmp_path, monkeypatch, change
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source(
        CATALOG, kind="specification" if change == "untrusted_price" else "erp_price"
    )
    lines = list(RFQ)
    if change == "unknown_model":
        lines[2] = "Catalog model: UNLISTED-999"
    elif change == "missing_quantity":
        lines = [line for line in lines if not line.startswith("Quantity")]
    elif change == "ambiguous_quantity":
        lines.append("Quantity: 7")
    rid = incoming(
        oid, tmp_path, rfq=lines, extra=["Unit cost: 1.00", "Gross margin percent: 0"]
    )
    assert stage(rid)["status"] == "blocked"
    packages = prepare_worker(oid)
    assert not packages
    with Session() as s:
        assert not list(s.scalars(select(ReplyDraft)))


def test_line_specific_extraction_keeps_number_evidence_on_actual_pdf_line(
    fixture, tmp_path
):
    oid, source = fixture
    source(CATALOG, kind="erp_price")
    incoming(oid, tmp_path)
    with Session() as s:
        w = records.view(s, s.get(Order, oid))
        rows, issues = commercial_intake.extract_rows(w)
        assert not issues and len(rows) == 1
        row = rows[0]
        spans = {span["id"]: span for span in w["sources"]}
        quantity = row["field_source_ids"]["quantity"]
        assert len(quantity) == 1 and spans[quantity[0]]["text"] == "Quantity: 2 each"
        assert spans[quantity[0]]["location"]["page"] == 1
        assert "unit_cost" not in row["value"]
        requests, _structured = lifecycle_preparation.automatic_inputs(
            s, s.get(Order, oid), w
        )
        assert requests[0]["lines"][0]["quantity"] == "2"
        assert quantity[0] in requests[0]["lines"][0]["source_ids"]


@pytest.mark.parametrize(
    "change",
    [
        "complete",
        "incomplete",
        "independent_instruction",
        "instruction_in_greeting",
        "instruction_in_signature",
        "wrong_quantity",
        "unlinked",
    ],
)
def test_cover_only_uses_its_complete_actual_commercial_attachment(
    fixture, tmp_path, monkeypatch, change
):
    from backend.mailbox.models import MailboxReceipt
    from backend.storage.models import Document

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source(CATALOG, kind="erp_price")
    body = RFQ_COVER
    if change == "independent_instruction":
        body = body.replace(
            "Thanks,", "Please also guarantee delivery next week.\n\nThanks,"
        )
    if change == "wrong_quantity":
        body = body.replace("the two CB-12", "the three CB-12")
    if change == "instruction_in_greeting":
        body = body.replace("Hi Rivet team,", "Hi team please confirm delivery!")
    if change == "instruction_in_signature":
        body = body.replace("Rivet demo reviewer", "Ship by Friday")
    lines = (
        [line for line in RFQ if not line.startswith("Quantity:")]
        if change == "incomplete"
        else RFQ
    )
    rid = incoming(oid, tmp_path, rfq=lines, body=body)
    with Session.begin() as s:
        mail = s.get(MailboxReceipt, rid)
        intake = s.get(RecordIntake, mail.intake_id)
        root = s.get(Document, intake.data["document_id"])
        if change == "unlinked":
            for did in root.meta["attachment_document_ids"]:
                document = s.get(Document, did)
                document.meta = {
                    k: v for k, v in document.meta.items() if k != "email_document_id"
                }
            root.meta = {**root.meta, "attachment_document_ids": []}
        workspace = records.view(s, s.get(Order, oid))
        rows, questions = commercial_intake.extract_rows(workspace)
        cover_questions = [q for q in questions if q["document_id"] == root.id]
        if change == "complete":
            assert len(rows) == 1 and not questions
            assert (
                len(
                    next(d for d in workspace["documents"] if d["id"] == root.id)[
                        "attachment_document_ids"
                    ]
                )
                == 1
            )
        else:
            assert len(cover_questions) == 1
            if change == "incomplete":
                assert not rows and len(questions) == 2
    assert stage(rid)["status"] == ("queued" if change == "complete" else "blocked")


def test_completed_commercial_cover_finishes_event_without_model_loop(
    fixture, tmp_path, monkeypatch
):
    from backend.storage.models import RecordAgentRun

    monkeypatch.setenv("OPENAI_API_KEY", "test-never-called")
    monkeypatch.setattr(
        agent_service,
        "provider_call",
        lambda *_: pytest.fail(
            "Covered commercial mail must not start a redundant model loop"
        ),
    )
    oid, source = fixture
    source(CATALOG, kind="erp_price")
    incoming(oid, tmp_path, body=RFQ_COVER)
    quote = next(p for p in prepare_worker(oid) if p["operation"] == "quote")
    with Session() as s:
        run = s.scalar(
            select(RecordAgentRun).order_by(RecordAgentRun.created_at.desc())
        )
        assert run.status == "completed" and run.mode == "ai"
        assert run.result["covered_comment_ids"]
        assert not run.result.get("incomplete")
        assert quote["status"] == "awaiting_decision"
    assert agent_service.scheduled_pass() == 0


def test_natural_po_cover_reuses_exact_po_number_and_revision(fixture, tmp_path):
    oid, source = fixture
    source(CATALOG, kind="erp_price")
    incoming(
        oid,
        tmp_path,
        po="PO-RIVET-DEMO-442",
        body="""Hi Rivet team,

Please compare the attached purchase order PO-RIVET-DEMO-442, revision 1,
for job LIFECYCLE-001 against your quotation. Identify any
differences and prepare a clarification before accepting the order.

This is a fictional integration rehearsal between my own accounts. It is
not a customer order, purchase authorization or engineering release.

Thanks,
Rivet demo reviewer
""",
    )
    with Session() as s:
        rows, issues = commercial_intake.extract_rows(
            records.view(s, s.get(Order, oid))
        )
        assert len(rows) == 1 and not issues


@pytest.mark.parametrize(
    "field,value",
    [
        ("special_requirement", "UL 891 certification required"),
        ("model", "OTHER-999"),
        ("currency", "CAD"),
    ],
)
def test_core_kickoff_cannot_bypass_po_scope_or_identity_comparison(
    fixture, tmp_path, monkeypatch, field, value
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source(CATALOG, kind="erp_price")
    incoming(oid, tmp_path)
    prepare_worker(oid)
    lines = [
        "Remote indication: not included"
        if line.startswith("Remote indication:")
        else line
        for line in RFQ
    ]
    if field == "model":
        lines = [
            "Catalog model: " + value if line.startswith("Catalog model:") else line
            for line in lines
        ]
    extra = ["Special requirement: " + value] if field == "special_requirement" else []
    incoming(oid, tmp_path, po="PO-442", price="1600.00", rfq=lines, extra=extra)
    with Session.begin() as s:
        from backend.storage.models import Span

        if field == "currency":
            # Simulate original parsed customer CAD currency, never a new price rule.
            w = records.view(s, s.get(Order, oid))
            for span in w["sources"]:
                if span["text"] == "Unit price: USD 1600.00":
                    s.get(Span, span["id"]).text = "Unit price: CAD 1600.00"
            s.flush()
        rows, _ = commercial_intake.extract_rows(records.view(s, s.get(Order, oid)))
        row = next(r for r in rows if r["role"] == "purchase_order")
    view = prepare_package(
        oid,
        operation="kickoff",
        purchase_order_number="PO-442",
        lines=[
            {
                "tag": "CB-12",
                "quantity": "2",
                "unit_price": "1600.00",
                "source_ids": row["source_ids"],
            }
        ],
    )
    package = next(p for p in view["packages"] if p["operation"] == "kickoff")
    assert package["status"] == "blocked"
    assert any(
        difference["field"] == field
        for difference in package["payload"]["scope_mismatches"]
    )
    assert package["payload"]["mismatches"] == []


@pytest.mark.parametrize(
    "change", ["purchase_order_number", "quantity", "unit_price", "unrelated_source"]
)
def test_core_kickoff_requires_actual_po_number_and_values(
    fixture, tmp_path, monkeypatch, change
):
    from fastapi import HTTPException

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    _, catalog_id = source(CATALOG, kind="erp_price")
    incoming(oid, tmp_path)
    prepare_worker(oid)
    incoming(oid, tmp_path, po="PO-442", price="1600.00")
    with Session() as s:
        rows, _ = commercial_intake.extract_rows(records.view(s, s.get(Order, oid)))
        row = next(r for r in rows if r["role"] == "purchase_order")
    line = {
        "tag": "CB-12",
        "quantity": "2",
        "unit_price": "1600.00",
        "source_ids": row["source_ids"],
    }
    if change in {"quantity", "unit_price"}:
        line[change] = "999"
    if change == "unrelated_source":
        line["source_ids"] = [catalog_id]
    with pytest.raises(HTTPException, match="cite this purchase order's exact"):
        prepare_package(
            oid,
            operation="kickoff",
            purchase_order_number="OTHER-PO"
            if change == "purchase_order_number"
            else "PO-442",
            lines=[line],
        )


@pytest.mark.parametrize("second_po", ["OTHER-PO", "PO-442"])
def test_core_kickoff_cannot_combine_values_from_different_original_po_rows(
    fixture, tmp_path, monkeypatch, second_po
):
    from fastapi import HTTPException

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source(CATALOG, kind="erp_price")
    incoming(oid, tmp_path)
    prepare_worker(oid)
    incoming(oid, tmp_path, po="PO-442", price="1500.00", revision=1)
    three = [
        "Quantity: 3 each" if line.startswith("Quantity:") else line for line in RFQ
    ]
    incoming(oid, tmp_path, po=second_po, price="1600.00", revision=2, rfq=three)
    with Session() as s:
        rows, _ = commercial_intake.extract_rows(records.view(s, s.get(Order, oid)))
        source_ids = sorted(
            {
                sid
                for row in rows
                if row["role"] == "purchase_order"
                for sid in row["source_ids"]
            }
        )
    with pytest.raises(HTTPException, match="one original row"):
        prepare_package(
            oid,
            operation="kickoff",
            purchase_order_number="PO-442",
            lines=[
                {
                    "tag": "CB-12",
                    "quantity": "2",
                    "unit_price": "1600.00",
                    "source_ids": source_ids,
                }
            ],
        )


def test_core_kickoff_keeps_cited_same_po_scope_only_requirement(
    fixture, tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source(CATALOG, kind="erp_price")
    incoming(oid, tmp_path)
    prepare_worker(oid)
    incoming(oid, tmp_path, po="PO-442", price="1600.00")
    _, extra_id = source(
        {
            "purchase_order_number": "PO-442",
            "tag": "CB-12",
            "special_requirement": "UL 891 certification required",
        },
        kind="purchase_order",
    )
    with Session() as s:
        rows, _ = commercial_intake.extract_rows(records.view(s, s.get(Order, oid)))
        row = next(row for row in rows if row["role"] == "purchase_order")
    result = prepare_package(
        oid,
        operation="kickoff",
        purchase_order_number="PO-442",
        lines=[
            {
                "tag": "CB-12",
                "quantity": "2",
                "unit_price": "1600.00",
                "source_ids": [*row["source_ids"], extra_id],
            }
        ],
    )
    package = next(p for p in result["packages"] if p["operation"] == "kickoff")
    assert package["status"] == "blocked"
    assert any(
        item["field"] == "special_requirement" and extra_id in item["source_ids"]
        for item in package["payload"]["scope_mismatches"]
    )


def test_agent_cannot_bypass_different_po_model_with_matching_numeric_values(
    fixture, tmp_path, monkeypatch
):
    from fastapi import HTTPException

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    oid, source = fixture
    source(CATALOG, kind="erp_price")
    incoming(oid, tmp_path)
    prepare_worker(oid)
    lines = [
        "Catalog model: DIFFERENT-9000" if line.startswith("Catalog model:") else line
        for line in RFQ
    ]
    rid = incoming(oid, tmp_path, po="PO-442", price="1600.00", rfq=lines)
    assert stage(rid)["status"] == "blocked"
    with Session.begin() as s:
        order = s.get(Order, oid)
        workspace = records.view(s, order)
        rows, _ = commercial_intake.extract_rows(workspace)
        row = next(r for r in rows if r["role"] == "purchase_order")
        run = agent_service.create_run(s, order)
        with pytest.raises(HTTPException, match="currency or equipment model"):
            lifecycle_preparation.prepare(
                s,
                order,
                run,
                workspace,
                {
                    "operation": "kickoff",
                    "purchase_order_number": "PO-442",
                    "lines": [
                        {
                            "tag": "CB-12",
                            "quantity": "2",
                            "unit_price": "1600.00",
                            "source_ids": row["source_ids"],
                        }
                    ],
                },
                read_source_ids=row["source_ids"],
            )
