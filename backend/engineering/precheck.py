"""Pure, bounded source conformance; file consistency is a separate check.

Inputs are already authorized, hash-pinned reader results. This module does no
I/O, model calls, source interpretation, approval, or mutation. A matching
proposed observation remains unverified until a person confirms its meaning.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy

from .requirements import CUSTOMER_ROLES, canonical_requirement_value

VERSION = "rivet.engineering-source-precheck.v1"
DEVICE_FIELDS = {
    "poles": "pole_count",
    "trip_type": "trip_type",
    "frame": "frame",
    "device_frame": "frame",
    "frame_a": "frame_a",
    "trip_a": "trip_a",
    "rated_current_a": "rated_current_a",
    "interrupting_ka": "interrupting_ka",
    "minimum_interrupting_ka": "interrupting_ka",
    "part_number": "part_number",
    "nameplate": "nameplate",
    "supplied": "supplied",
    "position_state": "position_state",
    "quantity": "quantity",
    "kind": "kind",
    "required_ampacity_a": "rated_current_a",
}
ARTIFACT_FIELDS = {"trip_type": "trip_unit", "frame": "device_frame"}
SYSTEM_FIELDS = frozenset(
    {
        "service_rating_a",
        "voltage_v",
        "voltage_to_ground_v",
        "current_type",
        "frequency_hz",
        "phases",
        "wires",
        "grounding",
        "service_disconnect",
        "code_edition",
        "bus_material",
        "bus_plating",
        "neutral_bus_percent",
        "ground_bus_material",
        "bus_insulation",
        "enclosure_type",
        "access",
        "alignment",
        "available_fault_ka",
        "minimum_interrupting_ka",
        "short_time_withstand_ka",
    }
)
OPTION_FIELDS = frozenset(
    {
        "metering",
        "utility_metering",
        "customer_metering",
        "spd",
        "spd_rating_ka",
        "ground_fault",
        "arc_energy_reduction",
    }
)
CONSTRAINT_FIELDS = frozenset({"service_side", "seismic"})


def _equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(left) and math.isfinite(right) and left == right
    # No fuzzy tags, trip-function aliases, case folding, or prose semantics.
    return type(left) is type(right) and left == right


def _value(value):
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def _refs_valid(refs):
    return bool(refs) and all(
        isinstance(ref, dict)
        and ref.get("source_id")
        and re.fullmatch(r"[a-fA-F0-9]{64}", str(ref.get("sha256", "")))
        and isinstance(ref.get("page"), int)
        and not isinstance(ref["page"], bool)
        and ref["page"] >= 1
        for ref in refs
    )


def _unique_device(design, subject):
    devices = design.get("devices", [])
    if subject == "main":
        matches = [d for d in devices if d.get("role") == "main"]
    else:
        matches = [d for d in devices if d.get("id") == subject]
        if not matches:
            matches = [d for d in devices if d.get("tag") == subject]
    return matches[0] if len(matches) == 1 else None


def _model_value(design, subject, key):
    """Only explicit model fields count; bus capacity never becomes service demand."""
    if subject == "order" and key.startswith("main."):
        subject, key = "main", key.split(".", 1)[1]
    if subject != "order":
        device = _unique_device(design, subject)
        return (
            (device or {}).get(DEVICE_FIELDS.get(key, "")),
            device,
            key in DEVICE_FIELDS,
        )
    if key in SYSTEM_FIELDS:
        return _value(design.get("system", {}).get(key, design.get(key))), None, True
    if key in OPTION_FIELDS:
        return _value(design.get("options", {}).get(key)), None, True
    if key in CONSTRAINT_FIELDS:
        return _value(design.get("constraints", {}).get(key)), None, True
    if key in {"max_height_in", "max_width_in", "max_depth_in"}:
        field = key.removeprefix("max_")
        values = [section.get(field) for section in design.get("sections", [])]
        if not values or any(
            not isinstance(v, (int, float))
            or isinstance(v, bool)
            or not math.isfinite(v)
            or v <= 0
            for v in values
        ):
            return None, None, True
        return sum(values) if key == "max_width_in" else max(values), None, True
    return None, None, False


def _comparison(expected, actual, key):
    if actual is None:
        return "missing"
    if key.startswith("max_") or key.endswith("required_ampacity_a"):
        relation = "maximum" if key.startswith("max_") else "minimum"
    elif key == "minimum_interrupting_ka":
        relation = "minimum"
    else:
        return "match" if _equal(expected, actual) else "differ"
    if any(
        not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
        for v in (expected, actual)
    ):
        return "unknown"
    matches = actual <= expected if relation == "maximum" else actual >= expected
    return "match" if matches else "differ"


def _summary(rows):
    return {
        status: sum(row["status"] == status for row in rows)
        for status in ("pass", "fail", "unverified", "not_applicable")
    }


def _artifact_fact(inventory, role, device, key, design):
    """Use independently read original-file facts, never a model projection."""
    supplied = inventory.get(role, {})
    report, facts = supplied.get("report", {}), supplied.get("facts", [])
    if (
        report.get("role") != role
        or report.get("status") not in {"parsed", "partial"}
        or not report.get("id")
        or not re.fullmatch(r"[a-fA-F0-9]{64}", str(report.get("sha256", "")))
    ):
        return None, [], "A usable hash-bound saved-file reading is unavailable."
    field = ARTIFACT_FIELDS.get(
        DEVICE_FIELDS.get(key, key), DEVICE_FIELDS.get(key, key)
    )
    tags = (
        {str(device.get("tag") or device["id"]), str(device["id"])} if device else set()
    )
    candidates = [
        fact
        for fact in facts
        if fact.get("field") == field
        and (
            (
                device is not None
                and (
                    fact.get("device_tag") in tags
                    or fact.get("model_local_identity") is True
                    and fact.get("model_device_id") == device.get("id")
                )
            )
            or (device is None and fact.get("scope") == "system")
        )
    ]
    if not candidates:
        return (
            None,
            [],
            "The saved file has no independently located fact for this requirement.",
        )
    for fact in candidates:
        if (
            fact.get("source_id") != report["id"]
            or fact.get("sha256", "").lower() != report["sha256"].lower()
            or fact.get("role") != role
            or not fact.get("evidence")
        ):
            return (
                None,
                [],
                "A saved-file fact is missing its source/hash/location binding.",
            )
        if (fact.get("source_local_identity") or fact.get("model_local_identity")) and (
            str(fact.get("model_id")) != str(design.get("model_id"))
            or str(fact.get("model_version")) != str(design.get("model_version"))
            or device is None
            or fact.get("model_device_id") != device.get("id")
        ):
            return (
                None,
                [],
                "A source-local saved-file identity is bound to a different model.",
            )
    values = [fact.get("value") for fact in candidates]
    if any(not _equal(values[0], value) for value in values[1:]):
        return None, [], "Conflicting saved-file facts cannot establish one value."
    refs = [
        {
            "source_id": f["source_id"],
            "sha256": f["sha256"],
            "location": deepcopy(f["evidence"]),
            "fact_id": f.get("id"),
        }
        for f in candidates
    ]
    return values[0], refs, None


def _requirement_checks(requirements, design, inventory):
    checks, artifact_checks = [], []
    conflicts = {
        identifier
        for conflict in requirements.get("conflicts", [])
        for identifier in conflict.get("item_ids", [])
    }
    for item in requirements.get("items", []):
        subject, key = item.get("subject_id", "order"), item.get("key", "")
        expected, _unit = canonical_requirement_value(
            key, item.get("value"), item.get("unit")
        )
        actual, device, supported = _model_value(design, subject, key)
        source_refs = deepcopy(item.get("source_refs", []))
        reason = None
        reference_only = (
            item.get("equipment_role") == "reference"
            and item.get("configuration_eligible") is False
        )
        if reference_only:
            reason = "Explicit engineer reference-only decision; retained as source observation, not a purchase/design requirement."
        elif item.get("dismissed"):
            reason = (
                "Dismissed by the recorded requirement decision; not a compliance pass."
            )
        elif item.get("excluded"):
            reason = "Quote exclusion still needs an explicit engineer scope decision."
        elif item.get("id") in conflicts or item.get("unresolved_role"):
            reason = "Conflicting value or ambiguous equipment ownership needs review."
        elif (
            item.get("origin_role") not in CUSTOMER_ROLES
            or not _refs_valid(source_refs)
            or not item.get("source_claim_ids")
        ):
            reason = "Customer source role and hash/page/claim provenance are not established."
        elif expected is None:
            reason = "The source value is unknown or has unsupported units."
        elif not supported:
            reason = "This clause has no implemented scalar conformance comparison."
        elif not item.get("confirmed"):
            reason = "A proposed source observation is not a confirmed engineering requirement."
        comparison = (
            _comparison(expected, actual, key.removeprefix("main."))
            if supported and expected is not None
            else "unknown"
        )
        status = (
            "not_applicable"
            if item.get("dismissed") or reference_only
            else "unverified"
            if reason
            else "pass"
            if comparison == "match"
            else "fail"
        )
        row = {
            "requirement_id": item.get("id"),
            "subject_id": subject,
            "key": key,
            "expected": deepcopy(expected),
            "observed": deepcopy(actual),
            "comparison": comparison,
            "status": status,
            "reason": reason
            or "Compared the explicit source requirement with the selected model.",
            "source_refs": source_refs,
            "source_claim_ids": deepcopy(item.get("source_claim_ids", [])),
            "source_role": item.get("origin_role"),
        }
        checks.append(row)
        if item.get("dismissed"):
            continue
        for role in ("drawing", "bom"):
            observed, refs, error = _artifact_fact(
                inventory, role, device, key.removeprefix("main."), design
            )
            compared = (
                _comparison(expected, observed, key.removeprefix("main."))
                if supported and expected is not None
                else "unknown"
            )
            artifact_checks.append(
                {
                    **deepcopy(row),
                    "artifact_role": role,
                    "observed": observed,
                    "comparison": compared,
                    "artifact_refs": refs,
                    "status": "unverified"
                    if reason or error
                    else "pass"
                    if compared == "match"
                    else "fail",
                    "reason": reason
                    or error
                    or "Compared against a separately read saved-file fact.",
                }
            )
    return checks, artifact_checks


def _source_topology(requirements, design, observations):
    """Compare explicit direct visible endpoints only, without inferring ports."""
    pinned = {
        (ref["source_id"], ref["sha256"].lower(), ref["page"])
        for item in requirements.get("items", [])
        for ref in item.get("source_refs", [])
        if _refs_valid([ref]) and item.get("origin_role") == "customer_one_line"
    }
    for report in requirements.get("drawing_readings", []):
        if re.fullmatch(r"[a-fA-F0-9]{64}", str(report.get("sha256", ""))):
            pinned.update(
                (report.get("source_id"), report["sha256"].lower(), page)
                for page in report.get("pages", [])
                if isinstance(page, int) and not isinstance(page, bool) and page > 0
            )
    model_bus = design.get("topology", {}).get("bus_id")
    model_pairs = set()
    for edge in design.get("topology", {}).get("connections", []):
        start = edge.get("from_device_id") or edge.get("from_bus_id")
        end = edge.get("to_device_id") or edge.get("to_bus_id")
        if start and end:
            model_pairs.add(frozenset((start, end)))
    checks = []
    for report in observations or []:
        for page in report.get("pages", []):
            source = (
                report.get("source_id"),
                str(report.get("sha256", "")).lower(),
                page.get("page"),
            )
            ref = {"source_id": source[0], "sha256": source[1], "page": source[2]}
            reading = page.get("reading", {})
            if source not in pinned:
                checks.append(
                    {
                        "status": "unverified",
                        "reason": "Drawing observation is not pinned to a current customer one-line page.",
                        "source_refs": [ref],
                    }
                )
                continue
            mapped = {}
            busbars = [
                obj for obj in reading.get("objects", []) if obj.get("kind") == "busbar"
            ]
            if len(busbars) == 1 and model_bus:
                mapped[busbars[0]["key"]] = model_bus
            for obj in reading.get("objects", []):
                owners = set()
                for item in requirements.get("items", []):
                    if (
                        item.get("dismissed")
                        or item.get("unresolved_role")
                        or item.get("source_object_key") != obj.get("key")
                    ):
                        continue
                    if not any(
                        (
                            r.get("source_id"),
                            str(r.get("sha256", "")).lower(),
                            r.get("page"),
                        )
                        == source
                        for r in item.get("source_refs", [])
                    ):
                        continue
                    subject = (
                        "main"
                        if item.get("key", "").startswith("main.")
                        else item.get("subject_id")
                    )
                    device = _unique_device(design, subject)
                    if device:
                        owners.add(device["id"])
                if len(owners) == 1:
                    mapped[obj["key"]] = next(iter(owners))
            for wire in reading.get("conductors", []):
                endpoints = [
                    mapped.get(wire.get(key)) for key in ("from_object", "to_object")
                ]
                supported = (
                    wire.get("connected_visible") is True
                    and wire.get("kind") in {"conductor", "bus"}
                    and all(endpoints)
                    and len(set(endpoints)) == 2
                )
                checks.append(
                    {
                        "connection_key": wire.get("key"),
                        "source_refs": [{**ref, "bbox": deepcopy(wire.get("bbox"))}],
                        "model_endpoints": endpoints,
                        "status": "unverified"
                        if not supported
                        else "pass"
                        if frozenset(endpoints) in model_pairs
                        else "fail",
                        "reason": "Direct visible endpoint pair compared; direction and physical terminals are not verified."
                        if supported
                        else "Ambiguous, prepared, multiply mapped or unsupported source connectivity needs engineer review.",
                    }
                )
            for unknown in reading.get("unknown_regions", []) + reading.get(
                "ambiguities", []
            ):
                checks.append(
                    {
                        "status": "unverified",
                        "reason": "Source drawing retains an unresolved region or association.",
                        "detail": deepcopy(unknown),
                        "source_refs": [ref],
                    }
                )
    return {
        "checks": checks,
        "summary": _summary(checks),
        "full_topology_checked": False,
        "supported_connection_count": sum(
            row["status"] in {"pass", "fail"} for row in checks
        ),
        "scope": "Explicit pinned direct visible object pairs only. No complete topology, absence, port, wiring or engineering-validity claim.",
    }


def _memory_checks(brief, design, inventory):
    rows = []
    if not brief.get("snapshot"):
        rows.append(
            {
                "status": "unverified",
                "reason": "A current customer-memory retrieval snapshot is unavailable.",
            }
        )
    for note in brief.get("preferences", []):
        evidence = deepcopy(note.get("provenance", {}).get("evidence", []))
        bindings = note.get("provenance", {}).get("engineering_conformance", [])
        eligible = (
            note.get("status") == "approved"
            and note.get("evidence_current") is True
            and note.get("applicability") == "standing_preference"
            and not note.get("conflicts_with")
            and evidence
        )
        if not eligible or not isinstance(bindings, list) or not bindings:
            rows.append(
                {
                    "memory_id": note.get("id"),
                    "title": note.get("title"),
                    "status": "unverified",
                    "source_evidence": evidence,
                    "reason": "An applicable approved current note needs an explicit typed conformance binding; prose/topic retrieval is not validation.",
                }
            )
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                rows.append(
                    {
                        "memory_id": note.get("id"),
                        "status": "unverified",
                        "reason": "The typed preference binding is malformed.",
                    }
                )
                continue
            subject, key = binding.get("subject_id", "order"), binding.get("key", "")
            expected = binding.get("value")
            observed, device, supported = _model_value(design, subject, key)
            comparison = (
                _comparison(expected, observed, key.removeprefix("main."))
                if supported and expected is not None
                else "unknown"
            )
            artifacts = []
            for role in ("drawing", "bom"):
                value, refs, error = _artifact_fact(
                    inventory, role, device, key.removeprefix("main."), design
                )
                compared = (
                    _comparison(expected, value, key.removeprefix("main."))
                    if supported and expected is not None
                    else "unknown"
                )
                artifacts.append(
                    {
                        "artifact_role": role,
                        "observed": value,
                        "artifact_refs": refs,
                        "status": "unverified"
                        if error or not supported or expected is None
                        else "pass"
                        if compared == "match"
                        else "fail",
                    }
                )
            status = (
                "fail"
                if comparison == "differ"
                or any(a["status"] == "fail" for a in artifacts)
                else (
                    "pass"
                    if comparison == "match"
                    and all(a["status"] == "pass" for a in artifacts)
                    else "unverified"
                )
            )
            rows.append(
                {
                    "memory_id": note.get("id"),
                    "title": note.get("title"),
                    "subject_id": subject,
                    "key": key,
                    "expected": deepcopy(expected),
                    "observed": deepcopy(observed),
                    "comparison": comparison,
                    "status": status,
                    "source_evidence": evidence,
                    "artifacts": artifacts,
                    "reason": "Explicit typed approved preference compared with model and saved files; no prose inference.",
                }
            )
    for note in brief.get("withheld", []):
        rows.append(
            {
                "memory_id": note.get("id"),
                "title": note.get("title"),
                "status": "unverified",
                "reason": "Customer context was withheld: "
                + str(note.get("reason", "needs review")),
            }
        )
    if brief.get("partial"):
        rows.append(
            {"status": "unverified", "reason": "Customer-memory retrieval is partial."}
        )
    return {
        "checks": rows,
        "summary": _summary(rows),
        "brief_snapshot": brief.get("snapshot"),
        "status": "not_applicable"
        if not rows
        else "fail"
        if any(r["status"] == "fail" for r in rows)
        else "pass"
        if all(r["status"] == "pass" for r in rows)
        else "unverified",
        "scope": "Explicit approved typed preferences only; ordinary prose notes remain an engineer checklist.",
    }


def precheck_source_conformance(
    requirements,
    design,
    *,
    generated_inventory=None,
    customer_brief=None,
    drawing_observations=None,
):
    """Check preserved source facts separately from native DXF/BOM consistency.

    ``generated_inventory`` maps drawing/bom to ``{report, facts}`` returned by
    equipment.readers.read_source. Root/system saved-file facts must explicitly
    carry ``scope='system'``. No system values are inferred from a device label.
    Full drawing reader reports can be passed as ``drawing_observations``;
    summary-only drawing_readings never establish topology. Approved memory may
    supply an explicit list of {subject_id,key,value} in provenance's
    engineering_conformance; this function never derives bindings from prose.
    """
    if not isinstance(requirements, dict) or not isinstance(design, dict):
        raise TypeError("Requirements and selected design must be mappings.")
    inventory, brief = generated_inventory or {}, customer_brief or {}
    checks, artifact_checks = _requirement_checks(requirements, design, inventory)
    topology = _source_topology(requirements, design, drawing_observations)
    memory = _memory_checks(brief, design, inventory)
    coverage = deepcopy(requirements.get("unreadable_pages", []))
    coverage += [
        deepcopy(w)
        for w in requirements.get("warnings", [])
        if w.get("verification_required")
        or w.get("deferred_span_ids")
        or "failed" in str(w.get("reason", "")).lower()
    ]
    coverage += [
        deepcopy(r)
        for r in requirements.get("reader_coverage", [])
        if r.get("state") in {"partial", "failed"}
    ]
    saved_file_gaps = [
        {
            "artifact_role": role,
            "status": data.get("report", {}).get("status", "unavailable"),
            "warnings": deepcopy(data.get("report", {}).get("warnings", [])),
        }
        for role, data in inventory.items()
        if data.get("report", {}).get("status") != "parsed"
    ]
    one_line = [r for r in checks if r["source_role"] == "customer_one_line"]
    all_rows = checks + artifact_checks + topology["checks"] + memory["checks"]
    status = (
        "fail"
        if any(r["status"] == "fail" for r in all_rows)
        else "unverified"
        if (
            not checks
            or coverage
            or saved_file_gaps
            or any(r["status"] == "unverified" for r in all_rows)
        )
        else "pass"
    )
    return {
        "schema_version": VERSION,
        "status": status,
        "model_id": design.get("model_id"),
        "model_version": design.get("model_version"),
        "requirement_checks": checks,
        "requirement_summary": _summary(checks),
        "saved_file_source_checks": artifact_checks,
        "saved_file_summary": _summary(artifact_checks),
        "coverage_blockers": coverage,
        "saved_file_reading_gaps": saved_file_gaps,
        "source_topology": topology,
        "customer_memory": memory,
        "one_line_scalar_summary": _summary(one_line),
        "customer_one_line_checked": False,
        "customer_memory_checked": memory["status"] == "pass",
        "engineering_approved": False,
        "production_released": False,
        "scope": "Bounded explicit-source scalar/device conformance. Separate from native file consistency, full customer-one-line semantics, engineering validity and approval.",
    }
