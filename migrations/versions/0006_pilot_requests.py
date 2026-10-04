"""Public pilot requests are independent of organization data."""

from alembic import op

from backend.storage.models import PilotRequest

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    PilotRequest.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    PilotRequest.__table__.drop(op.get_bind(), checkfirst=True)
