"""Microsoft notification subscription checkpoints."""

from alembic import op
from backend.mailbox.notifications import GraphSubscription

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    GraphSubscription.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    GraphSubscription.__table__.drop(op.get_bind(), checkfirst=True)
