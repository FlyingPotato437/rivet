"""Allow internal capture-confirmation updates before an order exists."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("mailbox_updates", "order_id", nullable=True)
    op.alter_column("mailbox_updates", "run_id", nullable=True)


def downgrade():
    # Preserve undelivered audit records; an operator must remove or archive
    # pre-order notices explicitly before restoring the former constraints.
    op.alter_column("mailbox_updates", "order_id", nullable=False)
    op.alter_column("mailbox_updates", "run_id", nullable=False)
