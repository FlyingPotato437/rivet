from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row


class ApprovalCard(Row, Base):
    __tablename__ = "approval_cards"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    package_id: Mapped[str] = mapped_column(String(36))
    gate: Mapped[str] = mapped_column(String(40))
    order_version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    source_ids: Mapped[list] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint("order_id", "package_id", "gate", "order_version"),
    )
