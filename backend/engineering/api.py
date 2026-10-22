"""Engineering order stages; uses existing tenant, version and idempotency gates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse
from pydantic import Field

from backend.domain.service import get, idempotent
from backend.orders import service as orders
from backend.orders.schemas import OrderVersion, StrictModel
from backend.storage.db import Session
from backend.storage.models import Order

from . import service
from .phase_change import PhaseScenario

router = APIRouter(prefix="/api/orders", tags=["Switchboard engineering"])


class SourceRoles(OrderVersion):
    source_roles: dict[str, str] = Field(max_length=100)


class PhaseChangeRequest(PhaseScenario):
    expected_version: int = Field(ge=1)


@router.get("/{id}/engineering/phase-change")
def phase_change(id: str):
    from backend.domain.service import fail

    from .phase_change_service import for_order

    with Session() as s:
        order = get(s, Order, id)
        try:
            return for_order(s, order)
        except ValueError as exc:
            fail(str(exc), 409)


@router.post("/{id}/engineering/phase-change/analyze")
def phase_change_scenario(id: str, body: PhaseChangeRequest):
    from backend.company_context.service import require_writer
    from backend.domain.service import fail

    from .phase_change_service import for_order

    require_writer()
    with Session() as s:
        order = get(s, Order, id)
        orders.check_version(order, body.expected_version)
        try:
            return for_order(
                s, order, PhaseScenario(**body.model_dump(exclude={"expected_version"}))
            )
        except ValueError as exc:
            fail(str(exc), 409)


class RequirementDecision(StrictModel):
    id: str = Field(min_length=1, max_length=180)
    value: str | int | float | bool | None = None
    dismissed: bool = False


class ConfirmRequirements(OrderVersion):
    decisions: list[RequirementDecision] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=3, max_length=2000)


class DesignEdit(OrderVersion):
    design_id: str | None = Field(default=None, max_length=180)
    design: dict[str, Any] | None = None


class RuleEdit(OrderVersion):
    rule_pack: dict[str, Any]
    reason: str = Field(min_length=8, max_length=2000)


class PackageReview(OrderVersion):
    package_id: str = Field(min_length=1, max_length=80)


class CutSheetMapping(StrictModel):
    document_id: str = Field(min_length=1, max_length=180)
    component_ids: list[str] = Field(min_length=1, max_length=2000)
    pages: list[int] = Field(min_length=1, max_length=250)
    identity: dict[str, str] = Field(max_length=4)
    verified: bool


class GenerationSettings(OrderVersion):
    template: dict[str, Any] | None = None
    cut_sheets: list[CutSheetMapping] = Field(default_factory=list, max_length=250)
    reason: str = Field(min_length=8, max_length=2000)


class Approval(OrderVersion):
    signer: str = Field(min_length=2, max_length=180)
    reason: str = Field(min_length=8, max_length=2000)


class ReviewChange(OrderVersion):
    package_id: str = Field(min_length=1, max_length=80)
    comment_id: str = Field(min_length=1, max_length=180)
    evidence_id: str = Field(min_length=1, max_length=180)
    requirement_id: str = Field(min_length=1, max_length=180)
    value: str | int | float | bool
    reason: str = Field(min_length=8, max_length=2000)


class EquipmentRoleDecision(OrderVersion):
    source_id: str = Field(min_length=1, max_length=180)
    source_sha: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: int = Field(ge=1)
    source_object_key: str = Field(min_length=1, max_length=500)
    role: str = Field(pattern=r"^(main|feeder|reference)$")
    reason: str = Field(min_length=8, max_length=2000)


class AddRequirement(OrderVersion):
    key: str = Field(min_length=1, max_length=120)
    subject_id: str = Field(min_length=1, max_length=180)
    value: str | int | float | bool
    source_id: str = Field(min_length=1, max_length=180)
    page: int = Field(ge=1)
    reason: str = Field(min_length=8, max_length=2000)


@router.get("/{id}/engineering")
def engineering(id: str):
    with Session() as s:
        return service.view(s, get(s, Order, id))


@router.get("/{id}/engineering/rules")
def rules(id: str):
    from .configurator import load_rule_pack

    with Session() as s:
        order = get(s, Order, id)
        data = service.state(order)
        return {
            "rule_pack": data.get("rule_pack") or load_rule_pack(),
            "history": data["rule_history"],
        }


def mutate(id, body, key, action, fn):
    from backend.domain.service import fail

    with Session.begin() as s:
        order = orders.lock_order(s, id)

        def perform():
            orders.check_version(order, body.expected_version)
            try:
                fn(s, order)
            except ValueError as exc:
                fail(str(exc))
            return service.view(s, order)

        return idempotent(
            s, f"engineering:{id}:{action}:{key}", body.model_dump(), perform
        )


@router.post("/{id}/engineering/sources")
def sources(id: str, body: SourceRoles, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "sources",
        lambda s, o: service.classify(s, o, body.source_roles),
    )


@router.post("/{id}/engineering/equipment-role")
def equipment_role(
    id: str, body: EquipmentRoleDecision, idempotency_key: str = Header()
):
    return mutate(
        id,
        body,
        idempotency_key,
        "equipment-role",
        lambda s, o: service.decide_source_role(
            s, o, **body.model_dump(exclude={"expected_version"})
        ),
    )


@router.post("/{id}/engineering/extract")
def extract(id: str, body: OrderVersion, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "extract", service.extract)


@router.post("/{id}/engineering/requirements")
def requirements(id: str, body: ConfirmRequirements, idempotency_key: str = Header()):
    decisions = [item.model_dump(exclude_unset=True) for item in body.decisions]
    return mutate(
        id,
        body,
        idempotency_key,
        "requirements",
        lambda s, o: service.confirm(s, o, decisions, body.reason),
    )


@router.post("/{id}/engineering/requirements/add")
def add_requirement(id: str, body: AddRequirement, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "add-requirement",
        lambda s, o: service.add_requirement(
            s, o, **body.model_dump(exclude={"expected_version"})
        ),
    )


@router.post("/{id}/engineering/configure")
def configure(id: str, body: OrderVersion, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "configure", service.configure)


@router.post("/{id}/engineering/design")
def design(id: str, body: DesignEdit, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "design",
        lambda s, o: service.select_design(s, o, body.design_id, body.design),
    )


@router.post("/{id}/engineering/design-preview")
def design_preview(id: str, body: DesignEdit):
    from backend.domain.service import fail

    with Session() as s:
        order = get(s, Order, id)
        orders.check_version(order, body.expected_version)
        try:
            return service.preview_design(s, order, body.design_id, body.design)
        except ValueError as exc:
            fail(str(exc))


@router.post("/{id}/engineering/rules")
def edit_rules(id: str, body: RuleEdit, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "rules",
        lambda s, o: service.update_rules(s, o, body.rule_pack, body.reason),
    )


@router.post("/{id}/engineering/generate")
def generate(id: str, body: OrderVersion, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "generate", service.generate)


@router.post("/{id}/engineering/generation-settings")
def generation_settings(
    id: str, body: GenerationSettings, idempotency_key: str = Header()
):
    return mutate(
        id,
        body,
        idempotency_key,
        "generation-settings",
        lambda s, o: service.generation_settings(
            s,
            o,
            body.template,
            [item.model_dump() for item in body.cut_sheets],
            body.reason,
        ),
    )


@router.post("/{id}/engineering/review")
def review(id: str, body: PackageReview, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "review",
        lambda s, o: service.register_package(s, o, body.package_id),
    )


@router.post("/{id}/engineering/review-change")
def review_change(id: str, body: ReviewChange, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "review-change",
        lambda s, o: service.apply_review_change(
            s, o, **body.model_dump(exclude={"expected_version"})
        ),
    )


@router.post("/{id}/engineering/precheck")
def precheck(id: str, body: OrderVersion, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "precheck", service.precheck)


@router.post("/{id}/engineering/approve")
def approve(id: str, body: Approval, idempotency_key: str = Header()):
    return mutate(
        id,
        body,
        idempotency_key,
        "approve",
        lambda s, o: service.approve(s, o, body.signer, body.reason),
    )


@router.post("/{id}/engineering/release-export")
def release(id: str, body: OrderVersion, idempotency_key: str = Header()):
    return mutate(id, body, idempotency_key, "release-export", service.release_export)


@router.get("/{id}/engineering/packages/{package_id}/files/{name}")
def file(id: str, package_id: str, name: str):
    with Session() as s:
        path = service.artifact(s, get(s, Order, id), package_id, name)
        return FileResponse(
            path, filename=path.name, headers={"Cache-Control": "private, no-store"}
        )
