"""Private persisted order questions."""

from alembic import op

from backend.records.conversations import RecordConversationMessage

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    RecordConversationMessage.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    RecordConversationMessage.__table__.drop(op.get_bind(), checkfirst=True)
