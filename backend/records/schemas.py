from typing import Literal

from pydantic import Field

from backend.orders.schemas import StrictModel


class Write(StrictModel):
    expected_version: int = Field(ge=1)
    actor: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=3, max_length=2000)


class CommentWrite(Write):
    text: str = Field(min_length=1, max_length=16000)
    number: str = Field(min_length=1, max_length=40)
    revision: str = Field(default="", max_length=40)
    author: str = Field(default="", max_length=180)
    authored_at: str = Field(default="", max_length=80)
    status: Literal["open", "responded", "closed"] = "open"
    response: str = Field(default="", max_length=16000)
    responder: str = Field(default="", max_length=180)
    target_source_id: str = ""
    linked_email_id: str = ""
    reviewed: bool = False


class ChangeWrite(Write):
    title: str = Field(min_length=1, max_length=240)
    before: str = Field(default="", max_length=2000)
    after: str = Field(min_length=1, max_length=2000)
    from_revision: str = Field(default="", max_length=40)
    to_revision: str = Field(default="", max_length=40)
    comment_ids: list[str] = Field(default_factory=list, max_length=100)
    requested_by: str = Field(default="", max_length=180)
    approved_by: str = Field(default="", max_length=180)
    approved_at: str = Field(default="", max_length=80)


class SubscriberWrite(Write):
    name: str = Field(min_length=1, max_length=180)
    email: str = Field(min_length=3, max_length=254)
    team: Literal["Manufacturer", "Customer", "Production"]


class ApprovalWrite(Write):
    label: str = Field(min_length=1, max_length=180)


class ShareWrite(Write):
    approval_id: str


class ConversationMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class Ask(StrictModel):
    question: str = Field(min_length=1, max_length=6000)
    history: list[ConversationMessage] = Field(default_factory=list, max_length=6)
