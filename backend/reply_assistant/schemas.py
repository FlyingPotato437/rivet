from typing import Literal

from pydantic import Field

from backend.orders.schemas import StrictModel


class GenerateReply(StrictModel):
    order_id: str = Field(min_length=1, max_length=36)
    internet_message_id: str = Field(default="", max_length=1024)
    focus_document_ids: list[str] = Field(default_factory=list, max_length=80)
    instruction: str = Field(
        default="Prepare a response to the returned comments.", max_length=3000
    )


class ReviewReply(StrictModel):
    body: str = Field(default="", max_length=10000)
    decision: Literal["approved", "rejected"]
    use_as_example: bool = False
    expected_evidence_hash: str = Field(min_length=64, max_length=64)
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)


class ComposeReply(StrictModel):
    expected_evidence_hash: str = Field(min_length=64, max_length=64)
    internet_message_id: str = Field(default="", max_length=1024)
