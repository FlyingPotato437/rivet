from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OrderCreate(StrictModel):
    title: str = Field(min_length=1, max_length=180)
    customer: str = Field(min_length=1, max_length=180)
    number: str = Field(default="", max_length=80)
    category: str = Field(default="Custom equipment", min_length=1, max_length=100)
    synthetic: bool = False


class OrderVersion(StrictModel):
    expected_version: int = Field(ge=1)


class OrderDecision(OrderVersion):
    reason: str = Field(min_length=1, max_length=4000)
    edited_text: str | None = Field(default=None, max_length=12000)


class OrderWaive(OrderVersion):
    check_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=64, max_length=64)
    signer: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=8, max_length=4000)


class OrderReleaseRequest(OrderVersion):
    snapshot_hash: str = Field(min_length=64, max_length=64)
    signer: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=8, max_length=4000)


class OrderCommand(OrderVersion):
    prompt: str = Field(min_length=1, max_length=6000)


class VerifyTask(OrderVersion):
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class ProposeAction(OrderVersion):
    type: Literal["configuration", "response", "rfi", "task", "alias"]
    title: str = Field(min_length=1, max_length=240)
    summary: str = Field(default="", max_length=4000)
    after: dict[str, Any]
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class ClassifyDocument(OrderVersion):
    role: Literal[
        "purchase_order",
        "accepted_exception",
        "specification",
        "drawing",
        "bom",
        "supplier_po",
        "nameplate",
        "markup",
        "quote",
    ]
    revision_label: str | None = Field(default=None, max_length=40)
