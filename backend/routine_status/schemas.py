from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator

from backend.orders.schemas import StrictModel


def exact_address(value):
    import re

    value = value.strip().lower()
    if "*" in value or not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}",
        value,
    ):
        raise ValueError(
            "Use a single exact email address, without display names or wildcards."
        )
    return value


class PolicyWrite(StrictModel):
    auto_send: bool = False
    recipients: list[str] = Field(default_factory=list, max_length=100)
    status_approvers: list[str] = Field(default_factory=list, max_length=100)
    max_fact_age_hours: int = Field(default=24, ge=1, le=168)
    reason: str = Field(min_length=8, max_length=2000)

    @field_validator("recipients")
    @classmethod
    def addresses(cls, values):
        return sorted({exact_address(value) for value in values})

    @field_validator("status_approvers")
    @classmethod
    def approvers(cls, values):
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("Use exact named company approver IDs.")
        return sorted(set(values))


class FactWrite(StrictModel):
    expected_version: int = Field(ge=1)
    kind: Literal["confirmed_ship_date", "material_available_date"]
    value: date
    valid_until: datetime
    source_ids: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=8, max_length=2000)


class PrepareWrite(StrictModel):
    receipt_id: str = Field(min_length=36, max_length=36)
