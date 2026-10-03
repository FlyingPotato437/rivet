"""Versioned engineering order graph and explicit review records."""

from alembic import op
from backend.storage.models import (
    Order,
    OrderRevision,
    OrderEvent,
    OrderAction,
    OrderWaiver,
    OrderRelease,
)

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = [Order, OrderRevision, OrderEvent, OrderAction, OrderWaiver, OrderRelease]


def upgrade():
    for model in TABLES:
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in reversed(TABLES):
        model.__table__.drop(op.get_bind(), checkfirst=True)
