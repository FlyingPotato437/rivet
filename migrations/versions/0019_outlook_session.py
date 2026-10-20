"""Persist Outlook sessions without storing browser-readable credentials."""

from alembic import op

from backend.outlook_session import OutlookSession

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    OutlookSession.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    OutlookSession.__table__.drop(op.get_bind(), checkfirst=True)
