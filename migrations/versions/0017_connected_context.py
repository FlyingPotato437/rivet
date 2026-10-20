"""Order-scoped external source retrieval and evidence status."""

from alembic import op
from backend.connected_context.models import ConnectedContext

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    ConnectedContext.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ConnectedContext.__table__.drop(op.get_bind(), checkfirst=True)
