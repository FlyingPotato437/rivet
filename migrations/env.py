from alembic import context

from backend.records.conversations import (
    RecordConversationMessage,  # noqa: F401 - register table metadata
)
from backend.storage.db import engine
from backend.storage.models import Base
from backend.equipment.evidence import models as equipment_evidence_models  # noqa: F401 - register append-only evidence tables
from backend.mailbox import models as mailbox_models  # noqa: F401
from backend.company_context import models as company_models  # noqa: F401
from backend.reply_assistant import models as reply_models  # noqa: F401
from backend.connected_context import models as connected_models  # noqa: F401
from backend.autopilot import models as autopilot_models  # noqa: F401
from backend.outlook_session import OutlookSession  # noqa: F401
from backend.improvement import models as improvement_models  # noqa: F401
from backend.project_flow import models as project_flow_models  # noqa: F401
from backend.equipment import profile_models as equipment_profile_models  # noqa: F401

with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
