"""Durable team email intake before an order is known."""

from alembic import op
from sqlalchemy import Column, ForeignKey, String, inspect, text

from backend.storage.models import TeamInbox

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    TeamInbox.__table__.create(bind, checkfirst=True)
    columns = {column["name"] for column in inspect(bind).get_columns("inbound_emails")}
    for name, target in (
        ("team_inbox_id", "team_inboxes.id"),
        ("intake_id", "record_intakes.id"),
    ):
        if name not in columns:
            op.add_column(
                "inbound_emails",
                Column(name, String(36), ForeignKey(target), nullable=True),
            )
            op.create_index("ix_inbound_emails_" + name, "inbound_emails", [name])
    op.alter_column("inbound_emails", "order_id", nullable=True)
    op.alter_column("jobs", "project_id", nullable=True)


def downgrade():
    bind = op.get_bind()
    if bind.scalar(
        text("SELECT COUNT(*) FROM inbound_emails WHERE order_id IS NULL")
    ) or bind.scalar(text("SELECT COUNT(*) FROM jobs WHERE project_id IS NULL")):
        raise RuntimeError(
            "Preserve or resolve team intake and its jobs before removing team forwarding."
        )
    op.alter_column("inbound_emails", "order_id", nullable=False)
    op.alter_column("jobs", "project_id", nullable=False)
    for name in ("intake_id", "team_inbox_id"):
        op.drop_index("ix_inbound_emails_" + name, table_name="inbound_emails")
        op.drop_column("inbound_emails", name)
    TeamInbox.__table__.drop(bind, checkfirst=True)
