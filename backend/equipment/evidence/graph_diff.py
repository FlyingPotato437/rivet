"""Diff reconciled equipment without treating an unobserved device as absent.

This compares model assertions, not the engineering acceptability of a change.
Identity and completeness are explicit inputs. An incomplete target cannot prove
a removal, and matching section positions across issues requires a correspondence.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pymupdf

_EQUIPMENT_KINDS = {"device", "equipment", "component", "breaker", "protective_device"}
_CONNECTION_KINDS = {
    "connected_to",
    "connection",
    "wire_connection",
    "terminal_connectivity_node",
    "equipment_terminal",
    "connects",
    "terminal_connection",
}
_RATING = re.compile(
    r"current|frame|trip|voltage|interrupt|kaic|poles|rating|amp", re.IGNORECASE
)


def _node_kind(node):
    return str(node.get("kind", "")).lower().rsplit(".", 1)[-1]


def _is_equipment(node):
    return _node_kind(node) in _EQUIPMENT_KINDS


def _identity(node):
    identifiers = node.get("identifiers") or {}
    meta = node.get("metadata") or {}
    if meta.get("source_local") or meta.get("identity_ambiguous"):
        return None
    # Whitespace and case normalization preserve separators. BKR-12 is not
    # automatically interchangeable with ANSI 52-12, and 1086M is not 86M.
    tag = identifiers.get("raw_tag") or identifiers.get("normalized_tag")
    scope = node.get("scope_key")
    if not scope or not tag:
        return None
    # A repeated tag in a different physical lineup is a different identity.
    # A missing lineup does not match a known lineup; renames need the explicit
    # cited identity correspondence above, never a guessed string equivalence.
    lineup = node.get("lineup_id") or meta.get("lineup_identity_key")
    return (
        str(scope),
        str(lineup) if lineup is not None else None,
        re.sub(r"\s+", " ", str(tag).strip()).upper(),
        _node_kind(node),
    )


def _resolutions(model, side=None, state="observed"):
    groups = {}
    for item in model.get("resolutions", []):
        if item.get("state", "observed") != state or item.get("side") == "requirements":
            continue
        if side is not None and item.get("side", "target") != side:
            continue
        subject = item.get("subject_id", item.get("entity_id"))
        if subject and item.get("feature"):
            groups.setdefault((subject, item["feature"]), []).append(item)
    result = {}
    for key, items in groups.items():
        strata = {
            json.dumps(
                {
                    "side": i.get("side", "target"),
                    "conditions": i.get("conditions", {}),
                    "value": i.get("value"),
                    "unit": i.get("unit"),
                },
                sort_keys=True,
            )
            for i in items
        }
        if len(strata) == 1:
            result[key] = {
                **items[0],
                "needs_review": any(i.get("needs_review", True) for i in items),
            }
        else:
            result[key] = {
                "subject_id": key[0],
                "feature": key[1],
                "value": None,
                "status": "ambiguous",
                "needs_review": True,
                "reason": "Multiple snapshot/condition strata require an explicit comparison scope.",
            }
    return result


def _usable(resolution):
    return (
        resolution.get("value") is not None
        and not resolution.get("needs_review", True)
        and resolution.get("status")
        not in {"unknown", "conflict", "ambiguous", "rejected"}
    )


def _candidate_usable(resolution):
    return bool(
        resolution
        and resolution.get("value") is not None
        and resolution.get("status")
        not in {"unknown", "conflict", "ambiguous", "rejected"}
        and not resolution.get("conflicting_claim_ids")
        and not resolution.get("unknown_claim_ids")
        and not resolution.get("constraint_failures")
        and len(resolution.get("alternatives", [resolution])) == 1
    )


def _comparable(item):
    value, unit = item.get("value"), str(item.get("unit") or "").lower()
    if isinstance(value, bool):
        return value, unit
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value, unit
    conversion = {
        "ma": ("a", Decimal(".001")),
        "ka": ("a", Decimal(1000)),
        "kv": ("v", Decimal(1000)),
    }
    if unit in conversion:
        unit, factor = conversion[unit]
        number *= factor
    return number, unit


def _complete(model, facet, scope):
    coverage = model.get("coverage") or {}
    if not coverage.get(facet, False):
        return False
    scoped = model.get("scope_coverage") or []
    if scoped:
        matches = [s for s in scoped if s.get("scope_key") == scope]
        return bool(matches) and all(s.get(facet, False) for s in matches)
    return True


def _references(model, node_id, resolution=None):
    claim_ids = set((resolution or {}).get("supporting_claim_ids") or [])
    claims = [
        c
        for c in model.get("claims", [])
        if c.get("subject_id") == node_id
        and (not claim_ids or c.get("id") in claim_ids)
    ]
    refs = [c["source"] for c in claims if isinstance(c.get("source"), dict)]
    if not refs:
        refs = next(
            (
                n.get("source_refs", [])
                for n in model.get("nodes", [])
                if n.get("id") == node_id
            ),
            [],
        )
    return refs


def _crop(refs, sources, output, name):
    """Crop only hash-bound, physical-page provenance; unavailable is explicit."""
    if output is None:
        return None
    for ref in refs:
        source = sources.get(ref.get("source_id"))
        if not source or not ref.get("page") or not ref.get("bbox"):
            continue
        path = Path(source["path"] if isinstance(source, dict) else source)
        expected = ref.get("sha256") or (
            source.get("sha256") if isinstance(source, dict) else None
        )
        if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Graph-diff crop source hash mismatch or missing hash.")
        bbox = ref["bbox"]
        if len(bbox) != 4 or not (
            0 <= bbox[0] < bbox[2] <= 1 and 0 <= bbox[1] < bbox[3] <= 1
        ):
            continue
        with pymupdf.open(path) as doc:
            page = doc[int(ref["page"]) - 1]
            area = pymupdf.Rect(
                bbox[0] * page.rect.width,
                bbox[1] * page.rect.height,
                bbox[2] * page.rect.width,
                bbox[3] * page.rect.height,
            )
            dest = output / f"{name}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=area, alpha=False).save(
                dest
            )
        return str(dest)
    return None


def compare_reconciled_models(
    before,
    after,
    *,
    matches=None,
    scope_matches=None,
    output_dir=None,
    sources=None,
    before_side=None,
    after_side=None,
    state="observed",
):
    """Return conservative model changes and separately listed unknown candidates.

    ``matches`` explicitly maps old node IDs to new IDs. Callers must supply
    provenance using ``identity_matches`` records for source-local correspondences;
    a bare mapping is accepted only for already non-local exact identities.
    ``scope_matches`` cannot itself establish that two device tags are the same.
    """
    output = Path(output_dir) if output_dir else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    sources, scope_matches = sources or {}, scope_matches or {}
    old = {n["id"]: n for n in before.get("nodes", [])}
    new = {n["id"]: n for n in after.get("nodes", [])}
    pairs, used, unknown = {}, set(), []
    ambiguous_old, ambiguous_new = set(), set()
    verified = list(before.get("identity_matches", [])) + list(
        after.get("identity_matches", [])
    )
    for item in verified:
        if (
            item.get("verified")
            and item.get("source_refs")
            and item.get("from") in old
            and item.get("to") in new
        ):
            if item["from"] in pairs or item["to"] in used:
                unknown.append(
                    {
                        "kind": "conflicting_identity_correspondence",
                        "before_node_id": item["from"],
                        "after_node_id": item["to"],
                        "needs_review": True,
                    }
                )
                continue
            pairs[item["from"]] = item["to"]
            used.add(item["to"])
    for old_id, new_id in (matches or {}).items():
        if (
            old_id in old
            and new_id in new
            and _identity(old[old_id])
            and _identity(new[new_id])
        ):
            a, b = _identity(old[old_id]), _identity(new[new_id])
            if (
                old_id not in pairs
                and new_id not in used
                and (scope_matches.get(a[0], a[0]), *a[1:]) == b
            ):
                pairs[old_id] = new_id
                used.add(new_id)
    index = {}
    for node in new.values():
        ident = _identity(node)
        if ident:
            index.setdefault(ident, []).append(node["id"])
    old_counts = {}
    for node in old.values():
        ident = _identity(node)
        if ident:
            old_counts[ident] = old_counts.get(ident, 0) + 1
    for node in old.values():
        ident = _identity(node)
        if node["id"] in pairs or not ident:
            continue
        target_ident = (scope_matches.get(ident[0], ident[0]), *ident[1:])
        candidates = index.get(target_ident, [])
        if (
            len(candidates) == 1
            and old_counts[ident] == 1
            and candidates[0] not in used
        ):
            pairs[node["id"]] = candidates[0]
            used.add(candidates[0])
        elif candidates:
            ambiguous_old.add(node["id"])
            ambiguous_new.update(candidates)
            unknown.append(
                {
                    "kind": "ambiguous_identity",
                    "before_node_id": node["id"],
                    "candidate_node_ids": candidates,
                    "needs_review": True,
                }
            )
    changes, candidate_changes = [], []
    old_res, new_res = (
        _resolutions(before, before_side, state),
        _resolutions(after, after_side, state),
    )

    def emit(
        kind,
        old_id=None,
        new_id=None,
        old_value=None,
        new_value=None,
        feature=None,
        old_resolution=None,
        new_resolution=None,
        candidate=False,
    ):
        before_refs = _references(before, old_id, old_resolution) if old_id else []
        after_refs = _references(after, new_id, new_resolution) if new_id else []
        item = {
            "kind": kind,
            "before_node_id": old_id,
            "after_node_id": new_id,
            "feature": feature,
            "before": old_value,
            "after": new_value,
            "before_sources": before_refs,
            "after_sources": after_refs,
            "status": "candidate_reconciled_delta"
            if candidate
            else "reconciled_model_change",
            "engineering_approved": False,
            "needs_review": True,
        }
        count = len(changes) + len(candidate_changes) + 1
        item["before_crop"] = _crop(
            before_refs, sources, output, f"change-{count}-before"
        )
        item["after_crop"] = _crop(after_refs, sources, output, f"change-{count}-after")
        (candidate_changes if candidate else changes).append(item)

    for old_id, new_id in pairs.items():
        features = {f for sid, f in old_res if sid == old_id} | {
            f for sid, f in new_res if sid == new_id
        }
        for feature in sorted(features):
            a, b = old_res.get((old_id, feature)), new_res.get((new_id, feature))
            if not a or not b or not _usable(a) or not _usable(b):
                if (
                    _candidate_usable(a)
                    and _candidate_usable(b)
                    and _comparable(a) != _comparable(b)
                ):
                    emit(
                        "rating_changed"
                        if _RATING.search(feature)
                        else "attribute_changed",
                        old_id,
                        new_id,
                        a.get("value"),
                        b.get("value"),
                        feature,
                        a,
                        b,
                        candidate=True,
                    )
                unknown.append(
                    {
                        "kind": "attribute_not_comparable",
                        "before_node_id": old_id,
                        "after_node_id": new_id,
                        "feature": feature,
                        "reason": "A value is absent, conflicting, or requires review.",
                        "needs_review": True,
                    }
                )
            elif _comparable(a) != _comparable(b):
                emit(
                    "rating_changed"
                    if _RATING.search(feature)
                    else "attribute_changed",
                    old_id,
                    new_id,
                    a.get("value"),
                    b.get("value"),
                    feature,
                    a,
                    b,
                )
    for node_id, node in old.items():
        if not _is_equipment(node) or node_id in pairs or node_id in ambiguous_old:
            continue
        if _identity(node) and _complete(
            after,
            "identity_complete",
            scope_matches.get(node.get("scope_key"), node.get("scope_key")),
        ):
            emit("device_removed", node_id, old_value=node.get("identifiers"))
        else:
            unknown.append(
                {
                    "kind": "possible_device_removed",
                    "before_node_id": node_id,
                    "reason": "Target identity coverage is incomplete or device identity is source-local.",
                    "needs_review": True,
                }
            )
    for node_id, node in new.items():
        if not _is_equipment(node) or node_id in used or node_id in ambiguous_new:
            continue
        old_scope = next(
            (a for a, b in scope_matches.items() if b == node.get("scope_key")),
            node.get("scope_key"),
        )
        if _identity(node) and _complete(before, "identity_complete", old_scope):
            emit("device_added", new_id=node_id, new_value=node.get("identifiers"))
        else:
            unknown.append(
                {
                    "kind": "possible_device_added",
                    "after_node_id": node_id,
                    "reason": "Baseline identity coverage is incomplete or device identity is source-local.",
                    "needs_review": True,
                }
            )

    # Edges with an unmapped endpoint cannot be compared. Containment is not an
    # electrical connection, and terminals without stable identity remain unknown.
    inverse = {b: a for a, b in pairs.items()}
    old_edges, new_edges = {}, {}
    for model, mapping, target in (
        (before, pairs, old_edges),
        (after, inverse, new_edges),
    ):
        for edge in model.get("edges", []):
            if edge.get("kind") not in _CONNECTION_KINDS:
                continue
            if edge.get("from") not in mapping or edge.get("to") not in mapping:
                unknown.append(
                    {
                        "kind": "connection_not_comparable",
                        "edge_id": edge.get("id"),
                        "reason": "A terminal or node lacks a proven cross-issue identity.",
                        "needs_review": True,
                    }
                )
                continue
            ends = (
                (edge["from"], edge["to"])
                if model is before
                else (mapping[edge["from"]], mapping[edge["to"]])
            )
            # Connectivity is undirected. Both endpoint identities are retained.
            target[(edge["kind"], *sorted(ends))] = edge
    for signature in old_edges.keys() - new_edges.keys():
        edge = old_edges[signature]
        scope = old[edge["from"]].get("scope_key")
        if _complete(after, "connectivity_complete", scope_matches.get(scope, scope)):
            emit(
                "connection_removed",
                edge["from"],
                pairs[edge["from"]],
                old_value={"from": edge["from"], "to": edge["to"]},
            )
        else:
            unknown.append(
                {
                    "kind": "possible_connection_removed",
                    "edge_id": edge.get("id"),
                    "reason": "Target connectivity coverage is incomplete.",
                    "needs_review": True,
                }
            )
    for signature in new_edges.keys() - old_edges.keys():
        edge = new_edges[signature]
        scope = new[edge["from"]].get("scope_key")
        old_scope = next((a for a, b in scope_matches.items() if b == scope), scope)
        if _complete(before, "connectivity_complete", old_scope):
            emit(
                "connection_added",
                inverse[edge["from"]],
                edge["from"],
                new_value={"from": edge["from"], "to": edge["to"]},
            )
        else:
            unknown.append(
                {
                    "kind": "possible_connection_added",
                    "edge_id": edge.get("id"),
                    "reason": "Baseline connectivity coverage is incomplete.",
                    "needs_review": True,
                }
            )
    return {
        "schema_version": "rivet.reconciled-graph-diff.v1",
        "changes": changes,
        "candidate_changes": candidate_changes,
        "unknowns": unknown,
        "identity_matches": pairs,
        "coverage": {
            "before": before.get("coverage", {}),
            "after": after.get("coverage", {}),
        },
        "engineering_verified": False,
        "limitations": [
            "A model change does not approve an equipment or engineering change.",
            "Absence is asserted only with explicit complete coverage in the opposite issue.",
        ],
    }
