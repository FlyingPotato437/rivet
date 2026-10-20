"""Explicit customer identity and reviewed historical customer memory."""

from alembic import op

from backend.company_context.models import (
    CustomerMemory,
    CustomerProfile,
    OrderCustomerBinding,
)

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    for model in (CustomerProfile, OrderCustomerBinding, CustomerMemory):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (CustomerMemory, OrderCustomerBinding, CustomerProfile):
        model.__table__.drop(op.get_bind(), checkfirst=True)
