"""Mailbox capture is not a task queue: preserve originals, ask only useful questions."""

from email.message import EmailMessage
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.identity import Identity, identity_scope
from backend.mailbox.models import MailboxReceipt
from backend.records.intake import inspect_file, receive
from backend.records.mail_relevance import classify
from backend.storage.db import Session
from backend.storage.models import Document, Order, RecordIntake
from scripts.reclassify_mailbox_intake import reclassify_pending


def email(subject, body="", attachments=(), **headers):
    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = "Reviewer <reviewer@example.test>"
    for key, value in headers.items():
        mail[key.replace("_", "-")] = value
    mail.set_content(body)
    for name in attachments:
        mail.add_attachment(
            b"synthetic attachment",
            maintype="application",
            subtype="octet-stream",
            filename=name,
        )
    return mail.as_bytes()


@pytest.mark.parametrize(
    "subject,body,attachments,headers",
    [
        ("Your verification code", "Use 12345 to sign in.", (), {}),
        ("Microsoft account activity", "Please confirm this was you.", (), {}),
        ("Team lunch", "Please confirm Friday works.", (), {}),
        ("Payment receipt", "Thank you", ("receipt.pdf",), {}),
        ("Monthly billing", "Invoice attached", ("invoice.xlsx",), {}),
        ("Please confirm your subscription", "Welcome", (), {}),
        (
            "Switchgear industry newsletter",
            "Breaker technology and drawing review",
            (),
            {"List_Unsubscribe": "<mailto:leave@example.test>"},
        ),
        ("Register for our webinar", "New switchboard launch", (), {}),
        ("Quarterly results", "For your records", ("results.pdf",), {}),
    ],
)
def test_routine_messages_do_not_create_review_tasks(
    subject, body, attachments, headers
):
    payload = email(subject, body, attachments, **headers)
    with Session.begin() as s:
        row = receive(s, "captured.eml", payload, forwarded=True, mailbox_capture=True)
        assert row["status"] == "not_order_related"
        assert row["order_id"] is None
        assert list(s.scalars(select(Order))) == []
        assert list(s.scalars(select(Document))) == []
        assert s.get(RecordIntake, row["intake_id"]).blob


@pytest.mark.parametrize(
    "subject,body,attachments",
    [
        ("Returned drawings", "Please see attached", ()),
        ("Equipment package", "Please confirm the trip unit", ()),
        ("Comments attached", "Please review", ("package.pdf",)),
        ("Updated", "See attached", ("shop_drawings.pdf",)),
        ("GA review", "For your review", ("drawing.dwg",)),
        ("Check sheet E-10", "The cable route is unclear", ()),
    ],
)
def test_plausible_unmatched_order_mail_asks_instead_of_disappearing(
    subject, body, attachments
):
    payload = email(subject, body, attachments)
    with Session.begin() as s:
        row = receive(s, "captured.eml", payload, forwarded=True, mailbox_capture=True)
        assert row["status"] == "needs_confirmation"
        assert row["mail_relevance"]["relevant"]
        assert row["order_id"] is None


def test_existing_thread_routing_works_before_parse_and_never_inherits_revision():
    with Session.begin() as s:
        first = receive(
            s,
            "first.eml",
            email(
                "Job OLD-901 Rev B",
                "Returned comments",
                Message_ID="<first@example.test>",
            ),
            forwarded=True,
            mailbox_capture=True,
        )
        reply = receive(
            s,
            "reply.eml",
            email(
                "Re: Equipment package",
                "Please confirm the note",
                In_Reply_To="<first@example.test>",
            ),
            forwarded=True,
            mailbox_capture=True,
        )
        assert reply["status"] == "matched"
        assert reply["order_id"] == first["order_id"]
        assert reply["revision_label"] == "Unspecified"
        assert reply["detected"]["revision"] == ""
        doc = s.get(Document, reply["document_id"])
        assert doc.meta["revision_label"] == "Unspecified"


def test_thread_reference_to_another_tenant_never_creates_a_match():
    payload = email(
        "Job PRIVATE-901 Rev B",
        "Returned drawings",
        Message_ID="<private@example.test>",
    )
    with identity_scope(Identity(str(uuid4()), "Other PM")), Session.begin() as s:
        receive(s, "private.eml", payload, forwarded=True, mailbox_capture=True)
    with Session.begin() as s:
        row = receive(
            s,
            "reply.eml",
            email(
                "Re: Returned drawings",
                "Please review",
                In_Reply_To="<private@example.test>",
            ),
            forwarded=True,
            mailbox_capture=True,
        )
        assert row["status"] == "needs_confirmation"
        assert row["candidates"] == []


def test_routine_text_does_not_override_an_exact_known_order_thread():
    payload = email(
        "Subscription issue",
        "Please confirm",
        List_Unsubscribe="<mailto:leave@example.test>",
    )
    detected, _ = inspect_file("captured.eml", payload)
    assert classify(payload, detected, [{"id": "known-order"}])["relevant"]


def test_conflicting_identifiers_and_revisions_stay_unmatched():
    with Session.begin() as s:
        first = receive(
            s,
            "first.eml",
            email(
                "Job OLD-901 Rev B",
                "Returned comments",
                Message_ID="<first@example.test>",
            ),
            forwarded=True,
            mailbox_capture=True,
        )
        for subject in ("Job OTHER-991", "Job OLD-901 Rev B and Rev C"):
            reply = receive(
                s,
                subject + ".eml",
                email(subject, "Please review", In_Reply_To="<first@example.test>"),
                forwarded=True,
                mailbox_capture=True,
            )
            assert reply["status"] == "needs_confirmation"
            assert reply["order_id"] is None
            assert reply["candidates"][0]["id"] == first["order_id"]


def test_direct_customer_markup_with_unsubscribe_footer_stays_relevant():
    payload = email(
        "Returned drawings",
        "Please check the cable entry.\nUnsubscribe from marketing in your account.",
    )
    detected, _ = inspect_file("message.eml", payload)
    assert classify(payload, detected, [])["relevant"]


def test_job_found_only_inside_attached_pdf_is_captured_without_a_pm_form(tmp_path):
    from scripts.make_order_replay import write_pdf

    path = tmp_path / "package.pdf"
    write_pdf(
        path,
        "Job ATTACH-901 | Revision C",
        ["Drawing E-10"],
        [{"id": "c1", "text": "Comment 1: Confirm cable entry."}],
    )
    mail = EmailMessage()
    mail["Subject"] = "See attached"
    mail.set_content("Returned for review.")
    mail.add_attachment(
        path.read_bytes(), maintype="application", subtype="pdf", filename=path.name
    )
    with Session.begin() as s:
        result = receive(
            s, "message.eml", mail.as_bytes(), forwarded=True, mailbox_capture=True
        )
        assert result["status"] == "created"
        assert result["detected"]["job_number"] == "ATTACH-901"
        assert result["revision_label"] == "C"


def test_attached_pdf_cannot_override_a_conflicting_email_job(tmp_path):
    from scripts.make_order_replay import write_pdf

    path = tmp_path / "package.pdf"
    write_pdf(path, "Job ATTACH-901 | Revision C", ["Drawing E-10"], [])
    mail = EmailMessage()
    mail["Subject"] = "Job DIFFERENT-902 | Revision C"
    mail.set_content("Please review.")
    mail.add_attachment(
        path.read_bytes(), maintype="application", subtype="pdf", filename=path.name
    )
    with Session.begin() as s:
        result = receive(
            s, "message.eml", mail.as_bytes(), forwarded=True, mailbox_capture=True
        )
        assert result["status"] == "needs_confirmation"
        assert not result["order_id"]
        assert set(result["detected"]["job_numbers"]) == {"ATTACH-901", "DIFFERENT-902"}


def test_reclassification_is_dry_by_default_scoped_and_ignores_human_decisions():
    from backend.mailbox.models import MailboxConnection

    with Session.begin() as s:
        connection = MailboxConnection(
            microsoft_tenant_id=str(uuid4()), mailbox="pm@example.test"
        )
        s.add(connection)
        s.flush()
        ids = []
        for label, disposition, capture in (
            ("routine", False, True),
            ("restored", True, True),
            ("upload", False, False),
        ):
            row = receive(s, label + ".eml", email("Account verification", label))
            saved = s.get(RecordIntake, row["intake_id"])
            if disposition:
                saved.data = {
                    **saved.data,
                    "disposition_history": [{"status": "needs_confirmation"}],
                }
            if capture:
                s.add(
                    MailboxReceipt(
                        connection_id=connection.id,
                        provider_message_id=label,
                        intake_id=saved.id,
                    )
                )
            ids.append(saved.id)
        s.flush()
        report = reclassify_pending(s)
        assert [c["intake_id"] for c in report["changes"]] == [ids[0]]
        assert all(s.get(RecordIntake, id).status == "needs_confirmation" for id in ids)
    with identity_scope(Identity(str(uuid4()), "Other PM")), Session.begin() as s:
        assert reclassify_pending(s, apply=True)["examined"] == 0
    with Session.begin() as s:
        report = reclassify_pending(s, apply=True)
        assert len(report["changes"]) == 1
        assert s.get(RecordIntake, ids[0]).status == "not_order_related"
        assert s.get(RecordIntake, ids[1]).status == "needs_confirmation"
        assert s.get(RecordIntake, ids[2]).status == "needs_confirmation"
        assert list(s.scalars(select(Order))) == []
        assert list(s.scalars(select(Document))) == []
