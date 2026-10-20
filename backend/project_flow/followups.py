"""Waiting timers prepare a single unsent follow-up; they never send customer mail."""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from backend.autopilot import outlook
from backend.domain.service import get, scoped
from backend.identity import Identity, identity_scope
from backend.mailbox.models import MailboxConnection
from backend.storage.db import Session
from backend.storage.models import Base, Order, Row, now

from .models import ProjectSentMessage
from .service import project_view, receipts, safe_url


class FollowUp(Row, Base):
    __tablename__ = "order_followups"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    sent_message_id: Mapped[str] = mapped_column(ForeignKey("project_sent_messages.id"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="waiting")
    draft_id: Mapped[str] = mapped_column(String(1024), default="")
    outlook_url: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("sent_message_id"),)


def due_after(sent_at, days=3):
    result = sent_at.astimezone(ZoneInfo("America/Los_Angeles"))
    while days:
        result += timedelta(days=1)
        if result.weekday() < 5:
            days -= 1
    return result.astimezone(sent_at.tzinfo)


def schedule(s, order):
    p = project_view(s, order)
    if p["status"] != "waiting_customer":
        for row in s.scalars(
            scoped(FollowUp).where(
                FollowUp.order_id == order.id, FollowUp.status == "waiting"
            )
        ):
            row.status = "superseded"
        return
    sent = s.scalar(
        scoped(ProjectSentMessage)
        .where(ProjectSentMessage.order_id == order.id)
        .order_by(ProjectSentMessage.sent_at.desc())
        .limit(1)
    )
    if not sent or s.scalar(
        scoped(FollowUp).where(FollowUp.sent_message_id == sent.id)
    ):
        return
    for old in s.scalars(
        scoped(FollowUp).where(
            FollowUp.order_id == order.id, FollowUp.status == "waiting"
        )
    ):
        old.status = "superseded"
    s.add(
        FollowUp(
            order_id=order.id, sent_message_id=sent.id, due_at=due_after(sent.sent_at)
        )
    )


def prepare(id, graph=None):
    from backend.mailbox.service import client_for

    with Session.begin() as s:
        row = get(s, FollowUp, id, True)
        if row.status != "waiting" or row.due_at > now():
            return
        order = get(s, Order, row.order_id)
        if project_view(s, order)["status"] != "waiting_customer":
            row.status = "superseded"
            return
        sent = get(s, ProjectSentMessage, row.sent_message_id)
        c = get(s, MailboxConnection, sent.connection_id)
        from backend.autopilot.models import AutopilotSettings

        policy = s.scalar(scoped(AutopilotSettings))
        if policy and not policy.draft_in_outlook:
            return
        candidates = [
            r
            for r in receipts(s, order.id)
            if r.connection_id == c.id
            and r.data.get("conversation_id") == sent.conversation_id
        ]
        if not c.enabled or not candidates:
            return
        original = candidates[0]
        # Commit the attempt BEFORE the external write. An interrupted attempt
        # is reconciled by a person; it never blindly creates a second draft.
        row.status = "creating"
        original_id = original.provider_message_id
        connection_id = c.id
        body = f"Hello,\n\nFollowing up on our response for {order.number} sent on {sent.sent_at.strftime('%B %d')}. Please let us know if you need any clarification or have an update on the outstanding points.\n\nThank you."
    owned = graph is None
    if not graph:
        with Session() as s:
            graph = client_for(get(s, MailboxConnection, connection_id))
    try:
        existing = outlook.existing_reply(graph, original_id)
        if existing:
            result, status = existing, "existing_draft"
        else:
            result = outlook.create_reply(graph, original_id)
            with Session.begin() as s:
                row = get(s, FollowUp, id, True)
                row.draft_id = result["id"]
            # Recheck incoming mail after draft creation, before changing body.
            with Session() as s:
                row = get(s, FollowUp, id)
                order = get(s, Order, row.order_id)
                if project_view(s, order)["status"] != "waiting_customer":
                    raise ValueError(
                        "New activity arrived while the follow-up was being prepared. Review the empty Outlook draft."
                    )
            existing_body = result.get("body", {})
            outlook.set_body(
                graph,
                result["id"],
                outlook.compose_body(
                    existing_body.get("content", ""),
                    body,
                    existing_body.get("contentType", "html"),
                ),
            )
            status = "drafted"
        with Session.begin() as s:
            row = get(s, FollowUp, id, True)
            row.status = status
            row.draft_id = result["id"]
            row.outlook_url = safe_url(result.get("webLink", ""))
    except Exception:  # noqa: BLE001 - an uncertain write must not be retried blindly
        with Session.begin() as s:
            row = get(s, FollowUp, id, True)
            row.status = "needs_check"
            row.error = "Follow-up creation needs reconciliation in Outlook. No automatic second draft will be created."
    finally:
        if owned:
            graph.close()


def scheduled_pass():
    # Timer visibility is always on. Native drafts require explicit opt-in.
    with Session() as s:
        orders = [(o.id, o.organization_id) for o in s.scalars(select(Order))]
    for id, org in orders:
        with (
            identity_scope(Identity(org, "Rivet follow-up timer")),
            Session.begin() as s,
        ):
            schedule(s, get(s, Order, id))
    if os.getenv("RIVET_FOLLOWUP_DRAFTS_ENABLED", "false").lower() != "true":
        return
    with Session() as s:
        due = [
            (r.id, r.organization_id)
            for r in s.scalars(
                select(FollowUp)
                .where(FollowUp.status == "waiting", FollowUp.due_at <= now())
                .limit(5)
            )
        ]
    for id, org in due:
        with identity_scope(Identity(org, "Rivet follow-up writer")):
            prepare(id)
