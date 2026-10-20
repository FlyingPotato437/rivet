"""Private company rules, reviewed examples, and feedback provenance."""

from alembic import op

from backend.company_context.models import (
    CompanyContextEvent,
    CompanyFeedback,
    CompanyRule,
)

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    for model in (CompanyRule, CompanyFeedback, CompanyContextEvent):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (CompanyContextEvent, CompanyFeedback, CompanyRule):
        model.__table__.drop(op.get_bind(), checkfirst=True)
