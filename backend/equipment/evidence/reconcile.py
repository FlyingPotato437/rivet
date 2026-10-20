"""Reconcile immutable observations; constraints never manufacture source values.

This bounded solver retains feasible competing values and their proof ancestry.
A selected candidate is separate from acceptance, engineering approval and release.
"""

from __future__ import annotations

import hashlib
import json
import math
import operator
import re
from collections import defaultdict
from copy import deepcopy

VERSION = "rivet.evidence-reconciliation.v2.1"
CONDITION_KEYS = (
    "voltage_v",
    "current_type",
    "rating_standard",
    "frequency_hz",
    "connection_system",
)
LITERAL_EXTRACTORS = frozenset(
    {
        "native_literal",
        "native_equipment_reader",
        "native_readers",
        "source_bom_row_reader",
        "cad_report",
        "autocad_electrical_harness_xml",
        "native_cad_attributes",
        "native_pdf_text",
        "equipment_readers",
        "read_source",
    }
)
POLICY = {
    "version": VERSION,
    "maximum_claims": 100000,
    "maximum_nodes": 20000,
    "maximum_candidates": 32,
    "catalog_default_current_type": None,
    "catalog_default_standard": None,
    "model_confidence_is_probability": False,
    "contradictions_require_review": True,
    "minimum_calibration_packages": 5,
    "minimum_calibration_lower_bound": 0.99,
    "maximum_trip_frame_pairs": 1024,
    "trip_frame_decision": "exact bounded feasible pair; maximum combined source support; ties unknown",
}


def _canonical(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value) if value == int(value) else value
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def _key(value):
    return json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _id(*values):
    return hashlib.sha256(_key(values).encode()).hexdigest()[:32]


def _snapshot(claim):
    conditions = claim.get("conditions", {})
    if (
        conditions.get("role") == "specification"
        or conditions.get("source_role") == "specification"
    ):
        return "requirements"
    return conditions.get("side", "target")


def _number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _literal(claim, claims, seen=None):
    seen = set(seen or ())
    if claim["id"] in seen:
        return False
    seen.add(claim["id"])
    name = claim.get("extractor", {}).get("name")
    parents = [claims.get(k) for k in claim.get("derived_from", [])]
    if parents:
        return (name == "catalog_decoder" or name in LITERAL_EXTRACTORS) and all(
            p and _literal(p, claims, seen) for p in parents
        )
    return name in LITERAL_EXTRACTORS


def _roots(claim, claims, seen=None):
    seen = set(seen or ())
    if claim["id"] in seen:
        return {"cyclic:" + claim["id"]}
    seen.add(claim["id"])
    parents = claim.get("derived_from", [])
    if parents:
        roots = set()
        for parent in parents:
            roots |= (
                _roots(claims[parent], claims, seen)
                if parent in claims
                else {"missing:" + parent}
            )
        return roots
    source = claim["source"]
    physical = _id(
        "physical-source-ancestry",
        source["sha256"],
        source.get("page"),
        source.get("sheet"),
        None
        if source.get("page") or source.get("sheet")
        else source.get("entity", source.get("xml_path")),
    )
    return {
        "physical:" + physical,
        str(claim.get("independence_group") or "unestablished:" + claim["id"]),
    }


def _proof_families(proofs):
    """One joint derivation is one proof, not one vote per dependency page.

    Proofs sharing any source ancestry are correlated and form one component.
    Separate extractor calls over the same page never add independent support.
    """
    families = []
    for proof in proofs:
        roots, literal = set(proof["roots"]), proof["literal"]
        touched = [f for f in families if f["roots"] & roots]
        for old in touched:
            roots |= old["roots"]
            literal |= old["literal"]
            families.remove(old)
        families.append({"roots": roots, "literal": literal})
    return families


def _typed_alias(feature, value):
    if feature in {"manufacturer", "product_family"} and isinstance(value, str):
        from .catalogs import _family, _manufacturer

        return (
            _manufacturer(value) if feature == "manufacturer" else _family(value)
        ) or value
    from .ontology import FEATURES, normalize_feature_value

    spec = FEATURES.get(feature)
    if spec and spec.allowed_values:
        normalized = normalize_feature_value(feature, value)
        if normalized["valid"]:
            return normalized["value"]
    return value


def _context_value(feature, value):
    """Canonicalize only known operating vocabulary, retaining raw claims."""
    if isinstance(value, list):
        return [_context_value(feature, item) for item in value]
    if isinstance(value, str):
        if value.strip().casefold() in {"", "-", "unknown", "n/a", "tbd", "?"}:
            return None
        if feature == "rating_standard":
            # Exactly the standards named by the curated catalog code. No
            # unknown/private standard is interpreted as a listed standard.
            return {"ul489": "UL489", "csa489": "CSA489", "csa": "CSA"}.get(
                re.sub(r"[^a-z0-9]+", "", value.casefold()), value
            )
        if feature == "current_type":
            from .ontology import normalize_feature_value

            normalized = normalize_feature_value(feature, value)
            return normalized["value"] if normalized["valid"] else value
    return _canonical(value)


def _unambiguous(claims, feature):
    selected = [
        c for c in claims if c["feature"] == feature and c.get("value") is not None
    ]
    normalized = [
        _context_value(feature, _typed_alias(feature, c["value"]))
        if feature in CONDITION_KEYS
        else _typed_alias(feature, c["value"])
        for c in selected
    ]
    values = {_key(value): value for value in normalized}
    return (next(iter(values.values())), selected) if len(values) == 1 else (None, [])


def _observed_frequency_value(claim):
    if (
        claim.get("extractor", {}).get("name") == "catalog_decoder"
        or claim.get("normalization", {}).get("valid") is False
    ):
        return None
    if claim["feature"] == "frequency_hz":
        value = claim.get("value") if claim.get("unit") == "Hz" else None
    else:
        value = claim.get("conditions", {}).get("frequency_hz")
    return value if _number(value) and value > 0 else None


def _observed_frequency(claims):
    selected = [c for c in claims if _observed_frequency_value(c) is not None]
    values = {
        _key(_observed_frequency_value(c)): _observed_frequency_value(c)
        for c in selected
    }
    return (next(iter(values.values())), selected) if len(values) == 1 else (None, [])


def _catalog_evidence(graph, lookup):
    from .schema import make_claim

    graph = deepcopy(graph)
    by_entity = defaultdict(list)
    for claim in graph["claims"]:
        if (
            claim.get("state", "observed") == "observed"
            and _snapshot(claim) != "requirements"
        ):
            by_entity[(claim["subject_id"], _snapshot(claim))].append(claim)
    additions, constraints, lookups = [], [], []
    existing = {c["id"] for c in graph["claims"]}
    for (subject, side), claims in sorted(by_entity.items()):
        features = {
            name: _unambiguous(claims, name)
            for name in (
                "manufacturer",
                "product_family",
                "part_number",
                "device_frame",
                *CONDITION_KEYS,
            )
        }
        # Source conditions may establish frequency, but the catalog's own
        # applicability range cannot. Retain every observing parent so the
        # rating and its conditional derivation share their evidence ancestry.
        features["frequency_hz"] = _observed_frequency(claims)
        manufacturer, family, part, frame = (
            features[n][0]
            for n in ("manufacturer", "product_family", "part_number", "device_frame")
        )
        if part is None and (manufacturer is None or family is None):
            continue
        found = lookup(
            manufacturer=manufacturer,
            family=family,
            part_number=part,
            frame=frame,
            voltage_v=features["voltage_v"][0],
            current_type=features["current_type"][0] or "",
            connection_system=features["connection_system"][0],
            rating_standard=features["rating_standard"][0] or "",
        )
        lookups.append({"subject_id": subject, "side": side, **deepcopy(found)})
        parent_claims = [
            c
            for n in ("part_number", "manufacturer", "product_family", "device_frame")
            for c in features[n][1]
        ]
        for observed in found.get("claims", []):
            citation = observed["source_citation"]
            digest = citation.get("sha256") or citation.get("original_sha256")
            if not digest or not citation.get("page"):
                continue  # No forged digest for a web-only source.
            feature = (
                "product_family"
                if observed["feature"] == "family"
                else observed["feature"]
            )
            if feature == "interrupting_ka" and features["frequency_hz"][0] is None:
                lookups[-1]["applicability"]["operating_rating_bound"] = False
                lookups[-1].setdefault("unknowns", []).append(
                    "Actual operating frequency is unestablished; the cited conditional kA candidate requires review."
                )
            if feature == "interrupting_ka" and features["frequency_hz"][0] is not None:
                operating_frequency = features["frequency_hz"][0]
                allowed_frequencies = observed.get("conditions", {}).get("frequency_hz")
                if allowed_frequencies is not None and operating_frequency not in (
                    allowed_frequencies
                    if isinstance(allowed_frequencies, list)
                    else [allowed_frequencies]
                ):
                    lookups[-1]["applicability"]["operating_rating_bound"] = False
                    lookups[-1].setdefault("unknowns", []).append(
                        "Explicit operating frequency is outside the cited rating conditions; no applicable kA value was established."
                    )
                    continue
            conditions = {
                **observed.get("conditions", {}),
                "side": side,
                "role": "catalog",
            }
            parents = list(parent_claims)
            if feature == "interrupting_ka":
                parents += [c for n in CONDITION_KEYS for c in features[n][1]]
            derived = make_claim(
                subject,
                feature,
                observed["value"],
                source={
                    "source_id": citation["source_id"],
                    "sha256": digest,
                    "page": citation["page"],
                    "entity": citation.get("section", "catalog table"),
                },
                extractor={"name": "catalog_decoder", "version": "1"},
                raw_value=observed["value"],
                raw_confidence=None,
                unit=observed.get("unit"),
                conditions=conditions,
                derived_from=list(dict.fromkeys(c["id"] for c in parents)),
                independence_group=_id(
                    "derived-product-binding", [c["id"] for c in parents]
                ),
            )
            derived["catalog_citation"] = citation
            if derived["id"] not in existing:
                existing.add(derived["id"])
                additions.append(derived)
        for constraint in found.get("constraints", []):
            constraints.append(
                {
                    "id": _id("catalog-rule", subject, side, constraint),
                    "subject_id": subject,
                    "side": side,
                    **deepcopy(constraint),
                }
            )
    graph["claims"].extend(additions)
    return graph, constraints, lookups


def _context_index(claims):
    values = defaultdict(lambda: defaultdict(dict))
    for c in claims:
        identity = (c["subject_id"], _snapshot(c), c.get("state", "observed"))
        conditions = c.get("conditions", {})
        for key in CONDITION_KEYS:
            if key == "frequency_hz":
                v = _observed_frequency_value(c)
            else:
                v = conditions.get(key)
                if c["feature"] == key:
                    v = c.get("value")
            v = _context_value(key, v)
            if v is not None and not isinstance(v, (dict, list)):
                values[identity][key][_key(v)] = v
    return values


def _context(claim, index):
    if claim["feature"] != "interrupting_ka":
        return {}  # Operating context partitions ratings, not identity/features.
    conditions = claim.get("conditions", {})
    identity = (claim["subject_id"], _snapshot(claim), claim.get("state", "observed"))
    context = {}
    for key in CONDITION_KEYS:
        raw = _context_value(key, conditions.get(key))
        known = index[identity].get(key, {})
        unique = next(iter(known.values())) if len(known) == 1 else None
        if raw is None or (isinstance(raw, list) and unique in raw):
            raw = unique
        if raw is not None:
            context[key] = raw
    return _canonical(context)


def _range_reasons(candidate, constraints):
    reasons, alternatives = [], []
    for rule in constraints:
        kind, value = rule.get("kind"), candidate["value"]
        if kind not in {"conditional_range", "range", "allowed_values"}:
            continue
        fails = False
        if kind == "allowed_values":
            fails = value not in rule.get("values", [])
        elif _number(value):
            minimum = rule.get("minimum", rule.get("min"))
            maximum = rule.get("maximum", rule.get("max"))
            fails = (minimum is not None and value < minimum) or (
                maximum is not None and value > maximum
            )
        if kind == "conditional_range":
            alternatives.append((rule, fails))
        elif fails:
            reasons.append(rule["id"])
    # Catalog variants (e.g. L400 / L600) are alternatives, not intersected ranges.
    if alternatives and all(fails for _, fails in alternatives):
        reasons.extend(r["id"] for r, _ in alternatives)
    return reasons


def reliability_bucket(resolution):
    support = resolution["support"]
    return f"groups:{min(support['independent_group_count'], 3)}|literal:{int(support['literal_source'])}|conflict:{int(bool(resolution['conflicting_claim_ids']))}"


def _flag(resolution, reason, rule_id):
    resolution["needs_review"] = True
    resolution["constraint_failures"] = sorted(
        set(resolution["constraint_failures"] + [rule_id])
    )
    resolution["status"] = "conflict"
    resolution["explanation"].append(reason)


def _review_unknown(resolution, reason, rule_id):
    resolution["needs_review"] = True
    if resolution["status"] != "conflict":
        resolution["status"] = (
            "provisional" if resolution["value"] is not None else "unknown"
        )
    resolution.setdefault("constraint_unknowns", []).append(rule_id)
    resolution["explanation"].append(reason)


def _evaluate_rules(result, rules):
    resolutions = result["resolutions"]
    checks = []
    claim_ids = {c["id"] for c in result["claims"]}
    for rule in rules:
        affected = [
            r
            for r in resolutions
            if r["subject_id"] == rule.get("subject_id")
            and r["feature"] == rule.get("feature")
            and r["side"] == rule.get("side", "target")
        ]
        cited = rule.get("source_claim_ids", [])
        invalid = (
            not cited
            or not set(cited) <= claim_ids
            or (
                rule.get("state") in {"approved", "released"}
                and not rule.get("authority")
            )
        )
        if invalid or rule.get("kind") not in {
            "attribute_constraint",
            "symmetry",
            "bus_device_constraint",
        }:
            status = (
                "invalid_rule_citation_or_authority" if invalid else "unsupported_rule"
            )
            for r in affected:
                _review_unknown(
                    r,
                    "This graph rule is unsupported or lacks bound source/approval evidence; its requirement is not verified.",
                    rule.get("id"),
                )
            checks.append(
                {"rule_id": rule.get("id"), "status": status, "needs_review": True}
            )
            continue
        target = rule.get("target", {})
        lefts = [
            r
            for r in resolutions
            if r["subject_id"] == rule.get("subject_id")
            and r["feature"] == rule.get("feature")
            and r["side"] == rule.get("side", "target")
            and r["state"] == "observed"
        ]
        rights = [
            r
            for r in resolutions
            if r["subject_id"] == target.get("other_subject_id")
            and r["feature"] == target.get("feature")
            and r["side"] == rule.get("side", "target")
            and r["state"] == "observed"
        ]
        right = (
            target.get("value")
            if "value" in target
            else rights[0]["value"]
            if len(rights) == 1 and not rights[0]["needs_review"]
            else None
        )
        rid = rule.get("id", _id(rule))
        if not lefts:
            checks.append(
                {
                    "rule_id": rid,
                    "status": "unknown",
                    "needs_review": True,
                    "reason": "No actual equipment observation; requirement cannot fill it.",
                }
            )
        for left in lefts:
            a, op = left["value"], rule.get("operator")
            status = "unknown"
            unit = target.get("unit") or (
                rights[0].get("unit") if len(rights) == 1 else None
            )
            if (
                a is not None
                and (op == "exists" or right is not None)
                and (unit is None or unit == left.get("unit"))
            ):
                try:
                    ok = (
                        True
                        if op == "exists"
                        else {
                            "eq": operator.eq,
                            "ne": operator.ne,
                            "le": operator.le,
                            "lt": operator.lt,
                            "ge": operator.ge,
                            "gt": operator.gt,
                            "in": lambda x, y: x in y,
                        }[op](a, right)
                    )
                    status = "satisfied" if ok else "violated"
                except (KeyError, TypeError, ValueError):
                    status = "unknown"
            if status == "satisfied" and left["needs_review"]:
                status = "candidate_only"
            weak_symmetry = rule.get("kind") == "symmetry" and (
                rule.get("state") not in {"approved", "released"}
                or not rule.get("authority")
            )
            if weak_symmetry:
                if status != "satisfied":
                    _review_unknown(
                        left,
                        "Unapproved symmetry is a review clue only; no sibling value was copied or imposed.",
                        rid,
                    )
                status = "review_clue"
            elif status in {"violated", "unknown"}:
                _flag(
                    left,
                    "A cited graph requirement is violated or cannot be established; no source value was replaced.",
                    rid,
                )
            checks.append(
                {
                    "rule_id": rid,
                    "resolution_id": left["id"],
                    "status": status,
                    "needs_review": status != "satisfied",
                    "source_claim_ids": rule.get("source_claim_ids", []),
                    "left": a,
                    "right": right,
                    "operator": op,
                    "authority": rule.get("authority"),
                    "engineering_approved": False,
                }
            )
    # A shared bus does not imply sum(branch trips) <= bus. Detect only an
    # individual device exceeding a directly containing bus, as a review clue.
    by_subject = defaultdict(dict)
    for r in resolutions:
        if (
            r["side"] == "target"
            and r["state"] == "observed"
            and r["value"] is not None
        ):
            by_subject[r["subject_id"]][r["feature"]] = r
    for edge in result.get("edges", []):
        if edge.get("kind") != "contains":
            continue
        parent, child = by_subject[edge["from"]], by_subject[edge["to"]]
        bus = parent.get("bus_current_a")
        device = child.get("trip_a") or child.get("rated_current_a")
        if (
            bus
            and device
            and _number(bus["value"])
            and _number(device["value"])
            and device["value"] > bus["value"]
        ):
            rid = _id("individual_device_bus_review", edge["id"])
            _flag(
                device,
                "Individual device rating exceeds its directly containing bus observation; topology and rating applicability need review. Branch ratings were not summed.",
                rid,
            )
            checks.append(
                {"rule_id": rid, "status": "review_clue", "needs_review": True}
            )
    return checks


def _prepare_candidates(key, claims, catalog_rules, claims_by_id):
    """Prepare all typed/range-valid domains before any coupled decision."""
    from .ontology import FEATURES, normalize_feature_value

    subject, feature, side, _, _ = key
    candidates = {}
    for claim in claims:
        if (
            claim.get("value") is None
            or claim.get("normalization", {}).get("valid") is False
        ):
            continue
        value = _typed_alias(feature, claim["value"])
        candidate = candidates.setdefault(
            _key([value, claim.get("unit")]),
            {"value": value, "unit": claim.get("unit"), "claim_ids": [], "proofs": []},
        )
        candidate["claim_ids"].append(claim["id"])
        candidate["proofs"].append(
            {
                "roots": _roots(claim, claims_by_id),
                "literal": _literal(claim, claims_by_id),
            }
        )
    if len(candidates) > POLICY["maximum_candidates"]:
        raise ValueError(
            "More than 32 competing values for one attribute; split the evidence scope"
        )
    applicable = [
        r
        for r in catalog_rules
        if r.get("subject_id") == subject
        and r.get("side") == side
        and r.get("feature") == feature
    ]
    for candidate in candidates.values():
        candidate["rejected_by"] = _range_reasons(candidate, applicable)
        spec = FEATURES.get(feature)
        typed = normalize_feature_value(feature, candidate["value"], candidate["unit"])
        if (
            spec is None
            or candidate["unit"] != spec.unit
            or not typed["valid"]
            or _key(typed["value"]) != _key(candidate["value"])
        ):
            candidate["rejected_by"].append("invalid_typed_value_or_noncanonical_unit")
        candidate["families"] = _proof_families(candidate.pop("proofs"))
        candidate["score"] = sum(
            4 if f["literal"] else 1 for f in candidate["families"]
        )
    return list(candidates.values())


def _joint_trip_frame_choices(domains):
    """Exactly enumerate at most 32 x 32 pairs for one subject/snapshot/state.

    This is a local two-attribute decision. It does not optimize the equipment
    graph, invent an absent attribute, or resolve contradictory source claims.
    A tie between maximum-support feasible pairs leaves both decisions unknown.
    """
    grouped = defaultdict(dict)
    for key in domains:
        subject, feature, side, state, _ = key
        if feature in {"trip_a", "frame_a"} and side != "requirements":
            identity = (subject, side, state)
            if feature in grouped[identity]:
                raise ValueError(
                    "Trip/frame context is ambiguous within one equipment snapshot"
                )
            grouped[identity][feature] = key
    choices, decisions = {}, {}
    for (subject, side, state), keys in sorted(grouped.items()):
        if set(keys) != {"trip_a", "frame_a"}:
            continue
        trip_key, frame_key = keys["trip_a"], keys["frame_a"]
        trips = [c for c in domains[trip_key] if not c["rejected_by"]]
        frames = [c for c in domains[frame_key] if not c["rejected_by"]]
        if not trips or not frames:
            # A missing/invalid opposite attribute supplies no numeric bound.
            continue
        pair_count = len(trips) * len(frames)
        if pair_count > POLICY["maximum_trip_frame_pairs"]:
            raise ValueError("Trip/frame candidate pair budget exceeded")
        feasible = [
            (trip, frame)
            for trip in trips
            for frame in frames
            if trip["value"] <= frame["value"]
        ]
        for candidates, feasible_ids in (
            (trips, {id(pair[0]) for pair in feasible}),
            (frames, {id(pair[1]) for pair in feasible}),
        ):
            for candidate in candidates:
                if id(candidate) not in feasible_ids:
                    candidate["rejected_by"].append("trip_exceeds_observed_frame")
        maximum = max(
            (trip["score"] + frame["score"] for trip, frame in feasible), default=None
        )
        maximizers = [
            (trip, frame)
            for trip, frame in feasible
            if trip["score"] + frame["score"] == maximum
        ]
        selected = maximizers[0] if len(maximizers) == 1 else None
        status = (
            "selected_pair"
            if selected
            else "tied_feasible_pairs"
            if maximizers
            else "no_feasible_pair"
        )
        decision = {
            "id": _id("joint-trip-frame", subject, side, state),
            "kind": "trip_not_above_frame",
            "subject_id": subject,
            "side": side,
            "state": state,
            "status": status,
            "method": "exact_bounded_pair_enumeration",
            "evaluated_pair_count": pair_count,
            "feasible_pair_count": len(feasible),
            "maximum_support_sum": maximum,
            "maximum_pair_count": len(maximizers),
            "selected_pair": {
                "trip_a": selected[0]["value"],
                "frame_a": selected[1]["value"],
                "trip_claim_ids": list(selected[0]["claim_ids"]),
                "frame_claim_ids": list(selected[1]["claim_ids"]),
            }
            if selected
            else None,
            "engineering_approved": False,
            "production_released": False,
        }
        choices[trip_key] = selected[0] if selected else None
        choices[frame_key] = selected[1] if selected else None
        decisions[trip_key] = decisions[frame_key] = decision
    return choices, decisions


def _source_local_projection(graph, claim, parent, nodes):
    """Allow only an unchanged claim projected through a cited local-row proof.

    This exception is not a physical-device alias, a cross-page join, or a way
    for catalog claims to borrow another equipment identity.
    """
    from .schema import source_ref, stable_id
    from .table_reader import normalize_header

    if (
        claim.get("extractor", {}).get("name")
        != "source_local_correspondence_projection"
    ):
        return False
    if claim.get("derived_from") != [parent["id"]]:
        return False
    link_id = claim.get("conditions", {}).get("source_local_correspondence_id")
    links = [
        link
        for link in graph.get("identity_correspondence", [])
        if link.get("id") == link_id
    ]
    if len(links) != 1:
        return False
    link = links[0]
    if (
        link.get("id")
        != stable_id(
            "source-local-correspondence", {k: v for k, v in link.items() if k != "id"}
        )
        or link.get("status") != "proven_source_local"
        or link.get("verified") is not True
        or link.get("scope_id") != graph["scope_id"]
        or link.get("physical_identity_verified") is not False
        or link.get("unresolved") != []
        or not link.get("table_subject_id")
    ):
        return False
    left, right = nodes[parent["subject_id"]], nodes[claim["subject_id"]]
    if (
        left.get("metadata", {}).get("source_local_id") != link.get("table_local_id")
        or right.get("metadata", {}).get("source_local_id")
        != str(link.get("visual_local_id"))
        or any(
            node.get("metadata", {}).get("source_id") != link.get("source_id")
            for node in (left, right)
        )
    ):
        return False
    for node, local in (
        (left, link["table_local_id"]),
        (right, str(link["visual_local_id"])),
    ):
        matches = [
            n
            for n in nodes.values()
            if n.get("metadata", {}).get("source_id") == link["source_id"]
            and n.get("metadata", {}).get("source_local_id") == local
        ]
        if len(matches) != 1 or matches[0]["id"] != node["id"]:
            return False
    if any(
        claim.get(key) != parent.get(key)
        for key in (
            "feature",
            "value",
            "unit",
            "raw_value",
            "raw_confidence",
            "normalization",
            "source",
            "state",
            "independence_group",
        )
    ):
        return False
    if claim.get("independence_group") != link.get("independence_group"):
        return False
    expected_conditions = {
        **parent.get("conditions", {}),
        "source_local_correspondence_id": link["id"],
        "physical_identity_verified": False,
        "candidate_only": True,
    }
    if (
        claim.get("conditions") != expected_conditions
        or link.get("engineering_approved") is not False
    ):
        return False
    basis = link.get("basis", {})
    circuit = link.get("circuit_literal")
    if (
        not circuit
        or basis.get("exact_printed_circuit") != circuit
        or any(
            basis.get(key) != 1
            for key in (
                "circuit_occurrences_in_source_table",
                "circuit_occurrences_in_visual_table",
                "matching_ruled_table_count",
            )
        )
        or basis.get("ratings_or_row_ordinals_used") is not False
    ):
        return False
    refs = link.get("source_refs", [])
    visual_ref = link.get("visual_source_ref", {})
    if (
        not refs
        or visual_ref.get("entity") != str(link.get("visual_local_id"))
        or str(visual_ref.get("text")) != str(circuit)
    ):
        return False
    for ref in [parent["source"], *refs, visual_ref]:
        try:
            source_ref(ref)
        except ValueError:
            return False
        if any(
            ref.get(key) != link.get(key) for key in ("source_id", "sha256", "page")
        ):
            return False
    if sum(str(ref.get("text")) == str(circuit) for ref in refs) < 2:
        return False
    return any(
        normalize_header(str(ref.get("text", ""))) == "circuit_no" for ref in refs
    )


def _validate_graph(graph):
    from .schema import source_ref

    scope = graph.get("scope_id")
    if not scope:
        raise ValueError("Reconciliation requires an explicit evidence scope")
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    if len(nodes) != len(graph.get("nodes", [])) or any(
        n.get("scope_key") != scope for n in nodes.values()
    ):
        raise ValueError("Equipment nodes must belong to this evidence scope")
    claims = {c["id"]: c for c in graph.get("claims", [])}
    if len(claims) != len(graph.get("claims", [])):
        raise ValueError("Evidence claim IDs must be unique")
    indegrees, children = {}, defaultdict(list)
    for cid, claim in claims.items():
        if claim["subject_id"] not in nodes:
            raise ValueError("Evidence subject is outside this graph")
        source_ref(claim["source"])
        parents = set(claim.get("derived_from", []))
        if not parents <= claims.keys():
            raise ValueError("Derived evidence has a missing source claim")
        if any(
            claims[parent]["subject_id"] != claim["subject_id"]
            and not _source_local_projection(graph, claim, claims[parent], nodes)
            for parent in parents
        ):
            raise ValueError(
                "Derived evidence cannot borrow another equipment identity"
            )
        if claim.get("extractor", {}).get(
            "name"
        ) != "authorized_lifecycle_event" and any(
            _snapshot(claims[parent]) != _snapshot(claim)
            or claims[parent].get("state", "observed") != claim.get("state", "observed")
            for parent in parents
        ):
            raise ValueError(
                "Derived evidence cannot turn a requirement, proposal or prior issue into actual equipment"
            )
        indegrees[cid] = len(parents)
        for parent in parents:
            children[parent].append(cid)
    ready = [cid for cid, degree in indegrees.items() if not degree]
    visited = 0
    while ready:
        cid = ready.pop()
        visited += 1
        for child in children[cid]:
            indegrees[child] -= 1
            if not indegrees[child]:
                ready.append(child)
    if visited != len(claims):
        raise ValueError("Evidence derivation contains a cycle")


def reconcile_graph(graph, *, catalog_lookup=None, calibration=None, rules=None):
    """Select supported candidates with an exact local trip/frame pair decision.

    Hard local ranges and trip/frame constraints prune candidates. Explicit graph
    requirements are checked against observations without creating observations.
    This is bounded constraint propagation, not a claim of global engineering
    optimization or a standards compliance certificate.
    """
    if (
        len(graph.get("claims", [])) > POLICY["maximum_claims"]
        or len(graph.get("nodes", [])) > POLICY["maximum_nodes"]
    ):
        raise ValueError("Evidence graph exceeds this bounded pass")
    if catalog_lookup is None:
        from .catalogs import lookup_product

        catalog_lookup = lookup_product
    _validate_graph(graph)
    original = {c["id"]: deepcopy(c) for c in graph["claims"]}
    result, catalog_rules, lookups = _catalog_evidence(graph, catalog_lookup)
    claims_by_id = {c["id"]: c for c in result["claims"]}
    if len(claims_by_id) != len(result["claims"]):
        raise ValueError("Evidence claim IDs must be unique")
    index = _context_index(result["claims"])
    by_group = defaultdict(list)
    for claim in result["claims"]:
        key = (
            claim["subject_id"],
            claim["feature"],
            _snapshot(claim),
            claim.get("state", "observed"),
            _key(_context(claim, index)),
        )
        by_group[key].append(claim)
    domains = {
        key: _prepare_candidates(key, claims, catalog_rules, claims_by_id)
        for key, claims in sorted(by_group.items())
    }
    joint_choices, joint_decisions = _joint_trip_frame_choices(domains)
    resolutions = []
    for key, claims in sorted(by_group.items()):
        subject, feature, side, state, context = key
        values = domains[key]
        known = [c for c in values if not c["rejected_by"]]
        best = max((c["score"] for c in known), default=None)
        winners = [c for c in known if c["score"] == best]
        winner = (
            joint_choices[key]
            if key in joint_choices
            else winners[0]
            if len(winners) == 1
            else None
        )
        supporting = winner["claim_ids"] if winner else []
        conflicting = [
            c["id"]
            for c in claims
            if c.get("value") is not None and c["id"] not in supporting
        ]
        unknown = [
            c["id"]
            for c in claims
            if c.get("value") is None
            or c.get("normalization", {}).get("valid") is False
        ]
        rejected = [i for c in values for i in c["rejected_by"]]
        families = winner["families"] if winner else []
        groups = sorted(_id("proof_family", sorted(f["roots"])) for f in families)
        literal = any(f["literal"] for f in families)
        unresolved = bool(conflicting or unknown or rejected or not winner)
        conditions = json.loads(context)
        actual_frequencies = index[(subject, side, state)].get("frequency_hz", {})
        frequency_observed = len(actual_frequencies) == 1 and _key(
            next(iter(actual_frequencies.values()))
        ) == _key(conditions.get("frequency_hz"))
        context_missing = feature == "interrupting_ka" and (
            any(
                k not in conditions
                for k in ("voltage_v", "current_type", "rating_standard")
            )
            or not _number(conditions.get("frequency_hz"))
            or conditions.get("frequency_hz", 0) <= 0
            or not frequency_observed
        )
        explanations = [
            "No normalized source value was established; raw claims are retained."
            if not values
            else "Competing or unsatisfiable values have no unique supported solution."
            if winner is None
            else f"Candidate has {len(groups)} independent proof families; shared source ancestry and joint derivations add no extra votes."
        ]
        if conflicting:
            explanations.append(
                "Contradictory claims remain visible and require a person; majority does not remove them."
            )
        if unknown:
            explanations.append(
                "Some source claims are unknown or normalization is unsupported."
            )
        if rejected:
            explanations.append(
                "At least one observed value violates an applicable constraint."
            )
        if context_missing:
            explanations.append(
                "Interrupting rating lacks voltage, current type, rating standard or an explicitly observed operating frequency. Catalog frequency ranges do not establish actual operating frequency."
            )
        joint = joint_decisions.get(key)
        if joint:
            explanations.append(
                {
                    "selected_pair": "Trip and frame were selected together from the bounded feasible pairs by combined source support; contradictory observations remain visible.",
                    "tied_feasible_pairs": "Multiple equally supported feasible trip/frame pairs remain; both values require review.",
                    "no_feasible_pair": "No typed source trip/frame pair satisfies trip at or below frame; both values remain unknown.",
                }[joint["status"]]
            )
        needs_review = unresolved or context_missing or not literal
        resolution = {
            "id": _id("resolution", key),
            "subject_id": subject,
            "entity_id": subject,
            "feature": feature,
            "side": side,
            "state": state,
            "conditions": conditions,
            "value": winner["value"] if winner else None,
            "unit": winner["unit"] if winner else None,
            "status": "conflict"
            if conflicting or rejected
            else "unknown"
            if not winner
            else "provisional"
            if needs_review
            else "literal_observation",
            "needs_review": needs_review,
            "supporting_claim_ids": supporting,
            "conflicting_claim_ids": conflicting,
            "unknown_claim_ids": unknown,
            "constraint_failures": sorted(set(rejected)),
            "support": {
                "independent_group_count": len(groups),
                "groups": groups,
                "literal_source": literal,
                "proof_ancestry": [sorted(f["roots"]) for f in families],
            },
            "explanation": explanations,
            "alternatives": [
                {
                    **{k: v for k, v in c.items() if k != "families"},
                    "independent_groups": [
                        _id("proof_family", sorted(f["roots"])) for f in c["families"]
                    ],
                }
                for c in values
            ],
            "calibration": {
                "method": "source_ancestry_agreement",
                "raw_model_confidence_used": False,
                "empirical_probability": None,
                "status": "not_established",
            },
            "engineering_approved": False,
            "production_released": False,
        }
        if joint:
            resolution["joint_constraint"] = deepcopy(joint)
        if calibration:
            bucket = calibration.get("buckets", {}).get(reliability_bucket(resolution))
            if bucket:
                resolution["calibration"].update(
                    method="clustered_development_reliability",
                    status="measured",
                    **deepcopy(bucket),
                )
                if (
                    not unresolved
                    and not context_missing
                    and len(groups) >= 2
                    and bucket.get("independent_package_count", 0)
                    >= max(
                        POLICY["minimum_calibration_packages"],
                        calibration.get("minimum_packages", 5),
                    )
                    and bucket.get("package_success_lower_bound", 0)
                    >= max(
                        POLICY["minimum_calibration_lower_bound"],
                        calibration.get("acceptance_lower_bound", 0.99),
                    )
                ):
                    resolution.update(needs_review=False, status="corroborated")
        resolutions.append(resolution)
    # A syntactically unambiguous retained raw number cannot disagree with its
    # typed value (e.g. raw 600A -> normalized 60). Retain both and flag it.
    from .ontology import FEATURES, normalize_feature_value

    for resolution in resolutions:
        for claim_id in resolution["supporting_claim_ids"]:
            claim = claims_by_id[claim_id]
            feature_spec = FEATURES.get(claim["feature"])
            raw = claim.get("raw_value")
            if (
                feature_spec
                and feature_spec.value_type == "numeric"
                and isinstance(raw, (str, int, float))
            ):
                parsed = normalize_feature_value(
                    claim["feature"],
                    raw,
                    unit=claim.get("normalization", {}).get("input_unit"),
                )
                if parsed["valid"] and _key(parsed["value"]) != _key(
                    claim.get("value")
                ):
                    _flag(
                        resolution,
                        "Retained raw numeric text disagrees with its normalized value; no digit was corrected automatically.",
                        "raw_numeric_normalization_conflict",
                    )
    # Catalog deductions inherit unresolved product/operating dependencies.
    # They cannot bypass a disputed or unknown source part number.
    by_claim_resolution = {
        cid: r for r in resolutions for cid in r["supporting_claim_ids"]
    }
    for resolution in resolutions:
        for cid in resolution["supporting_claim_ids"]:
            claim = claims_by_id[cid]
            if claim.get("extractor", {}).get("name") != "catalog_decoder":
                continue
            dependencies = [
                by_claim_resolution.get(parent)
                for parent in claim.get("derived_from", [])
            ]
            if any(dep is None or dep["needs_review"] for dep in dependencies):
                _flag(
                    resolution,
                    "Catalog deduction depends on unresolved source evidence; review its product and operating bindings.",
                    "unresolved_catalog_dependency",
                )
    result["resolutions"] = resolutions
    result["catalog_lookups"] = lookups
    result["joint_constraints"] = list(
        {value["id"]: value for value in joint_decisions.values()}.values()
    )
    active_rules = deepcopy(rules if rules is not None else graph.get("rules", []))
    result["constraint_rules"] = catalog_rules + active_rules
    result["rule_checks"] = _evaluate_rules(result, active_rules)
    result["policy"] = deepcopy(POLICY)
    result["calibration"] = deepcopy(
        calibration or {"version": "unfitted", "buckets": {}}
    )
    result["reconciliation"] = {
        "version": VERSION,
        "objective": "bounded local source support with exact trip/frame pair enumeration and explicit graph-rule checks",
        "source_claims_preserved": all(
            claims_by_id[k] == v for k, v in original.items()
        ),
        "original_claim_count": len(original),
        "derived_catalog_claim_count": len(result["claims"]) - len(original),
        "resolution_count": len(resolutions),
        "review_count": sum(r["needs_review"] for r in resolutions),
        "engineering_approved": False,
        "production_released": False,
    }
    return result
