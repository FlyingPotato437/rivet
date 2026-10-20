"""Autopilot settings and the Outlook drafts Rivet places for people."""

from alembic import op
from backend.autopilot.models import AutopilotSettings, OutlookDraft

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    AutopilotSettings.__table__.create(op.get_bind(), checkfirst=True)
    OutlookDraft.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    OutlookDraft.__table__.drop(op.get_bind(), checkfirst=True)
    AutopilotSettings.__table__.drop(op.get_bind(), checkfirst=True)
