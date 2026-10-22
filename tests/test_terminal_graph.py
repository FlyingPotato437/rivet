"""Synthetic graph controls, not manufacturer electrical accuracy."""

import pytest

from backend.engineering.terminal_graph import audit


@pytest.fixture(autouse=True)
def database():
    yield


A, B, C = ("PNL", "L1"), ("PNL", "C1_A"), ("PNL", "L2")


def edge(a=A, b=B, verified=True):
    return {"a": a, "b": b, "source": "cad:123", "geometry_verified": verified}


def run(edges, *, complete=True, rules=None):
    return audit(
        [A, B, C],
        edges,
        rules
        or [
            {"id": "feed", "operator": "connected", "a": A, "b": B},
            {"id": "separate", "operator": "isolated", "a": A, "b": C},
        ],
        coverage_complete=complete,
        source_sha256="a" * 64,
    )


def test_actual_graph_paths_have_locators_and_cross_phase_short_fails():
    good = run([edge()])
    assert good["all_rules_passed"]
    assert good["laplacian_rank"] == 1 and good["cycle_rank"] == 0
    assert good["findings"][0]["evidence"] == ["cad:123"]
    bad = run([edge(), edge(B, C)])
    assert bad["findings"][1]["status"] == "fail"
    assert bad["findings"][1]["evidence"]


def test_missing_geometry_is_unknown_instead_of_a_pass():
    partial = run([edge(verified=False)])
    assert not partial["coverage_complete"] and not partial["all_rules_passed"]
    assert {f["status"] for f in partial["findings"]} == {"unknown"}
    assert partial["omissions"][0]["source"] == "cad:123"
    assert run([], complete=True)["findings"][0]["status"] == "fail"


def test_positive_short_is_retained_even_with_partial_extraction():
    assert run([edge(A, C)], complete=False)["findings"][1]["status"] == "fail"


def test_cycles_are_descriptive_and_never_implicitly_electrical_faults():
    report = run(
        [edge(), edge(B, C), edge(C, A)],
        rules=[
            {
                "id": "path",
                "operator": "connected",
                "a": A,
                "b": C,
            }
        ],
    )
    assert report["cycle_rank"] == 1 and report["all_rules_passed"]
    with pytest.raises(ValueError, match="Unsupported"):
        run([], rules=[{"id": "unsafe", "operator": "exec"}])


def test_ground_symbols_are_not_independent_bonding_points():
    rule = {
        "id": "ground",
        "operator": "one_grounding_point",
        "circuit_terminals": [A, B],
        "boundary_verified": True,
        "grounding_points": [
            {"id": "bond-1", "terminal": A, "source": "cad:1"},
            {"id": "bond-1", "terminal": B, "source": "cad:2"},
        ],
    }
    assert run([edge()], rules=[rule])["findings"][0]["status"] == "pass"
    assert run([], rules=[rule])["findings"][0]["status"] == "fail"
    rule["boundary_verified"] = False
    assert run([edge()], rules=[rule])["findings"][0]["status"] == "unknown"
