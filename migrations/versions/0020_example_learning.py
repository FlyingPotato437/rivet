"""Scoped trainable example ranking and explicit aggregate-feature consent."""

from alembic import op
from backend.improvement.models import ExampleRanker, LearningPolicy

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    LearningPolicy.__table__.create(op.get_bind(), checkfirst=True)
    ExampleRanker.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ExampleRanker.__table__.drop(op.get_bind(), checkfirst=True)
    LearningPolicy.__table__.drop(op.get_bind(), checkfirst=True)
