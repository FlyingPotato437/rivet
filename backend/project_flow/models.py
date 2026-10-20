from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row, now


class ProjectWorkspace(Row, Base):
    """Display metadata is separate from job identifiers and engineering evidence."""

    __tablename__ = "project_workspaces"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    name: Mapped[str] = mapped_column(String(180), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    changes: Mapped[list] = mapped_column(JSONB, default=list)


class ProjectThread(Row, Base):
    __tablename__ = "project_threads"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("mailbox_connections.id"))
    conversation_id: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("connection_id", "conversation_id"),)


class ProjectSentMessage(Row, Base):
    """A confirmed Sent Items message, not an inference from a missing draft."""

    __tablename__ = "project_sent_messages"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("mailbox_connections.id"))
    provider_id: Mapped[str] = mapped_column(String(1024))
    internet_message_id: Mapped[str] = mapped_column(
        String(1024), default="", index=True
    )
    conversation_id: Mapped[str] = mapped_column(String(1024))
    subject: Mapped[str] = mapped_column(String(2000), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    recipients: Mapped[list] = mapped_column(JSONB, default=list)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outlook_url: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(24), default="outlook")
    __table_args__ = (UniqueConstraint("connection_id", "provider_id"),)
