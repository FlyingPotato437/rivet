"""Per-order receiving addresses and durable email delivery records."""

from alembic import op
from backend.storage.models import OrderInbox, InboundEmail, NoticeDelivery

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    for model in (OrderInbox, InboundEmail, NoticeDelivery):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (NoticeDelivery, InboundEmail, OrderInbox):
        model.__table__.drop(op.get_bind(), checkfirst=True)
