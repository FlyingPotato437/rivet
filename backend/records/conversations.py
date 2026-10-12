"""Private, tenant-scoped question history. Shared agent work lives separately."""

from fastapi import APIRouter
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.domain.service import fail, scoped
from backend.identity import current_actor
from backend.orders.service import lock_order
from backend.storage.db import Session
from backend.storage.models import Base, Row


class RecordConversationMessage(Row, Base):
    __tablename__ = "record_conversation_messages"
    __table_args__ = (
        UniqueConstraint("organization_id", "order_id", "user_id", "request_key"),
    )
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(180), index=True)
    question: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSONB)
    request_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


router = APIRouter(prefix="/api", tags=["Order questions"])


def messages(s, order_id, limit=40):
    return list(
        reversed(
            s.scalars(
                scoped(RecordConversationMessage)
                .where(
                    RecordConversationMessage.order_id == order_id,
                    RecordConversationMessage.user_id == current_actor(),
                )
                .order_by(
                    RecordConversationMessage.created_at.desc(),
                    RecordConversationMessage.id.desc(),
                )
                .limit(limit)
            ).all()
        )
    )


def replay(s, order_id, key, question):
    if not key:
        return None
    row = s.scalar(
        scoped(RecordConversationMessage).where(
            RecordConversationMessage.order_id == order_id,
            RecordConversationMessage.user_id == current_actor(),
            RecordConversationMessage.request_key == key,
        )
    )
    if row and row.question != question:
        fail("This request key was already used for a different question.", 409)
    return row.result if row else None


@router.get("/orders/{id}/record/conversation")
def conversation(id: str):
    with Session.begin() as s:
        lock_order(s, id)
        return {
            "order_id": id,
            "messages": [
                {
                    "id": m.id,
                    "question": m.question,
                    "result": m.result,
                    "created_at": m.created_at.isoformat(),
                }
                for m in messages(s, id)
            ],
        }


def ask_with_history(order_id, body, key=None):
    from . import service
    from .assistant import ask

    if key and len(key) > 200:
        fail("The request key is too long.")
    with Session.begin() as s:
        order = lock_order(s, order_id)
        previous = replay(s, order_id, key, body.question)
        if previous is not None:
            return previous
        snapshot = service.view(s, order)
        history = []
        for m in messages(s, order_id, 3):
            history.extend(
                [
                    {"role": "user", "content": m.question[:6000]},
                    {
                        "role": "assistant",
                        "content": str(m.result.get("answer", ""))[:6000],
                    },
                ]
            )
    # No database lock is held while waiting for the model. Citations are checked
    # against this exact snapshot by assistant.ask before anything is persisted.
    result = ask(body.question, snapshot, history)
    with Session.begin() as s:
        lock_order(s, order_id)
        previous = replay(s, order_id, key, body.question)
        if previous is not None:
            return previous
        s.add(
            RecordConversationMessage(
                order_id=order_id,
                user_id=current_actor(),
                question=body.question,
                result=result,
                request_key=key,
            )
        )
    return result
