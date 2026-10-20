from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from backend.orders.schemas import StrictModel

Action = Literal[
    "sap.draft_sales_order",
    "sap.propose_change",
    "sap.release_order",
    "sharepoint.create_job_folder",
    "sharepoint.publish_release",
    "vault.release_revision",
    "teams.buyer_handoff",
    "schedule.confirm_material_date",
]
OPERATIONS = {
    "sap.draft_sales_order": "kickoff",
    "sap.propose_change": "revision_review",
    "sap.release_order": "release",
    "sharepoint.create_job_folder": "kickoff",
    "sharepoint.publish_release": "release",
    "vault.release_revision": "release",
    "teams.buyer_handoff": "purchasing",
    "schedule.confirm_material_date": "purchasing",
}
RESOURCE = {
    "sap.draft_sales_order": "sales_order",
    "sap.propose_change": "sales_order",
    "sap.release_order": "sales_order",
    "sharepoint.create_job_folder": "job_folder",
    "sharepoint.publish_release": "released_documents",
    "vault.release_revision": "released_revision",
    "teams.buyer_handoff": "buyer_channel",
    "schedule.confirm_material_date": "material_schedule",
}


class FilePayload(StrictModel):
    document_id: UUID
    name: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media_type: str = Field(min_length=1, max_length=100)
    content_base64: str = Field(max_length=44 * 1024 * 1024)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value):
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("Use a filename, not a path.")
        return value


class Command(StrictModel):
    mode: Literal["local_http_demo"] = "local_http_demo"
    organization_id: UUID
    order_id: UUID
    job_number: str = Field(min_length=1, max_length=80)
    action: Action
    package_id: UUID
    package_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    order_version: int = Field(ge=1)
    source_state_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    approvals: dict[str, dict[str, Any]]
    requested_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=8, max_length=4000)
    payload: dict[str, Any]
    files: list[FilePayload] = Field(default_factory=list, max_length=20)
    expected_remote_version: int = Field(ge=0)


class Execute(StrictModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=4000)
