"""Make reconciliation the value-decision authority for existing equipment APIs.

Original reader facts, source-specific observations and old check inputs remain
intact. Canonical values and acceptance come exclusively from the reconciler.
Exact source-native designations join an equipment *record* within one explicit
package/order; this never establishes physical topology or standards compliance.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

from .adapters import adapt_sources
from .reconcile import VERSION, reconcile_graph
from .schema import source_ref

POLICY = {
    "version": VERSION,
    "identity_policy": "cited-company-profile-first; bounded-exact-native-designation-record; no-function-number-aliases",
    "decision_policy": "typed-and-catalog-constrained evidence; retained contradictions; no raw model confidence acceptance",
    "requirements_are_actual_observations": False,
    "engineering_approval_automatic": False,
    "production_release_automatic": False,
}


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def implementation_signature(calibration, contexts):
    """Invalidate decisions when solver/catalog/schema code or inputs change."""
    root = Path(__file__).resolve().parent
    code = {}
    for name in (
        "reconcile.py",
        "catalogs.py",
        "catalogs.json",
        "ontology.py",
        "identity.py",
        "schema.py",
        "calibration.py",
        # Source-local projection validation and exact raw-header typing are
        # decision dependencies even when no new extraction is performed.
        "table_reader.py",
        "../bom_rows.py",
        "adapters.py",
        "integration.py",
    ):
        path = root / name
        code[name] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
    return _hash({"code": code, "calibration": calibration, "source_context": contexts})


def comparison_input_hash(raw):
    return _hash(
        {
            side: {
                key: raw.get(side, {}).get(key, [])
                for key in ("facts", "requested_changes", "sources")
            }
            for side in ("baseline", "target", "requirements")
        }
        | {
            "identity_resolution": raw.get("identity_resolution"),
            "identity_issues": raw.get("identity_issues", []),
        }
    )


def _native_anchor(fact):
    location = fact.get("evidence", {})
    if not isinstance(location, dict):
        return None
    # A source-native attribute, a literal tabular row, or a direct printed PDF
    # tag/value span can bind an equipment-record value. Filenames cannot.
    if not any(
        location.get(k) is not None
        for k in ("cad_handle", "row", "cell", "page", "xml_path")
    ):
        return None
    try:
        return source_ref(
            {"source_id": fact["source_id"], "sha256": fact["sha256"]},
            **location,
            entity="tag:" + str(fact.get("device_tag")),
        )
    except (KeyError, ValueError):
        return None


def _identity_table(raw, scope_id):
    groups = defaultdict(list)
    occurrences = defaultdict(set)
    for side in ("baseline", "target", "requirements"):
        for fact in raw.get(side, {}).get("facts", []) + raw.get(side, {}).get(
            "requested_changes", []
        ):
            tag = fact.get("device_tag")
            anchor = _native_anchor(fact)
            if not isinstance(tag, str) or not tag.strip() or not anchor:
                continue
            groups[(fact["source_id"], tag)].append((fact, anchor))
            loc = fact.get("evidence", {})
            if loc.get("cad_handle"):
                occurrences[(fact["source_id"], tag)].add(("cad", loc["cad_handle"]))
            elif loc.get("row") is not None:
                occurrences[(fact["source_id"], tag)].add(
                    ("row", loc.get("sheet"), loc["row"])
                )
    table = []
    for (sid, tag), observations in groups.items():
        alias = next(
            (
                f
                for f, _ in observations
                if f.get("alias_id") and f.get("physical_device_id")
            ),
            None,
        )
        # A bare function designation is not a unique piece of equipment.
        function_only = bool(
            re.fullmatch(r"(?:[1-9]|[1-9]\d)[A-Z]*", tag, re.IGNORECASE)
        )
        if len(occurrences[(sid, tag)]) > 1 or function_only and not alias:
            continue
        refs = list(
            {json.dumps(ref, sort_keys=True): ref for _, ref in observations}.values()
        )
        canonical = (
            "profile-physical:" + alias["physical_device_id"]
            if alias
            else "exact-native-designation-record:" + tag
        )
        table.append(
            {
                "source_id": sid,
                "tag": tag,
                "canonical_id": canonical,
                "verified": True,
                "scope_key": scope_id,
                "evidence": refs,
                "physical_identity_verified": bool(alias),
            }
        )
    return table


def _tag_decisions(graph):
    nodes = {node["id"]: node for node in graph["nodes"]}
    values = []
    for resolution in graph.get("resolutions", []):
        node = nodes.get(resolution["subject_id"], {})
        tag = node.get("identifiers", {}).get("raw_tag")
        if tag:
            values.append(
                {
                    **deepcopy(resolution),
                    "device_tag": tag,
                    "identity_verified": bool(
                        node.get("metadata", {}).get("identity_verified")
                    ),
                    "physical_identity_verified": bool(
                        node.get("metadata", {}).get("physical_identity_verified")
                    ),
                }
            )
    return values


def decision_for(raw, tag, feature, side="target", state="observed"):
    candidates = [
        item
        for item in raw.get("decisions", [])
        if item.get("device_tag") == tag
        and item.get("feature") == feature
        and item.get("side") == side
        and item.get("state") == state
    ]
    if len(candidates) == 1:
        return deepcopy(candidates[0])
    if not candidates:
        return None
    # Separate entities or rating-condition contexts are not made equivalent by
    # sharing an exact printed token. Retain the alternatives for the PM.
    return {
        "device_tag": tag,
        "feature": feature,
        "side": side,
        "state": state,
        "value": None,
        "unit": None,
        "status": "identity_or_condition_ambiguous",
        "needs_review": True,
        "supporting_claim_ids": [],
        "conflicting_claim_ids": sorted(
            {
                cid
                for c in candidates
                for cid in c.get("supporting_claim_ids", [])
                + c.get("conflicting_claim_ids", [])
            }
        ),
        "explanation": [
            "The printed designation has multiple unresolved equipment identities or rating conditions."
        ],
        "alternatives": candidates,
        "engineering_approved": False,
        "production_released": False,
    }


def _decision_view(view, decision):
    result = deepcopy(view or {})
    if decision is not None:
        result["raw_values"] = deepcopy(result.get("values", []))
        result["canonical_value"] = decision.get("value")
        result["canonical_unit"] = decision.get("unit")
        result["value_status"] = "source_observation"
        result["decision"] = deepcopy(decision)
        result["needs_review"] = decision.get("needs_review", True)
    return result


def reconcile_comparison(raw, *, scope_id=None, source_context=None, calibration=None):
    result = deepcopy(raw)
    input_hash = comparison_input_hash(result)
    scope_id = scope_id or "package:" + input_hash
    sources, contexts = [], deepcopy(source_context or {})
    for side in ("baseline", "target", "requirements"):
        for source in result.get(side, {}).get("sources", []):
            sid = source.get("id", source.get("source_id"))
            sources.append(source)
            contexts[sid] = {
                "side": side,
                "role": source.get("role"),
                **contexts.get(sid, {}),
            }
    graph = adapt_sources(
        sources=sources,
        rawcomparison=result,
        scope_id=scope_id,
        source_context=contexts,
        verified_identities=_identity_table(result, scope_id),
        metadata={"comparison_input_hash": input_hash},
    )
    for node in graph["nodes"]:
        key = node.get("metadata", {}).get("identity_key", "")
        node["metadata"]["physical_identity_verified"] = key.startswith(
            "profile-physical:"
        )
        if key.startswith("exact-native-designation-record:"):
            node["metadata"]["identity_basis"] = (
                "source-native equipment designation record; not physical topology"
            )
    graph["policy"] = deepcopy(POLICY)
    graph["calibration"] = deepcopy(
        calibration
        or {
            "status": "not_established",
            "buckets": {},
            "minimum_packages": 5,
            "acceptance_lower_bound": 0.99,
        }
    )
    graph = reconcile_graph(graph, calibration=calibration)
    graph["metadata"]["source_context"] = deepcopy(contexts)
    graph["metadata"]["implementation_signature"] = implementation_signature(
        graph["calibration"], contexts
    )
    graph["policy"].update({"identity_policy": POLICY["identity_policy"]})
    result["evidence_graph"] = graph
    result["reconciliation"] = graph.get("reconciliation", {})
    result["decisions"] = _tag_decisions(graph)
    for check in result.get("checks", []):
        tag, feature = check.get("device_tag"), check.get("field")
        target = decision_for(result, tag, feature)
        baseline = decision_for(result, tag, feature, "baseline")
        required = decision_for(result, tag, feature, "requirements")
        check["decision"] = target or {
            "value": None,
            "status": "no_value_decision",
            "needs_review": True,
        }
        for key, decision in (
            ("baseline_drawing", baseline),
            ("baseline_bom", baseline),
            ("target_drawing", target),
            ("target_bom", target),
            ("required", required),
        ):
            # Keep the source role empty if no supporting literal existed. A
            # reconciled drawing value cannot fill a missing BOM observation.
            if check.get(key, {}).get("evidence"):
                check[key] = _decision_view(check[key], decision)
        if feature == "device_presence":
            continue
        if target is None:
            if check.get("status") not in {"mismatch", "missing"}:
                check["status"] = "unknown"
            check["needs_review"] = True
            check["reason"] = (
                "No actual equipment value decision exists; a specification or request cannot fill the missing observation."
            )
            check["summary"] = check["reason"]
            continue
        previous = check.get("status", "unknown")
        governing_ambiguous = (
            previous == "unknown"
            and "specification and requested values conflict"
            in str(check.get("reason", "")).lower()
        )
        if governing_ambiguous:
            reason = "The specification and requested values conflict; a person must establish which requirement governs."
        elif target.get("status") == "conflict" or target.get("constraint_failures"):
            check["status"] = "mismatch"
            reason = (
                "Conflicting or constraint-invalid equipment evidence requires review."
            )
        elif target.get("needs_review", True) or target.get("value") is None:
            check["status"] = (
                previous if previous in {"missing", "mismatch"} else "unknown"
            )
            reason = "The equipment value has not met the evidence requirements for acceptance."
        else:
            reason = None
        if reason:
            check["reason"] = reason
            check["summary"] = reason
        check["needs_review"] = bool(
            target.get("needs_review", True)
            or check["status"] not in {"verified", "unchanged"}
        )
    counts = Counter(check["status"] for check in result.get("checks", []))
    summary = result.setdefault("summary", {})
    summary.update(
        {
            key: counts[key]
            for key in ("verified", "unchanged", "mismatch", "missing", "unknown")
        }
    )
    summary.update(
        remaining=counts["mismatch"] + counts["missing"],
        cannot_verify=counts["unknown"],
        total=len(result.get("checks", [])),
        reconciliation_review_count=sum(
            d.get("needs_review", True) for d in result["decisions"]
        ),
        reconciliation_version=VERSION,
    )
    if summary["remaining"]:
        summary["outcome"] = "changes_remaining"
    elif summary["cannot_verify"] or summary.get("source_issues"):
        summary["outcome"] = "cannot_verify"
    pending_rules = [
        check for check in graph.get("rule_checks", []) if check.get("needs_review")
    ]
    summary["graph_rule_review_count"] = len(pending_rules)
    # A graph conflict cannot turn an unresolved governing requirement into a
    # definite missed edit. Preserve the explicit unknown until it is resolved.
    if (
        pending_rules
        and not summary["remaining"]
        and not summary["cannot_verify"]
        and not summary.get("source_issues")
    ):
        summary["outcome"] = (
            "changes_remaining"
            if any(check.get("status") == "violated" for check in pending_rules)
            else "cannot_verify"
        )
    summary["revision_changes_verified"] = (
        summary.get("outcome") == "supported_fields_match"
        and not summary["cannot_verify"]
    )
    summary["production_release_verified"] = False
    result["reconciliation_projection_signature"] = projection_signature(result)
    return result


def projection_signature(raw):
    return _hash(
        {
            key: raw.get(key)
            for key in ("evidence_graph", "decisions", "checks", "summary")
        }
    )


def ensure_reconciled(raw, *, source_context=None, calibration=None):
    """Invalidate stale raw evidence, solver/catalog policy and calibration."""
    graph = raw.get("evidence_graph", {})
    metadata = graph.get("metadata", {})
    contexts = (
        source_context
        if source_context is not None
        else metadata.get("source_context", {})
    )
    fitted = calibration if calibration is not None else graph.get("calibration")
    if (
        metadata.get("comparison_input_hash") == comparison_input_hash(raw)
        and metadata.get("implementation_signature")
        == implementation_signature(fitted, contexts)
        and raw.get("reconciliation_projection_signature") == projection_signature(raw)
    ):
        return raw
    return reconcile_comparison(
        raw, scope_id=graph.get("scope_id"), source_context=contexts, calibration=fitted
    )
