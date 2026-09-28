from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProjectCreate(Strict):
    title: str = Field(min_length=2, max_length=180)
    customer: str = Field(min_length=2, max_length=180)
    due_date: str | None = None
    category: str = "Power distribution"


class LineInput(Strict):
    tag: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=4000)
    quantity: Decimal = Field(gt=0, le=1000000, decimal_places=4)
    unit: Literal["each", "ft", "package"] = "each"
    model: str = Field(default="", max_length=150)
    cost: Decimal | None = Field(default=None, ge=0, le=100000000, decimal_places=2)
    price: Decimal | None = Field(default=None, ge=0, le=100000000, decimal_places=2)
    lead_time: str = Field(default="Needs confirmation", max_length=1000)
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class Operation(Strict):
    type: Literal[
        "add_line",
        "remove_line",
        "set_quantity",
        "update_description",
        "set_gross_margin",
        "set_markup",
        "override_selling_price",
        "set_cost",
        "set_lead_time",
        "select_catalog_item",
        "review_line",
        "set_terms",
        "reconcile_inputs",
        "attach_evidence",
        "record_assumption",
    ]
    line_id: str | None = None
    value: str | None = Field(default=None, max_length=6000)
    expected_before: str | None = None
    line: LineInput | None = None
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    reason: str = Field(default="", max_length=2000)


class Command(Strict):
    expected_version: int = Field(ge=1)
    expected_input_revision: int = Field(ge=1)
    operations: list[Operation] = Field(min_length=1, max_length=500)


class VersionRequest(Strict):
    expected_version: int
    expected_input_revision: int
    reason: str = Field(default="", max_length=2000)


class RunRequest(Strict):
    goal: str = Field(min_length=3, max_length=5000)
    selected_lines: list[str] = Field(default_factory=list)


class Answer(Strict):
    answer: str = Field(min_length=1, max_length=8000)


class Mapping(Strict):
    expected_input_revision: int
    mapping: dict[str, str]
    purpose: Literal["schedule", "addendum", "catalog", "offers", "quote"] = "schedule"


class TextInput(Strict):
    name: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100000)
    sender: str = Field(min_length=1, max_length=200)
    date: str
    kind: Literal["schedule", "addendum", "offer", "specification"] = "schedule"


class Revert(VersionRequest):
    target_version: int


class CheckView(BaseModel):
    code: str
    message: str
    line_id: str | None
    severity: str
    result: str


class LineView(BaseModel):
    id: str
    tag: str
    description: str
    quantity: str
    unit: str
    model: str
    cost: str | None
    price: str | None
    extended: str | None
    lead_time: str
    review: str
    meta: dict
    position: int


class QuoteView(BaseModel):
    id: str
    number: str
    version: int
    input_revision: int
    currency: str
    terms: str
    notes: str
    project: str
    customer: str
    synthetic: bool
    lines: list[LineView]
    status: str
    total: str
    total_complete: bool
    total_cost: str
    margin: str | None
    checks: list[CheckView]
    reconciled_input: int


class ProjectView(BaseModel):
    id: str
    title: str
    customer: str
    category: str
    due_date: str | None
    synthetic: bool
    input_revision: int
    color: str
    quote_id: str
    number: str
    status: str
    version: int
    total: str
    blockers: int
    pending: int
    lines: int
    updated_at: str


class DocumentView(BaseModel):
    id: str
    name: str
    kind: str
    state: str
    coverage: list[dict]
    meta: dict
    created_at: str


class ProposalView(BaseModel):
    id: str
    quote_id: str
    base_version: int
    input_revision: int
    title: str
    summary: str
    operations: list[Operation]
    sources: list[str]
    status: str
    reason: str


class WorkspaceView(BaseModel):
    project: ProjectView
    quote: QuoteView
    documents: list[DocumentView]
    proposals: list[ProposalView]
    sources: list[dict]
    requirements: list[dict]
    history: list[dict]
    events: list[dict]
    runs: list[dict]
