"""Versioned, numeric-only example rankers and explicit sharing policy."""

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row


class LearningPolicy(Row, Base):
    __tablename__ = "learning_policies"
    __table_args__ = (UniqueConstraint("organization_id"),)
    private_training: Mapped[bool] = mapped_column(Boolean, default=True)
    share_features: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    actor: Mapped[str] = mapped_column(String(180), default="")
    reason: Mapped[str] = mapped_column(String(1000), default="")


class ExampleRanker(Row, Base):
    __tablename__ = "example_rankers"
    __table_args__ = (UniqueConstraint("organization_id", "scope", "dataset_hash"),)
    scope: Mapped[str] = mapped_column(String(16), default="private")
    dataset_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    weights: Mapped[list] = mapped_column(JSONB, default=list)
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    dependencies: Mapped[dict] = mapped_column(JSONB, default=dict)
