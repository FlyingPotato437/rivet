"""Persistent order coordination runs and reviewable proposals."""

from alembic import op

from backend.storage.models import RecordAgentProposal, RecordAgentRun

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    RecordAgentRun.__table__.create(op.get_bind(), checkfirst=True)
    RecordAgentProposal.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    RecordAgentProposal.__table__.drop(op.get_bind(), checkfirst=True)
    RecordAgentRun.__table__.drop(op.get_bind(), checkfirst=True)
