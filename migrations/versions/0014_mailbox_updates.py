"""Frozen internal PM-update outbox with provider-idempotent delivery."""

from alembic import op
from sqlalchemy import Column, DateTime, inspect, text

from backend.mailbox.models import MailboxUpdate

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    if "update_check_at" not in {
        c["name"] for c in inspect(op.get_bind()).get_columns("mailbox_receipts")
    }:
        op.add_column(
            "mailbox_receipts",
            Column(
                "update_check_at",
                DateTime(timezone=True),
                server_default=text("now()"),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_mailbox_receipts_update_check_at",
            "mailbox_receipts",
            ["update_check_at"],
        )
    MailboxUpdate.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    MailboxUpdate.__table__.drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_mailbox_receipts_update_check_at", table_name="mailbox_receipts")
    op.drop_column("mailbox_receipts", "update_check_at")
