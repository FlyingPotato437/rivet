"""Approved artifact publication receipts."""

from alembic import op
from backend.workflow_actions.publication import Publication

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    Publication.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    Publication.__table__.drop(op.get_bind(), checkfirst=True)
