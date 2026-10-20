"""Release freshness controls, not real-document accuracy or engineering approval."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.equipment import service
from backend.equipment.evidence.integration import reconcile_comparison


@pytest.fixture(autouse=True)
def database():
    """Pure gate controls never rebuild or modify a database."""
    yield


def _comparison(trip):
    sources, facts = [], []
    for sid, role, digest in [("drawing", "drawing", "a" * 64), ("bom", "bom", "b" * 64)]:
        sources.append({"id": sid, "sha256": digest, "role": role, "revision": "B", "status": "parsed"})
        for feature, value in [("trip_a", trip), ("frame_a", 250)]:
            facts.append({"id": sid + feature, "source_id": sid, "sha256": digest, "role": role, "revision": "B",
                          "device_tag": "CB-12", "field": feature, "value": value, "unit": "A", "raw_value": str(value),
                          "evidence": {"page": 1, "row": 1}})
    return {
        "baseline": {"facts": [], "sources": [], "requested_changes": []},
        "target": {"facts": facts, "sources": sources, "requested_changes": []},
        "requirements": {"facts": [], "sources": [], "requested_changes": []},
        "checks": [{"id": "check", "device_tag": "CB-12", "field": "trip_a", "status": "verified"}],
        "summary": {"outcome": "supported_fields_match", "source_issues": 0},
        "coverage": {"baseline_comparison_available": True},
    }


def _gate(monkeypatch, raw):
    order = SimpleNamespace(state={"equipment_check": {"fingerprint": "same-originals"}})
    monkeypatch.setattr(service, "documents", lambda session, order: [])
    monkeypatch.setattr(service, "selected_sources", lambda session, order: (
        [], [{"revision": "A"}, {"revision": "B"}], {"baseline": [], "target": []}, "A", "B", "same-originals"))
    monkeypatch.setattr(service, "raw_for_order", lambda *args: raw)
    return service.release_blockers(None, order)


def test_obsolete_cached_success_cannot_bypass_current_constraint_policy(monkeypatch):
    raw = reconcile_comparison(_comparison(600), scope_id="order:test:one")
    raw["summary"].update(outcome="supported_fields_match", revision_changes_verified=True)
    raw["evidence_graph"]["metadata"]["implementation_signature"] = "obsolete-policy"
    before = deepcopy(raw)
    blockers = _gate(monkeypatch, raw)
    assert blockers and "could not be verified" in blockers[0]
    assert raw == before


def test_changed_raw_facts_cannot_keep_cached_supported_summary(monkeypatch):
    raw = reconcile_comparison(_comparison(60), scope_id="order:test:one")
    assert raw["summary"]["outcome"] == "supported_fields_match"
    for fact in raw["target"]["facts"]:
        if fact["field"] == "trip_a": fact.update(value=600, raw_value="600")
    assert _gate(monkeypatch, raw)


def test_unchanged_current_supported_comparison_keeps_existing_gate_behavior(monkeypatch):
    raw = reconcile_comparison(_comparison(60), scope_id="order:test:one")
    assert raw["summary"]["revision_changes_verified"]
    assert _gate(monkeypatch, raw) == []


def test_tampered_success_projection_does_not_authorize_release(monkeypatch):
    raw = reconcile_comparison(_comparison(600), scope_id="order:test:one")
    raw["summary"].update(outcome="supported_fields_match", revision_changes_verified=True)
    # The policy signature is current; projection integrity must still catch it.
    assert _gate(monkeypatch, raw)
