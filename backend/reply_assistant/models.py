from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row


class ReplyDraft(Row, Base):
    __tablename__ = "reply_drafts"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    request_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    internet_message_id: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(24), default="generating")
    evidence_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    original_body: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_refs: Mapped[list] = mapped_column(JSONB, default=list)
    unresolved: Mapped[list] = mapped_column(JSONB, default=list)
    coverage: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    review: Mapped[dict] = mapped_column(JSONB, default=dict)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("organization_id", "request_key"),)
