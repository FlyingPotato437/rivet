"""Pure cache dependency controls; no source documents or database access."""
from copy import deepcopy

import pytest

from backend.equipment.evidence import integration


@pytest.fixture(autouse=True)
def database():
    yield


DEPENDENCIES = (
    "reconcile.py", "catalogs.py", "catalogs.json", "ontology.py", "identity.py",
    "schema.py", "calibration.py", "table_reader.py", "../bom_rows.py", "adapters.py", "integration.py",
)


def _code_tree(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    root.mkdir()
    for name in DEPENDENCIES:
        (root / name).write_text("original synthetic module content")
    monkeypatch.setattr(integration, "__file__", str(root / "integration.py"))
    return root


@pytest.mark.parametrize("dependency", DEPENDENCIES)
def test_every_actual_decision_dependency_invalidates_signature(tmp_path, monkeypatch, dependency):
    root = _code_tree(tmp_path, monkeypatch)
    before = integration.implementation_signature({"version": "control"}, {"s": {"side": "target"}})
    (root / dependency).write_text("updated synthetic module content")
    after = integration.implementation_signature({"version": "control"}, {"s": {"side": "target"}})
    assert before != after


def test_unchanged_dependencies_are_stable_and_unrelated_code_is_ignored(tmp_path, monkeypatch):
    root = _code_tree(tmp_path, monkeypatch)
    before = integration.implementation_signature({}, {})
    (root / "unrelated_ui.py").write_text("irrelevant synthetic UI")
    assert integration.implementation_signature({}, {}) == before


def test_source_context_and_fitted_calibration_are_independent_invalidation_inputs(tmp_path, monkeypatch):
    _code_tree(tmp_path, monkeypatch)
    before = integration.implementation_signature({"version": "fit1"}, {"s": {"side": "target"}})
    assert integration.implementation_signature({"version": "fit2"}, {"s": {"side": "target"}}) != before
    assert integration.implementation_signature({"version": "fit1"}, {"s": {"side": "baseline"}}) != before


def test_dependency_change_forces_ensure_reconciled_to_rebuild_cached_decisions(tmp_path, monkeypatch):
    root = _code_tree(tmp_path, monkeypatch)
    calibration, context = {"version": "control"}, {"s": {"side": "target"}}
    raw = {"baseline": {"facts": []}, "target": {"facts": []}, "requirements": {"facts": []}}
    raw["evidence_graph"] = {
        "scope_id": "order:control", "calibration": calibration,
        "metadata": {"source_context": context,
                     "comparison_input_hash": integration.comparison_input_hash(raw),
                     "implementation_signature": integration.implementation_signature(calibration, context)},
    }
    raw["reconciliation_projection_signature"] = integration.projection_signature(raw)
    rebuilt = {"sentinel": "new canonical decision"}
    calls = []
    def rebuild(value, **kwargs):
        calls.append((deepcopy(value), kwargs))
        return rebuilt
    monkeypatch.setattr(integration, "reconcile_comparison", rebuild)
    assert integration.ensure_reconciled(raw) is raw and not calls
    (root / "identity.py").write_text("updated synthetic identity policy")
    assert integration.ensure_reconciled(raw) is rebuilt
    assert calls[0][1] == {"scope_id": "order:control", "source_context": context, "calibration": calibration}
