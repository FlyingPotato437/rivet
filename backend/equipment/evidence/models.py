"""Durable insert-only equipment observations and reconciliation decisions."""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.models import Base, Row


class EquipmentEvidenceClaim(Row, Base):
    __tablename__ = "equipment_evidence_claims"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "order_id",
            "claim_id",
            name="uq_equipment_evidence_claim_scope",
        ),
    )

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    claim_id: Mapped[str] = mapped_column(String(160), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    subject_id: Mapped[str] = mapped_column(Text)
    feature: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20))
    source_sha256: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)


class EquipmentEvidenceRun(Row, Base):
    __tablename__ = "equipment_evidence_runs"

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    graph_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_hash: Mapped[str] = mapped_column(String(64))
    calibration_hash: Mapped[str] = mapped_column(String(64))
    graph: Mapped[dict] = mapped_column(JSONB)


GUARD_FUNCTION = """
CREATE OR REPLACE FUNCTION rivet_guard_equipment_evidence() RETURNS trigger AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Equipment evidence is append-only; updates and deletes are forbidden'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM orders
        WHERE orders.id = NEW.order_id
          AND orders.organization_id = NEW.organization_id
    ) THEN
        RAISE EXCEPTION 'Equipment evidence order/organization mismatch'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def install_guards(connection, table_name):
    """Use the same database boundary for migrations and metadata.create_all."""
    if table_name not in {"equipment_evidence_claims", "equipment_evidence_runs"}:
        raise ValueError("Unknown append-only equipment table")
    connection.exec_driver_sql(GUARD_FUNCTION)
    connection.exec_driver_sql(
        f"CREATE TRIGGER {table_name}_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table_name} FOR EACH ROW EXECUTE FUNCTION rivet_guard_equipment_evidence()"
    )
    connection.exec_driver_sql(
        f"CREATE TRIGGER {table_name}_no_truncate BEFORE TRUNCATE ON {table_name} FOR EACH STATEMENT EXECUTE FUNCTION rivet_guard_equipment_evidence()"
    )


def _after_create(target, connection, **kwargs):
    if connection.dialect.name == "postgresql":
        install_guards(connection, target.name)


def _forbid_orm_change(mapper, connection, target):
    raise ValueError("Equipment evidence is append-only; append a new claim or run")


for _model in (EquipmentEvidenceClaim, EquipmentEvidenceRun):
    event.listen(_model.__table__, "after_create", _after_create)
    event.listen(_model, "before_update", _forbid_orm_change)
    event.listen(_model, "before_delete", _forbid_orm_change)
