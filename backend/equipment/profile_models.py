"""Company-reviewed drawing conventions, immutable versions and order bindings."""

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row, now


class ManufacturerProfileVersion(Row, Base):
    __tablename__ = "equipment_profile_versions"
    __table_args__ = (UniqueConstraint("organization_id", "version_hash"),)
    profile_key: Mapped[str] = mapped_column(String(100), index=True)
    version_hash: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("equipment_profile_versions.id"), nullable=True
    )
    manifest: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(180))


class ManufacturerProfileRevocation(Row, Base):
    __tablename__ = "equipment_profile_revocations"
    __table_args__ = (UniqueConstraint("organization_id", "profile_version_id"),)
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("equipment_profile_versions.id"), index=True
    )
    reason: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(String(180))


class OrderManufacturerProfile(Row, Base):
    __tablename__ = "equipment_order_profiles"
    __table_args__ = (UniqueConstraint("organization_id", "order_id"),)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    profile_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("equipment_profile_versions.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_by: Mapped[str] = mapped_column(String(180))
    confirmed_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)
    reason: Mapped[str] = mapped_column(Text)
