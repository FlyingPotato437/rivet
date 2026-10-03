"""Independent comment records and immutable read-only shares."""

from alembic import op
from backend.storage.models import OrderRecord, RecordShare

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    for model in (OrderRecord, RecordShare):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (RecordShare, OrderRecord):
        model.__table__.drop(op.get_bind(), checkfirst=True)
