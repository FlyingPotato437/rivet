"""Runtime authority controls; these cases do not count as real accuracy."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.equipment.evidence.integration import decision_for, reconcile_comparison
from backend.equipment.model import compile_model
from backend.equipment.service import normalize_comparison

HASHES = {"drawing": "a" * 64, "bom": "b" * 64, "old": "c" * 64, "spec": "d" * 64}


def _fact(source, role, feature, value, *, tag="CB-12", raw=None):
    return {
        "id": f"{source}:{feature}",
        "source_id": source,
        "source_name": source + ".pdf",
        "sha256": HASHES[source],
        "role": role,
        "revision": "D",
        "lifecycle": "draft",
        "device_tag": tag,
        "field": feature,
        "value": value,
        "unit": "A" if feature in {"trip_a", "frame_a"} else None,
        "raw_value": str(value) if raw is None else raw,
        "evidence": {"page": 1, "bbox": [0.1, 0.1, 0.8, 0.2]},
    }


def _raw(trip=600, frame=250, *, tag="CB-12"):
    drawing = [
        _fact("drawing", "drawing", "trip_a", trip, tag=tag),
        _fact("drawing", "drawing", "frame_a", frame, tag=tag),
    ]
    bom = [
        _fact("bom", "bom", "trip_a", trip, tag=tag),
        _fact("bom", "bom", "frame_a", frame, tag=tag),
    ]
    old = [_fact("old", "drawing", "trip_a", 100, tag=tag)]

    def analysis(facts):
        sources = [
            {
                "id": f["source_id"],
                "sha256": f["sha256"],
                "role": f["role"],
                "revision": "D",
                "status": "parsed",
                "observed_device_tags": [tag],
                "warnings": [],
            }
            for f in facts
        ]
        return {
            "facts": facts,
            "requested_changes": [],
            "sources": list({x["id"]: x for x in sources}.values()),
            "devices": [{"tag": tag}],
        }

    def view(facts):
        return {
            "value": trip,
            "unit": "A",
            "values": [{"value": trip, "unit": "A"}],
            "evidence": facts,
        }

    return {
        "baseline": analysis(old),
        "target": analysis(drawing + bom),
        "requirements": analysis([]),
        "checks": [
            {
                "id": "check",
                "device_tag": tag,
                "field": "trip_a",
                "status": "verified",
                "reason": "legacy equal values",
                "baseline_drawing": view(old),
                "baseline_bom": {},
                "target_drawing": view([drawing[0]]),
                "target_bom": view([bom[0]]),
                "required": {},
                "evidence": old + drawing + bom,
            }
        ],
        "summary": {"outcome": "supported_fields_match", "source_issues": 0},
        "coverage": {"complete_inventory_verified": False},
    }


def test_constraint_decision_replaces_legacy_equal_value_success_without_rewriting_sources():
    raw = _raw()
    original = deepcopy(raw)
    result = reconcile_comparison(raw, scope_id="owned-order:123")
    decision = decision_for(result, "CB-12", "trip_a")
    assert decision["value"] is None and decision["needs_review"]
    assert result["checks"][0]["status"] == "mismatch"
    assert result["summary"]["outcome"] == "changes_remaining"
    assert result["checks"][0]["target_drawing"]["value"] == 600
    assert result["checks"][0]["target_drawing"]["canonical_value"] is None
    assert raw == original
    assert result["target"]["facts"] == original["target"]["facts"]


def test_current_values_do_not_take_votes_from_previous_issue_or_specification():
    raw = _raw(trip=60, frame=250)
    raw["requirements"] = {
        "facts": [
            _fact("spec", "specification", "description", "Only historical context")
        ],
        "sources": [
            {
                "id": "spec",
                "sha256": HASHES["spec"],
                "role": "specification",
                "status": "parsed",
            }
        ],
        "requested_changes": [],
    }
    result = reconcile_comparison(raw, scope_id="owned-order:123")
    target = decision_for(result, "CB-12", "trip_a")
    baseline = decision_for(result, "CB-12", "trip_a", "baseline")
    assert target["value"] == 60 and baseline["value"] == 100
    assert all(
        result["evidence_graph"]["claims"][i]["state"] == "observed"
        for i in range(len(result["evidence_graph"]["claims"]))
    )
    assert (
        len({c["conditions"]["side"] for c in result["evidence_graph"]["claims"]}) == 3
    )


def test_function_number_does_not_create_a_cross_file_physical_identity():
    result = reconcile_comparison(
        _raw(trip=60, frame=250, tag="52"), scope_id="owned-order:123"
    )
    decision = decision_for(result, "52", "trip_a")
    assert decision["value"] is None
    assert decision["status"] == "identity_or_condition_ambiguous"
    assert decision["needs_review"]
    assert result["checks"][0]["status"] == "unknown"
    assert not any(
        node["metadata"].get("physical_identity_verified")
        for node in result["evidence_graph"]["nodes"]
    )


def test_compiled_model_uses_canonical_decision_and_recomputes_after_new_raw_evidence():
    reconciled = reconcile_comparison(
        _raw(trip=60, frame=250), scope_id="owned-order:123"
    )
    changed = deepcopy(reconciled)
    for fact in changed["target"]["facts"]:
        if fact["field"] == "trip_a":
            fact["value"] = 600
            fact["raw_value"] = "600"
    model = compile_model(changed, "e" * 64, "C", "D")
    field = model["devices"][0]["fields"]["trip_a"]
    assert field["status"] == "mismatch"
    assert field["decision"]["value"] is None and field["decision"]["needs_review"]
    assert {x["value"] for x in field["build"]} == {600}
    assert model["evidence_graph"]["policy"]["version"]
    assert model["reconciliation"]["source_claims_preserved"]
    assert not model["engineering_approved"] and not model["production_released"]


def test_normalized_api_response_exposes_canonical_outcome_and_raw_source_view_separately():
    result = reconcile_comparison(_raw(), scope_id="owned-order:123")
    report = normalize_comparison(
        SimpleNamespace(id="123"), result, [], "C", "D", "f" * 64
    )
    check = report["checks"][0]
    assert check["status"] == "mismatch"
    assert check["decision"]["value"] is None
    assert check["target_drawing"]["value"] == "600"
    assert check["target_drawing"]["decision"]["needs_review"]
    assert report["summary"]["outcome"] == "changes_remaining"
    assert report["reconciliation"]["source_claims_preserved"]


def test_distinct_order_scopes_cannot_share_an_equipment_subject():
    first = reconcile_comparison(_raw(), scope_id="order:company-a:one")
    second = reconcile_comparison(_raw(), scope_id="order:company-a:two")
    assert {n["id"] for n in first["evidence_graph"]["nodes"]}.isdisjoint(
        {n["id"] for n in second["evidence_graph"]["nodes"]}
    )


@pytest.fixture(autouse=True)
def database():
    """These pure source/decision controls do not use a database."""
    yield
