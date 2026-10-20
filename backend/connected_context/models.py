from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row


class ConnectedContext(Row, Base):
    __tablename__ = "connected_context"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    configuration_hash: Mapped[str] = mapped_column(String(64), default="")
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (UniqueConstraint("organization_id", "order_id"),)
