"""The connected demo contract, with real PDF bytes and a mail API that cannot send."""

import base64
import hashlib

from sqlalchemy import select
from test_autopilot import QUOTED, FakeMailbox
from test_company_context import identity
from test_connected_context import setup
from test_reply_assistant import receipt
from test_reply_automatic import run

from backend.agents import gateway
from backend.autopilot import outlook
from backend.autopilot import service as autopilot
from backend.company_context.models import CompanyFeedback
from backend.connected_context import providers
from backend.connected_context import service as connected
from backend.reply_assistant import service as replies
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import Document, Order, RecordIntake, now
from scripts.make_order_replay import write_pdf


def drawing(path, job, revision, trip_unit, lifecycle):
    write_pdf(
        path,
        "FICTIONAL DEMO ONLY",
        [
            f"Job: {job}",
            "Sheet: E-10",
            f"Revision: {revision}",
            f"Lifecycle: {lifecycle}",
            f"CB-12 trip unit: {trip_unit}",
        ],
        [],
    )
    data = path.read_bytes()
    return providers.Resource(
        external_id=f"E-10-{revision}",
        job_number=job,
        name=f"Drawing-E-10-Rev-{revision}.pdf",
        revision=revision,
        lifecycle=lifecycle,
        family="E-10",
        content_base64=base64.b64encode(data).decode(),
        sha256=hashlib.sha256(data).hexdigest(),
        facts={"CB-12.trip_unit": trip_unit},
    )


def test_connected_reply_attaches_released_pdf_then_records_the_sent_edit(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(gateway, "configured", lambda: True)
    with identity():
        order_id, job, records, _path = setup(monkeypatch, tmp_path)
        released = drawing(tmp_path / "B.pdf", job, "B", "TU-2500-DEMO", "released")
        draft = drawing(tmp_path / "C.pdf", job, "C", "TU-3000-DEMO", "draft")
        records["vault"] = [released, draft]
        with Session.begin() as s:
            order = s.get(Order, order_id)
            original = s.scalar(
                select(Document).where(Document.project_id == order.project_id)
            )
            incoming = receipt(s, order_id, original.id)
            incoming.status = "imported"
            incoming.data = {**incoming.data, "received_at": now().isoformat()}
            s.get(RecordIntake, incoming.intake_id).status = "matched"

        def prepare(snapshot):
            context = snapshot["current_order"]["connected_context"]
            assert context["status"] == "current"
            assert any(f["kind"] == "source_conflict" for f in context["findings"])
            return {
                "subject": "Re: returned drawing comments",
                "body": "Released drawing B identifies TU-2500-DEMO. The specification differs. Please confirm which requirement governs before engineering revises the drawing.",
                "source_ids": [s["id"] for s in snapshot["current_order"]["sources"]],
                "unresolved": ["Confirm the governing trip unit."],
            }

        monkeypatch.setattr(replies, "model_reply", prepare)
        connected.synchronize(order_id)
    run()
    graph = FakeMailbox()
    placed = autopilot.deliver(client_factory=graph)
    assert placed[0]["status"] == "created"
    assert graph.draft()["attachments"] == ["Drawing-E-10-Rev-B.pdf"]
    assert placed[0]["attachments"][0]["sha256"] == released.sha256
    assert placed[0]["tagged"] is True
    assert any(
        outlook.TAG in message.get("categories", [])
        for message in graph.messages.values()
    )
    with identity(), Session() as s:
        prepared = s.scalar(select(ReplyDraft))
        assert prepared.status == "ready"
        assert prepared.unresolved
        assert all("source=" in source["url"] for source in prepared.source_refs)
        assert autopilot.activity(s)["summary"]["waiting_in_outlook"] == 1

    # A normal Outlook edit and send is observed; Rivet has no send endpoint.
    edited = "Please confirm the governing trip unit so we can resolve the discrepancy before the next submittal."
    graph.draft().update(
        {
            "isDraft": False,
            "sentDateTime": now().isoformat(),
            "body": {"contentType": "html", "content": f"<p>{edited}</p>" + QUOTED},
        }
    )
    result = autopilot.sync_sent(client_factory=graph)[0]
    assert result["outcome"] == "edited"
    assert result["learning_status"] == "approved"
    with identity(), Session() as s:
        feedback = s.scalar(select(CompanyFeedback))
        assert feedback.human_response == edited
        activity = autopilot.activity(s)
        assert activity["summary"]["waiting_in_outlook"] == 0
        sent = next(item for item in activity["feed"] if item["kind"] == "sent")
        assert (sent["outcome"], sent["learning_status"]) == ("edited", "approved")
    assert autopilot.deliver(client_factory=graph) == []
