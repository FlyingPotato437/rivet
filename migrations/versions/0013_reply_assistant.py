"""Auditable Outlook draft generation and human review."""
from alembic import op
from backend.reply_assistant.models import ReplyDraft

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    ReplyDraft.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ReplyDraft.__table__.drop(op.get_bind(), checkfirst=True)
