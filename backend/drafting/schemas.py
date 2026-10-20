from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidencedChange(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    evidence_id: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=1200)
    # A source conflict must be resolved upstream before a concrete value is used.
    decision_status: Literal["confirmed_for_draft"]
    decision_record_id: str = Field(min_length=1, max_length=240)


class CadAttributeEdit(EvidencedChange):
    kind: Literal["cad_attribute"] = "cad_attribute"
    entity_handle: str = Field(pattern=r"^[0-9A-Fa-f]+$", max_length=32)
    block_name: str = Field(min_length=1, max_length=120)
    equipment_tag: str = Field(min_length=1, max_length=120)
    attribute_tag: str = Field(min_length=1, max_length=120)
    expected_value: str = Field(max_length=500)
    replacement_value: str = Field(min_length=1, max_length=500)


class BomCellEdit(EvidencedChange):
    kind: Literal["bom_cell"] = "bom_cell"
    row_key_column: str = Field(min_length=1, max_length=120)
    row_key: str = Field(min_length=1, max_length=240)
    column: str = Field(min_length=1, max_length=120)
    expected_value: str = Field(max_length=500)
    replacement_value: str = Field(min_length=1, max_length=500)
    worksheet: str = Field(default="BOM", min_length=1, max_length=120)


class LayoutTask(EvidencedChange):
    """Location of the existing object; no requested geometry is applied."""

    kind: Literal["layout_task"] = "layout_task"
    entity_handle: str = Field(pattern=r"^[0-9A-Fa-f]+$", max_length=32)
    block_name: str = Field(min_length=1, max_length=120)
    equipment_tag: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=1, max_length=1600)


class DraftRevisionRequest(StrictModel):
    schema_version: Literal[1] = 1
    bundle_id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    order_id: UUID
    job_number: str = Field(min_length=1, max_length=120)
    drawing_number: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    source_revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
    target_revision: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
    source_lifecycle: Literal["draft", "released", "approved"]
    drawing_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bom_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: Literal["local_demo", "authorized_upload"]
    allowed_cad_attributes: tuple[str, ...] = ("TRIP_UNIT", "NAMEPLATE")
    allowed_bom_columns: tuple[str, ...] = ("part_number", "description", "quantity")
    edits: tuple[CadAttributeEdit | BomCellEdit, ...] = Field(
        min_length=1, max_length=100
    )
    layout_tasks: tuple[LayoutTask, ...] = Field(default=(), max_length=100)

    @field_validator("allowed_cad_attributes", "allowed_bom_columns")
    @classmethod
    def unique_allowlist(cls, value):
        if not value or len(value) != len(set(value)):
            raise ValueError(
                "Edit allowlists must be nonempty and contain unique values"
            )
        return value

    @model_validator(mode="after")
    def distinct_targets(self):
        if self.source_revision == self.target_revision:
            raise ValueError("A draft must have a distinct target revision")
        ids = [item.id for item in (*self.edits, *self.layout_tasks)]
        if len(ids) != len(set(ids)):
            raise ValueError("Each edit and layout task needs a unique ID")
        targets = []
        for edit in self.edits:
            if edit.expected_value == edit.replacement_value:
                raise ValueError("An edit must change its expected value")
            if isinstance(edit, CadAttributeEdit):
                if edit.attribute_tag not in self.allowed_cad_attributes:
                    raise ValueError(
                        "CAD attribute is outside the explicit edit policy"
                    )
                targets.append(("cad", edit.entity_handle.upper(), edit.attribute_tag))
            else:
                if edit.column not in self.allowed_bom_columns:
                    raise ValueError("BOM column is outside the explicit edit policy")
                if edit.column == edit.row_key_column:
                    raise ValueError("BOM row identity cannot be edited")
                targets.append(
                    (
                        "bom",
                        edit.worksheet,
                        edit.row_key_column,
                        edit.row_key,
                        edit.column,
                    )
                )
        if len(targets) != len(set(targets)):
            raise ValueError("The same field cannot be edited twice in one bundle")
        return self


class ArtifactRef(StrictModel):
    name: str
    role: str
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DraftRevisionBundle(StrictModel):
    bundle_id: UUID
    organization_id: UUID
    order_id: UUID
    status: Literal["pending_engineering_approval"] = "pending_engineering_approval"
    source_revision: str
    target_revision: str
    report_path: str
    report_sha256: str
    artifacts: tuple[ArtifactRef, ...]
    changes: tuple[dict, ...]
    layout_tasks: tuple[dict, ...]
