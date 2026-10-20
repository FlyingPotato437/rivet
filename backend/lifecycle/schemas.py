"""Typed agent operations. Every engineering and commercial input cites its source."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field

from backend.orders.schemas import StrictModel

Gate = Literal["money", "scope", "engineering", "customer_commitment"]
Sources = Annotated[list[str], Field(min_length=1, max_length=100)]


class Mutation(StrictModel):
    expected_version: int = Field(ge=1)


class PolicyWrite(StrictModel):
    level: int = Field(ge=1, le=4)
    reason: str = Field(min_length=8, max_length=2000)
    # Authority is deliberately narrower than a numeric autonomy level.
    allowed_routine_work: list[
        Literal["filing", "drafting_tasks", "internal_status"]
    ] = Field(default_factory=list)
    gate_approvers: dict[Gate, list[str]] = Field(default_factory=dict)


class QuoteLine(StrictModel):
    tag: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    gross_margin_percent: Decimal = Field(ge=0, lt=100, max_digits=6, decimal_places=2)
    source_ids: Sources


class ExceptionLine(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    source_ids: Sources


class QuotePrepare(Mutation):
    operation: Literal["quote"]
    currency: Literal["USD", "CAD", "EUR", "GBP"] = "USD"
    lines: list[QuoteLine] = Field(min_length=1, max_length=1000)
    exceptions: list[ExceptionLine] = Field(default_factory=list, max_length=100)


class PurchaseOrderLine(StrictModel):
    tag: str = Field(min_length=1, max_length=80)
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    source_ids: Sources


class KickoffPrepare(Mutation):
    operation: Literal["kickoff"]
    purchase_order_number: str = Field(min_length=1, max_length=100)
    lines: list[PurchaseOrderLine] = Field(min_length=1, max_length=1000)
    approval_document_ids: list[str] = Field(default_factory=list, max_length=100)


class CommentPlan(StrictModel):
    comment_id: str = Field(min_length=1, max_length=100)
    kind: Literal["answer", "text_edit", "layout_task"]
    source_ids: Sources
    response: str = Field(default="", max_length=16000)
    draft_bundle_id: str = Field(default="", max_length=100)
    draft_change_id: str = Field(default="", max_length=100)
    layout_task_id: str = Field(default="", max_length=100)
    layout_location: dict = Field(default_factory=dict)


class ScopeAddition(StrictModel):
    tag: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1000)
    baseline_quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    requested_quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    gross_margin_percent: Decimal = Field(ge=0, lt=100, max_digits=6, decimal_places=2)
    source_ids: Sources
    # The contract owner must establish billability; quantity alone cannot do so.
    billability: Literal["unconfirmed", "contract_confirmed"] = "unconfirmed"
    billability_source_ids: list[str] = Field(default_factory=list, max_length=100)


class RevisionPrepare(Mutation):
    operation: Literal["revision_review"]
    revision: str = Field(min_length=1, max_length=40)
    comments: list[CommentPlan] = Field(min_length=1, max_length=1000)
    scope_additions: list[ScopeAddition] = Field(default_factory=list, max_length=100)


class Resolution(StrictModel):
    comment_id: str = Field(min_length=1, max_length=100)
    source_ids: Sources
    disposition: Literal["answered", "edit_verified", "layout_verified"]
    note: str = Field(min_length=3, max_length=2000)


class BomRow(StrictModel):
    tag: str = Field(min_length=1, max_length=80)
    quantity: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    part_number: str = Field(min_length=1, max_length=180)
    source_ids: Sources


class RevisionCheckPrepare(Mutation):
    operation: Literal["revision_check"]
    revision: str = Field(min_length=1, max_length=40)
    resolutions: list[Resolution] = Field(min_length=1, max_length=1000)
    approved_bom: list[BomRow] = Field(min_length=1, max_length=1000)
    revision_bom: list[BomRow] = Field(min_length=1, max_length=1000)
    outbound_document_ids: list[str] = Field(min_length=1, max_length=100)


class PurchasingPrepare(Mutation):
    operation: Literal["purchasing"]
    part_number: str = Field(min_length=1, max_length=180)
    lead_time_days: int = Field(ge=0, le=3660)
    order_date: date
    current_ship_date: date
    source_ids: Sources


class ReleasePrepare(Mutation):
    operation: Literal["release"]
    customer_approval_source_ids: Sources
    engineering_approval_source_ids: Sources


class CloseoutPrepare(Mutation):
    operation: Literal["closeout"]
    handover_request_source_ids: list[str] = Field(default_factory=list, max_length=20)
    test_report_document_ids: list[str] = Field(min_length=1, max_length=100)
    as_built_document_ids: list[str] = Field(min_length=1, max_length=100)
    other_document_ids: list[str] = Field(default_factory=list, max_length=100)


Prepare = Annotated[
    QuotePrepare
    | KickoffPrepare
    | RevisionPrepare
    | RevisionCheckPrepare
    | PurchasingPrepare
    | ReleasePrepare
    | CloseoutPrepare,
    Field(discriminator="operation"),
]


class DecisionWrite(Mutation):
    decision: Literal["approve", "reject"]
    gate: Gate
    reason: str = Field(min_length=8, max_length=4000)
    source_ids: Sources
