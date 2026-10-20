"""Waiting timers and native unsent follow-ups."""

from alembic import op
from backend.project_flow.followups import FollowUp

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    FollowUp.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    FollowUp.__table__.drop(op.get_bind(), checkfirst=True)
