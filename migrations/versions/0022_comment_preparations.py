"""Durable independently prepared comment responses."""

from alembic import op
from backend.records.agent_tasks import CommentPreparation

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    CommentPreparation.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    CommentPreparation.__table__.drop(op.get_bind(), checkfirst=True)
