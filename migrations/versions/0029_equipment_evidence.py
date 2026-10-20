"""Append-only, tenant-bound equipment claims and reconciliation runs."""

from alembic import op

from backend.equipment.evidence.models import (
    EquipmentEvidenceClaim,
    EquipmentEvidenceRun,
)

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    for model in (EquipmentEvidenceClaim, EquipmentEvidenceRun):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (EquipmentEvidenceRun, EquipmentEvidenceClaim):
        model.__table__.drop(op.get_bind(), checkfirst=True)
    op.execute("DROP FUNCTION IF EXISTS rivet_guard_equipment_evidence()")
