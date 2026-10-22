"""Synthetic acceptance controls, not real manufacturer accuracy."""

from copy import deepcopy

import ezdxf
import pytest

from backend.engineering.pdu_phase import execute, file_sha, plan
from backend.engineering.pdu_phase_demo import baseline, fixture
from scripts.build_phase_conversion_demo import inject_faults


@pytest.fixture(autouse=True)
def database():
    yield


@pytest.fixture
def sample(tmp_path):
    catalog, parts, loads, request = fixture()
    source = baseline(tmp_path / "source", catalog, parts, loads)
    return source, catalog, request


def test_saved_entities_and_reports_change_and_sources_survive(sample, tmp_path):
    source, catalog, request = sample
    drawing, bom = source / "PDU-30-Rev-A.dxf", source / "BOM-Rev-A.csv"
    original = (file_sha(drawing), file_sha(bom))
    change_plan = plan(drawing, bom, catalog, request)
    report = execute(drawing, bom, catalog, request, change_plan, tmp_path / "draft")
    assert report["supported_checks_passed"]
    assert report["devices_checked"] == 21
    assert report["connections_checked"] == 48
    assert report["phase_load_va"] == {"A": 6800, "B": 6800, "C": 6800}
    assert report["calculations"]["primary_full_load_a"] == pytest.approx(36.0843918)
    assert report["calculations"]["secondary_full_load_a"] == pytest.approx(83.2716734)
    assert not report["engineering_approved"] and not report["released"]
    assert (file_sha(drawing), file_sha(bom)) == original
    faults = inject_faults(tmp_path / "draft", catalog, request)
    assert len(faults) == 7 and all(f["detected"] for f in faults)
    assert report["power_flow"]["fundamental_neutral_a"] < 0.000001
    assert report["power_flow"]["recovered_apparent_va"] == pytest.approx(20400)


@pytest.mark.parametrize(
    "case",
    [
        "single_phase_supply",
        "ambiguous_two_phase",
        "old_240_load",
        "overload",
        "missing_circuit",
        "two_pole_main",
        "no_source_ref",
    ],
)
def test_unsafe_or_ambiguous_scenario_blocks_writing(sample, case):
    source, catalog, request = sample
    if case == "single_phase_supply":
        request["supply_phases"] = 1
    elif case == "ambiguous_two_phase":
        request["target"] = "two_phase"
    elif case == "old_240_load":
        request["loads"][-1]["replacement_load_confirmed"] = False
    elif case == "overload":
        request["loads"][0]["va"] = 120000
    elif case == "missing_circuit":
        request["loads"].pop()
    elif case == "two_pole_main":
        catalog["parts"][request["parts"]["CB-M1"]]["poles"] = 2
    else:
        catalog["parts"][request["parts"]["XF-1"]].pop("source_ref")
    with pytest.raises(ValueError):
        plan(source / "PDU-30-Rev-A.dxf", source / "BOM-Rev-A.csv", catalog, request)


def test_stale_source_and_edited_plan_are_rejected(sample, tmp_path):
    source, catalog, request = sample
    drawing, bom = source / "PDU-30-Rev-A.dxf", source / "BOM-Rev-A.csv"
    original = plan(drawing, bom, catalog, request)
    tampered = deepcopy(original)
    tampered["parts"]["XF-1"]["kva"] = 100
    with pytest.raises(ValueError, match="Stale or edited"):
        execute(drawing, bom, catalog, request, tampered, tmp_path / "never-written")
    doc = ezdxf.readfile(drawing)
    doc.modelspace().add_line((10, 10), (20, 20))
    doc.saveas(drawing)
    with pytest.raises(ValueError, match="Stale or edited"):
        execute(drawing, bom, catalog, request, original, tmp_path / "never-written")
    assert not (tmp_path / "never-written").exists()


def test_arbitrary_manufacturer_template_is_not_accepted(sample):
    source, catalog, request = sample
    drawing = source / "PDU-30-Rev-A.dxf"
    doc = ezdxf.readfile(drawing)
    marker = next(
        e for e in doc.modelspace().query("TEXT") if e.dxf.layer == "PDU_IDENTITY"
    )
    marker.dxf.text = "ANY-MANUFACTURER-CAD"
    doc.saveas(drawing)
    with pytest.raises(ValueError, match="Manufacturer CAD needs an adapter"):
        plan(drawing, source / "BOM-Rev-A.csv", catalog, request)


def test_independent_unbalanced_load_control_uses_the_saved_internal_buses(
    sample, tmp_path
):
    source, catalog, request = sample
    # Separate hand-calculated control, never read by the generator.
    for load, va in zip(
        request["loads"], [1200, 1800, 2400, 3000, 3600, 600, 6000], strict=True
    ):
        load["va"] = va
    drawing, bom = source / "PDU-30-Rev-A.dxf", source / "BOM-Rev-A.csv"
    report = execute(
        drawing,
        bom,
        catalog,
        request,
        plan(drawing, bom, catalog, request),
        tmp_path / "unbalanced",
    )
    assert report["power_flow"]["phase_load_va"] == {"L1": 6200, "L2": 7400, "L3": 5000}
    assert report["power_flow"]["recovered_apparent_va"] == pytest.approx(18600)
    # Neutral = sqrt(A^2+B^2+C^2-AB-BC-CA) / nominal phase voltage.
    assert report["power_flow"]["fundamental_neutral_a"] == pytest.approx(
        (4320000**0.5) / (208 / (3**0.5))
    )
