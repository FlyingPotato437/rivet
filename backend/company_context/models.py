"""Append-only rule versions and feedback; explicit promotion/revocation metadata."""

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row


class CompanyRule(Row, Base):
    __tablename__ = "company_rules"
    __table_args__ = (UniqueConstraint("organization_id", "key", "version"),)
    key: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(180))
    text: Mapped[str] = mapped_column(Text)
    topics: Mapped[list] = mapped_column(JSONB, default=list)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(100), default="")
    provenance: Mapped[dict] = mapped_column(JSONB)
    author: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    reviewed_by: Mapped[str] = mapped_column(String(180), default="")
    reviewed_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_reason: Mapped[str] = mapped_column(Text, default="")


class CompanyFeedback(Row, Base):
    __tablename__ = "company_response_feedback"
    __table_args__ = (UniqueConstraint("organization_id", "order_id", "request_key"),)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    proposal_id: Mapped[str | None] = mapped_column(
        ForeignKey("record_agent_proposals.id"), nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("record_agent_runs.id"), nullable=True
    )
    request_key: Mapped[str] = mapped_column(String(180))
    request_hash: Mapped[str] = mapped_column(String(64))
    generation: Mapped[dict] = mapped_column(JSONB)
    generation_hash: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[list] = mapped_column(JSONB)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    input_text: Mapped[str] = mapped_column(Text)
    original_text: Mapped[str] = mapped_column(Text)
    human_response: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    topics: Mapped[list] = mapped_column(JSONB)
    category: Mapped[str] = mapped_column(String(100))
    reviewer: Mapped[str] = mapped_column(String(180))
    # Human review of a response and permission to reuse it are separate decisions.
    reuse_status: Mapped[str] = mapped_column(
        String(20), default="unapproved", index=True
    )
    reuse_scope: Mapped[str] = mapped_column(String(20), default="order")
    reuse_purpose: Mapped[str] = mapped_column(String(20), default="retrieval")
    approved_by: Mapped[str] = mapped_column(String(180), default="")
    approved_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CompanyContextEvent(Row, Base):
    __tablename__ = "company_context_events"
    subject_type: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(180))
    reason: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class CustomerProfile(Row, Base):
    """An explicitly named tenant customer; display names are never identity keys."""

    __tablename__ = "customer_profiles"
    __table_args__ = (UniqueConstraint("organization_id", "key"),)
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(180))


class OrderCustomerBinding(Row, Base):
    __tablename__ = "order_customer_bindings"
    __table_args__ = (UniqueConstraint("organization_id", "order_id"),)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customer_profiles.id"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_by: Mapped[str] = mapped_column(String(180))
    confirmed_at: Mapped[object] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)


class CustomerMemory(Row, Base):
    """Human-approved historical guidance, never current-order evidence."""

    __tablename__ = "customer_memories"
    __table_args__ = (
        UniqueConstraint("organization_id", "customer_id", "key", "version"),
    )
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customer_profiles.id"), index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(180))
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30))
    topics: Mapped[list] = mapped_column(JSONB, default=list)
    category: Mapped[str] = mapped_column(String(100), default="")
    applicability: Mapped[str] = mapped_column(
        String(30), default="historical_reference"
    )
    applicability_note: Mapped[str] = mapped_column(Text)
    source_order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    provenance: Mapped[dict] = mapped_column(JSONB)
    conflicts_with: Mapped[list] = mapped_column(JSONB, default=list)
    author: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)
    reviewed_by: Mapped[str] = mapped_column(String(180), default="")
    reviewed_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_reason: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
