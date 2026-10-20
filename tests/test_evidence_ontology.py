"""Contract controls for the evidence architecture; not real-file accuracy."""

import pytest

from backend.equipment.evidence import (
    adapt_sources,
    append_claim,
    make_claim,
    new_graph,
    transition_claim,
)
from backend.equipment.evidence.identity import describe_identity
from backend.equipment.evidence.ontology import (
    make_edge,
    make_node,
    normalize_feature_value,
    validate_topology,
)
from backend.equipment.evidence.schema import append_record

HASH = "a" * 64
REF = {"source_id": "drawing-a", "sha256": HASH, "page": 1, "bbox": [0, 0, 0.5, 0.5]}


def test_claims_are_append_only_unknown_and_conflicting_values_survive():
    graph = append_record(
        new_graph("tenant/order"), "nodes", make_node("cb", "device", "tenant/order")
    )
    first = make_claim(
        "cb",
        "trip_a",
        600,
        source=REF,
        extractor={"name": "vision"},
        raw_confidence="high",
    )
    other = make_claim(
        "cb", "trip_a", 60, source=REF, extractor={"name": "ocr"}, raw_confidence=0.99
    )
    unknown = make_claim(
        "cb", "trip_a", None, source=REF, extractor={"name": "human"}, raw_value="6?0"
    )
    changed = append_claim(append_claim(append_claim(graph, first), other), unknown)
    assert not graph["claims"]
    assert [c["value"] for c in changed["claims"]] == [600, 60, None]
    assert first["independence_group"] == other["independence_group"]
    assert unknown["raw_value"] == "6?0"
    conflict = {**first, "value": 20}
    with pytest.raises(ValueError, match="overwrite"):
        append_claim(changed, conflict)
    assert append_claim(changed, first) == changed


def test_copy_of_original_page_is_not_independent_evidence():
    a = make_claim("cb", "trip_a", 600, source=REF, extractor={"name": "vision"})
    b = make_claim(
        "cb",
        "trip_a",
        600,
        source={**REF, "source_id": "renamed-copy", "bbox": [0.1, 0.1, 0.4, 0.4]},
        extractor={"name": "second-vision"},
    )
    assert a["independence_group"] == b["independence_group"]


def test_units_and_enums_are_typed_not_model_guesstimates():
    assert normalize_feature_value("frame_a", "0.6 kA")["value"] == 600
    assert normalize_feature_value("interrupting_ka", "65000 A")["value"] == 65
    assert normalize_feature_value("width_mm", 2, "in")["value"] == 50.8
    assert normalize_feature_value("trip_a", "600/250")["value"] is None
    assert normalize_feature_value("trip_a", 600, "V")["valid"] is False
    assert normalize_feature_value("pole_count", True)["valid"] is False
    assert normalize_feature_value("pole_count", 2.5)["valid"] is False
    assert (
        normalize_feature_value("position_state", "installed_maybe")["valid"] is False
    )


def test_device_function_is_not_unique_identity_and_embedded_number_not_parsed():
    assert describe_identity("52-12")["function_numbers"][0]["number"] == "52"
    assert describe_identity("86T")["function_numbers"][0]["meaning"] == "lockout"
    assert describe_identity("1086M")["function_numbers"] == []
    aspects = describe_identity("=F1+L2-Q3")["reference_designations"]
    assert [x["aspect"] for x in aspects] == ["function", "location", "product"]
    assert all(x["conformance_verified"] is False for x in aspects)


def _pred(source_id="scan", digest=HASH):
    return {
        "schema_version": "rivet.visual-equipment.v4",
        "source_hashes": {source_id: digest},
        "observed_bom_rows": [
            {
                "id": "row-1",
                "source_id": source_id,
                "sha256": digest,
                "page": 1,
                "row": 1,
                "table": 1,
                "bbox": [0.1, 0.1, 0.9, 0.2],
                "fields": {"section_no": 1, "circuit_no": 12},
                "cells": [
                    {
                        "column": 1,
                        "field": "trip_a",
                        "value": 60,
                        "raw_text": "60",
                        "confidence": "high",
                        "header": "TRIP AMP",
                        "bbox": [0.1, 0.1, 0.2, 0.2],
                    },
                    {
                        "column": 2,
                        "field": "unknown",
                        "value": "AB123",
                        "raw_text": "AB123",
                        "confidence": "high",
                        "header": "MFG ITEM",
                        "bbox": [0.2, 0.1, 0.3, 0.2],
                    },
                    {
                        "column": 3,
                        "field": "device_frame",
                        "value": "J",
                        "raw_text": "J?",
                        "confidence": "low",
                        "header": "FRAME",
                        "bbox": [0.3, 0.1, 0.4, 0.2],
                    },
                ],
            }
        ],
        "devices": [],
        "pages": [{"source_id": source_id, "page": 1, "status": "processed"}],
    }


def test_visual_adapter_preserves_all_cells_and_never_guesses_unsupported_header():
    graph = adapt_sources(
        visual_predictions=_pred(),
        scope_id="company-a/order-a",
        source_context={"scan": {"side": "target", "role": "bom"}},
    )
    claims = {c["feature"]: c for c in graph["claims"]}
    assert claims["trip_a"]["value"] == 60
    assert claims["trip_a"]["conditions"]["side"] == "target"
    assert claims["trip_a"]["conditions"]["role"] == "bom"
    assert claims["unknown:mfg_item"]["value"] is None
    assert claims["unknown:mfg_item"]["raw_value"] == "AB123"
    assert claims["device_frame"]["value"] is None
    assert not graph["coverage"]["complete"]
    assert all(c["state"] == "observed" for c in graph["claims"])


def test_entities_do_not_merge_across_source_or_job_without_cited_identity():
    graph = adapt_sources(
        visual_predictions=[_pred("scan-a"), _pred("scan-b")],
        scope_id="company-a/order-a",
    )
    entities = [n for n in graph["nodes"] if n["kind"] == "unclassified_position"]
    assert len(entities) == 2
    other = adapt_sources(
        visual_predictions=_pred("scan-a"), scope_id="company-b/order-a"
    )
    assert entities[0]["id"] != other["nodes"][0]["id"]
    verified = [
        {
            "verified": True,
            "scope_key": "company-a/order-a",
            "canonical_id": "section1/circuit12",
            "source_id": sid,
            "entity_id": "row-1",
            "evidence": [REF],
        }
        for sid in ("scan-a", "scan-b")
    ]
    joined = adapt_sources(
        visual_predictions=[_pred("scan-a"), _pred("scan-b")],
        scope_id="company-a/order-a",
        verified_identities=verified,
    )
    assert len(joined["nodes"]) == 1
    assert len(joined["nodes"][0]["source_refs"]) == 2
    assert joined["nodes"][0]["metadata"]["identity_verified"] is True


def test_mixed_source_hashes_are_rejected_before_claims_are_used():
    with pytest.raises(ValueError, match="different original bytes"):
        adapt_sources(
            visual_predictions=[_pred(), _pred(digest="b" * 64)], scope_id="order"
        )


def test_physical_containment_cannot_stand_in_for_electrical_connection():
    graph = new_graph("order")
    graph["nodes"] = [
        make_node("cb", "device", "order"),
        make_node("t", "terminal", "order"),
        make_node("net", "connectivity_node", "order"),
        make_node("ln", "logical_node", "order"),
    ]
    graph["edges"] = [
        make_edge("equipment_terminal", "cb", "t"),
        make_edge("terminal_connectivity_node", "t", "net"),
        make_edge("logical_function_of", "ln", "cb"),
    ]
    assert validate_topology(graph) == []
    graph["edges"].append(make_edge("contains", "cb", "net"))
    assert validate_topology(graph)[0]["code"] == "invalid_physical_containment"


def test_doc_approval_stamp_is_only_observed_until_authorized_event():
    raw = {
        "sources": [
            {
                "id": "drawing-a",
                "sha256": HASH,
                "role": "drawing",
                "lifecycle": "released",
                "revision": "C",
            }
        ],
        "facts": [
            {
                "id": "x",
                "source_id": "drawing-a",
                "sha256": HASH,
                "device_tag": "52-12",
                "field": "trip_a",
                "value": 600,
                "evidence": {"page": 1},
            }
        ],
    }
    graph = adapt_sources(rawcomparison=raw, scope_id="order")
    assert graph["claims"][0]["state"] == "observed"
    assert graph["claims"][0]["conditions"]["document_lifecycle"] == "released"
    with pytest.raises(ValueError, match="authority"):
        transition_claim(
            graph,
            graph["claims"][0]["id"],
            "approved",
            actor="",
            authority="",
            evidence=REF,
        )
    changed = transition_claim(
        graph,
        graph["claims"][0]["id"],
        "approved",
        actor="pm",
        authority="verified test role",
        evidence=REF,
    )
    assert [c["state"] for c in changed["claims"]] == ["observed", "approved"]
    assert changed["claims"][1]["derived_from"] == [graph["claims"][0]["id"]]
    assert changed["metadata"]["engineering_approved"] is False


def test_cad_preserves_missing_or_ambiguous_endpoints():
    report = {
        "source_id": "cad",
        "sha256": HASH,
        "components": [
            {
                "id": "c1",
                "tag": "BKR-12",
                "properties": {
                    "part_number": "TEST123",
                    "raw_properties": {"RATING1": "600"},
                },
                "pins": [
                    {"pin": "1", "source_index": 1},
                    {"pin": "1", "source_index": 2},
                ],
                "source": {"xml_path": "/Connector[1]"},
            }
        ],
        "connections": [
            {
                "id": "w1",
                "from": {"device_tag": "BKR-12", "pin": "1"},
                "to": {"device_tag": None, "pin": "2"},
                "wire_number": "W1",
                "source": {"xml_path": "/Wire[1]"},
            }
        ],
    }
    graph = adapt_sources(cad_reports=report, scope_id="order")
    assert len([n for n in graph["nodes"] if n["kind"] == "terminal"]) == 2
    assert not any(e["kind"] == "terminal_connectivity_node" for e in graph["edges"])
    assert (
        next(n for n in graph["nodes"] if n["kind"] == "connectivity_node")["metadata"][
            "unresolved_endpoints"
        ]
        == 2
    )
    assert (
        next(
            c for c in graph["claims"] if c["feature"] == "unknown:cad_property:RATING1"
        )["value"]
        is None
    )
    assert not any(c["feature"] == "trip_a" for c in graph["claims"])


def test_specification_builds_rule_not_actual_equipment_agreement():
    raw = {
        "requirements": {
            "sources": [{"id": "spec", "sha256": HASH, "role": "specification"}],
            "facts": [
                {
                    "id": "req",
                    "source_id": "spec",
                    "sha256": HASH,
                    "device_tag": "CB1",
                    "field": "trip_a",
                    "value": 600,
                    "evidence": {"page": 2},
                }
            ],
        }
    }
    graph = adapt_sources(rawcomparison=raw, scope_id="order")
    assert len(graph["rules"]) == 1
    rule = graph["rules"][0]
    assert rule["target"] == {"value": 600, "unit": "A"}
    assert rule["observed_equipment_evidence"] is False
    assert graph["claims"][0]["conditions"]["role"] == "specification"
    assert graph["claims"][0]["conditions"]["side"] == "requirements"


def test_existing_compiled_model_keeps_source_observations_and_original_values():
    from backend.equipment.evidence import from_equipment_model

    model = {
        "devices": [
            {
                "tag": "CB1",
                "fields": {
                    "trip_a": {
                        "build": [
                            {
                                "observation_id": "o1",
                                "value": 600,
                                "raw_value": "600 A",
                                "unit": "A",
                                "source": {
                                    "document_id": "drawing",
                                    "name": "real.pdf",
                                    "revision": "C",
                                    "role": "drawing",
                                    "sha256": HASH,
                                    "location": {"page": 1},
                                },
                            }
                        ]
                    }
                },
            }
        ],
        "source_observations": {
            "visual_schedule_rows": _pred()["observed_bom_rows"],
            "visual_devices": [],
            "native_schedule_rows": [],
        },
    }
    graph = from_equipment_model(model, scope_id="order")
    assert any(c["raw_value"] == "600 A" and c["value"] == 600 for c in graph["claims"])
    assert any(c["raw_value"] == "AB123" for c in graph["claims"])
    assert model["devices"][0]["fields"]["trip_a"]["build"][0]["value"] == 600


def test_split_page_original_ancestry_cannot_inflate_agreement():
    context = {
        "scan-a": {"original_sha256": "b" * 64, "page_map": {"1": 200}},
        "scan-b": {"original_sha256": "b" * 64, "page_map": {"1": 200}},
    }
    graph = adapt_sources(
        visual_predictions=[_pred("scan-a"), _pred("scan-b", digest="c" * 64)],
        scope_id="order",
        source_context=context,
    )
    groups = {
        c["independence_group"] for c in graph["claims"] if c["feature"] == "trip_a"
    }
    assert len(groups) == 1


@pytest.fixture(autouse=True)
def database():
    """These pure source/decision controls do not use a database."""
    yield
