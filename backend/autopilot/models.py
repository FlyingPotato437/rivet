"""Autopilot state: per-team policy and the Outlook drafts Rivet placed for people."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row, now


class AutopilotSettings(Row, Base):
    """What Rivet may do without being asked. Sending to a customer is never one of them."""

    __tablename__ = "autopilot_settings"
    draft_in_outlook: Mapped[bool] = mapped_column(Boolean, default=True)
    tag_in_outlook: Mapped[bool] = mapped_column(Boolean, default=True)
    # "auto": a reply the PM sent becomes a reusable team example; "review": an
    # administrator approves examples, as before.
    learn_from_sent: Mapped[str] = mapped_column(String(16), default="auto")
    updated_by: Mapped[str] = mapped_column(String(180), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("organization_id"),)


class OutlookDraft(Row, Base):
    """One unsent native reply per prepared reply. Outlook keeps the thread; a person sends."""

    __tablename__ = "outlook_drafts"
    reply_draft_id: Mapped[str] = mapped_column(
        ForeignKey("reply_drafts.id"), index=True
    )
    receipt_id: Mapped[str | None] = mapped_column(
        ForeignKey("mailbox_receipts.id"), index=True
    )
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("mailbox_connections.id"), index=True
    )
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    # created/existing_draft -> sent | discarded; pending/creating while Rivet works; failed and
    # permission_required retry later; superseded when newer mail replaced the need.
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    graph_draft_id: Mapped[str] = mapped_column(String(1024), default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSONB, default=list)
    notes: Mapped[list] = mapped_column(JSONB, default=list)
    tagged: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str] = mapped_column(String(16), default="")
    feedback_id: Mapped[str] = mapped_column(String(36), default="")
    learning_status: Mapped[str] = mapped_column(String(24), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease: Mapped[str] = mapped_column(String(36), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )
    drafted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A sent draft can be briefly unreadable while Outlook moves it to Sent Items.
    missing_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("organization_id", "reply_draft_id"),)
