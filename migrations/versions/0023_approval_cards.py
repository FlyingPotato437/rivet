"""Evidence-bound approval cards."""

from alembic import op
from backend.notifications.models import ApprovalCard

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    ApprovalCard.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ApprovalCard.__table__.drop(op.get_bind(), checkfirst=True)
