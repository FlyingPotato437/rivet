"""Named projects and confirmed email communication history."""

from alembic import op
from backend.project_flow.models import (
    ProjectWorkspace,
    ProjectThread,
    ProjectSentMessage,
)

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    for model in (ProjectWorkspace, ProjectThread, ProjectSentMessage):
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in (ProjectSentMessage, ProjectThread, ProjectWorkspace):
        model.__table__.drop(op.get_bind(), checkfirst=True)
