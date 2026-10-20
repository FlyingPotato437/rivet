"""Opt-in internal notification outbox."""

from alembic import op
from backend.notifications.delivery import InternalNotification

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    InternalNotification.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    InternalNotification.__table__.drop(op.get_bind(), checkfirst=True)
