"""Bounded, cited QED-2 proposals; no approval or certification is produced.

Requirements are inputs, catalog facts constrain proposals, and optional approved
job habits only rank feasible layouts. Missing manufacturer data stays missing.
No approved reference lineup, answer key, OCR correction or equipment editing is
part of this API. Physical mounting offsets are distinct from packing coordinates.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from backend.equipment.evidence.schema import stable_id

VERSION = "rivet.switchboard-configurator.v2.2"
DEFAULT_RULE_PACK = "schneider-qed2-ul891-2026.07-v1"
MAXIMUM_DEVICES = 120


@dataclass(frozen=True)
class RequirementValue:
    """A confirmed value may still be proposed; confirmation is not approval."""

    value: Any
    confirmed: bool = False
    source_claim_ids: tuple[str, ...] = ()


def _value(value):
    if isinstance(value, RequirementValue):
        return value.value
    return value.get("value") if isinstance(value, dict) and "value" in value else value


def _number(value):
    value = _value(value)
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
        else None
    )


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=asdict,
        ).encode()
    ).hexdigest()


def load_rule_pack(path=None):
    """Load versioned DATA; injected packs must retain citations and history."""
    path = (
        Path(path)
        if path
        else Path(__file__).with_name("rules") / (DEFAULT_RULE_PACK + ".json")
    )
    pack = json.loads(path.read_text())
    validate_rule_pack(pack)
    return pack


def validate_rule_pack(pack):
    """Reject malformed edited data; validation does not approve its authority."""
    if (
        pack.get("schema_version") != "rivet.engineering-rule-pack.v1"
        or not pack.get("id")
        or not pack.get("version")
        or not pack.get("review_history")
    ):
        raise ValueError("A versioned rule pack with review history is required")
    for item in [
        *pack.get("devices", []),
        *pack.get("section_types", []),
        *pack.get("rules", []),
        pack.get("scope", {}),
        *pack.get("code_triggers", {}).values(),
    ]:
        if not item.get("source_refs"):
            raise ValueError("Every catalog/rule row requires a source citation")
        for ref in item["source_refs"]:
            if (
                ref.get("source_id") not in pack.get("sources", {})
                or not isinstance(ref.get("page"), int)
                or isinstance(ref.get("page"), bool)
                or ref["page"] < 1
            ):
                raise ValueError(
                    "Catalog rows require a known source and physical page"
                )
    if len({d["id"] for d in pack["devices"]}) != len(pack["devices"]) or len(
        {s["id"] for s in pack["section_types"]}
    ) != len(pack["section_types"]):
        raise ValueError("Catalog and section variant IDs must be unique")
    for row in pack["devices"]:
        if (
            any(
                _number(row.get(k)) is None
                for k in ("frame_a", "minimum_a", "maximum_a", "pole_count")
            )
            or not row["minimum_a"] <= row["maximum_a"] <= row["frame_a"]
        ):
            raise ValueError("Invalid device capacity/range")
        if "feeder" in row["roles"] and _number(row.get("mounting_space_in")) is None:
            raise ValueError("Group-mounted devices require cited mounting space")
        if (
            not row.get("trip_types")
            or not row.get("catalog_sccr_ka")
            or any(_number(v) is None for v in row["catalog_sccr_ka"].values())
        ):
            raise ValueError(
                "Device trip functions and conditional short-circuit ratings required"
            )
    for section in pack["section_types"]:
        if any(
            _number(section.get(k)) is None
            for k in ("width_in", "minimum_depth_in", "height_in", "interior_bus_a")
        ) or not section.get("sides"):
            raise ValueError("Invalid section dimensions/capacity")
        for side in section["sides"].values():
            if (
                _number(side.get("mounting_space_in")) is None
                or _number(side.get("maximum_frame_a")) is None
                or not side.get("families")
            ):
                raise ValueError("Invalid interior mounting limits")
    factors = pack.get("interior_unknown_loading_factors", [])
    if (
        not factors
        or factors[-1].get("maximum_devices") is not None
        or any(_number(x.get("factor")) is None or x["factor"] > 1 for x in factors)
    ):
        raise ValueError("Complete positive interior loading factors required")
    for key in ("maximum_voltage_v", "maximum_service_a", "height_in"):
        if _number(pack["scope"].get(key)) is None:
            raise ValueError("Positive QED scope limits required")
    bus_range = pack["scope"].get("through_bus_range_a")
    if (
        not isinstance(bus_range, list)
        or len(bus_range) != 2
        or any(_number(value) is None for value in bus_range)
        or bus_range[0] > bus_range[1]
    ):
        raise ValueError("An ordered cited through-bus range is required")
    bus_ratings = pack["scope"].get("through_bus_standard_ratings_a", [])
    if (
        not isinstance(bus_ratings, list)
        or any(
            _number(value) is None or not bus_range[0] <= value <= bus_range[1]
            for value in bus_ratings
        )
        or len(set(bus_ratings)) != len(bus_ratings)
    ):
        raise ValueError("Explicit through-bus ratings must be unique and within the cited range")
    for key in (
        "minimum_disconnect_a",
        "exclusive_minimum_voltage_to_ground_v",
        "maximum_phase_voltage_v",
    ):
        if _number(pack["code_triggers"]["nec23095"].get(key)) is None:
            raise ValueError("Positive cited ground-fault thresholds required")
    if _number(pack["code_triggers"]["nec24087"].get("minimum_maximum_trip_a")) is None:
        raise ValueError("Positive cited arc-energy threshold required")
    _digest(pack)


def _voltage_key(voltage):
    return (
        str(int(voltage)) if voltage is not None and voltage == int(voltage) else None
    )


def _required_short_circuit(inputs, device=None):
    values = [
        _number(inputs.get(k))
        for k in ("minimum_interrupting_ka", "available_fault_ka")
    ]
    values += [
        _number((device or {}).get(k))
        for k in ("minimum_interrupting_ka", "interrupting_ka")
    ]
    return max((v for v in values if v is not None), default=None)


def _refs(pack, refs):
    return [
        {**deepcopy(pack["sources"].get(r["source_id"], {})), **deepcopy(r)}
        for r in refs
    ]


def _claim_ids(value):
    if isinstance(value, RequirementValue):
        return list(value.source_claim_ids)
    if isinstance(value, dict):
        return list(dict.fromkeys(value.get("source_claim_ids", [])))
    return []


def _requirement_refs(requirement):
    refs = list(requirement.get("source_refs", []))
    for value in requirement.values():
        if isinstance(value, dict):
            refs += value.get("source_refs", [])
    return list({_digest(ref): deepcopy(ref) for ref in refs}.values())


def _question(key, reason, value=None):
    return {
        "id": stable_id("configuration-question", key, reason),
        "key": key,
        "reason": reason,
        "source_claim_ids": _claim_ids(value),
        "requirement_value": deepcopy(_value(value)),
        "state": "proposed",
    }


def _check(pack, rule, status, reason, refs=None, **extra):
    rows = [r for r in pack.get("rules", []) if r["id"] == rule]
    return {
        "rule_id": rule,
        "rule_version": rows[0]["version"] if rows else pack["version"],
        "status": status,
        "reason": reason,
        "source_refs": _refs(
            pack, refs if refs is not None else rows[0]["source_refs"] if rows else []
        ),
        **extra,
    }


def _input_check(key, status, reason, requirement, **extra):
    """Input conformance is not a manufacturer/installation-code rule."""
    refs, claim_ids = [], _claim_ids(requirement)
    if isinstance(requirement, dict) and "value" not in requirement:
        refs = _requirement_refs(requirement)
        claim_ids += [
            cid for value in requirement.values() for cid in _claim_ids(value)
        ]
    return {
        "rule_id": "input." + key,
        "rule_version": VERSION,
        "status": status,
        "reason": reason,
        "authority": "source_requirement",
        "source_refs": refs,
        "source_claim_ids": list(dict.fromkeys(claim_ids)),
        **extra,
    }


def _system(inputs):
    """Project explicit source scalars without creating electrical defaults."""
    numeric = {
        "service_rating_a", "voltage_v", "voltage_to_ground_v", "frequency_hz",
        "phases", "wires", "available_fault_ka", "minimum_interrupting_ka",
        "short_time_withstand_ka",
    }
    keys = sorted(numeric | {"current_type", "grounding", "service_disconnect", "code_edition"})
    system, provenance = {}, {}
    for key in keys:
        raw = inputs.get(key)
        value = _value(raw)
        if key in numeric:
            value = _number(raw)
        elif key == "service_disconnect":
            value = value if isinstance(value, bool) else None
        else:
            value = value if isinstance(value, (str, int)) and not isinstance(value, bool) else None
        system[key] = deepcopy(value)
        provenance[key] = {
            "source_claim_ids": _claim_ids(raw),
            "source_refs": deepcopy(raw.get("source_refs", [])) if isinstance(raw, dict) else [],
        }
    system["source_claim_ids"] = list(dict.fromkeys(
        claim for evidence in provenance.values() for claim in evidence["source_claim_ids"]
    ))
    refs = [*inputs.get("source_refs", []), *(
        ref for evidence in provenance.values() for ref in evidence["source_refs"]
    )]
    system["source_refs"] = list({_digest(ref): deepcopy(ref) for ref in refs}.values())
    system["provenance"] = provenance
    return system


def _through_bus_rating(inputs, pack):
    """A range or an interior rating does not enumerate through-bus choices."""
    service = _number(inputs.get("service_rating_a"))
    return min((
        rating for rating in pack["scope"].get("through_bus_standard_ratings_a", [])
        if service is not None and rating >= service
    ), default=None)


def _normalized_inputs(requirements):
    if not isinstance(requirements, dict):
        raise TypeError("Typed requirement mapping required")
    result = deepcopy(requirements)
    result.setdefault("feeders", [])
    result.setdefault("main", {})
    result.setdefault("constraints", {})
    if (
        not isinstance(result["feeders"], list)
        or len(result["feeders"]) > MAXIMUM_DEVICES
    ):
        raise ValueError("Feeder list exceeds the bounded configuration scope")
    if not all(isinstance(f, dict) for f in result["feeders"]):
        raise ValueError("Every feeder requires a typed mapping")
    if not isinstance(result["main"], dict) or not isinstance(
        result["constraints"], dict
    ):
        raise TypeError("Main and physical constraints must be mappings")
    ids = [_value(f.get("id")) for f in result["feeders"]]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("Feeder IDs must be nonempty and unique")
    if (_value(result["main"].get("id")) or "main") in ids:
        raise ValueError("Main and feeder IDs must be distinct")
    # Serializability/finiteness is part of deterministic identity, not a cast.
    _digest(result)
    return result


def _device(req, role, inputs, pack):
    """Choose catalog variants by constraints, never infer an exact order code."""
    did = _value(req.get("id")) or "main"
    questions = []
    reuse = role == "main" and _value(req.get("reuse_existing")) is True
    current = _number(req.get("rated_current_a")) or _number(req.get("trip_a"))
    trip = _number(req.get("trip_a"))
    poles = _number(req.get("poles")) or _number(req.get("pole_count"))
    frame = _value(req.get("frame")) or _value(req.get("device_frame"))
    requested_capacity = _number(req.get("frame_a"))
    trip_type = _value(req.get("trip_type"))
    voltage = _number(inputs.get("voltage_v"))
    minimum_ka = _required_short_circuit(inputs, req)
    matches = []
    for row in [] if reuse else pack["devices"]:
        if role == "main" and _value(req.get("kind")) not in {
            None,
            "breaker",
            "circuit_breaker",
        }:
            continue
        if role not in row["roles"] or current is None or poles != row["pole_count"]:
            continue
        if frame is not None and frame != row["frame"]:
            continue  # Bare/legacy letters are not aliases for a current catalog type.
        if requested_capacity is not None and requested_capacity != row["frame_a"]:
            continue
        if not row["minimum_a"] <= current <= row["maximum_a"]:
            continue
        capability = _number(req.get("maximum_trip_a"))
        if capability is not None and capability > row["frame_a"]:
            continue
        if trip is not None and trip > current:
            continue
        if trip_type is not None and trip_type not in row["trip_types"]:
            continue
        rating = row["catalog_sccr_ka"].get(_voltage_key(voltage))
        if minimum_ka is not None and (rating is None or rating < minimum_ka):
            continue
        if (
            row.get("requires_wye_at_480")
            and voltage == 480
            and _value(inputs.get("grounding")) != "solidly_grounded_wye"
        ):
            continue
        matches.append(row)
    matches.sort(key=lambda r: (r["frame_a"], r["mounting_space_in"] or 0, r["frame"]))
    selected = matches[0] if matches else None
    for key, v in (
        ("rated_current_a", current),
        ("trip_a", trip),
        ("pole_count", poles),
        ("trip_type", trip_type),
    ):
        if v is None:
            questions.append(
                _question(
                    f"{did}.{key}",
                    "Confirm this device requirement; no value was filled from a sibling or catalog.",
                    req.get(key),
                )
            )
    if reuse:
        questions.append(
            _question(
                f"{did}.existing_product",
                "Retain the source-described existing main. Verify its condition, operating ratings and compatibility; no new catalog product was selected.",
                req,
            )
        )
        if _value(req.get("supplied")) is True:
            questions.append(
                _question(
                    f"{did}.supplied",
                    "Resolve the supplied-new flag against the explicit instruction to reuse existing equipment.",
                    req.get("supplied"),
                )
            )
    elif selected is None:
        questions.append(
            _question(
                f"{did}.catalog_variant",
                "No compatible exact frame variant in this cited subset. Confirm type, poles, current, trip functions and voltage-specific rating.",
                req,
            )
        )
    else:
        questions.append(
            _question(
                f"{did}.part_number",
                "Select the exact I-Line/individual mounting catalog number, sensor, rating/trip unit and accessories with the manufacturer. A frame range does not establish every standard rating.",
                req,
            )
        )
    catalog_ka = (
        selected["catalog_sccr_ka"].get(_voltage_key(voltage)) if selected else None
    )
    device = {
        "id": did,
        "tag": _value(req.get("tag")),
        "display_tag": _value(req.get("display_tag")),
        "section_label": _value(req.get("section_label")),
        "source_local_identity": _value(req.get("source_local_identity")),
        "role": role,
        "kind": _value(req.get("kind")),
        "manufacturer": pack["manufacturer"],
        "family": pack["family"],
        "frame": selected["frame"] if selected else frame,
        "frame_a": selected["frame_a"] if selected else requested_capacity,
        "rated_current_a": current,
        "trip_a": trip,
        "pole_count": poles,
        "trip_type": trip_type,
        "interrupting_ka": None,
        "catalog_sccr_ka": catalog_ka,
        "catalog_variant_id": selected["id"] if selected else None,
        "position_state": _value(req.get("position_state")) or "unknown",
        "part_number": _value(req.get("part_number")),
        "nameplate": _value(req.get("nameplate")),
        "supplied": _value(req.get("supplied")),
        "quantity": 1,
        "section_id": None,
        "position_id": None,
        "state": "proposed",
        "source_claim_ids": list(
            dict.fromkeys(
                req.get("source_claim_ids", [])
                + [c for v in req.values() for c in _claim_ids(v)]
            )
        ),
        "source_refs": list(
            {
                _digest(ref): ref
                for ref in [
                    *_requirement_refs(req),
                    *(
                        _refs(
                            pack,
                            selected["source_refs"]
                            + selected.get("trip_type_source_refs", []),
                        )
                        if selected
                        else []
                    ),
                ]
            }.values()
        ),
    }
    if reuse:
        device.update(
            reuse_existing=True,
            product_origin="existing_source_equipment",
            manufacturer=_value(req.get("manufacturer")),
            family=_value(req.get("family")),
            part_number=_value(req.get("part_number")),
            interrupting_ka=_number(req.get("interrupting_ka")),
            supplied=None if _value(req.get("supplied")) is True else False,
            source_refs=_requirement_refs(req),
        )
    if (
        _reserved_position(device.get("position_state"))
        and device.get("supplied") is True
    ):
        device["supplied"] = None
        questions.append(
            _question(
                f"{did}.supplied",
                "A prepared/future position does not establish a supplied breaker; resolve the conflicting supplied-new instruction.",
                req.get("supplied"),
            )
        )
    return device, selected, questions


def _reserved_position(state):
    return isinstance(state, str) and state.casefold() in {
        "prepared",
        "prepared_space",
        "space",
        "future",
    }


def _interior_minimum(pack, devices):
    if not devices:
        return 0
    if any(d["rated_current_a"] is None for d in devices):
        return None
    factor = next(
        r["factor"]
        for r in pack["interior_unknown_loading_factors"]
        if r["maximum_devices"] is None or len(devices) <= r["maximum_devices"]
    )
    return max(
        max(d["rated_current_a"] for d in devices),
        sum(d["rated_current_a"] for d in devices) * factor,
    )


def _pack_sections(devices, selected, section_type, pack, model_id, option):
    sections = []
    for device in devices:
        row = selected.get(device["id"])
        if row is None:
            continue
        placed = False
        for section in [*sections, None]:
            new = section is None
            if new:
                sid = stable_id(model_id, option, "distribution", len(sections) + 1)
                section = {
                    "id": sid,
                    "kind": "distribution",
                    "section_type_id": section_type["id"],
                    "width_in": section_type["width_in"],
                    "depth_in": section_type["minimum_depth_in"],
                    "height_in": section_type["height_in"],
                    "bus_a": section_type["interior_bus_a"],
                    "positions": [],
                    "source_refs": _refs(pack, section_type["source_refs"]),
                }
            contents = [d for d in devices if d["section_id"] == section["id"]] + [
                device
            ]
            if _interior_minimum(pack, contents) > section["bus_a"]:
                continue
            for side, bounds in section_type["sides"].items():
                occupied = sum(
                    p["mounting_space_in"]
                    for p in section["positions"]
                    if p["side"] == side
                )
                height = row["mounting_space_in"]
                if (
                    row["frame_family"] not in bounds["families"]
                    or row["frame_a"] > bounds["maximum_frame_a"]
                    or occupied + height > bounds["mounting_space_in"]
                ):
                    continue
                pid = stable_id(section["id"], side, device["id"])
                section["positions"].append(
                    {
                        "id": pid,
                        "device_id": device["id"],
                        "side": side,
                        "start_in": occupied,
                        "mounting_space_in": height,
                        "datum": "top_of_interior_mounting_space",
                        "physical_datum_offset_in": None,
                        "status": "proposed_catalog_space_assignment",
                    }
                )
                device.update(section_id=section["id"], position_id=pid)
                if new:
                    sections.append(section)
                placed = True
                break
            if placed:
                break
        if not placed:
            return None
    return sections


def _unsupported_questions(inputs):
    root_keys = {
        "service_rating_a",
        "voltage_v",
        "voltage_to_ground_v",
        "current_type",
        "frequency_hz",
        "phases",
        "wires",
        "grounding",
        "minimum_interrupting_ka",
        "available_fault_ka",
        "short_time_withstand_ka",
        "service_disconnect",
        "code_edition",
        "ground_fault",
        "arc_energy_reduction",
        "metering",
        "spd",
        "feeders",
        "main",
        "constraints",
        "evidence_claims",
        "conflicts",
        "confirmed",
        "source_claim_ids",
        "source_refs",
        "schema_version",
        "version",
        "multiple_sources",
        "tie",
        "unreadable_pages",
    }
    device_keys = {
        "id",
        "tag",
        "display_tag",
        "section_label",
        "source_local_identity",
        "kind",
        "rated_current_a",
        "trip_a",
        "frame_a",
        "frame",
        "device_frame",
        "poles",
        "pole_count",
        "trip_type",
        "maximum_trip_a",
        "minimum_interrupting_ka",
        "interrupting_ka",
        "arc_energy_reduction",
        "position_state",
        "supplied",
        "source_claim_ids",
        "source_refs",
        "confirmed",
        "state",
        "origin_role",
        "nameplate",
        "part_number",
    }
    constraint_keys = {
        "enclosure",
        "line_load_connection",
        "max_width_in",
        "max_height_in",
        "max_depth_in",
    }
    questions = []
    for path, values, supported in [
        ("", inputs, root_keys),
        ("constraints.", inputs["constraints"], constraint_keys),
        ("main.", inputs["main"], device_keys),
        *[(str(_value(f["id"])) + ".", f, device_keys) for f in inputs["feeders"]],
    ]:
        for key, value in values.items():
            main_field = path == "main." and (
                key in {"required_ampacity_a", "reuse_existing"}
                or (
                    _value(inputs["main"].get("reuse_existing")) is True
                    and key in {"manufacturer", "family", "part_number"}
                )
            )
            if key not in supported and not main_field and _value(value) is not None:
                questions.append(
                    _question(
                        path + key,
                        "This provided requirement has no implemented cited rule in this pack; it is retained for engineer resolution.",
                        value,
                    )
                )
            if (
                isinstance(value, dict)
                and "value" in value
                and value.get("confirmed") is not True
            ):
                questions.append(
                    _question(
                        path + key + ".confirmation",
                        "This source requirement has not been confirmed by an engineer.",
                        value,
                    )
                )
    if inputs.get("unreadable_pages"):
        questions.append(
            _question(
                "unreadable_pages",
                "Unreadable source input pages prevent a complete requirements claim.",
                inputs["unreadable_pages"],
            )
        )
    if _value(inputs.get("multiple_sources")) or _value(inputs.get("tie")):
        questions.append(
            _question(
                "source_arrangement",
                "Multiple-source/tie coordination and topology are outside this single-main pack.",
            )
        )
    if _number(inputs.get("phases")) not in (None, 3) or _number(
        inputs.get("wires")
    ) not in (None, 4):
        questions.append(
            _question(
                "system_configuration",
                "Confirm device poles, neutral, grounding and catalog applicability for this phase/wire arrangement.",
            )
        )
    return questions


def _code_checks(design, requirements, pack):
    checks, questions = [], []
    edition = str(_value(requirements.get("code_edition")) or "")
    main_req = requirements["main"]
    main = next((d for d in design["devices"] if d["role"] == "main"), None)
    rule = pack["code_triggers"]["nec23095"]
    service = _value(requirements.get("service_disconnect"))
    volts = _number(requirements.get("voltage_v"))
    to_ground = _number(requirements.get("voltage_to_ground_v"))
    grounding = _value(requirements.get("grounding"))
    maximum = _number(main_req.get("maximum_trip_a"))
    if edition not in rule["verified_editions"]:
        status, reason = (
            "unknown",
            "Confirm adopted code edition and a reviewed rule for that edition; no default NEC adoption.",
        )
    elif (
        service is False
        or grounding in rule.get("explicit_nontrigger_groundings", [])
        or to_ground is not None
        and to_ground <= rule["exclusive_minimum_voltage_to_ground_v"]
        or maximum is not None
        and maximum < rule["minimum_disconnect_a"]
    ):
        status, reason = (
            "not_applicable",
            "At least one explicit service trigger condition is false; other code provisions remain unevaluated.",
        )
    elif (
        not isinstance(service, bool)
        or grounding != rule["grounding"]
        or to_ground is None
        or volts is None
        or maximum is None
    ):
        status, reason = (
            "unknown",
            "Service designation, grounding, voltage-to-ground and actual highest installed-trip capability are required.",
        )
    elif volts > rule["maximum_phase_voltage_v"]:
        status, reason = "unknown", "Voltage is outside this cited trigger subset."
    elif (
        _value(requirements.get("ground_fault")) is True
        and main
        and "G" in (main.get("trip_type") or "")
    ):
        status, reason = (
            "pass",
            "Proposed ground-fault function responds to the trigger; settings, neutral sensing, exceptions and site testing still need engineering verification.",
        )
    else:
        status, reason = (
            "unknown",
            "This service triggers ground-fault protection; propose a compatible scheme and confirm settings, sensing and exceptions.",
        )
    checks.append(_check(pack, rule["id"], status, reason, rule["source_refs"]))
    if status == "unknown":
        questions.append(_question("ground_fault", reason))
    rule = pack["code_triggers"]["nec24087"]
    req_by_id = {
        _value(d.get("id")) or "main": d for d in [main_req, *requirements["feeders"]]
    }
    for device in design["devices"]:
        dreq = req_by_id.get(device["id"])
        if dreq is None:
            checks.append(_input_check(
                "device_requirement_binding", "fail",
                "This design device has no matching source requirement; code-trigger capability cannot be established.",
                {}, device_id=device["id"],
            ))
            questions.append(_question(
                device["id"] + ".requirement_binding",
                "Restore the device's exact requirement identity or review the requirements and configure again.",
            ))
            continue
        maximum = _number(dreq.get("maximum_trip_a"))
        option = _value(dreq.get("arc_energy_reduction")) or _value(
            requirements.get("arc_energy_reduction")
        )
        if edition not in rule["verified_editions"] or maximum is None:
            status, reason = (
                "unknown",
                "Confirm code edition and actual highest installed overcurrent trip capability; frame size or a lowered setting is insufficient.",
            )
        elif maximum < rule["minimum_maximum_trip_a"]:
            status, reason = (
                "not_applicable",
                "Explicit highest actual trip capability is below this trigger.",
            )
        else:
            status, reason = (
                "unknown",
                "Arc energy reduction is triggered. Confirm selected method operates below studied arcing current, with documentation and site performance testing.",
            )
            if option not in rule["methods"]:
                reason += " No supported method has been selected."
        checks.append(
            _check(
                pack,
                rule["id"],
                status,
                reason,
                rule["source_refs"],
                device_id=device["id"],
            )
        )
        if status == "unknown":
            questions.append(_question(device["id"] + ".arc_energy_reduction", reason))
    return checks, questions


def validate_configuration(design, requirements, rule_pack=None):
    """Recompute constraints after edits; caller-supplied pass flags are ignored."""
    pack = deepcopy(rule_pack) if rule_pack is not None else load_rule_pack()
    validate_rule_pack(pack)
    inputs = _normalized_inputs(requirements)
    checks, questions = [], _unsupported_questions(inputs)
    if design.get("configurator_version") != VERSION:
        checks.append(
            _input_check(
                "configurator_version",
                "fail",
                "Configure again with this version; prior algorithm results are preserved separately.",
                {},
                authority="configurator_version_binding",
            )
        )
    if design.get("requirements_digest") != _digest(inputs):
        checks.append(
            _check(
                pack,
                "qed2.scope",
                "fail",
                "Design is not bound to this exact requirements version/digest; configure again.",
            )
        )
    if design.get("rule_pack_sha256") != _digest(pack):
        checks.append(
            _check(
                pack,
                "qed2.scope",
                "fail",
                "Design is not bound to these exact rule-pack bytes; configure again.",
            )
        )
    scope = pack["scope"]
    voltage, service = (
        _number(inputs.get("voltage_v")),
        _number(inputs.get("service_rating_a")),
    )
    mode = _value(inputs.get("current_type"))
    scope_ok = (
        voltage is not None
        and voltage <= scope["maximum_voltage_v"]
        and service is not None
        and service <= scope["maximum_service_a"]
        and mode == scope["current_type"]
    )
    scope_fail = (
        voltage is not None
        and voltage > scope["maximum_voltage_v"]
        or service is not None
        and service > scope["maximum_service_a"]
        or mode is not None
        and mode != scope["current_type"]
    )
    checks.append(
        _check(
            pack,
            "qed2.scope",
            "fail" if scope_fail else "pass" if scope_ok else "unknown",
            "Narrow catalog AC voltage/service range; this is not UL certification.",
        )
    )
    if not scope_ok:
        questions.append(
            _question(
                "product_scope",
                "Confirm service rating, voltage and AC system within the cited QED-2 subset.",
            )
        )
    checks.append(_input_check(
        "system_binding",
        "pass" if design.get("system") == _system(inputs) else "fail",
        "System values and their source bindings must match the explicit typed requirements; no electrical defaults are inferred.",
        inputs,
    ))
    rows = {r["id"]: r for r in pack["devices"]}
    types = {r["id"]: r for r in pack["section_types"]}
    devices = {d["id"]: d for d in design.get("devices", [])}
    if len(devices) != len(design.get("devices", [])):
        raise ValueError("Duplicate configuration device IDs")
    section_ids = [s["id"] for s in design.get("sections", [])]
    position_ids = [
        p["id"] for s in design.get("sections", []) for p in s.get("positions", [])
    ]
    if len(set(section_ids)) != len(section_ids) or len(set(position_ids)) != len(
        position_ids
    ):
        raise ValueError("Section and position IDs must be unique")
    expected_ids = {"main", *(_value(r["id"]) for r in inputs["feeders"])}
    if _value(inputs["main"].get("id")):
        expected_ids.remove("main")
        expected_ids.add(_value(inputs["main"]["id"]))
    if set(devices) != expected_ids:
        checks.append(
            _check(
                pack,
                "frame.range",
                "fail",
                "Every requested physical device must be retained exactly once.",
            )
        )
    if (
        design.get("manufacturer") != pack["manufacturer"]
        or design.get("family") != pack["family"]
    ):
        checks.append(
            _check(
                pack,
                "qed2.scope",
                "fail",
                "Edited design is outside the selected manufacturer/family.",
            )
        )
    ul = pack["ul891_scope"]
    fault = _number(inputs.get("available_fault_ka"))
    unsupported_ul = (
        (voltage is not None and voltage > ul["maximum_nominal_voltage_v"])
        or (fault is not None and fault > ul["maximum_available_fault_ka"])
        or _number(inputs.get("short_time_withstand_ka")) is not None
    )
    checks.append(
        _check(
            pack,
            "ul891.scope",
            "fail" if unsupported_ul else "pass",
            "Public UL891 scope boundary only; not product certification or construction compliance.",
            ul["source_refs"],
        )
    )
    seen = set()
    for section in design.get("sections", []):
        stype = types.get(section.get("section_type_id"))
        if section.get("kind") == "main":
            mains = [
                devices.get(p.get("device_id")) for p in section.get("positions", [])
            ]
            row = (
                rows.get(mains[0].get("catalog_variant_id"))
                if len(mains) == 1 and mains[0] and mains[0].get("role") == "main"
                else None
            )
            if row:
                dimension_minima = {
                    "width_in": row["section_width_in"],
                    "depth_in": row["minimum_depth_in"],
                    "height_in": scope["height_in"],
                }
                missing_dimensions = any(
                    section.get(k) is None for k in dimension_minima
                )
                good = (
                    mains[0]["id"] not in seen
                    and mains[0].get("section_id") == section["id"]
                    and all(
                        section.get(k) is None
                        or (_number(section[k]) is not None and section[k] >= v)
                        for k, v in dimension_minima.items()
                    )
                )
                if missing_dimensions:
                    questions.append(
                        _question(
                            "main.dimensions",
                            "Main enclosure/feed conditions are not established; conditional dimensions remain unknown.",
                        )
                    )
                checks.append(
                    _check(
                        pack,
                        "main.dimensions",
                        "fail"
                        if not good
                        else "unknown"
                        if missing_dimensions
                        else "pass",
                        "Conditional NEMA1 individual-main catalog envelope recomputed.",
                    )
                )
                seen.add(mains[0]["id"])
            else:
                questions.append(
                    _question(
                        "main.dimensions",
                        "No supported exact main mounting variant/envelope is established.",
                    )
                )
            continue
        valid = stype is not None
        if stype:
            valid &= (
                section.get("width_in") == stype["width_in"]
                and section.get("bus_a") == stype["interior_bus_a"]
            )
            for field, minimum_dimension in (
                ("depth_in", stype["minimum_depth_in"]),
                ("height_in", stype["height_in"]),
            ):
                if section.get(field) is None:
                    questions.append(
                        _question(
                            section["id"] + "." + field,
                            "Enclosure-dependent physical dimension is not established.",
                        )
                    )
                else:
                    valid &= (
                        _number(section[field]) is not None
                        and section[field] >= minimum_dimension
                    )
        intervals = {side: [] for side in (stype["sides"] if stype else {})}
        contents = []
        for position in section.get("positions", []):
            did = position.get("device_id")
            d = devices.get(did)
            row = rows.get(d.get("catalog_variant_id")) if d else None
            bounds = stype["sides"].get(position.get("side")) if stype else None
            valid &= (
                did not in seen
                and d is not None
                and row is not None
                and bounds is not None
            )
            seen.add(did)
            if row and bounds and d:
                start, height = (
                    position.get("start_in"),
                    position.get("mounting_space_in"),
                )
                good = (
                    isinstance(start, (int, float))
                    and not isinstance(start, bool)
                    and math.isfinite(start)
                    and start >= 0
                    and height == row["mounting_space_in"]
                    and row["frame_family"] in bounds["families"]
                    and row["frame_a"] <= bounds["maximum_frame_a"]
                    and start + height <= bounds["mounting_space_in"]
                    and d.get("section_id") == section["id"]
                    and d.get("position_id") == position.get("id")
                )
                valid &= good
                if good:
                    intervals[position["side"]].append((start, start + height))
                    contents.append(d)
        for spans in intervals.values():
            ordered = sorted(spans)
            valid &= all(a[1] <= b[0] for a, b in pairwise(ordered))
        minimum = _interior_minimum(pack, contents)
        if minimum is None:
            questions.append(
                _question(
                    section["id"] + ".bus",
                    "Unknown loading/current prevents the catalog interior bus test.",
                )
            )
        else:
            valid &= section.get("bus_a", 0) >= minimum
        checks.append(
            _check(
                pack,
                "packing.side_and_space",
                "pass" if valid else "fail",
                "Recomputed side, nonoverlap, mounting-height and interior-bus constraints.",
                device_ids=[d["id"] for d in contents],
            )
        )
        if any(
            p.get("physical_datum_offset_in") is None
            for p in section.get("positions", [])
        ):
            questions.append(
                _question(
                    section["id"] + ".mounting_origin",
                    "Packing starts at the interior datum. Its physical elevation offset and manufacturer mounting details are not established.",
                )
            )
    reqs = {
        (_value(r.get("id")) or "main"): r for r in [inputs["main"], *inputs["feeders"]]
    }
    for d in devices.values():
        req = reqs.get(d["id"], {})
        if _value(req.get("position_state")) is not None and d.get(
            "position_state"
        ) != _value(req.get("position_state")):
            checks.append(
                _input_check(
                    "position_state",
                    "fail",
                    "A printed prepared/populated/unknown state cannot be changed without revising its source requirement.",
                    req,
                    device_id=d["id"],
                )
            )
        if _reserved_position(d.get("position_state")):
            checks.append(
                _input_check(
                    "prepared_position_supply",
                    "fail" if d.get("supplied") is True else "pass",
                    "One reserved physical position is not evidence of a supplied installed device.",
                    req,
                    device_id=d["id"],
                )
            )
        for field in ("display_tag", "section_label", "source_local_identity"):
            if field in req and d.get(field) != _value(req[field]):
                checks.append(
                    _input_check(
                        "source_local_identity",
                        "fail",
                        "Source-local identity metadata must remain bound to the original position, not an enterprise-tag inference.",
                        req,
                        device_id=d["id"],
                        field=field,
                    )
                )
        reuse = d.get("role") == "main" and _value(req.get("reuse_existing")) is True
        row = rows.get(d.get("catalog_variant_id"))
        current, trip = _number(d.get("rated_current_a")), _number(d.get("trip_a"))
        good = (
            row is not None
            and current is not None
            and row["minimum_a"] <= current <= row["maximum_a"]
            and d.get("frame_a") == row["frame_a"]
            and d.get("frame") == row["frame"]
            and d.get("pole_count") == row["pole_count"]
            and d.get("role") in row["roles"]
            and (trip is None or trip <= current <= row["frame_a"])
            and (d.get("trip_type") is None or d["trip_type"] in row["trip_types"])
        )
        checks.append(
            _check(
                pack,
                "frame.range",
                "pass" if good else "unknown" if row is None else "fail",
                "Device must remain within the selected cited variant and trip range.",
                device_id=d["id"],
            )
        )
        if row:
            trip_status = (
                "unknown"
                if d.get("trip_type") is None
                else "pass"
                if d["trip_type"] in row["trip_types"]
                else "fail"
            )
            checks.append(
                _check(
                    pack,
                    "frame.trip_type",
                    trip_status,
                    "Protection functions checked against the cited trip-system offering; exact trip-unit ordering remains unresolved.",
                    row.get("trip_type_source_refs", []),
                    device_id=d["id"],
                )
            )
            actual_ka = row["catalog_sccr_ka"].get(_voltage_key(voltage))
            minimum = _required_short_circuit(inputs, reqs.get(d["id"], {}))
            mismatch = (
                d.get("catalog_sccr_ka") != actual_ka
                or (minimum is not None and (actual_ka is None or actual_ka < minimum))
                or (
                    row.get("requires_wye_at_480")
                    and voltage == 480
                    and _value(inputs.get("grounding")) != "solidly_grounded_wye"
                )
            )
            checks.append(
                _check(
                    pack,
                    "frame.range",
                    "fail"
                    if mismatch
                    else "pass"
                    if actual_ka is not None and minimum is not None
                    else "unknown",
                    "Recomputed catalog short-circuit column and applicability; this is not an assembly certification.",
                    device_id=d["id"],
                )
            )
        if d.get("role") == "main":
            required_main = _number(req.get("required_ampacity_a"))
            if (
                _value(req.get("required_ampacity_a")) is not None
                and required_main is None
            ):
                questions.append(
                    _question(
                        "main.required_ampacity_a",
                        "Confirm a positive finite ampere value for the explicit main minimum; no numeric value was inferred.",
                        req.get("required_ampacity_a"),
                    )
                )
            declared_main = _number(req.get("rated_current_a"))
            minimum_main = max(
                (v for v in (required_main, declared_main) if v is not None),
                default=None,
            )
            if minimum_main is not None:
                checks.append(
                    _input_check(
                        "main_required_ampacity",
                        "unknown"
                        if current is None
                        else "pass"
                        if current >= minimum_main
                        else "fail",
                        "Explicit main ampacity checked independently of board/bus ampacity; bus rating is not a minimum breaker current.",
                        req,
                        device_id=d["id"],
                        required_main_ampacity_a=minimum_main,
                    )
                )
        if reuse:
            expected_product = {
                "manufacturer": _value(req.get("manufacturer")),
                "family": _value(req.get("family")),
                "part_number": _value(req.get("part_number")),
                "frame": _value(req.get("frame")) or _value(req.get("device_frame")),
                "frame_a": _number(req.get("frame_a")),
                "interrupting_ka": _number(req.get("interrupting_ka")),
            }
            preserved = (
                d.get("reuse_existing") is True
                and d.get("product_origin") == "existing_source_equipment"
                and d.get("catalog_variant_id") is None
                and d.get("catalog_sccr_ka") is None
                and d.get("supplied") in (False, None)
                and all(d.get(key) == value for key, value in expected_product.items())
            )
            checks.append(
                _input_check(
                    "main_reuse_existing",
                    "pass" if preserved else "fail",
                    "Existing-main product fields must remain source-described; no new catalog variant or supplied-new device may be introduced.",
                    req,
                    device_id=d["id"],
                )
            )
        if d.get("role") == "feeder" and d["id"] not in seen:
            questions.append(
                _question(
                    d["id"] + ".position",
                    "No supported source-based mounting position was found.",
                )
            )
        if row is not None and d["id"] not in seen:
            checks.append(
                _check(
                    pack,
                    "packing.side_and_space",
                    "fail",
                    "Known catalog device requires exactly one mounting assignment.",
                    device_id=d["id"],
                )
            )
        for field, aliases in (
            ("trip_a", ("trip_a",)),
            ("pole_count", ("poles", "pole_count")),
            ("frame_a", ("frame_a",)),
        ):
            expected = next((_value(req[a]) for a in aliases if a in req), None)
            if expected is not None and d.get(field) != expected:
                checks.append(
                    _check(
                        pack,
                        "frame.range",
                        "fail",
                        "Edited device differs from an explicit requirement.",
                        device_id=d["id"],
                        field=field,
                    )
                )
        for field in ("kind", "nameplate", "part_number"):
            if d.get(field) != _value(req.get(field)):
                checks.append(_input_check(
                    "device_source_text", "fail",
                    "Proposed device type, nameplate and part text must retain the explicit source requirement; unknown values are not filled from a tag or catalog assumption.",
                    req, device_id=d["id"], field=field,
                ))
        if reuse:
            questions.append(
                _question(
                    d["id"] + ".existing_product",
                    "Verify retained existing-main ratings, condition, mounting and compatibility with the proposed lineup; no replacement product is configured.",
                    req,
                )
            )
        elif (
            row is None
            or row.get("exact_catalog_number") is None
            or d.get("part_number") != row["exact_catalog_number"]
            or d.get("interrupting_ka") is None
        ):
            questions.append(
                _question(
                    d["id"] + ".exact_product",
                    "Exact ordering code, standard rating, individual interrupting rating and assembly applicability remain unverified.",
                )
            )
    for key in ("metering", "spd"):
        if _value(inputs.get(key)) is not False:
            questions.append(
                _question(
                    key,
                    "Confirm this option and its component/section geometry; this subset does not invent it.",
                    inputs.get(key),
                )
            )
    for key in ("frequency_hz", "phases", "wires", "minimum_interrupting_ka"):
        if _number(inputs.get(key)) is None:
            questions.append(
                _question(
                    key,
                    "Confirm this system requirement and its source; no operating context is assumed.",
                    inputs.get(key),
                )
            )
    questions.append(
        _question(
            "catalog_frequency",
            "Confirm exact product ratings at the declared operating frequency; this subset does not provide frequency-specific ordering validation.",
        )
    )
    questions.append(
        _question(
            "code_adoption",
            "Confirm AHJ adoption, local amendments and licensed standards review; the cited trigger subset is not a compliance certificate.",
        )
    )
    questions.append(
        _question(
            "assembly_sccr",
            "Confirm complete assembly rating, tested/listed combinations, bus bracing and exact products; the device-table SCCR cannot certify a lineup.",
        )
    )
    if _value(inputs.get("main", {}).get("kind")) not in {"circuit_breaker", "breaker"}:
        questions.append(
            _question(
                "main.kind",
                "Only an explicitly requested circuit-breaker main is configured by this subset.",
            )
        )
    for key in ("enclosure", "line_load_connection"):
        if _value(inputs.get("constraints", {}).get(key)) not in (
            {"NEMA1"} if key == "enclosure" else {"opposite_side"}
        ):
            questions.append(
                _question(
                    key,
                    "Confirm enclosure and line/load arrangement; catalog depths are conditional, not fabrication dimensions.",
                )
            )
    limits = inputs["constraints"]
    widths = [s.get("width_in") for s in design.get("sections", [])]
    heights = [s.get("height_in") for s in design.get("sections", [])]
    depths = [s.get("depth_in") for s in design.get("sections", [])]
    for key, actual in (
        (
            "max_width_in",
            sum(widths) if widths and all(v is not None for v in widths) else None,
        ),
        (
            "max_height_in",
            max(heights) if heights and all(v is not None for v in heights) else None,
        ),
        (
            "max_depth_in",
            max(depths) if depths and all(v is not None for v in depths) else None,
        ),
    ):
        bound = _number(limits.get(key))
        if bound is not None:
            status = (
                "unknown" if actual is None else "pass" if actual <= bound else "fail"
            )
            checks.append(
                _check(
                    pack,
                    "packing.side_and_space",
                    status,
                    "Explicit physical envelope checked against known catalog dimensions.",
                    field=key,
                )
            )
    topology = design.get("topology", {})
    main_sections = [s for s in design.get("sections", []) if s.get("kind") == "main"]
    bus_values = [s.get("bus_a") for s in main_sections]
    if topology.get("connections"):
        bus_values.append(topology.get("bus_a"))
    low_bus, high_bus = scope["through_bus_range_a"]
    bus_ratings = scope.get("through_bus_standard_ratings_a", [])
    bus_fail = (
        not main_sections
        or any(value != bus_values[0] for value in bus_values)
        or any(
            value is not None and (
                _number(value) is None
                or not low_bus <= value <= high_bus
                or service is not None and value < service
                or bus_ratings and value not in bus_ratings
            )
            for value in bus_values
        )
    )
    bus_known = bool(bus_values) and all(
        value is not None and value in bus_ratings for value in bus_values
    ) and service is not None
    bus_status = "fail" if bus_fail else "pass" if bus_known else "unknown"
    checks.append(_check(
        pack, "qed2.through_bus", bus_status,
        "Through-bus rating is checked separately from service demand, breaker capacity and distribution interior ratings; a cited range does not establish discrete catalog choices.",
        refs=scope["source_refs"],
    ))
    if bus_status == "unknown":
        question = _question(
            "through_bus_a",
            "Confirm an explicit manufacturer-supported through-bus rating within the cited range and adequate for the service; no rating is selected from range endpoints or interior/breaker ratings.",
            inputs.get("service_rating_a"),
        )
        question["source_refs"] = _refs(pack, scope["source_refs"])
        questions.append(question)
    if topology.get("connections"):
        bus = topology.get("bus_id")
        mains = [d["id"] for d in devices.values() if d.get("role") == "main"]
        expected = (
            (
                [{"from_device_id": mains[0], "from_port": "load", "to_bus_id": bus}]
                + [
                    {"from_bus_id": bus, "to_device_id": d["id"], "to_port": "line"}
                    for d in devices.values()
                    if d.get("role") == "feeder"
                ]
            )
            if len(mains) == 1 and bus
            else []
        )
        good = (
            bool(expected)
            and sorted(topology["connections"], key=_digest)
            == sorted(expected, key=_digest)
            and all(s.get("bus_a") == topology.get("bus_a") for s in main_sections)
            and topology.get("state") == "proposed"
            and not _value(inputs.get("multiple_sources"))
            and not _value(inputs.get("tie"))
        )
        checks.append(
            _check(
                pack,
                "topology.single_main_bus",
                "pass" if good else "fail",
                "Recomputed proposed single-main/common-bus logical endpoints; no physical wiring or observed topology is inferred.",
            )
        )
    code_checks, code_questions = _code_checks(design, inputs, pack)
    checks += code_checks
    questions += code_questions
    if inputs.get("conflicts"):
        questions.append(
            _question(
                "requirements.conflicts",
                "Resolve conflicting source requirements before selecting a design.",
            )
        )
    if inputs.get("confirmed") is not True:
        questions.append(
            _question(
                "requirements.confirmation",
                "Engineer confirmation/version of the requirements is not established; confirmation does not approve this design.",
            )
        )
    if not inputs.get("evidence_claims"):
        questions.append(
            _question(
                "requirements.provenance",
                "Bind requirements to source claims; a typed software fixture is not real project evidence.",
            )
        )
    questions = list({q["id"]: q for q in questions}.values())
    return {
        "schema_version": "rivet.configuration-validation.v1",
        "configurator_version": VERSION,
        "rule_pack_id": pack["id"],
        "rule_pack_version": pack["version"],
        "rule_results": checks,
        "unresolved_questions": questions,
        "has_known_rule_violations": any(c["status"] == "fail" for c in checks),
        "fully_configured": not questions
        and all(c["status"] in {"pass", "not_applicable"} for c in checks),
        "engineering_approved": False,
        "production_released": False,
    }


def configure(
    requirements,
    *,
    rule_pack=None,
    priors=None,
    max_options=3,
    model_id=None,
    model_version=None,
):
    """Return up to three proposed layouts, excluding known rule violations."""
    if (
        not isinstance(max_options, int)
        or isinstance(max_options, bool)
        or not 1 <= max_options <= 3
    ):
        raise ValueError("max_options must be1–3")
    inputs = _normalized_inputs(requirements)
    pack = deepcopy(rule_pack) if rule_pack is not None else load_rule_pack()
    validate_rule_pack(pack)
    digest = _digest(inputs)
    mid = model_id or stable_id(
        "proposed-switchboard",
        VERSION,
        digest,
        pack["id"],
        pack["version"],
        _digest(pack),
    )
    version = model_version or "1"
    prototypes, selected, questions = [], {}, []
    for req, role in [
        (inputs["main"], "main"),
        *((r, "feeder") for r in inputs["feeders"]),
    ]:
        device, row, qs = _device(req, role, inputs, pack)
        prototypes.append(device)
        selected[device["id"]] = row
        questions += qs
    configurations, rejected = [], []
    for stype in pack["section_types"]:
        devices = deepcopy(prototypes)
        feeders = sorted(
            [d for d in devices if d["role"] == "feeder"],
            key=lambda d: (
                -(selected[d["id"]] or {}).get("mounting_space_in", 0),
                d["id"],
            ),
        )
        sections = _pack_sections(feeders, selected, stype, pack, mid, stype["id"])
        if sections is None:
            continue
        main = next(d for d in devices if d["role"] == "main")
        row = selected[main["id"]]
        reuse = _value(inputs["main"].get("reuse_existing")) is True
        sid = stable_id(mid, stype["id"], "main-section")
        pid = stable_id(sid, main["id"])
        main.update(section_id=sid, position_id=pid)
        main_section = {
            "id": sid,
            "kind": "main",
            "section_type_id": "existing-main-envelope-unknown"
            if reuse
            else "individual-main",
            "width_in": row.get("section_width_in") if row else None,
            "depth_in": row.get("minimum_depth_in") if row else None,
            "height_in": None if reuse else pack["scope"]["height_in"],
            "bus_a": _through_bus_rating(inputs, pack),
            "bus_source_refs": _refs(pack, pack["scope"]["source_refs"]),
            "positions": [
                {
                    "id": pid,
                    "device_id": main["id"],
                    "side": "individual",
                    "start_in": None,
                    "mounting_space_in": None,
                    "status": "manufacturer_position_unknown",
                }
            ],
            "source_refs": _refs(pack, row["source_refs"]) if row else [],
        }
        sections.insert(0, main_section)
        enclosure_known = _value(inputs["constraints"].get("enclosure")) == "NEMA1"
        feed_known = (
            _value(inputs["constraints"].get("line_load_connection")) == "opposite_side"
        )
        for section in sections:
            section["catalog_minimum_depth_in"] = section["depth_in"]
            section["catalog_frame_height_in"] = section["height_in"]
            if not enclosure_known or (section["kind"] == "main" and not feed_known):
                section["depth_in"] = None
            if not enclosure_known:
                section["height_in"] = None
        design = {
            "id": stable_id(VERSION, mid, version, digest, _digest(pack), stype["id"]),
            "schema_version": "rivet.proposed-switchboard-design.v1",
            "configurator_version": VERSION,
            "rule_pack_sha256": _digest(pack),
            "model_id": mid,
            "model_version": version,
            "state": "proposed",
            "manufacturer": pack["manufacturer"],
            "family": pack["family"],
            "requirements_digest": digest,
            "rule_pack_id": pack["id"],
            "rule_pack_version": pack["version"],
            "sections": sections,
            "devices": devices,
            "system": _system(inputs),
            "options": {
                k: _value(inputs.get(k))
                for k in ("metering", "spd", "ground_fault", "arc_energy_reduction")
            },
            "engineering_approved": False,
            "production_released": False,
        }
        # Generated design intent, not reconstructed customer wires or terminals.
        service_a = _number(inputs.get("service_rating_a"))
        topology_refs = next(
            r["source_refs"]
            for r in pack["rules"]
            if r["id"] == "topology.single_main_bus"
        )
        bus_id = stable_id(mid, version, "common-through-bus")
        connected = (
            (
                row is not None
                or reuse
                and _value(inputs["main"].get("kind")) in {"breaker", "circuit_breaker"}
            )
            and service_a is not None
            and not _value(inputs.get("multiple_sources"))
            and not _value(inputs.get("tie"))
        )
        design["topology"] = {
            "state": "proposed",
            "bus_id": bus_id if connected else None,
            "bus_a": _through_bus_rating(inputs, pack) if connected else None,
            "connections": (
                [
                    {
                        "from_device_id": main["id"],
                        "from_port": "load",
                        "to_bus_id": bus_id,
                    }
                ]
                + [
                    {"from_bus_id": bus_id, "to_device_id": d["id"], "to_port": "line"}
                    for d in devices
                    if d["role"] == "feeder"
                ]
            )
            if connected
            else [],
            "source_refs": _refs(pack, topology_refs),
            "source_claim_ids": list(
                dict.fromkeys(cid for d in devices for cid in d["source_claim_ids"])
            ),
            "basis": "Proposed single-main/common-bus design intent; not observed customer connectivity or physical wiring.",
            "engineering_approved": False,
        }
        if not connected:
            questions.append(
                _question(
                    "topology",
                    "Confirm main/source arrangement; no common-bus topology generated for unsupported or multiple-source input.",
                )
            )
        result = validate_configuration(design, inputs, pack)
        design.update(result)
        design["schema_version"] = "rivet.proposed-switchboard-design.v1"
        design["unresolved_questions"] = list(
            {q["id"]: q for q in [*questions, *result["unresolved_questions"]]}.values()
        )
        design["fully_configured"] = (
            not design["unresolved_questions"] and result["fully_configured"]
        )
        design["validity"] = (
            "known_constraints_pass_with_unresolved_requirements"
            if not result["has_known_rule_violations"]
            else "rejected"
        )
        if result["has_known_rule_violations"]:
            rejected.append(
                {"id": design["id"], "rule_results": result["rule_results"]}
            )
        else:
            width = sum(s["width_in"] or 0 for s in sections)
            prior_notes = []
            bonus = 0.0
            for prior in priors or []:
                if (
                    prior.get("approved_job") is True
                    and prior.get("source_ref")
                    and prior.get("preferred_section_type") == stype["id"]
                ):
                    bonus += min(1.0, max(0.0, float(prior.get("weight", 0))))
                    prior_notes.append(
                        {
                            "prior_id": prior.get("id"),
                            "source_ref": prior["source_ref"],
                            "effect": "rank_only",
                        }
                    )
            design["ranking"] = {
                "section_count": len(sections),
                "known_width_in": width,
                "prior_bonus": bonus,
                "priors": prior_notes,
                "meaning": "Ranks known-feasible proposals only; not a global optimum or approval.",
            }
            configurations.append(design)
    configurations.sort(
        key=lambda d: (
            len(d["unresolved_questions"]),
            d["ranking"]["section_count"],
            -d["ranking"]["prior_bonus"],
            d["ranking"]["known_width_in"],
            d["id"],
        )
    )
    questions += [
        q for c in configurations[:max_options] for q in c["unresolved_questions"]
    ]
    if not configurations:
        questions.append(
            _question(
                "configuration",
                "No candidate satisfies all known constraints in this bounded cited subset. Review rejected rule results.",
            )
        )
    return {
        "schema_version": VERSION,
        "configurator_version": VERSION,
        "model_id": mid,
        "model_version": version,
        "requirements_digest": digest,
        "rule_pack_id": pack["id"],
        "rule_pack_version": pack["version"],
        "rule_pack_sha256": _digest(pack),
        "state": "proposed",
        "configurations": configurations[:max_options],
        "rejected_configurations": rejected,
        "unresolved_questions": list({q["id"]: q for q in questions}.values()),
        "engineering_approved": False,
        "production_released": False,
    }
