"""Catalog-decoder contract controls; these are not real-document accuracy scores."""

import hashlib
from pathlib import Path

import pytest

from backend.equipment.evidence.catalogs import _catalog, lookup_product


def values(result):
    return {row["feature"]: row["value"] for row in result["claims"]}


def test_exact_schneider_thermal_product_and_source():
    result = lookup_product(part_number="HDL36060", voltage_v=480)
    facts = values(result)
    assert facts["device_frame"] == "H"
    assert facts["frame_a"] == 150
    assert facts["pole_count"] == 3
    assert facts["rated_current_a"] == 60
    assert facts["interrupting_ka"] == 18
    assert "trip_a" not in facts
    source = next(
        x["source_citation"]
        for x in result["claims"]
        if x["feature"] == "interrupting_ka"
    )
    assert source["page"] == 27
    assert source["document"] == "0611CT1001 R09/24"
    assert len(source["sha256"]) == 64


def test_electronic_sensor_is_not_trip_setting():
    facts = values(lookup_product(part_number="LGL36600U31X", voltage_v=600))
    assert facts["sensor_a"] == 600
    assert facts["frame_a"] == 600
    assert facts["interrupting_ka"] == 18
    assert "rated_current_a" not in facts
    assert "trip_a" not in facts


@pytest.mark.parametrize(
    "part",
    [
        "HDL36060XYZ",
        "HDL36061",
        "XHDL36060",
        "HDL 36060",
        "LGL36600U31XZZ",
        "HFD3200LL",
        "BQD315X",
    ],
)
def test_unknown_number_or_suffix_never_decodes_prefix(part):
    result = lookup_product(part_number=part, voltage_v=480)
    assert not result["claims"]
    assert not result["applicability"]["product_bound"]
    assert result["unknowns"]


def test_maker_and_family_conflicts_block_known_number():
    assert not lookup_product(manufacturer="Siemens", part_number="HDL36060")["claims"]
    assert not lookup_product(family="BQD", part_number="HDL36060")["claims"]
    assert lookup_product(
        manufacturer="Square D", family="PowerPacT", part_number="HDL36060"
    )["claims"]


def test_frame_ranges_require_binding_and_are_not_incorrect_user_shortcuts():
    assert not lookup_product(frame="J")["constraints"]
    assert not lookup_product(manufacturer="Square D", frame="J")["constraints"]
    j = lookup_product(manufacturer="Square D", family="PowerPacT", frame="J")[
        "constraints"
    ][0]
    assert (j["minimum"], j["maximum"]) == (70, 250)
    l = lookup_product(manufacturer="Square D", family="PowerPacT", frame="L")
    assert [(x["minimum"], x["maximum"]) for x in l["constraints"]] == [
        (70, 400),
        (200, 600),
    ]
    assert not l["claims"]
    assert l["unknowns"]


@pytest.mark.parametrize("frame", ["LI", "MJ", "LA", "L1"])
def test_old_frame_letters_are_not_modern_powerpact_aliases(frame):
    result = lookup_product(manufacturer="Square D", family="PowerPacT", frame=frame)
    assert not result["constraints"]
    assert not result["claims"]
    assert result["input"]["frame"] == frame


@pytest.mark.parametrize(
    "voltage,mode,standard",
    [
        (None, "AC", "UL489"),
        (460, "AC", "UL489"),
        (480, "DC", "UL489"),
        (480, "AC", "IEC60947-2"),
        (float("nan"), "AC", "UL489"),
        (True, "AC", "UL489"),
    ],
)
def test_no_unconditional_or_interpolated_interrupting_rating(voltage, mode, standard):
    result = lookup_product(
        part_number="HDL36060",
        voltage_v=voltage,
        current_type=mode,
        rating_standard=standard,
    )
    assert "interrupting_ka" not in values(result)
    assert not result["applicability"]["operating_rating_bound"]


def test_exact_eaton_number_with_voltage_bound_kA():
    result = lookup_product(part_number="HFD3200L", voltage_v=480)
    facts = values(result)
    assert (facts["device_frame"], facts["pole_count"], facts["rated_current_a"]) == (
        "F",
        3,
        200,
    )
    assert facts["interrupting_ka"] == 65
    assert "interrupting_ka" not in values(
        lookup_product(part_number="HFD3200L", voltage_v=600)
    )
    assert "interrupting_ka" not in values(
        lookup_product(part_number="HFD3200L", voltage_v=250, current_type="DC")
    )


def test_siemens_slash_voltage_needs_system_and_standard():
    assert "interrupting_ka" not in values(
        lookup_product(part_number="BQD315", voltage_v=480)
    )
    bqd = values(
        lookup_product(
            part_number="BQD315", voltage_v=480, connection_system="480Y/277"
        )
    )
    assert (bqd["pole_count"], bqd["rated_current_a"], bqd["interrupting_ka"]) == (
        3,
        15,
        14,
    )
    assert "frame_a" not in bqd
    assert "trip_type" not in bqd
    assert "interrupting_ka" not in values(
        lookup_product(
            part_number="BQD6315", voltage_v=600, connection_system="600Y/347"
        )
    )
    csa = values(
        lookup_product(
            part_number="BQD6315",
            voltage_v=600,
            connection_system="600Y/347",
            rating_standard="CSA",
        )
    )
    assert csa["interrupting_ka"] == 10


def test_every_curated_number_keeps_source_and_never_fabricates_dc_rating():
    for part, product in _catalog()["products"].items():
        facts = lookup_product(part_number=part, voltage_v=500, current_type="DC")
        assert facts["applicability"]["product_bound"]
        assert values(facts)["part_number"] == part
        assert "interrupting_ka" not in values(facts)
        assert all(
            x["source_citation"]["url"].startswith("https://") for x in facts["claims"]
        )
        assert all(not x["engineering_approved"] for x in facts["claims"])


def test_downloaded_primary_sources_match_recorded_bytes_when_present():
    for source in _catalog()["sources"].values():
        if source.get("local_original") and Path(source["local_original"]).exists():
            assert (
                hashlib.sha256(Path(source["local_original"]).read_bytes()).hexdigest()
                == source["sha256"]
            )


@pytest.fixture(autouse=True)
def database():
    """These pure source/decision controls do not use a database."""
    yield
