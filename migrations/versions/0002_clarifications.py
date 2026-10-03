"""Persistent coordination issues; existing projects and quotes are unchanged."""

from alembic import op

from backend.storage.models import Clarification

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # 0001 creates current metadata on a fresh installation; checkfirst also
    # supports upgrading an existing installation without touching its data.
    Clarification.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    Clarification.__table__.drop(op.get_bind(), checkfirst=True)
