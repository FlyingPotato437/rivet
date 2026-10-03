from datetime import datetime, timezone
from uuid import uuid4
from decimal import Decimal
from sqlalchemy import (
    String,
    Integer,
    DateTime,
    Numeric,
    Text,
    ForeignKey,
    UniqueConstraint,
    Boolean,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from backend.identity import current_org, current_actor


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Row:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    organization_id: Mapped[str] = mapped_column(
        String(36), default=current_org, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Project(Row, Base):
    __tablename__ = "projects"
    title: Mapped[str] = mapped_column(String(180))
    customer: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(100), default="Low-voltage switchgear")
    due_date: Mapped[str | None] = mapped_column(String(10))
    input_revision: Mapped[int] = mapped_column(Integer, default=1)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    color: Mapped[str] = mapped_column(String(20), default="violet")


class Quote(Row, Base):
    __tablename__ = "quotes"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True)
    number: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    terms: Mapped[str] = mapped_column(
        Text,
        default="Valid for 30 days. Payment: net 30. Freight, installation, and applicable taxes excluded. Delivery subject to written supplier confirmation.",
    )
    notes: Mapped[str] = mapped_column(Text, default="")
    reconciled_input: Mapped[int] = mapped_column(Integer, default=1)


class Line(Row, Base):
    __tablename__ = "lines"
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    tag: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    unit: Mapped[str] = mapped_column(String(30), default="each")
    model: Mapped[str] = mapped_column(String(150), default="")
    cost: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    price: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    lead_time: Mapped[str] = mapped_column(Text, default="Needs confirmation")
    review: Mapped[str] = mapped_column(String(20), default="unreviewed")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("quote_id", "tag"),)


class Document(Row, Base):
    __tablename__ = "documents"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(30), default="schedule")
    blob: Mapped[str] = mapped_column(String(90))
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="queued")
    coverage: Mapped[list] = mapped_column(JSONB, default=list)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class Span(Row, Base):
    __tablename__ = "spans"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    location: Mapped[dict] = mapped_column(JSONB)


class Requirement(Row, Base):
    __tablename__ = "requirements"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    tag: Mapped[str] = mapped_column(String(60))
    attribute: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict] = mapped_column(JSONB)
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    input_revision: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Catalog(Row, Base):
    __tablename__ = "catalog_items"
    manufacturer: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Offer(Row, Base):
    __tablename__ = "supplier_offers"
    catalog_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"))
    supplier: Mapped[str] = mapped_column(String(100))
    cost: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    unit: Mapped[str] = mapped_column(String(20), default="each")
    valid_until: Mapped[str | None] = mapped_column(String(10))
    max_quantity: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    lead_time: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSONB, default=list)


class Proposal(Row, Base):
    __tablename__ = "proposals"
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    base_version: Mapped[int] = mapped_column(Integer)
    input_revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    operations: Mapped[list] = mapped_column(JSONB)
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    reason: Mapped[str] = mapped_column(Text, default="")


class Version(Row, Base):
    __tablename__ = "quote_versions"
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    input_revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("quote_id", "version"),)


class Event(Row, Base):
    __tablename__ = "events"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="edit")
    actor: Mapped[str] = mapped_column(String(100), default=current_actor)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)


class Approval(Row, Base):
    __tablename__ = "approvals"
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"))
    version: Mapped[int] = mapped_column(Integer)
    input_revision: Mapped[int] = mapped_column(Integer)
    policy: Mapped[str] = mapped_column(String(80), default="buy-sell-v1")
    actor: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)


class Export(Row, Base):
    __tablename__ = "exports"
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("quote_versions.id"))
    format: Mapped[str] = mapped_column(String(10))
    blob: Mapped[str] = mapped_column(String(90))
    sha256: Mapped[str] = mapped_column(String(64))
    audience: Mapped[str] = mapped_column(String(20), default="customer")


class Job(Row, Base):
    __tablename__ = "jobs"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    lease: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    key: Mapped[str] = mapped_column(String(160), unique=True)


class Run(Row, Base):
    __tablename__ = "agent_runs"
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), index=True)
    goal: Mapped[str] = mapped_column(Text)
    base_version: Mapped[int] = mapped_column(Integer)
    input_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    plan: Mapped[list] = mapped_column(JSONB, default=list)
    steps: Mapped[list] = mapped_column(JSONB, default=list)
    overlay: Mapped[list] = mapped_column(JSONB, default=list)
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    questions: Mapped[list] = mapped_column(JSONB, default=list)
    answers: Mapped[list] = mapped_column(JSONB, default=list)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    model: Mapped[str] = mapped_column(String(80), default="")


class Idempotency(Row, Base):
    __tablename__ = "idempotency"
    key: Mapped[str] = mapped_column(String(200), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)


class Clarification(Row, Base):
    __tablename__ = "clarifications"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    question: Mapped[str] = mapped_column(Text)
    recipient: Mapped[str] = mapped_column(String(200), default="")
    due_date: Mapped[str | None] = mapped_column(String(10))
    line_ids: Mapped[list] = mapped_column(JSONB, default=list)
    source_ids: Mapped[list] = mapped_column(JSONB, default=list)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    last_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    answer: Mapped[str] = mapped_column(Text, default="")
    answer_source_ids: Mapped[list] = mapped_column(JSONB, default=list)
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Order(Row, Base):
    __tablename__ = "orders"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True)
    number: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    state: Mapped[dict] = mapped_column(JSONB, default=dict)
    snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderRevision(Row, Base):
    __tablename__ = "order_revisions"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    revision_label: Mapped[str] = mapped_column(String(40))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("order_id", "version"),)


class OrderEvent(Row, Base):
    __tablename__ = "order_events"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class OrderAction(Row, Base):
    __tablename__ = "order_actions"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(240))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    base_version: Mapped[int] = mapped_column(Integer)
    before: Mapped[dict] = mapped_column(JSONB, default=dict)
    after: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_ids: Mapped[list] = mapped_column(JSONB, default=list)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text, default="")
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)


class OrderWaiver(Row, Base):
    __tablename__ = "order_waivers"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    check_id: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    signer: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)


class OrderRelease(Row, Base):
    __tablename__ = "order_releases"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    signer: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text)


class OrderRecord(Row, Base):
    __tablename__ = "order_records"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class RecordShare(Row, Base):
    __tablename__ = "record_shares"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OrderInbox(Row, Base):
    __tablename__ = "order_inboxes"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    address: Mapped[str] = mapped_column(String(254), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class InboundEmail(Row, Base):
    __tablename__ = "inbound_emails"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    provider_id: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    error: Mapped[str] = mapped_column(Text, default="")


class NoticeDelivery(Row, Base):
    __tablename__ = "notice_deliveries"
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    notice_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    payload: Mapped[dict] = mapped_column(JSONB)
    provider_ids: Mapped[list] = mapped_column(JSONB, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(100), default=current_actor)
    __table_args__ = (UniqueConstraint("order_id", "notice_id"),)
