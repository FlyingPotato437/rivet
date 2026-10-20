"""Conservative customer-input extraction; manufacturer outputs are never inputs.

The extraction contract is independent of the reader. Native text and reviewed
page-image observations use the same hash-bound claims. No source precedence or
engineering approval is inferred by this module.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from decimal import Decimal

from backend.equipment.evidence.schema import make_claim, stable_id

CUSTOMER_ROLES = frozenset(
    {
        "specification",
        "customer_one_line",
        "addendum",
        "quote_exception",
        "purchase_order",
    }
)
SOURCE_ROLES = CUSTOMER_ROLES | {"manufacturer_reference", "component_cut_sheet"}
SCHEMA = "rivet.switchboard-requirements.v1"
UNITS = {
    "service_rating_a": "A",
    "voltage_v": "V",
    "voltage_to_ground_v": "V",
    "frequency_hz": "Hz",
    "minimum_interrupting_ka": "kA",
    "spd_rating_ka": "kA",
    "frame_a": "A",
    "trip_a": "A",
    "rated_current_a": "A",
    "interrupting_ka": "kA",
    "max_height_in": "in",
    "max_width_in": "in",
    "max_depth_in": "in",
    "main.frame_a": "A",
    "main.trip_a": "A",
    "main.rated_current_a": "A",
    "main.interrupting_ka": "kA",
    "main.required_ampacity_a": "A",
}
ALIASES = {
    "service rating": "service_rating_a",
    "main bus": "service_rating_a",
    "bus rating": "service_rating_a",
    "main bus ampacity": "service_rating_a",
    "voltage": "voltage_v",
    "rated voltage": "voltage_v",
    "voltage to ground": "voltage_to_ground_v",
    "frequency": "frequency_hz",
    "phases": "phases",
    "wires": "wires",
    "main type": "main.kind",
    "main breaker type": "main.kind",
    "main frame": "main.frame_a",
    "main trip": "main.trip_a",
    "main rating": "main.rated_current_a",
    "minimum main ampacity": "main.required_ampacity_a",
    "interrupting rating": "minimum_interrupting_ka",
    "short circuit rating": "minimum_interrupting_ka",
    "metering": "metering",
    "spd": "spd",
    "surge protection": "spd",
    "ground fault": "ground_fault",
    "ground fault protection": "ground_fault",
    "arc energy reduction": "arc_energy_reduction",
    "seismic": "seismic",
    "service side": "service_side",
    "max height": "max_height_in",
    "max width": "max_width_in",
    "max depth": "max_depth_in",
    "trip type": "trip_type",
    "frame": "frame_a",
    "trip": "trip_a",
    "rating": "rated_current_a",
    "poles": "poles",
    "option": "option",
    "current type": "current_type",
    "main bus continuous": "service_rating_a",
}


def canonical_requirement_value(key, value, unit=None):
    target = UNITS.get(key)
    if target and isinstance(value, str):
        # '208/120' is a printed system expression, not a scalar208V claim.
        # Preserve the raw claim but withhold scalar projection until read or
        # confirmed in the appropriate line/ground context.
        if not re.fullmatch(r"\s*\d+(?:\.\d+)?\s*", value):
            return None, target
        value = float(value)
    if (
        not target
        or value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        return value, unit
    actual = str(unit or target).casefold()
    if target == "A" and (
        (actual == "af" and key.endswith("frame_a"))
        or (actual == "at" and key.endswith("trip_a"))
        or actual in {"amps", "ampere", "amperes"}
    ):
        actual = "a"
    if (
        target == "kA"
        and key
        in {"minimum_interrupting_ka", "interrupting_ka", "main.interrupting_ka"}
        and actual in {"kaic", "kair"}
    ):
        actual = "ka"
    conversions = {
        ("V", "kv"): 1000,
        ("A", "ka"): 1000,
        ("kA", "a"): 0.001,
        ("in", "mm"): 1 / 25.4,
        ("in", "inches"): 1,
        ("in", '"'): 1,
    }
    if actual == target.casefold():
        return value, target
    factor = conversions.get((target, actual))
    return (value * factor, target) if factor is not None else (None, target)


def reviewed_value(key, value):
    """Validate an engineer edit against its typed field, including unknowns."""
    if key in UNITS or key in {
        "phases",
        "wires",
        "poles",
        "main.poles",
        "neutral_bus_percent",
    }:
        if isinstance(value, bool):
            raise ValueError("Enter a numeric value for this requirement")
        try:
            number = float(str(value).replace(",", "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("Enter a numeric value in the displayed unit") from exc
        if not math.isfinite(number) or number <= 0:
            raise ValueError("Requirement values must be positive finite numbers")
        if (
            key in {"phases", "wires", "poles", "main.poles"}
            and not number.is_integer()
        ):
            raise ValueError("Phase, wire and pole counts must be whole numbers")
        return int(number) if number.is_integer() else number
    if key in {
        "spd",
        "ground_fault",
        "arc_energy_reduction",
        "seismic",
        "service_disconnect",
        "utility_metering",
        "customer_metering",
        "main.reuse_existing",
    }:
        if isinstance(value, bool):
            return value
        literal = str(value).casefold().strip()
        if literal in {"true", "yes", "required", "included", "provided"}:
            return True
        if literal in {"false", "no", "not required", "excluded", "not included"}:
            return False
        raise ValueError("Confirm Required or Not required for this option")
    if not isinstance(value, (str, bool)) or not str(value).strip():
        raise ValueError("Enter an explicit requirement value")
    return value.strip() if isinstance(value, str) else value


def _protective_device_established(obj):
    """A bare current/pole label does not establish a protective-device role."""
    return any(
        prop["feature"] in {"frame_a", "trip_a"}
        or (
            prop["feature"] == "device_type"
            and re.search(r"breaker", str(prop.get("value")), re.IGNORECASE)
        )
        for prop in obj.get("properties", [])
    )


def _proposed_main_roles(reading):
    """A unique source→breaker→bus depiction proposes a role, never a release.

    Semantic object-key names do not count. Both visible paths need independent
    ink and endpoint support, and the upstream object needs a literal source
    label. Ambiguous/multi-main drawings keep their source-local identities.
    """
    objects = {o["key"]: o for o in reading.get("objects", [])}
    neighbors = {key: set() for key in objects}
    for wire in reading.get("conductors", []):
        if not (
            wire.get("connected_visible")
            and wire.get("kind") in {"conductor", "bus"}
            and wire.get("pixel_evidence", {}).get("supported")
            and wire.get("endpoint_evidence", {}).get("supported")
        ):
            continue
        a, b = wire["from_object"], wire["to_object"]
        if a in neighbors and b in neighbors:
            neighbors[a].add(b)
            neighbors[b].add(a)
    candidates = []
    for key, obj in objects.items():
        if obj["kind"] != "device_symbol" or obj.get("circuit_label"):
            continue
        if not any(
            p["feature"] in {"frame_a", "trip_a"} for p in obj.get("properties", [])
        ):
            continue
        adjacent = [objects[n] for n in neighbors[key]]
        source = any(
            re.search(
                r"\b(?:utility|service\s+(?:source|transformer)|incoming\s+(?:source|supply))\b",
                str(n.get("raw_label") or ""),
                re.IGNORECASE,
            )
            for n in adjacent
            if n["kind"] != "busbar"
        )
        if source and any(n["kind"] == "busbar" for n in adjacent):
            candidates.append(key)
    return set(candidates) if len(candidates) == 1 else set()


def _main_roles(reading):
    """One common main requirement cannot represent multiple source mains."""
    explicit = {
        obj["key"]
        for obj in reading.get("objects", [])
        if obj["kind"] in {"device_symbol", "prepared_device"}
        and _protective_device_established(obj)
        and re.search(
            r"\bmain\b",
            " ".join(str(obj.get(k) or "") for k in ("raw_tag", "raw_label")),
            re.IGNORECASE,
        )
    }
    if explicit:
        return explicit if len(explicit) == 1 else set(), len(explicit) > 1
    return _proposed_main_roles(reading), False


def _drawing_main_candidates(reports):
    return {
        (report["sha256"], page["page"], key)
        for report in reports
        for page in report.get("pages", [])
        for key in _main_roles(page["reading"])[0]
    }


def drawing_note_documents(requirements, reports):
    """Keep image-transcribed notes as derived evidence, never native text."""
    result = deepcopy(requirements)
    documents = []
    main_candidates = _drawing_main_candidates(reports)
    for report in reports:
        spans = []
        for page in report.get("pages", []):
            reading = page["reading"]
            mains, ambiguous_mains = _main_roles(reading)
            local_mains = set(mains)
            ambiguous_mains = ambiguous_mains or len(main_candidates) > 1
            if ambiguous_mains:
                mains = set()
            objects = {o["key"]: o for o in reading.get("objects", [])}
            labels = list(reading.get("labels", []))
            for obj in objects.values():
                for prop in obj.get("properties", []):
                    if prop["feature"] in {"description", "device_type"} and prop.get(
                        "raw_text"
                    ):
                        labels.append(
                            {
                                "text": prop["raw_text"],
                                "bbox": prop["bbox"],
                                "confidence": prop["confidence"],
                                "owner_key": obj["key"],
                            }
                        )
            for index, note in enumerate(labels):
                owner = objects.get(note.get("owner_key"), {})
                protection = any(
                    p["feature"] in {"frame_a", "trip_a"}
                    for p in owner.get("properties", [])
                )
                # Downstream load names/ratings are not automatically feeder
                # purchase requirements. Original report observations remain.
                if (
                    owner
                    and owner.get("kind") == "device_symbol"
                    and not protection
                    and not re.search(
                        r"\b(?:SPD|TVSS|surge)\b",
                        str(owner.get("raw_label") or "") + " " + note["text"],
                        re.IGNORECASE,
                    )
                ):
                    continue
                ref = {
                    "source_id": report["source_id"],
                    "sha256": report["sha256"],
                    "page": page["page"],
                    "bbox": note["bbox"],
                    "coordinate_system": "normalized_top_left",
                    "text": note["text"],
                }
                claim = make_claim(
                    "drawing-note:" + stable_id(report["sha256"], page["page"], index),
                    "text_observation",
                    note["text"],
                    source=ref,
                    raw_confidence=note.get("confidence"),
                    state="proposed",
                    extractor={
                        "name": "independent_drawing_page_image",
                        "version": report["version"],
                        "run_id": report["prompt_sha256"],
                    },
                )
                if not any(c["id"] == claim["id"] for c in result["evidence_claims"]):
                    result["evidence_claims"].append(claim)
                context = {
                    "transcription_only": True,
                    "owner_label": owner.get("raw_label"),
                    "owner_circuit_label": owner.get("circuit_label"),
                    "owner_tag": owner.get("raw_tag"),
                    "proposed_owner_role": "main"
                    if owner.get("key") in mains
                    else "protective_device"
                    if protection
                    else "bus"
                    if owner.get("kind") == "busbar"
                    else "unverified",
                    "role_engineering_verified": False,
                    "owner_role_ambiguous": ambiguous_mains
                    and (
                        owner.get("key") in local_mains
                        or bool(
                            re.search(
                                r"\bmain\b",
                                str(owner.get("raw_label") or ""),
                                re.IGNORECASE,
                            )
                        )
                    ),
                }
                spans.append(
                    {
                        "id": "vision-note:" + claim["id"],
                        "text": note["text"],
                        "location": {
                            k: ref[k] for k in ("page", "bbox", "coordinate_system")
                        },
                        "parent_claim_ids": [claim["id"]],
                        "source_context": context,
                    }
                )
        documents.append(
            {
                "id": report["source_id"],
                "sha256": report["sha256"],
                "role": "customer_one_line",
                "spans": spans,
                "coverage": [],
            }
        )
    return result, documents


def _value(key, raw):
    value = raw.strip().rstrip(".;")
    if key in UNITS or key in {
        "phases",
        "wires",
        "poles",
        "main.frame_a",
        "main.trip_a",
    }:
        # Multiple ratings/ranges are evidence of a choice, not a scalar value.
        found = re.fullmatch(
            r"([\d,]+(?:\.\d+)?)\s*(kV|V|kA|A|Hz|in|inches|\")?", value, re.IGNORECASE
        )
        if not found:
            return None
        number = float(found[1].replace(",", ""))
        number, _ = canonical_requirement_value(key, number, found[2])
        if number is None:
            return None
        return int(number) if number.is_integer() else number
    if key in {
        "spd",
        "ground_fault",
        "arc_energy_reduction",
        "seismic",
        "service_disconnect",
    }:
        if value.lower() in {"yes", "required", "true", "provided", "included"}:
            return True
        if value.lower() in {"no", "not required", "false", "excluded", "not included"}:
            return False
        return None
    return value or None


def _assertions(text):
    """Explicit clauses only; no equipment defaults, inferred voltage or frame."""
    result = []
    for label, raw in re.findall(
        r"(?:^|[|;\n])\s*([^:|;\n]{2,70})\s*[:=]\s*([^|;\n]+)", text
    ):
        key = ALIASES.get(re.sub(r"[\s-]+", " ", label.lower().strip()))
        if key:
            result.append((key, _value(key, raw), raw))
    patterns = [
        (
            "service_rating_a",
            r"(?:main\s+bus|bus(?:bar)?\s+(?:rating|ampacity)|service\s+rating)\s*(?:shall\s+be|of|is|rated|:|=)?\s*([\d,]+)\s*(?:A\b|amperes?\b|amps?\b)",
        ),
        (
            "service_rating_a",
            r"([\d,]+)\s*(?:A\b|ampere[s]?\b|amp[s]?\b)[ -]+(?:main\s+)?bus\b",
        ),
        (
            "minimum_interrupting_ka",
            r"(?:interrupting|short[ -]circuit)(?:\s+current)?(?:\s+rating|\s+capacity)?\s*(?:shall\s+be|of|is|:|=|minimum)?\s*(\d+(?:\.\d+)?)\s*kA\b",
        ),
        (
            "voltage_v",
            r"\b(\d{3,4})\s*(?:V(?:AC)?\b|volts?\b)(?:[ ,/-]+(?:3|three)[ -]?(?:phase|ph))",
        ),
        ("frequency_hz", r"\b(50|60)\s*Hz\b"),
        ("phases", r"\b(1|3)[ -]*(?:phase|ph|∅)(?!\w)"),
        ("wires", r"\b(3|4)[ -]*(?:wire|w)\b"),
        (
            "max_height_in",
            r"(?:maximum|max\.?)(?:\s+overall)?\s+height\s*(?:of|:|=|shall\s+be)?\s*(\d+(?:\.\d+)?)\s*(?:inches|in\b|\")",
        ),
    ]
    for key, pattern in patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            result.append((key, _value(key, match[1]), match[0]))
    for match in re.finditer(
        r"\b(\d{3,4})\s*Y\s*/\s*(\d{2,3})\s*V\b", text, re.IGNORECASE
    ):
        result.extend(
            [
                ("voltage_v", int(match[1]), match[0]),
                ("voltage_to_ground_v", int(match[2]), match[0]),
            ]
        )
    for key, phrase in [
        ("spd", r"surge protective device|surge protection|\bSPD\b"),
        ("ground_fault", r"ground[ -]fault protection"),
        ("arc_energy_reduction", r"arc[ -]energy reduction"),
    ]:
        match = re.search(
            r"\b(?:provide|shall (?:have|include|be equipped with))\s+(?:an?\s+)?("
            + phrase
            + r")",
            text,
            re.IGNORECASE,
        )
        if match:
            result.append((key, True, match[0]))
    return result


def merge_drawing_readings(requirements, reports):
    """Project the existing image reader's source observations into requirements.

    A source-local circuit numeral remains source-local, not a guessed enterprise
    tag. Image confidence is displayed; it does not confer confirmation. Unknown
    frames/roles stay unknown and engineering interpretation remains a decision.
    """
    result = deepcopy(requirements)
    main_candidates = _drawing_main_candidates(reports)
    for report in reports:
        for page in report.get("pages", []):
            reading = page["reading"]
            proposed_mains = _proposed_main_roles(reading)
            mains, ambiguous_mains = _main_roles(reading)
            local_mains = set(mains)
            ambiguous_mains = ambiguous_mains or (
                bool(mains) and len(main_candidates) > 1
            )
            if ambiguous_mains:
                mains = set()
            if ambiguous_mains:
                result["warnings"].append(
                    {
                        "source_id": report["source_id"],
                        "page": page["page"],
                        "reason": "Multiple printed mains require engineering scope review; retained as separate source devices, not collapsed into one main.",
                        "verification_required": True,
                    }
                )
            for obj in reading.get("objects", []):
                if obj["kind"] not in {"device_symbol", "prepared_device", "busbar"}:
                    continue
                equipment_role_unknown = obj["kind"] != "busbar" and (
                    not _protective_device_established(obj)
                    or obj.get("configuration_eligible") is False
                )
                if equipment_role_unknown:
                    result.setdefault("reference_observations", []).append(
                        {
                            "source_id": report["source_id"],
                            "sha256": report["sha256"],
                            "page": page["page"],
                            "object": deepcopy(obj),
                            "reason": "Source equipment role is unverified; retained values do not establish a switchboard protective device or supplied feeder",
                        }
                    )
                raw_label = " ".join(
                    str(obj.get(k) or "") for k in ("raw_tag", "raw_label")
                )
                main = obj["key"] in mains and not equipment_role_unknown
                unresolved_role = equipment_role_unknown or (
                    ambiguous_mains
                    and (
                        obj["key"] in local_mains
                        or bool(re.search(r"\bmain\b", raw_label, re.IGNORECASE))
                    )
                )
                subject = (
                    "main"
                    if main
                    else "drawing:"
                    + stable_id(report["sha256"], page["page"], obj["key"])
                )
                for prop in obj.get("properties", []):
                    feature = prop["feature"]
                    if (
                        feature in {"bus_current_a", "voltage_v"}
                        and obj["kind"] == "busbar"
                    ):
                        key = {
                            "bus_current_a": "service_rating_a",
                            "voltage_v": "voltage_v",
                        }[feature]
                        owner = "order"
                    elif obj["kind"] in {
                        "device_symbol",
                        "prepared_device",
                    } and feature in {
                        "rated_current_a",
                        "trip_a",
                        "frame_a",
                        "pole_count",
                        "device_frame",
                        "device_type",
                        "interrupting_ka",
                    }:
                        key = {
                            "pole_count": "poles",
                            "device_frame": "frame",
                            "device_type": "kind",
                        }.get(feature, feature)
                        owner = subject
                        if main:
                            key, owner = "main." + key, "order"
                    else:
                        continue
                    ref = {
                        "source_id": report["source_id"],
                        "sha256": report["sha256"],
                        "page": page["page"],
                        "bbox": prop["bbox"],
                        "coordinate_system": "normalized_top_left",
                        "text": prop["raw_text"],
                    }
                    claim = make_claim(
                        owner,
                        key,
                        prop["value"],
                        source=ref,
                        unit=prop.get("unit"),
                        raw_value=prop["raw_text"],
                        raw_confidence=prop["confidence"],
                        state="proposed",
                        conditions={
                            "drawing_depiction_only": True,
                            "role_basis": "proposed_unique_source_breaker_bus_depiction"
                            if obj["key"] in proposed_mains
                            else "printed_label_or_source_local_circuit",
                            "role_engineering_verified": False,
                            "value_verification": deepcopy(
                                prop.get(
                                    "value_verification",
                                    report.get("value_verification"),
                                )
                            ),
                            "unresolved_role": unresolved_role,
                            "configuration_eligible": not unresolved_role,
                            "scope": "source_equipment_observation"
                            if equipment_role_unknown
                            else "proposed_protective_device_observation",
                            "schedule_provenance": deepcopy(
                                prop.get("schedule_provenance")
                            ),
                        },
                        extractor={
                            "name": "independent_drawing_page_image",
                            "version": report["version"],
                            "run_id": report["prompt_sha256"],
                        },
                    )
                    if any(c["id"] == claim["id"] for c in result["evidence_claims"]):
                        continue
                    result["evidence_claims"].append(claim)
                    normalized_value, normalized_unit = canonical_requirement_value(
                        key, prop["value"], prop.get("unit")
                    )
                    result["items"].append(
                        {
                            "id": stable_id("drawing-requirement", claim["id"]),
                            "key": key,
                            "subject_id": owner,
                            "value": normalized_value,
                            "unit": normalized_unit,
                            "source_claim_ids": [claim["id"]],
                            "source_refs": [ref],
                            "confirmed": False,
                            "state": "proposed",
                            "origin_role": "customer_one_line",
                            "raw_label": raw_label,
                            "display_tag": obj.get("raw_tag")
                            or obj.get("circuit_label"),
                            "source_object_key": obj["key"],
                            "source_object_kind": obj["kind"],
                            "representation_role": obj.get("representation_role"),
                            "equipment_presence": obj.get("equipment_presence"),
                            "schedule_provenance": deepcopy(
                                prop.get("schedule_provenance")
                            ),
                            "position_state": obj["position_state"],
                            "section_label": obj.get("section_label"),
                            "raw_confidence": prop["confidence"],
                            "identity_kind": "printed_tag"
                            if obj.get("raw_tag")
                            else "source_local_circuit",
                            "role_basis": "proposed_unique_source_breaker_bus_depiction"
                            if obj["key"] in proposed_mains
                            else "printed_label_or_source_local_circuit",
                            "unresolved_role": unresolved_role,
                            "configuration_eligible": not unresolved_role,
                            "observation_scope": "source_equipment_observation"
                            if equipment_role_unknown
                            else "proposed_protective_device_observation",
                        }
                    )
            for warning in [
                *reading.get("ambiguities", []),
                *reading.get("unknown_regions", []),
            ]:
                result["warnings"].append(
                    {
                        "source_id": report["source_id"],
                        "page": page["page"],
                        "reason": warning
                        if isinstance(warning, str)
                        else warning.get("reason", "Unresolved drawing region"),
                        "region": deepcopy(warning)
                        if isinstance(warning, dict)
                        else None,
                        "verification_required": True,
                    }
                )
            # A successfully read scan still needs human verification. Keep its
            # coverage marker; replacing it with an absence claim would be wrong.
    result["conflicts"] = find_conflicts(result["items"])
    return result


def extract_requirements(documents, *, version=1, scope_id="requirements"):
    items, claims, unreadable, excluded, warnings = [], [], [], [], []
    seen = set()
    for document in documents:
        role = document.get("role")
        if role not in CUSTOMER_ROLES:
            excluded.append(
                {
                    "id": document.get("id"),
                    "role": role,
                    "reason": "Not classified as a customer-side input",
                }
            )
            continue
        for page in document.get("coverage", []):
            if page.get("state") in {"scanned", "empty", "unsupported", "failed"}:
                unreadable.append(
                    {
                        "document_id": document["id"],
                        **deepcopy(page),
                        "verification_required": True,
                    }
                )
        context = "order"
        spans = [
            {**span, "text": line}
            for span in document.get("spans", [])
            for line in str(span.get("text", "")).splitlines()
            if line.strip()
        ]
        for span in spans:
            text = str(span.get("text", ""))
            location = span.get("location", {})
            ref = {
                "source_id": document["id"],
                "sha256": document["sha256"],
                **location,
                "text": text[:3000],
            }
            # Device identity requires an explicit tag label, not a guessed
            # interpretation of a general specification's breaker paragraph.
            tag = re.search(
                r"(?:Device|Feeder|Equipment tag)\s*[:=]\s*([A-Za-z0-9][\w .-]{0,59}?)(?:[|;\n]|$)",
                text,
                re.IGNORECASE,
            )
            if tag:
                context = tag[1].strip()
            for key, value, raw in _assertions(text):
                subject = (
                    context
                    if key
                    in {"frame_a", "trip_a", "trip_type", "poles", "rated_current_a"}
                    else "order"
                )
                if subject == "order" and key in {
                    "frame_a",
                    "trip_a",
                    "trip_type",
                    "poles",
                    "rated_current_a",
                }:
                    warnings.append(
                        {
                            "source_id": document["id"],
                            "span_id": span.get("id"),
                            "reason": "Device property has no explicit equipment tag",
                            "text": raw,
                        }
                    )
                    continue
                ident = stable_id(scope_id, subject, key, value, ref)
                if ident in seen:
                    continue
                try:
                    claim = make_claim(
                        subject,
                        key,
                        value,
                        source=ref,
                        unit=UNITS.get(key),
                        raw_value=raw,
                        extractor={
                            "name": "switchboard-customer-requirements",
                            "version": "1",
                        },
                        state="proposed",
                    )
                except ValueError as exc:
                    warnings.append(
                        {"source_id": document["id"], "reason": str(exc), "text": raw}
                    )
                    continue
                seen.add(ident)
                claims.append(claim)
                items.append(
                    {
                        "id": ident,
                        "key": key,
                        "value": value,
                        "unit": UNITS.get(key),
                        "subject_id": subject,
                        "source_claim_ids": [claim["id"]],
                        "source_refs": [claim["source"]],
                        "confirmed": False,
                        "state": "proposed",
                        "origin_role": role,
                        "excluded": role == "quote_exception"
                        and bool(
                            re.search(
                                r"\b(?:exclude[ds]?|not included|by others)\b",
                                text,
                                re.IGNORECASE,
                            )
                        ),
                    }
                )
    result = {
        "schema_version": SCHEMA,
        "version": version,
        "items": items,
        "evidence_claims": claims,
        "unreadable_pages": unreadable,
        "excluded_documents": excluded,
        "warnings": warnings,
        "engineering_approved": False,
    }
    result["conflicts"] = find_conflicts(items)
    return result


def find_conflicts(items):
    groups = {}
    for item in items:
        if item.get("dismissed"):
            continue
        groups.setdefault((item["subject_id"], item["key"]), []).append(item)
    result = []
    for (subject, key), rows in groups.items():

        def comparable(value):
            if isinstance(value, bool):
                return ("boolean", str(value))
            if isinstance(value, (float, int)):
                return ("number", str(Decimal(str(value)).normalize()))
            return ("text", str(value))

        values = {comparable(row["value"]) for row in rows if row["value"] is not None}
        if (len(values) > 1 and key != "option") or any(
            row.get("excluded") for row in rows
        ):
            result.append(
                {
                    "id": stable_id(subject, key, sorted(values)),
                    "subject_id": subject,
                    "key": key,
                    "item_ids": [row["id"] for row in rows],
                    "reason": "Quote exclusion requires review"
                    if any(row.get("excluded") for row in rows)
                    else "Customer sources disagree; no precedence applied",
                }
            )
    return result


def to_config_inputs(requirements):
    active = [
        item
        for item in requirements.get("items", [])
        if not item.get("dismissed") and item.get("equipment_role") != "reference"
    ]
    result, feeders = (
        {
            "evidence_claims": deepcopy(requirements.get("evidence_claims", [])),
            "conflicts": deepcopy(requirements.get("conflicts", [])),
            "confirmed": bool(active) and all(item.get("confirmed") for item in active),
        },
        {},
    )
    conflict_ids = {i for c in requirements.get("conflicts", []) for i in c["item_ids"]}
    for item in requirements.get("items", []):
        if (
            item.get("dismissed")
            or item.get("excluded")
            or item.get("unresolved_role")
            or item.get("configuration_eligible") is False
            or item.get("equipment_role") == "reference"
            or item["id"] in conflict_ids
            or item["value"] is None
        ):
            continue
        payload = {
            "value": item["value"],
            "confirmed": item.get("confirmed", False),
            "source_claim_ids": list(item["source_claim_ids"]),
            "source_refs": deepcopy(item.get("source_refs", [])),
        }
        subject, key = item["subject_id"], item["key"]
        if subject != "order":
            target = feeders.setdefault(
                subject,
                {
                    "id": subject,
                    "tag": subject,
                    "source_claim_ids": [],
                    "source_refs": [],
                    "confirmed": True,
                },
            )
            target[key] = item["value"]
            if "position_state" in item:
                target["position_state"] = item["position_state"]
                target["source_local_identity"] = True
                target["display_tag"] = item.get("display_tag")
                target["section_label"] = item.get("section_label")
            target["source_claim_ids"] += item["source_claim_ids"]
            target["source_refs"] += deepcopy(item.get("source_refs", []))
            target["confirmed"] &= item.get("confirmed", False)
        elif key.startswith("main."):
            result.setdefault("main", {})[key.split(".", 1)[1]] = payload
        elif key in {
            "max_height_in",
            "max_width_in",
            "max_depth_in",
            "service_side",
            "seismic",
        }:
            result.setdefault("constraints", {})[key] = payload
        elif key == "option":
            result.setdefault("options", []).append(payload)
        else:
            # Same-valued corroboration retains every origin rather than
            # arrival-order replacement. Conflicting values were withheld.
            existing = result.get(key)
            if existing and existing["value"] == payload["value"]:
                payload["source_claim_ids"] += existing["source_claim_ids"]
                payload["source_refs"] += existing.get("source_refs", [])
                payload["confirmed"] &= existing["confirmed"]
            result[key] = payload
    result["feeders"] = list(feeders.values())
    return result
