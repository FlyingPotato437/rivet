from typing import Literal

from pydantic import Field, field_validator

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


class NoticeRecipientsWrite(Write):
    recipient_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("recipient_ids")
    @classmethod
    def unique_recipients(cls, values):
        if any(not value or len(value) > 100 for value in values):
            raise ValueError("Choose valid saved recipient IDs.")
        if len(values) != len(set(values)):
            raise ValueError("Choose each recipient only once.")
        return values


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


class SuggestionWrite(Write):
    action: Literal["accept", "skip"]
    target_source_id: str = Field(default="", max_length=100)
    draft: str = Field(default="", max_length=32000)


class TaskWrite(Write):
    role: Literal["pm", "drafting", "production", "commercial"]
    owner: str = Field(default="", max_length=180)
    status: Literal["open", "in_progress", "done"]
    note: str = Field(default="", max_length=4000)


class CoordinationSettingsWrite(Write):
    weekly_digest_enabled: bool
    customer_due_date: str = ""
    role_owners: dict[Literal["pm", "drafting", "production", "commercial"], str]

    @field_validator("customer_due_date")
    @classmethod
    def valid_date(cls, value):
        from datetime import date

        if value:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("Use a YYYY-MM-DD date.")
        return value

    @field_validator("role_owners")
    @classmethod
    def valid_owners(cls, value):
        if any(len(owner) > 180 for owner in value.values()):
            raise ValueError("Owner names must be at most 180 characters.")
        return {
            role: value.get(role, "")
            for role in ("pm", "drafting", "production", "commercial")
        }
