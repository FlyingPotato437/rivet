"""Synthetic conformance controls; no real design-accuracy measurement."""

from copy import deepcopy

import pytest

from backend.engineering.precheck import precheck_source_conformance


@pytest.fixture(autouse=True)
def database():
    """Pure controls never connect to or reset a database."""
    yield


SOURCE_SHA = "a" * 64
FILE_SHA = "b" * 64


def item(
    key, value, subject="order", *, identifier=None, origin="specification", **extra
):
    return {
        "id": identifier or f"{subject}:{key}",
        "subject_id": subject,
        "key": key,
        "value": value,
        "confirmed": True,
        "origin_role": origin,
        "source_claim_ids": [f"claim:{subject}:{key}"],
        "source_refs": [
            {"source_id": "customer-input", "sha256": SOURCE_SHA, "page": 1}
        ],
        **extra,
    }


def control():
    requirements = {
        "items": [
            item("voltage_v", 480),
            item("trip_a", 225, "F1"),
            item(
                "main.trip_a",
                1200,
                origin="customer_one_line",
                source_object_key="main-symbol",
            ),
        ],
        "conflicts": [],
        "unreadable_pages": [],
        "warnings": [],
        "drawing_readings": [
            {"source_id": "customer-input", "sha256": SOURCE_SHA, "pages": [1]}
        ],
    }
    design = {
        "model_id": "synthetic-model",
        "model_version": 3,
        "system": {"voltage_v": 480},
        "devices": [
            {"id": "main", "tag": "MAIN", "role": "main", "trip_a": 1200},
            {"id": "F1", "tag": "FDR-1", "role": "feeder", "trip_a": 225},
        ],
        "sections": [],
        "topology": {
            "bus_id": "bus",
            "connections": [
                {"from_device_id": "main", "to_bus_id": "bus"},
                {"from_bus_id": "bus", "to_device_id": "F1"},
            ],
        },
    }
    inventory = {}
    for role in ("drawing", "bom"):
        source_id = f"saved-{role}"
        base = {
            "source_id": source_id,
            "sha256": FILE_SHA,
            "role": role,
            "evidence": {"entity_handle": "A1"}
            if role == "drawing"
            else {"sheet": "BOM", "row": 2, "column": 3},
        }
        inventory[role] = {
            "report": {
                "id": source_id,
                "sha256": FILE_SHA,
                "role": role,
                "status": "parsed",
                "observed_device_tags": ["MAIN", "FDR-1"],
            },
            "facts": [
                {
                    **deepcopy(base),
                    "field": "voltage_v",
                    "value": 480,
                    "scope": "system",
                },
                {
                    **deepcopy(base),
                    "field": "trip_a",
                    "value": 225,
                    "device_tag": "FDR-1",
                },
                {
                    **deepcopy(base),
                    "field": "trip_a",
                    "value": 1200,
                    "device_tag": "MAIN",
                },
            ],
        }
    brief = {
        "snapshot": "current-brief-snapshot",
        "preferences": [],
        "withheld": [],
        "partial": False,
    }
    return requirements, design, inventory, brief


def run(values=None, **kwargs):
    requirements, design, inventory, brief = values or control()
    return precheck_source_conformance(
        requirements,
        design,
        generated_inventory=inventory,
        customer_brief=brief,
        **kwargs,
    )


def test_confirmed_explicit_values_match_model_and_separately_read_saved_files_without_mutation():
    values = control()
    before = deepcopy(values)
    result = run(values)
    assert result["status"] == "pass"
    assert result["requirement_summary"]["pass"] == 3
    assert result["saved_file_summary"]["pass"] == 6
    assert result["customer_memory"]["status"] == "not_applicable"
    assert result["customer_one_line_checked"] is False
    assert result["source_topology"]["full_topology_checked"] is False
    assert result["engineering_approved"] is result["production_released"] is False
    assert values == before


def test_mutually_consistent_wrong_model_and_files_do_not_pass_source_requirement():
    values = control()
    values[1]["devices"][1]["trip_a"] = 250
    for role in ("drawing", "bom"):
        values[2][role]["facts"][1]["value"] = 250
    result = run(values)
    assert result["requirement_summary"]["fail"] == 1
    assert result["saved_file_summary"]["fail"] == 2
    assert result["status"] == "fail"


def test_correct_model_does_not_hide_saved_bom_mismatch():
    values = control()
    values[2]["bom"]["facts"][1]["value"] = 250
    result = run(values)
    assert result["requirement_summary"]["fail"] == 0
    assert result["saved_file_summary"]["fail"] == 1
    assert result["status"] == "fail"


def test_partial_saved_reading_retains_located_facts_but_reports_remaining_coverage():
    values = control()
    values[2]["bom"]["report"].update(
        status="partial", warnings=[{"code": "unknown_part_number"}]
    )
    result = run(values)
    assert result["saved_file_summary"]["pass"] == 6
    assert result["saved_file_reading_gaps"][0]["artifact_role"] == "bom"
    assert result["status"] == "unverified"


@pytest.mark.parametrize(
    "change",
    [
        {"confirmed": False},
        {"unresolved_role": True},
        {"excluded": True},
        {"source_refs": []},
        {"origin_role": "manufacturer_reference"},
        {"source_claim_ids": []},
        {"value": None},
    ],
)
def test_proposed_ambiguous_uncited_excluded_or_unknown_source_never_passes(change):
    values = control()
    values[0]["items"][1].update(change)
    result = run(values)
    assert result["requirement_checks"][1]["status"] == "unverified"
    assert result["status"] == "unverified"


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "c" * 64},
        {"source_id": "other"},
        {"role": "drawing"},
        {"evidence": {}},
    ],
)
def test_actual_file_fact_requires_the_report_source_hash_role_and_location(change):
    values = control()
    values[2]["bom"]["facts"][1].update(change)
    result = run(values)
    row = next(
        row
        for row in result["saved_file_source_checks"]
        if row["artifact_role"] == "bom" and row["key"] == "trip_a"
    )
    assert row["status"] == "unverified"
    assert row["comparison"] == "missing"


def test_source_local_actual_identity_cannot_reuse_an_older_model_version():
    values = control()
    values[2]["drawing"]["facts"][1].update(
        source_local_identity=True,
        model_id="synthetic-model",
        model_version=2,
        model_device_id="F1",
    )
    assert run(values)["saved_file_summary"]["unverified"] == 1


def test_repeated_circuit_display_label_cannot_supply_an_identity():
    values = control()
    values[0]["items"][1].update(subject_id="7")
    values[1]["devices"][1].update(
        tag="drawing:opaque-1", display_tag="7", source_local_identity=True
    )
    values[1]["devices"].append(
        {"id": "F2", "tag": "drawing:opaque-2", "display_tag": "7", "trip_a": 225}
    )
    row = run(values)["requirement_checks"][1]
    assert row["status"] == "fail" and row["comparison"] == "missing"


def test_unknown_scalar_is_not_filled_from_bus_or_device_fields():
    values = control()
    values[0]["items"] = [item("service_rating_a", 1000)]
    values[1]["topology"]["bus_a"] = 1200
    values[2]["drawing"]["facts"] = [
        {**values[2]["drawing"]["facts"][1], "field": "service_rating_a", "value": 1000}
    ]
    result = run(values)
    assert result["requirement_checks"][0]["observed"] is None
    assert result["saved_file_source_checks"][0]["status"] == "unverified"


def test_units_normalize_for_comparison_and_source_value_remains_unchanged():
    values = control()
    values[0]["items"][0].update(value=0.48, unit="kV")
    assert run(values)["requirement_checks"][0]["status"] == "pass"
    assert values[0]["items"][0]["value"] == 0.48


def test_known_physical_maximum_uses_all_sections_and_unknown_dimension_stays_missing():
    values = control()
    values[0]["items"] = [item("max_width_in", 90)]
    values[1]["sections"] = [{"width_in": 42}, {"width_in": 54}]
    assert run(values)["requirement_checks"][0]["status"] == "fail"
    values[1]["sections"][1]["width_in"] = None
    assert run(values)["requirement_checks"][0]["comparison"] == "missing"


def test_conflicting_sources_and_reader_coverage_remain_blockers():
    values = control()
    values[0]["conflicts"] = [{"item_ids": ["F1:trip_a"]}]
    values[0]["reader_coverage"] = [{"state": "partial", "source_id": "customer-input"}]
    result = run(values)
    assert result["requirement_checks"][1]["status"] == "unverified"
    assert result["coverage_blockers"]


def observations():
    return [
        {
            "source_id": "customer-input",
            "sha256": SOURCE_SHA,
            "pages": [
                {
                    "page": 1,
                    "reading": {
                        "objects": [
                            {"key": "main-symbol", "kind": "device_symbol"},
                            {"key": "bus-symbol", "kind": "busbar"},
                        ],
                        "conductors": [
                            {
                                "key": "visible-wire",
                                "from_object": "main-symbol",
                                "to_object": "bus-symbol",
                                "kind": "conductor",
                                "connected_visible": True,
                                "bbox": [0.1, 0.1, 0.2, 0.2],
                            }
                        ],
                        "unknown_regions": [],
                    },
                }
            ],
        }
    ]


def test_pinned_visible_direct_source_connection_is_compared_with_explicit_model_endpoints():
    result = run(drawing_observations=observations())
    assert result["source_topology"]["summary"]["pass"] == 1
    assert result["source_topology"]["supported_connection_count"] == 1
    assert result["customer_one_line_checked"] is False
    values = control()
    values[1]["topology"]["connections"] = []
    assert (
        run(values, drawing_observations=observations())["source_topology"]["summary"][
            "fail"
        ]
        == 1
    )


@pytest.mark.parametrize(
    "change",
    [
        {"connected_visible": False},
        {"kind": "prepared_or_dashed"},
        {"to_object": "unmapped"},
    ],
)
def test_unsupported_source_connectivity_never_becomes_a_topology_pass(change):
    readings = observations()
    readings[0]["pages"][0]["reading"]["conductors"][0].update(change)
    assert (
        run(drawing_observations=readings)["source_topology"]["summary"]["unverified"]
        == 1
    )


def test_wrong_source_hash_and_summary_only_reading_cannot_establish_topology():
    readings = observations()
    readings[0]["sha256"] = "c" * 64
    assert (
        run(drawing_observations=readings)["source_topology"][
            "supported_connection_count"
        ]
        == 0
    )
    assert run()["source_topology"]["supported_connection_count"] == 0


def note(**extra):
    return {
        "id": "memory-1",
        "title": "Use the approved trip rating",
        "text": "FDR-1 trip 225 A",
        "status": "approved",
        "applicability": "standing_preference",
        "evidence_current": True,
        "conflicts_with": [],
        "provenance": {
            "evidence": [
                {"source_id": "historical-span", "evidence_version": "pinned-note"}
            ]
        },
        **extra,
    }


def test_approved_prose_preference_is_a_checklist_not_an_invented_pass():
    values = control()
    values[3]["preferences"] = [note()]
    result = run(values)
    assert result["customer_memory"]["status"] == "unverified"
    assert result["customer_memory_checked"] is False


def test_explicit_approved_typed_memory_is_compared_to_model_and_actual_files():
    values = control()
    preference = note()
    preference["provenance"]["engineering_conformance"] = [
        {"subject_id": "F1", "key": "trip_a", "value": 225}
    ]
    values[3]["preferences"] = [preference]
    result = run(values)
    assert result["customer_memory"]["status"] == "pass"
    assert result["customer_memory_checked"] is True
    values[2]["bom"]["facts"][1]["value"] = 250
    assert run(values)["customer_memory"]["status"] == "fail"


@pytest.mark.parametrize(
    "change",
    [
        {"evidence_current": False},
        {"status": "candidate"},
        {"conflicts_with": ["other"]},
    ],
)
def test_unapproved_stale_or_conflicting_memory_cannot_pass(change):
    values = control()
    preference = note(**change)
    preference["provenance"]["engineering_conformance"] = [
        {"subject_id": "F1", "key": "trip_a", "value": 225}
    ]
    values[3]["preferences"] = [preference]
    assert run(values)["customer_memory"]["status"] == "unverified"


def test_absent_partial_and_withheld_customer_context_is_not_silently_clean():
    values = control()
    values[3].clear()
    assert run(values)["customer_memory"]["status"] == "unverified"
    values[3].update(
        snapshot="snapshot",
        partial=True,
        withheld=[{"id": "blocked-memory", "reason": "Expired"}],
    )
    assert run(values)["customer_memory"]["summary"]["unverified"] == 2
