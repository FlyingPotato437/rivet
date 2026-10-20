"""Durable Microsoft 365 folder reconciliation and message receipt ledger."""

from alembic import op

from backend.mailbox.models import MailboxConnection, MailboxFolder, MailboxReceipt

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    for model in (MailboxConnection, MailboxFolder, MailboxReceipt):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (MailboxReceipt, MailboxFolder, MailboxConnection):
        model.__table__.drop(op.get_bind(), checkfirst=True)
