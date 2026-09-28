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
from .db import ORG


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Row:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    organization_id: Mapped[str] = mapped_column(String(36), default=ORG, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Project(Row, Base):
    __tablename__ = "projects"
    title: Mapped[str] = mapped_column(String(180))
    customer: Mapped[str] = mapped_column(String(180))
    category: Mapped[str] = mapped_column(String(100), default="Power distribution")
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
    actor: Mapped[str] = mapped_column(String(100), default="Local estimator")
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
