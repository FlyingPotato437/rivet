"""Mailbox checkpoints and receipts are durable independently of extraction."""

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


class MailboxConnection(Row, Base):
    __tablename__ = "mailbox_connections"
    microsoft_tenant_id: Mapped[str] = mapped_column(String(36))
    mailbox: Mapped[str] = mapped_column(String(254))
    update_recipient: Mapped[str] = mapped_column(String(254), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("organization_id", "microsoft_tenant_id", "mailbox"),
    )


class MailboxFolder(Row, Base):
    __tablename__ = "mailbox_folders"
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("mailbox_connections.id"), index=True
    )
    folder_id: Mapped[str] = mapped_column(String(512))
    provider_folder_id: Mapped[str] = mapped_column(String(512), default="")
    cursor: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(32), default="initial_sync")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_page_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )
    lease: Mapped[str] = mapped_column(String(36), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    resync_count: Mapped[int] = mapped_column(Integer, default=0)
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("connection_id", "folder_id"),)


class MailboxReceipt(Row, Base):
    __tablename__ = "mailbox_receipts"
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("mailbox_connections.id"), index=True
    )
    # Immutable Graph IDs survive a move within the same mailbox. Never lowercase.
    provider_message_id: Mapped[str] = mapped_column(String(1024))
    internet_message_id: Mapped[str] = mapped_column(
        String(1024), default="", index=True
    )
    folder_ids: Mapped[list] = mapped_column(JSONB, default=list)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    intake_id: Mapped[str | None] = mapped_column(
        ForeignKey("record_intakes.id"), index=True
    )
    blob: Mapped[str] = mapped_column(String(90), default="")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )
    update_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )
    lease: Mapped[str] = mapped_column(String(36), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("connection_id", "provider_message_id"),)


class MailboxUpdate(Row, Base):
    """Frozen internal-PM notification; provider acceptance is not delivery proof."""

    __tablename__ = "mailbox_updates"
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("mailbox_connections.id"), index=True
    )
    receipt_id: Mapped[str] = mapped_column(
        ForeignKey("mailbox_receipts.id"), index=True
    )
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("record_agent_runs.id"), index=True
    )
    input_hash: Mapped[str] = mapped_column(String(64))
    run_status: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease: Mapped[str] = mapped_column(String(36), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, index=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_id: Mapped[str] = mapped_column(String(100), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("organization_id", "dedupe_key"),)
