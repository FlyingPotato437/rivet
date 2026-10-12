"""Narrow coordination capabilities, independent of engineering edit tools."""

from typing import Literal

from pydantic import Field

from backend.orders.schemas import StrictModel

from .schemas import Write


class RunWrite(Write):
    goal: str = Field(min_length=3, max_length=3000)


class RunContinue(Write):
    answer: str = Field(default="", max_length=6000)


class ProposalResolve(Write):
    action: Literal["accept", "reject"]


class AgentSettingsWrite(Write):
    enabled: bool
    max_steps: int = Field(default=8, ge=2, le=12)
    max_output_tokens: int = Field(default=1600, ge=512, le=3000)
    playbook: str = Field(default="", max_length=4000)


class CoordinationOperation(StrictModel):
    type: Literal["link_source", "draft_clarification", "create_task", "draft_summary"]
    comment_id: str = Field(default="", max_length=100)
    target_source_id: str = Field(default="", max_length=100)
    text: str = Field(default="", max_length=6000)
    role: Literal["pm", "drafting", "production", "commercial"] = "pm"
    title: str = Field(default="", max_length=240)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
