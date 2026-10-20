"""Behavior checks use synthetic text; these are not real-document accuracy."""

from copy import deepcopy

import pytest

from backend.engineering.requirements import extract_requirements, to_config_inputs


def document(
    role="specification",
    text="Main bus: 2000 A\nVoltage: 480 V\nPhases: 3\nWires: 4",
    source="spec",
):
    return {
        "id": source,
        "sha256": "a" * 64,
        "role": role,
        "coverage": [],
        "spans": [{"id": source + "-p1", "text": text, "location": {"page": 1}}],
    }


def test_customer_inputs_only_not_reference_or_cut_sheet():
    result = extract_requirements(
        [
            document(),
            document("manufacturer_reference", "Main bus: 4000 A"),
            document("component_cut_sheet", "Voltage: 600 V"),
        ]
    )
    assert {i["value"] for i in result["items"] if i["key"] == "service_rating_a"} == {
        2000
    }
    assert len(result["excluded_documents"]) == 2
    assert not result["engineering_approved"]
    assert all(
        c["source"]["page"] == 1 and c["source"]["sha256"] == "a" * 64
        for c in result["evidence_claims"]
    )


def test_conflicting_po_withheld_and_quote_exclusion_not_implicitly_accepted():
    result = extract_requirements(
        [
            document(),
            document("purchase_order", "Main bus: 3000 A", "po"),
            document("quote_exception", "SPD: excluded", "quote"),
        ]
    )
    assert {c["key"] for c in result["conflicts"]} == {"service_rating_a", "spd"}
    inputs = to_config_inputs(result)
    assert "service_rating_a" not in inputs and "spd" not in inputs
    assert inputs["conflicts"] and not inputs["confirmed"]


def test_no_device_defaults_or_inferred_tag():
    result = extract_requirements(
        [
            document(
                text="Frame: 250 A\nTrip: 200 A\nDevice: FDR 12 | Frame: 250 A | Trip: 200 A | Trip type: LSIG | Poles: 3"
            )
        ]
    )
    inputs = to_config_inputs(result)
    assert len(inputs["feeders"]) == 1
    assert inputs["feeders"][0]["tag"] == "FDR 12"
    assert inputs["feeders"][0]["frame_a"] == 250
    assert inputs["feeders"][0]["trip_a"] == 200
    assert not inputs["feeders"][0]["confirmed"]
    assert len(result["warnings"]) == 2


def test_unreadable_and_ambiguous_values_stay_visible():
    doc = document(text="Main bus: 1600/2000 A\nMain type: breaker")
    doc["coverage"] = [
        {"label": "Page 2", "state": "scanned", "detail": "No reliable text"}
    ]
    result = extract_requirements([doc])
    row = next(i for i in result["items"] if i["key"] == "service_rating_a")
    assert row["value"] is None and not row["confirmed"]
    assert result["unreadable_pages"][0]["verification_required"]
    assert "service_rating_a" not in to_config_inputs(result)


def test_corroborating_items_keep_both_origins_and_do_not_mutate_input():
    result = extract_requirements([document(), document(source="one-line")])
    snapshot = deepcopy(result)
    inputs = to_config_inputs(result)
    assert len(inputs["service_rating_a"]["source_claim_ids"]) == 2
    assert result == snapshot


def test_claims_need_a_physical_page_or_cad_cell_location():
    doc = document()
    doc["spans"][0]["location"] = {}
    result = extract_requirements([doc])
    assert not result["items"] and result["warnings"]


def test_reviewed_unknown_numeric_and_boolean_are_typed():
    from backend.engineering.requirements import reviewed_value

    assert reviewed_value("frame_a", "2,000") == 2000
    assert reviewed_value("main.trip_a", "1600") == 1600
    assert reviewed_value("spd", "Required") is True
    assert reviewed_value("ground_fault", "Not required") is False
    for key, value in [
        ("frame_a", True),
        ("frame_a", "NaN"),
        ("phases", 2.5),
        ("spd", "maybe"),
    ]:
        with pytest.raises(ValueError):
            reviewed_value(key, value)
