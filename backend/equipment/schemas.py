"""Immutable source contract for the equipment revision checker."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["drawing", "bom", "specification", "markup"]
CheckStatus = Literal["verified", "unchanged", "mismatch", "missing", "unknown"]
EQUIPMENT_FIELDS = (
    "frame_a",
    "trip_a",
    "trip_unit",
    "part_number",
    "quantity",
    "nameplate",
    "control_voltage_v",
    "interrupting_ka",
)


class SourceInput(BaseModel):
    """An already authorized, immutable local file supplied by the caller.

    Authorization and tenant storage checks belong to the caller. The reader
    independently checks the bytes against sha256 before opening the document.
    A path is deliberately never included in returned public evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=500)
    path: str = Field(min_length=1, max_length=4096)
    role: Role
    revision: str = Field(min_length=1, max_length=80)
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    lifecycle: str = Field(default="draft", min_length=1, max_length=80)
