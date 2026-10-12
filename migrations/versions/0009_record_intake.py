"""Team-scoped incoming files awaiting an order match."""

from alembic import op

from backend.storage.models import RecordIntake

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    RecordIntake.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    RecordIntake.__table__.drop(op.get_bind(), checkfirst=True)
