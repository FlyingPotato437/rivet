"""Compare hash-bound actual drawing/BOM state with explicit requirements.

Verdicts are restricted to the supported labelled properties. A verified field
is not a verified circuit, complete package, approved change or released order.
"""

import json
from collections import Counter, defaultdict

from .readers import read_source, stable_id
from .schemas import SourceInput

SCHEMA_VERSION = "2"
LIMITATIONS = [
    "Observed device counts describe supported explicit tags, not a complete engineering inventory.",
    "Wiring connectivity, physical geometry, clearances, protection coordination, standards compliance and manufacturing release are not verified.",
    "Image-only scans, handwriting, unlabelled schematic symbols and unknown units remain unverified.",
    "Comments and claims that a change was completed do not establish actual drawing or BOM state.",
]


def analyze_package(sources):
    """Extract independently cited equipment observations from authorized files."""
    reports, facts, requests, warnings = [], [], [], []
    inputs = [
        source
        if isinstance(source, SourceInput)
        else SourceInput.model_validate(source)
        for source in sources
    ]
    ids = Counter(source.id for source in inputs)
    for source in inputs:
        if ids[source.id] > 1:
            warnings.append(
                {
                    "code": "duplicate_source_id",
                    "source_id": source.id,
                    "message": "Source identifiers must be unique within a package. This source was excluded.",
                }
            )
            reports.append(
                {
                    "id": source.id,
                    "name": source.name,
                    "role": source.role,
                    "revision": source.revision,
                    "sha256": source.sha256,
                    "lifecycle": source.lifecycle,
                    "status": "unreadable",
                    "observed_device_tags": [],
                    "uncertain_fields": [],
                    "warnings": [],
                }
            )
            continue
        report, extracted = read_source(
            source, requested_changes=source.role == "markup"
        )
        reports.append(report)
        warnings.extend(report["warnings"])
        if source.role == "markup":
            requests.extend(extracted)
            warnings.append(
                {
                    "code": "requests_are_not_design_state",
                    "source_id": source.id,
                    "message": "Only explicit target/requested/desired values were captured as requested changes; completion must be established independently in drawing and BOM sources.",
                }
            )
        else:
            facts.extend(extracted)
    devices = defaultdict(lambda: defaultdict(list))
    for fact in facts:
        if fact["role"] not in {"drawing", "bom"}:
            continue
        devices[fact["device_tag"]][fact["field"]].append(fact["id"])
    # A tag without supported values is still an observed device, not a
    # silently omitted instance.
    for report in reports:
        if report["role"] in {"drawing", "bom"}:
            for tag in report["observed_device_tags"]:
                devices[tag]
    roles = {}
    for role in ("drawing", "bom", "specification", "markup"):
        selected = [report for report in reports if report["role"] == role]
        if selected:
            roles[role] = {
                "source_count": len(selected),
                "readable_source_count": sum(
                    report["status"] in {"parsed", "partial"} for report in selected
                ),
                "partial_source_count": sum(
                    report["status"] == "partial" for report in selected
                ),
                "observed_device_count": len(
                    {
                        tag
                        for report in selected
                        for tag in report["observed_device_tags"]
                    }
                ),
            }
    return {
        "schema_version": SCHEMA_VERSION,
        "sources": reports,
        "facts": sorted(
            facts,
            key=lambda fact: (
                fact["device_tag"],
                fact["field"],
                fact["source_id"],
                fact["id"],
            ),
        ),
        "requested_changes": sorted(
            requests, key=lambda fact: (fact["device_tag"], fact["field"], fact["id"])
        ),
        "devices": [
            {"tag": tag, "fields": dict(sorted(fields.items()))}
            for tag, fields in sorted(devices.items())
        ],
        "coverage": {
            "observed_device_count": len(devices),
            "fact_count": len(facts),
            "source_count": len(reports),
            "by_role": roles,
            "complete_inventory_verified": False,
            "limitations": LIMITATIONS,
        },
        "warnings": warnings,
    }


def _value_key(fact):
    return json.dumps([fact["value"], fact.get("unit")], sort_keys=True)


def _values(facts):
    return {_value_key(fact) for fact in facts}


def _group(analysis, roles=None):
    result = defaultdict(list)
    for fact in analysis["facts"]:
        if roles is None or fact["role"] in roles:
            result[(fact["device_tag"], fact["field"])].append(fact)
    return result


def _view(facts):
    unique = {}
    for fact in facts:
        unique[_value_key(fact)] = {"value": fact["value"], "unit": fact.get("unit")}
    values = list(unique.values())
    return {
        "value": values[0]["value"] if len(values) == 1 else None,
        "unit": values[0]["unit"] if len(values) == 1 else None,
        "values": values,
        "evidence": facts,
    }


def _role_observed(analysis, role, tag):
    return any(
        report["role"] == role and tag in report["observed_device_tags"]
        for report in analysis["sources"]
    )


def _role_uncertain(analysis, role, tag, field=None):
    reports = [report for report in analysis["sources"] if report["role"] == role]
    if not reports:
        return False
    # A partial parse cannot prove a device absent, even when another
    # representation was readable. It may contain the missing instance.
    if any(
        report["status"] in {"unreadable", "unsupported", "partial"}
        for report in reports
    ) and not _role_observed(analysis, role, tag):
        return True
    return any(
        item["device_tag"] == tag and (field is None or item["field"] == field)
        for report in reports
        for item in report.get("uncertain_fields", [])
    )


def _check(tag, field, status, reason, old, new, specs, requested=None, **extra):
    requested = requested or []
    label = {
        "frame_a": "Frame rating",
        "trip_a": "Trip rating",
        "trip_unit": "Trip unit",
        "part_number": "Part number",
        "quantity": "Quantity",
        "nameplate": "Nameplate",
        "control_voltage_v": "Control voltage",
        "interrupting_ka": "Interrupting rating",
        "device_presence": "Device presence",
    }.get(field, field)
    return {
        "id": stable_id("equipment-check", tag, field),
        "device_tag": tag,
        "field": field,
        "label": f"{tag} · {label}",
        "status": status,
        "summary": reason,
        "reason": reason,
        "baseline_fact_ids": [fact["id"] for fact in old],
        "target_fact_ids": [fact["id"] for fact in new],
        "required_fact_ids": [fact["id"] for fact in specs],
        "requested_fact_ids": [fact["id"] for fact in requested],
        "baseline_drawing": _view([fact for fact in old if fact["role"] == "drawing"]),
        "baseline_bom": _view([fact for fact in old if fact["role"] == "bom"]),
        "target_drawing": _view([fact for fact in new if fact["role"] == "drawing"]),
        "target_bom": _view([fact for fact in new if fact["role"] == "bom"]),
        "required": _view(specs),
        "requested": _view(requested),
        "evidence": old + new + specs + requested,
        **extra,
    }


def compare_packages(
    baseline_sources,
    target_sources,
    requirement_sources=None,
    *,
    identity_profile=None,
    identity_source_catalog=None,
    scope_id=None,
    source_context=None,
    calibration=None,
):
    """Compare actual file contents, never a response claiming they were fixed.

    Specification values and explicit requested changes must agree. Conflicting
    requested values require engineering clarification rather than overriding
    the specification. Reconciliation adjudicates values after preserving every
    source observation; scoped native designations or approved profiles bind records.
    """
    baseline = analyze_package(baseline_sources)
    target = analyze_package(target_sources)
    requirements = analyze_package(requirement_sources or [])
    identity, identity_errors = None, []
    if identity_profile is not None:
        from .identity import IdentityContext, IdentityError, logical_analysis

        try:
            context = IdentityContext(identity_profile, identity_source_catalog)
            baseline, target, requirements = [
                logical_analysis(a, context) for a in (baseline, target, requirements)
            ]
            identity = context.receipt()
        except IdentityError as exc:
            identity_errors.append(str(exc))
    old = _group(baseline, {"drawing", "bom"})
    new = _group(target, {"drawing", "bom"})
    # Actual drawing/BOM documents supplied as requirements are not promoted
    # to authority. They must be explicitly registered as specifications.
    specs = _group(requirements, {"specification"})
    requests = defaultdict(list)
    for analysis in (baseline, requirements, target):
        for fact in analysis["requested_changes"]:
            requests[(fact["device_tag"], fact["field"])].append(fact)
    checks = []
    keys = sorted(set(old) | set(new) | set(specs) | set(requests))
    all_tags = {tag for tag, _ in keys} | {
        device["tag"] for device in baseline["devices"]
    }
    presence_issues = {}
    for tag in sorted(all_tags):
        expected_roles = {
            role for role in ("drawing", "bom") if _role_observed(baseline, role, tag)
        }
        # An explicit specification/request establishes a device expected in
        # the revised package, even if it was absent from the baseline.
        if any(key[0] == tag for key in specs.keys() | requests.keys()):
            expected_roles |= {"drawing", "bom"}
        missing_roles = sorted(
            role for role in expected_roles if not _role_observed(target, role, tag)
        )
        if missing_roles:
            uncertain = any(
                _role_uncertain(target, role, tag) for role in missing_roles
            )
            status = "unknown" if uncertain else "missing"
            reason = (
                f"{tag} was not established in the revised {' and '.join(missing_roles)}. "
                + (
                    "Unreadable or partially interpreted sources prevent proving its absence."
                    if uncertain
                    else "The baseline or explicit requirements include this device; the revised package does not show it."
                )
            )
            facts_old = [
                fact for key, group in old.items() if key[0] == tag for fact in group
            ]
            facts_new = [
                fact for key, group in new.items() if key[0] == tag for fact in group
            ]
            facts_spec = [
                fact for key, group in specs.items() if key[0] == tag for fact in group
            ]
            checks.append(
                _check(
                    tag,
                    "device_presence",
                    status,
                    reason,
                    facts_old,
                    facts_new,
                    facts_spec,
                    missing_roles=missing_roles,
                )
            )
            presence_issues[tag] = set(missing_roles)
    for tag, field in keys:
        old_facts, new_facts, required = (
            old[(tag, field)],
            new[(tag, field)],
            specs[(tag, field)],
        )
        requested = requests[(tag, field)]
        expected_roles = {fact["role"] for fact in old_facts}
        if required or requested:
            # Change closure is explicitly a drawing-and-BOM check. A missing
            # whole source role cannot disappear from the denominator.
            expected_roles |= {"drawing", "bom"}
        missing_roles = sorted(
            role
            for role in expected_roles
            if not any(fact["role"] == role for fact in new_facts)
        )
        duplicates = [
            role
            for role in ("drawing", "bom")
            if len(_values([fact for fact in new_facts if fact["role"] == role])) > 1
        ]
        absence_only = False
        # Repeated targets cannot vote a contradiction out of existence.
        if (
            len(_values(required)) > 1
            or len(_values(requested)) > 1
            or required
            and requested
            and _values(required) != _values(requested)
        ):
            status, reason = (
                "unknown",
                "The specification and requested values conflict or contain contradictory requirements. An engineer must establish which requirement governs.",
            )
        elif duplicates:
            status, reason = (
                "mismatch",
                f"The revised {' and '.join(duplicates)} contains contradictory values for this exact device and field.",
            )
        elif required and any(
            _value_key(fact) not in _values(required) for fact in new_facts
        ):
            status, reason = (
                "mismatch",
                "The actual revised drawing or BOM disagrees with the explicit specification value.",
            )
        elif requested and any(
            _value_key(fact) not in _values(requested) for fact in new_facts
        ):
            status, reason = (
                "mismatch",
                "The requested change is not reflected consistently in the actual revised drawing and BOM.",
            )
        elif len(_values(new_facts)) > 1:
            status, reason = (
                "mismatch",
                "The actual revised drawing and BOM disagree on this field.",
            )
        elif missing_roles:
            uncertain = any(
                _role_uncertain(target, role, tag, field) for role in missing_roles
            )
            status = "unknown" if uncertain else "missing"
            reason = (
                f"The revised {' and '.join(missing_roles)} does not establish this field. "
                + (
                    "A formula, unreadable source, ambiguous value or unsupported unit prevents verification."
                    if uncertain
                    else "The value cannot be verified from the supplied file contents."
                )
            )
            absence_only = set(missing_roles) <= presence_issues.get(tag, set())
        elif not new_facts:
            status, reason = (
                "unknown",
                "No supported actual drawing or BOM value was established for this field.",
            )
        elif required or requested:
            status, reason = (
                "verified",
                "The supported actual drawing and BOM values match the explicit requirement and requested change."
                if required and requested
                else "The supported actual drawing and BOM values match the explicit specification."
                if required
                else "The supported actual drawing and BOM values match the explicit requested change. Engineering approval is separate.",
            )
        elif _values(new_facts) == _values(old_facts):
            status, reason = (
                "unchanged",
                "The observed drawing and BOM value is unchanged from the baseline; no explicit new requirement was supplied for this field.",
            )
        else:
            status, reason = (
                "unknown",
                "The actual value changed without an explicit governing requirement. The change was observed but its intent cannot be verified.",
            )
        if absence_only:
            # One absent device/source-role is one practical issue, rather
            # than a separate task for every property it cannot establish.
            # Earlier mismatch/requirement-conflict branches are retained:
            # an incorrect BOM value is still a distinct problem even when
            # that device is also absent from the drawing.
            continue
        checks.append(
            _check(
                tag,
                field,
                status,
                reason,
                old_facts,
                new_facts,
                required,
                requested,
                missing_roles=missing_roles,
            )
        )
    if identity_errors:
        checks.append(
            _check(
                "Company identity",
                "identity_resolution",
                "unknown",
                " ".join(identity_errors),
                [],
                [],
                [],
            )
        )
    if identity:
        for check in checks:
            check["original_device_tags"] = sorted(
                {
                    f.get("original_device_tag", f.get("device_tag"))
                    for f in check["evidence"]
                }
            )
            check["physical_device_id"] = context.physical(check["device_tag"])
    counts = Counter(check["status"] for check in checks)
    source_issues = [
        report
        for analysis in (baseline, target, requirements)
        for report in analysis["sources"]
        if report["role"] != "markup"
        and report["status"] in {"unreadable", "unsupported", "partial"}
    ]
    # No checks or unreadable coverage must never turn into a green zero-item
    # success. Overall state is explicitly bounded to extracted properties.
    baseline_available = bool(baseline["facts"]) and all(
        any(
            report["role"] == role and report["status"] in {"parsed", "partial"}
            for report in baseline["sources"]
        )
        for role in ("drawing", "bom")
    )
    if counts["mismatch"] or counts["missing"]:
        outcome = "changes_remaining"
    elif (
        counts["unknown"]
        or source_issues
        or not checks
        or not baseline_available
        or not (specs or requests)
    ):
        outcome = "cannot_verify"
    else:
        outcome = "supported_fields_match"
    warnings = baseline["warnings"] + target["warnings"] + requirements["warnings"]
    if not baseline_available:
        warnings.append(
            {
                "code": "baseline_incomplete",
                "message": "A readable baseline drawing and BOM were not both supplied. Current values may match a requirement, but a complete before/after revision loop was not established.",
            }
        )
    if not specs and not requests:
        warnings.append(
            {
                "code": "no_explicit_requirements",
                "message": "No explicit specification values or target changes were supplied. Observed consistency is not proof that requested changes were completed.",
            }
        )
    result = {
        "schema_version": SCHEMA_VERSION,
        "baseline": baseline,
        "target": target,
        "requirements": requirements,
        "checks": checks,
        "summary": {
            "outcome": outcome,
            "verified": counts["verified"],
            "unchanged": counts["unchanged"],
            "mismatch": counts["mismatch"],
            "missing": counts["missing"],
            "unknown": counts["unknown"],
            "remaining": counts["mismatch"] + counts["missing"],
            "cannot_verify": counts["unknown"],
            "total": len(checks),
            "source_issues": len(source_issues),
            "production_release_verified": False,
            "revision_changes_verified": outcome == "supported_fields_match",
        },
        "coverage": {
            "baseline_observed_device_count": baseline["coverage"][
                "observed_device_count"
            ],
            "target_observed_device_count": target["coverage"]["observed_device_count"],
            "explicit_requirement_count": sum(len(group) for group in specs.values()),
            "explicit_requested_change_count": sum(
                len(group) for group in requests.values()
            ),
            "complete_inventory_verified": False,
            "baseline_comparison_available": baseline_available,
            "limitations": LIMITATIONS,
        },
        "warnings": warnings,
        "identity_resolution": identity,
        "identity_issues": identity_errors,
    }

    from .evidence.integration import reconcile_comparison

    return reconcile_comparison(
        result,
        scope_id=scope_id,
        source_context=source_context,
        calibration=calibration,
    )
