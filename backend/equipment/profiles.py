"""Company-reviewed, source-cited equipment rules; no inferred design authority.

This is a pure evaluator. The caller must authorize the tenant, verify that quoted
source bytes are owned/current, and record approval/revocation in its own ledger.
An approved profile means a company reviewer approved these explicit rules; it
does not assert professional credentials, code compliance or engineering release.

``seal_profile(payload)`` creates a content-addressed manifest. ``evaluate``
accepts the existing EquipmentModel projection and, optionally, a source-anchored
native graph. Only current build observations are evaluated. Requested/specifed
values, a declared wire endpoint or a raster picture never establish actual work.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from .model import EquipmentModel, SourceAnchor

Scalar = StrictStr | StrictInt | Annotated[StrictFloat, Field(allow_inf_nan=False)]
Status = Literal["proposed", "approved", "revoked"]
Identifier = Annotated[str, Field(min_length=1, max_length=160)]
FieldName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ProfileError(ValueError):
    """Malformed or changed manifest; never a successful rule evaluation."""


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CompanyReview(Frozen):
    reviewer_id: Identifier
    reviewed_at: datetime

    @field_validator("reviewed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("The company review time must include its timezone.")
        return value


class DeviceAliasGroup(Frozen):
    alias_id: Identifier
    physical_device_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
    ]
    tags: tuple[Identifier, ...] = Field(min_length=2, max_length=30)
    status: Status = "proposed"
    source_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=30)

    @field_validator("tags")
    @classmethod
    def exact_distinct_tags(cls, value):
        if any(tag != tag.strip() for tag in value) or len(set(value)) != len(value):
            raise ValueError("Alias tags must be distinct, exact, non-padded tags.")
        return value


class FieldCondition(Frozen):
    field: FieldName
    equals: Scalar
    unit: str | None = Field(default=None, max_length=30)


class EqualsConstraint(Frozen):
    op: Literal["equals"]
    field: FieldName
    expected: Scalar
    unit: str | None = Field(default=None, max_length=30)


class AllowedSetConstraint(Frozen):
    op: Literal["allowed_set"]
    field: FieldName
    allowed: tuple[Scalar, ...] = Field(min_length=1, max_length=100)
    unit: str | None = Field(default=None, max_length=30)

    @field_validator("allowed")
    @classmethod
    def distinct_values(cls, value):
        if len({_value_key(item) for item in value}) != len(value):
            raise ValueError("Allowed values must be distinct.")
        return value


class RangeConstraint(Frozen):
    op: Literal["range"]
    field: FieldName
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def valid_range(self):
        if self.minimum is None and self.maximum is None:
            raise ValueError("A range needs at least one bound.")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("Range minimum exceeds maximum.")
        return self


class RequiresConstraint(Frozen):
    op: Literal["requires"]
    required_device_tag: Identifier


class DimensionBoundsConstraint(Frozen):
    op: Literal["dimension_bounds"]
    unit: Literal["mm"] = "mm"
    x_min: float | None = Field(default=None, allow_inf_nan=False)
    x_max: float | None = Field(default=None, allow_inf_nan=False)
    y_min: float | None = Field(default=None, allow_inf_nan=False)
    y_max: float | None = Field(default=None, allow_inf_nan=False)
    width_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    width_max: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    height_min: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    height_max: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_bounds(self):
        if not any(
            getattr(self, field) is not None
            for field in type(self).model_fields
            if field not in {"op", "unit"}
        ):
            raise ValueError("Dimension bounds need at least one explicit bound.")
        for axis in ("x", "y", "width", "height"):
            low, high = getattr(self, axis + "_min"), getattr(self, axis + "_max")
            if low is not None and high is not None and low > high:
                raise ValueError(f"Invalid {axis} bounds.")
        return self


class TerminalPairConstraint(Frozen):
    op: Literal["terminal_pair"]
    from_terminal: Identifier
    to_terminal: Identifier
    wire_id: Identifier

    @field_validator("from_terminal", "to_terminal")
    @classmethod
    def exact_terminal(cls, value):
        if ":" not in value or not all(value.rsplit(":", 1)):
            raise ValueError("A terminal must name an exact device tag and pin.")
        return value


Constraint = Annotated[
    EqualsConstraint
    | AllowedSetConstraint
    | RangeConstraint
    | RequiresConstraint
    | DimensionBoundsConstraint
    | TerminalPairConstraint,
    Field(discriminator="op"),
]


class ManufacturerRule(Frozen):
    rule_id: Identifier
    label: str = Field(min_length=1, max_length=240)
    status: Status = "proposed"
    device_tag: Identifier
    source_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=30)
    when: tuple[FieldCondition, ...] = Field(default=(), max_length=12)
    constraint: Constraint


class ManufacturerProfile(Frozen):
    schema_version: Literal["1"] = "1"
    version: Hash
    profile_id: Identifier
    tenant_id: Identifier
    manufacturer_name: str = Field(min_length=1, max_length=240)
    name: str = Field(min_length=1, max_length=240)
    status: Status = "proposed"
    review: CompanyReview | None = None
    sources: tuple[SourceAnchor, ...] = Field(min_length=1, max_length=200)
    aliases: tuple[DeviceAliasGroup, ...] = Field(default=(), max_length=300)
    rules: tuple[ManufacturerRule, ...] = Field(default=(), max_length=500)

    @model_validator(mode="after")
    def valid_manifest(self):
        if self.status == "approved" and self.review is None:
            raise ValueError("An approved profile requires a company review record.")
        for group, label in ((self.aliases, "alias_id"), (self.rules, "rule_id")):
            ids = [getattr(item, label) for item in group]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate {label} in profile.")
        source_ids = {source.document_id for source in self.sources}
        for source in self.sources:
            if not _valid_anchor(source.model_dump(mode="json"), quoted=True):
                raise ValueError(
                    "Profile sources require a hash, quoted text and precise location."
                )
        for item in (*self.aliases, *self.rules):
            if not set(item.source_ids).issubset(source_ids):
                raise ValueError("A rule or alias cites an unavailable profile source.")
        return self


class AliasBinding(Frozen):
    original_tag: str
    physical_device_id: str
    alias_id: str | None = None


class AliasResolution(Frozen):
    status: Literal["resolved", "blocked", "unknown"]
    requested_tag: str
    actual_tag: str | None = None
    physical_device_id: str | None = None
    alias_id: str | None = None
    sources: tuple[SourceAnchor, ...] = ()
    reason: str


class ObservedEvidence(Frozen):
    original_tag: str
    physical_device_id: str
    field: str
    value: Scalar | tuple[float, ...]
    unit: str | None = None
    observation_id: str | None = None
    source: SourceAnchor


class RuleEvaluation(Frozen):
    rule_id: str
    label: str
    status: Literal["pass", "fail", "unknown"]
    applicable: bool | None = True
    reason: str
    original_tags: tuple[str, ...] = ()
    physical_device_id: str | None = None
    rule_sources: tuple[SourceAnchor, ...]
    evidence: tuple[ObservedEvidence, ...] = ()


class ProfileEvaluation(Frozen):
    schema_version: Literal["1"] = "1"
    profile_id: str
    profile_version: str
    tenant_id: str
    equipment_version: str | None = None
    active: bool
    status: Literal["pass", "fail", "unknown", "inactive"]
    review: CompanyReview | None = None
    aliases: tuple[AliasBinding, ...] = ()
    blockers: tuple[str, ...] = ()
    results: tuple[RuleEvaluation, ...] = ()
    engineering_approved: Literal[False] = False
    production_released: Literal[False] = False


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _value_key(value):
    # Numeric 2500 and 2500.0 denote the same value; strings never become numbers.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ("number", float(value))
    return (type(value).__name__, value)


def _digest(payload):
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def seal_profile(payload: Mapping) -> ManufacturerProfile:
    """Validate and hash an exact manifest; does not authorize its approval.

    Approval status/reviewer must come from the authenticated caller, never an
    uploaded guide or model response. A supplied version is verified, not reused.
    """
    raw = dict(payload)
    supplied = raw.pop("version", None)
    parsed = ManufacturerProfile.model_validate({**raw, "version": "0" * 64})
    canonical = parsed.model_dump(mode="json", exclude={"version"})
    version = _digest(canonical)
    if supplied is not None and supplied != version:
        raise ProfileError(
            "The profile contents no longer match their immutable version."
        )
    return parsed.model_copy(update={"version": version})


def _valid_anchor(source, *, quoted=False, catalog=None):
    try:
        anchor = SourceAnchor.model_validate(source)
    except (ValueError, TypeError):
        return False
    location = anchor.location
    if (
        not anchor.document_id.strip()
        or not anchor.name.strip()
        or not anchor.revision.strip()
    ):
        return False
    if not isinstance(location, dict) or not location:
        return False
    page = location.get("page")
    if page is not None and (
        isinstance(page, bool) or not isinstance(page, int) or page < 1
    ):
        return False
    locator = any(
        location.get(key) not in (None, "", [], {})
        for key in (
            "page",
            "sheet",
            "cell",
            "row",
            "cad_handle",
            "section",
            "coordinate_system",
        )
    )
    if not locator:
        return False
    if quoted and (
        not isinstance(location.get("text"), str) or not location["text"].strip()
    ):
        return False
    return not (
        catalog is not None and catalog.get(anchor.document_id) != anchor.sha256
    )


def _sources(profile, source_ids):
    return tuple(
        source for source in profile.sources if source.document_id in source_ids
    )


def _native_data(model, native_graph):
    raw = native_graph if native_graph is not None else model.get("native")
    if isinstance(raw, BaseModel):
        raw = raw.model_dump(mode="json", by_alias=True)
    if not isinstance(raw, dict):
        return None
    for collection in ("devices", "wires"):
        if not isinstance(raw.get(collection), list) or not all(
            isinstance(item, dict) for item in raw[collection]
        ):
            return None
    return raw


def _native_ready(native, catalog):
    if not native or native.get("status") != "verified":
        return False
    if not _valid_anchor(native.get("source"), catalog=catalog):
        return False
    if native["source"].get("role") != "drawing":
        return False
    checks = native.get("checks")
    return bool(checks) and all(
        isinstance(check, dict) and check.get("status") == "pass" for check in checks
    )


def _aliases(profile, devices, native):
    """Exact tags only. A model may not merge two physical entries via aliases."""
    tags_to_groups = defaultdict(list)
    physical_groups = defaultdict(list)
    for group in profile.aliases:
        if group.status != "approved":
            continue
        physical_groups[group.physical_device_id].append(group)
        for tag in group.tags:
            tags_to_groups[tag].append(group)
    blockers = []
    blocked_tags = set()
    model_tags = [device.get("tag") for device in devices]
    for tag in set(model_tags):
        if model_tags.count(tag) > 1:
            blockers.append(
                f"The equipment projection repeats device tag {tag}; no duplicate entries were merged."
            )
            blocked_tags.add(tag)
    for tag, groups in tags_to_groups.items():
        if len(groups) > 1:
            blockers.append(f"Alias tag {tag} belongs to more than one approved group.")
            blocked_tags.update(t for group in groups for t in group.tags)
    for identity, groups in physical_groups.items():
        if len(groups) > 1:
            blockers.append(
                f"Physical device {identity} has more than one approved alias group."
            )
            blocked_tags.update(t for group in groups for t in group.tags)
    # A drawing tag and a BOM tag in independent documents may legitimately be
    # the same physical device. Detect distinct entries inside each actual
    # document/sheet inventory; do not treat the whole projection as one sheet.
    source_inventories = defaultdict(dict)
    for device in devices:
        tag = device.get("tag")
        for field in device.get("fields", {}).values():
            if not isinstance(field, dict):
                continue
            for observation in field.get("build", []):
                if not isinstance(observation, dict):
                    continue
                source = observation.get("source", {})
                if not isinstance(source, dict):
                    continue
                location = source.get("location", {})
                if not isinstance(location, dict):
                    continue
                if not source.get("document_id"):
                    continue
                inventory = (
                    source["document_id"],
                    source.get("sha256"),
                    location.get("sheet", location.get("page", "")),
                )
                entity = location.get("cad_handle") or location.get("row")
                if entity is None and isinstance(location.get("cell"), str):
                    entity = "".join(c for c in location["cell"] if c.isdigit()) or None
                occurrence = (tag, str(entity) if entity is not None else tag)
                source_inventories[inventory][occurrence] = {"tag": tag}
    inventories = [
        list(inventory.values()) for inventory in source_inventories.values()
    ]
    if native:
        inventories.append(native.get("devices", []))
    for inventory in inventories:
        seen = defaultdict(list)
        for item in inventory:
            tag = item.get("tag")
            if not isinstance(tag, str):
                continue
            groups = tags_to_groups.get(tag, [])
            identity = (
                groups[0].physical_device_id if len(groups) == 1 else "tag:" + tag
            )
            seen[identity].append(tag)
        for identity, tags in seen.items():
            if len(tags) > 1:
                blockers.append(
                    f"Multiple model entries {', '.join(tags)} map to {identity}; no merge was made."
                )
                blocked_tags.update(tags)
                for tag in tags:
                    for group in tags_to_groups.get(tag, []):
                        blocked_tags.update(group.tags)

    def binding(tag):
        group = tags_to_groups.get(tag, [])
        if tag in blocked_tags or len(group) > 1:
            return None
        if group:
            return group[0].physical_device_id, group[0].alias_id
        return "tag:" + tag, None

    result = []
    for tag in sorted(
        {item.get("tag") for item in devices if isinstance(item.get("tag"), str)}
    ):
        bound = binding(tag)
        if bound:
            result.append(
                AliasBinding(
                    original_tag=tag, physical_device_id=bound[0], alias_id=bound[1]
                )
            )
    return binding, tuple(result), list(dict.fromkeys(blockers))


def resolve_alias(
    profile: ManufacturerProfile | Mapping,
    requested_tag: str,
    actual_native_tags,
    *,
    revoked_versions=(),
    source_catalog: Mapping[str, str] | None = None,
) -> AliasResolution:
    """Resolve one explicit request to one actual native tag without changing it.

    The actual tag list represents one native inventory, including duplicates.
    An exact match does not bypass a conflicting approved alias declaration.
    """
    profile = seal_profile(
        profile.model_dump(mode="json")
        if isinstance(profile, ManufacturerProfile)
        else profile
    )
    tags = list(actual_native_tags)
    if (
        not isinstance(requested_tag, str)
        or not requested_tag
        or not all(isinstance(tag, str) and tag for tag in tags)
    ):
        raise ProfileError("Alias resolution requires exact nonempty device tags.")
    base = {"requested_tag": requested_tag}
    if profile.status != "approved" or profile.version in revoked_versions:
        return AliasResolution(
            **base,
            status="blocked",
            reason="The profile is not active; its aliases cannot identify a native editing target.",
        )
    binding, _, blockers = _aliases(
        profile, [], {"devices": [{"tag": tag} for tag in tags]}
    )
    requested = binding(requested_tag)
    groups = [
        group
        for group in profile.aliases
        if group.status == "approved" and requested_tag in group.tags
    ]
    sources = _sources(profile, groups[0].source_ids) if len(groups) == 1 else ()
    if any(
        not _valid_anchor(
            source.model_dump(mode="json"), quoted=True, catalog=source_catalog
        )
        for source in sources
    ):
        return AliasResolution(
            **base,
            status="blocked",
            sources=sources,
            reason="The alias source is stale or unavailable.",
        )
    if requested is None:
        return AliasResolution(
            **base,
            status="blocked",
            sources=sources,
            reason="The approved alias declaration or actual native inventory collides; no target was chosen.",
        )
    matches = [tag for tag in tags if binding(tag) and binding(tag)[0] == requested[0]]
    if len(matches) != 1:
        return AliasResolution(
            **base,
            status="blocked" if blockers else "unknown",
            physical_device_id=requested[0],
            sources=sources,
            reason="No single actual native device matches the exact approved identity.",
        )
    return AliasResolution(
        **base,
        status="resolved",
        actual_tag=matches[0],
        physical_device_id=requested[0],
        alias_id=requested[1],
        sources=sources,
        reason="One actual native device matches this exact company-reviewed identity."
        if groups
        else "One exact original native tag matches; no alias was inferred.",
    )


def _merged_devices(devices):
    fields = defaultdict(lambda: {"build": []})
    for device in devices:
        for name, field in device.get("fields", {}).items():
            fields[name]["build"].extend(
                {**observation, "original_tag": device["tag"]}
                for observation in field.get("build", [])
            )
            if field.get("agreement") == "conflicting":
                fields[name]["agreement"] = "conflicting"
    return {"tag": devices[0]["tag"], "fields": dict(fields)}


def _observation_value(device, field, identity, catalog):
    """A disagreement or malformed/missing observation is unknown, never a vote."""
    equipment_field = device.get("fields", {}).get(field, {})
    if not isinstance(equipment_field, dict):
        return None, (), f"The observed field {field} is malformed."
    observations = equipment_field.get("build", [])
    if not isinstance(observations, list) or not observations:
        return None, (), f"No current build observation establishes {field}."
    evidence = []
    for observation in observations:
        if (
            not isinstance(observation, dict)
            or not _valid_anchor(observation.get("source"), catalog=catalog)
            or observation["source"].get("role") not in {"drawing", "bom"}
        ):
            return (
                None,
                tuple(evidence),
                f"The evidence for {field} is missing, malformed or stale.",
            )
        value = observation.get("value")
        if (
            isinstance(value, bool)
            or not isinstance(value, (str, int, float))
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            return (
                None,
                tuple(evidence),
                f"The observed value of {field} is unsupported.",
            )
        evidence.append(
            ObservedEvidence(
                original_tag=observation.get("original_tag", device["tag"]),
                physical_device_id=identity,
                field=field,
                value=value,
                unit=observation.get("unit"),
                observation_id=observation.get("observation_id"),
                source=observation["source"],
            )
        )
    values = {(_value_key(item.value), item.unit) for item in evidence}
    if len(values) != 1 or equipment_field.get("agreement") == "conflicting":
        return (
            None,
            tuple(evidence),
            f"Current build sources disagree about {field}; no value was selected.",
        )
    return (evidence[0].value, evidence[0].unit), tuple(evidence), None


def evaluate(
    profile: ManufacturerProfile | Mapping,
    equipment_model: EquipmentModel | Mapping,
    native_graph: Mapping | None = None,
    *,
    revoked_versions=(),
    source_catalog: Mapping[str, str] | None = None,
) -> ProfileEvaluation:
    """Evaluate only active company-reviewed constraints against actual evidence.

    ``source_catalog`` is the caller's owned/current document ID → SHA256 map.
    Without it hashes/anchors are checked structurally only; caller-side source
    byte and quote verification remains mandatory before accepting a profile.
    ``native_graph`` requires its own SourceAnchor, verified checks and actual
    endpoints. Unverified PDF/raster suggestions are intentionally insufficient.
    """
    profile = seal_profile(
        profile.model_dump(mode="json")
        if isinstance(profile, ManufacturerProfile)
        else profile
    )
    model = (
        equipment_model.model_dump(mode="json", by_alias=True)
        if isinstance(equipment_model, EquipmentModel)
        else dict(equipment_model)
    )
    base = {
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
        "tenant_id": profile.tenant_id,
        "equipment_version": model.get("version"),
        "review": profile.review,
    }
    if profile.status != "approved" or profile.version in revoked_versions:
        return ProfileEvaluation(
            **base,
            active=False,
            status="inactive",
            blockers=(
                "The profile is proposed or revoked; none of its rules or aliases are active.",
            ),
        )
    devices = model.get("devices", [])
    if not isinstance(devices, list) or not all(
        isinstance(item, dict) and isinstance(item.get("tag"), str) for item in devices
    ):
        raise ProfileError(
            "The equipment model has no valid explicit device inventory."
        )
    native = _native_data(model, native_graph)
    binding, bindings, blockers = _aliases(profile, devices, native)
    approved_aliases = [
        alias for alias in profile.aliases if alias.status == "approved"
    ]
    bad_alias_tags = set()
    for alias in approved_aliases:
        if any(
            not _valid_anchor(
                source.model_dump(mode="json"), quoted=True, catalog=source_catalog
            )
            for source in _sources(profile, alias.source_ids)
        ):
            bad_alias_tags.update(alias.tags)
            blockers.append(
                f"Alias {alias.alias_id} has unavailable or stale source evidence."
            )
    bindings = tuple(
        item for item in bindings if item.original_tag not in bad_alias_tags
    )
    original_binding = binding

    def binding(tag):
        return None if tag in bad_alias_tags else original_binding(tag)

    by_identity = defaultdict(list)
    for device in devices:
        bound = binding(device["tag"])
        if (
            bound
            and device["tag"] not in bad_alias_tags
            and device.get("observed_in_current_package") is True
        ):
            by_identity[bound[0]].append(device)
    results = []
    for rule in profile.rules:
        if rule.status != "approved":
            continue
        sources = _sources(profile, rule.source_ids)
        bound = binding(rule.device_tag)
        reason = None
        if any(
            not _valid_anchor(
                source.model_dump(mode="json"), quoted=True, catalog=source_catalog
            )
            for source in sources
        ):
            reason = "The rule's quoted source evidence is unavailable or stale."
        elif not bound or rule.device_tag in bad_alias_tags:
            reason = "The device identity is ambiguous or its alias evidence is unavailable; no merge was made."
        matching = by_identity.get(bound[0], []) if bound else []
        if reason is None and not matching:
            reason = (
                "No single current device observation establishes the rule's subject."
            )
        common = {
            "rule_id": rule.rule_id,
            "label": rule.label,
            "rule_sources": sources,
            "physical_device_id": bound[0] if bound else None,
            "original_tags": tuple(item["tag"] for item in matching),
        }
        if reason:
            results.append(RuleEvaluation(**common, status="unknown", reason=reason))
            continue
        device = _merged_devices(matching)
        evidence = []
        conditional = True
        for condition in rule.when:
            actual, observed, error = _observation_value(
                device, condition.field, bound[0], source_catalog
            )
            evidence.extend(observed)
            if error:
                reason, conditional = (
                    "The rule's applicability is unknown: " + error,
                    None,
                )
                break
            if actual[1] != condition.unit:
                reason, conditional = (
                    "The rule's applicability has an unestablished unit conversion.",
                    None,
                )
                break
            if _value_key(actual[0]) != _value_key(condition.equals):
                conditional = False
                break
        if conditional is not True:
            results.append(
                RuleEvaluation(
                    **common,
                    status="unknown",
                    applicable=conditional,
                    reason=reason
                    or "This rule's explicit applicability conditions do not match this device; it was not evaluated.",
                    evidence=tuple(evidence),
                )
            )
            continue
        constraint = rule.constraint
        if isinstance(
            constraint, (EqualsConstraint, AllowedSetConstraint, RangeConstraint)
        ):
            actual, observed, error = _observation_value(
                device, constraint.field, bound[0], source_catalog
            )
            evidence.extend(observed)
            if error or actual[1] != constraint.unit:
                status, reason = (
                    "unknown",
                    error
                    or "Observed and required units differ; no implicit conversion was applied.",
                )
            else:
                value = actual[0]
                if isinstance(constraint, EqualsConstraint):
                    passed = _value_key(value) == _value_key(constraint.expected)
                elif isinstance(constraint, AllowedSetConstraint):
                    passed = _value_key(value) in {
                        _value_key(item) for item in constraint.allowed
                    }
                elif not isinstance(value, (int, float)) or isinstance(value, bool):
                    passed = None
                else:
                    passed = (
                        constraint.minimum is None or value >= constraint.minimum
                    ) and (constraint.maximum is None or value <= constraint.maximum)
                status = "unknown" if passed is None else "pass" if passed else "fail"
                reason = (
                    "The rating is not a numeric observation; it was not coerced."
                    if passed is None
                    else "The observed build value satisfies the company-reviewed constraint."
                    if passed
                    else "The observed build value violates the company-reviewed constraint."
                )
        elif isinstance(constraint, RequiresConstraint):
            required = binding(constraint.required_device_tag)
            if required is None or constraint.required_device_tag in bad_alias_tags:
                status, reason = (
                    "unknown",
                    "The required device's identity is ambiguous.",
                )
            elif by_identity.get(required[0]):
                status, reason = (
                    "pass",
                    "The explicitly required device is observed in the current package.",
                )
                required_device = _merged_devices(by_identity[required[0]])
                required_evidence = []
                for field in required_device.get("fields", {}):
                    _, observed, _ = _observation_value(
                        required_device, field, required[0], source_catalog
                    )
                    required_evidence.extend(observed)
                    evidence.extend(observed)
                if not required_evidence:
                    status, reason = (
                        "unknown",
                        "The required device has no cited current source observation.",
                    )
            elif model.get("coverage", {}).get("complete_inventory_verified") is True:
                # Completeness alone is a caller assertion; require a cited
                # inventory anchor to establish absence rather than mere silence.
                inventory = model.get("coverage", {}).get("inventory_source")
                if _valid_anchor(inventory, catalog=source_catalog):
                    status, reason = (
                        "fail",
                        "The cited complete inventory does not contain the required device.",
                    )
                    evidence.append(
                        ObservedEvidence(
                            original_tag=constraint.required_device_tag,
                            physical_device_id=required[0],
                            field="device_presence",
                            value="absent from complete inventory",
                            source=inventory,
                        )
                    )
                else:
                    status, reason = (
                        "unknown",
                        "The complete-inventory claim has no valid source anchor.",
                    )
            else:
                status, reason = (
                    "unknown",
                    "The required device was not observed; the package does not establish a complete inventory.",
                )
        elif not _native_ready(native, source_catalog):
            status, reason = (
                "unknown",
                "Verified native geometry/connectivity and its current source are required; document images or declared endpoints are insufficient.",
            )
        elif isinstance(constraint, DimensionBoundsConstraint):
            status, reason, observed = _dimensions(
                native, constraint, bound[0], binding, source_catalog
            )
            evidence.extend(observed)
        else:
            status, reason, observed = _terminal_pair(
                native, constraint, bound[0], binding
            )
            evidence.extend(observed)
        results.append(
            RuleEvaluation(
                **common, status=status, reason=reason, evidence=tuple(evidence)
            )
        )
    applicable_results = [
        result for result in results if result.applicable is not False
    ]
    overall = (
        "fail"
        if any(result.status == "fail" for result in applicable_results)
        else "unknown"
        if blockers
        or not applicable_results
        or any(result.status == "unknown" for result in applicable_results)
        else "pass"
    )
    return ProfileEvaluation(
        **base,
        active=True,
        status=overall,
        aliases=bindings,
        blockers=tuple(dict.fromkeys(blockers)),
        results=tuple(results),
    )


def _dimensions(native, constraint, identity, binding, catalog):
    matches = [
        device
        for device in native.get("devices", [])
        if binding(device.get("tag", "")) and binding(device["tag"])[0] == identity
    ]
    if len(matches) != 1 or native.get("units") != "mm":
        return (
            "unknown",
            "No unique millimetre native body establishes this device's dimensions.",
            (),
        )
    device = matches[0]
    bounds = device.get("bounds")
    if (
        not isinstance(bounds, (list, tuple))
        or len(bounds) != 4
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in bounds
        )
        or bounds[2] <= bounds[0]
        or bounds[3] <= bounds[1]
    ):
        return "unknown", "The native body bounds are incomplete or invalid.", ()
    source = dict(native["source"])
    source["location"] = {
        **source["location"],
        "cad_handle": device.get("handle", ""),
        "bbox": bounds,
    }
    observed = (
        ObservedEvidence(
            original_tag=device["tag"],
            physical_device_id=identity,
            field="native_bounds",
            value=tuple(bounds),
            unit="mm",
            source=source,
        ),
    )
    values = {
        "x_min": bounds[0],
        "x_max": bounds[2],
        "y_min": bounds[1],
        "y_max": bounds[3],
        "width_min": bounds[2] - bounds[0],
        "width_max": bounds[2] - bounds[0],
        "height_min": bounds[3] - bounds[1],
        "height_max": bounds[3] - bounds[1],
    }
    failed = [
        field
        for field, value in values.items()
        if getattr(constraint, field) is not None
        and (
            value < getattr(constraint, field)
            if field.endswith("_min")
            else value > getattr(constraint, field)
        )
    ]
    return (
        (
            "fail",
            "The actual native body violates: " + ", ".join(failed) + ".",
            observed,
        )
        if failed
        else (
            "pass",
            "The actual native body satisfies every supplied millimetre bound.",
            observed,
        )
    )


def _terminal_identity(value, binding):
    if not isinstance(value, str) or ":" not in value:
        return None
    tag, pin = value.rsplit(":", 1)
    bound = binding(tag)
    return (bound[0], pin) if bound and pin else None


def _terminal_pair(native, constraint, identity, binding):
    wires = [
        wire for wire in native.get("wires", []) if wire.get("id") == constraint.wire_id
    ]
    if len(wires) != 1:
        return (
            "unknown",
            "No unique actual native wire establishes this terminal pair.",
            (),
        )
    wire = wires[0]
    actual = [
        _terminal_identity(wire.get(key), binding)
        for key in ("actual_from", "actual_to")
    ]
    expected = [
        _terminal_identity(value, binding)
        for value in (constraint.from_terminal, constraint.to_terminal)
    ]
    if any(value is None for value in (*actual, *expected)):
        return (
            "unknown",
            "Actual terminal incidence is incomplete or an endpoint identity is ambiguous; declared wire labels were not substituted.",
            (),
        )
    source = dict(native["source"])
    source["location"] = {
        **source["location"],
        "cad_handle": wire.get("handle", ""),
        "wire_id": wire["id"],
    }
    evidence = tuple(
        ObservedEvidence(
            original_tag=value.rsplit(":", 1)[0],
            physical_device_id=actual[index][0],
            field=key,
            value=value,
            source=source,
        )
        for index, (key, value) in enumerate(
            (key, wire[key]) for key in ("actual_from", "actual_to")
        )
    )
    passed = set(actual) == set(expected)
    return (
        "pass" if passed else "fail",
        "Actual native terminal incidence matches the company-reviewed wire expectation."
        if passed
        else "Actual native terminal incidence differs from the company-reviewed wire expectation.",
        evidence,
    )
