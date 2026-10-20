from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from backend.orders.schemas import StrictModel


class RuleWrite(StrictModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    expected_version: int = Field(default=0, ge=0)
    title: str = Field(min_length=2, max_length=180)
    text: str = Field(min_length=3, max_length=6000)
    topics: list[str] = Field(default_factory=list, max_length=12)
    order_id: str | None = Field(default=None, max_length=36)
    source_order_id: str | None = Field(default=None, max_length=36)
    category: str = Field(default="", max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=30)
    provenance_note: str = Field(min_length=8, max_length=2000)


class Review(StrictModel):
    reason: str = Field(min_length=8, max_length=2000)


class FeedbackWrite(StrictModel):
    proposal_id: str = Field(min_length=1, max_length=36)
    operation_index: int = Field(ge=0, le=100)
    outcome: Literal["accepted", "edited", "rejected"]
    human_response: str = Field(default="", max_length=6000)
    reason: str = Field(min_length=8, max_length=2000)
    topics: list[str] = Field(default_factory=list, max_length=12)


class ReuseReview(Review):
    scope: Literal["order", "company"] = "order"
    purpose: Literal["retrieval", "evaluation"] = "retrieval"
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Use a timezone-aware expiration date.")
        return value


class ContextQuery(StrictModel):
    order_id: str = Field(min_length=1, max_length=36)
    query: str = Field(min_length=2, max_length=6000)
    topics: list[str] = Field(default_factory=list, max_length=12)


class CustomerWrite(StrictModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=2, max_length=180)
    description: str = Field(default="", max_length=2000)


class CustomerBindingWrite(Review):
    customer_id: str | None = Field(default=None, max_length=36)
    expected_version: int = Field(default=0, ge=0)


class CustomerNoteWrite(StrictModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    expected_version: int = Field(default=0, ge=0)
    title: str = Field(min_length=2, max_length=180)
    text: str = Field(min_length=3, max_length=6000)
    kind: Literal["preference", "requirement", "process"] = "preference"
    topics: list[str] = Field(default_factory=list, max_length=12)
    category: str = Field(default="", max_length=100)
    applicability: Literal["historical_reference", "standing_preference"] = (
        "historical_reference"
    )
    applicability_note: str = Field(min_length=8, max_length=2000)
    source_order_id: str = Field(min_length=1, max_length=36)
    source_ids: list[str] = Field(min_length=1, max_length=20)
    provenance_note: str = Field(min_length=8, max_length=2000)
    conflicts_with: list[str] = Field(default_factory=list, max_length=20)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Use a timezone-aware expiration date.")
        return value


class CustomerNoteReview(Review):
    expected_version: int = Field(ge=1)


class CustomerHistorySearch(StrictModel):
    query: str = Field(min_length=2, max_length=400)


class CustomerDocumentCheck(StrictModel):
    document_ids: list[str] = Field(min_length=1, max_length=20)
