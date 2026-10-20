"""Software controls only; these are not real design accuracy measurements."""

from copy import deepcopy

import pytest

from backend.engineering.configurator import (
    RequirementValue,
    configure,
    load_rule_pack,
    validate_configuration,
    validate_rule_pack,
)


@pytest.fixture(autouse=True)
def database():
    """Pure controls never reset any database."""
    yield


def requirements():
    return {
        "service_rating_a": 1200,
        "voltage_v": 480,
        "voltage_to_ground_v": 277,
        "current_type": "AC",
        "frequency_hz": 60,
        "phases": 3,
        "wires": 4,
        "grounding": "solidly_grounded_wye",
        "minimum_interrupting_ka": 65,
        "service_disconnect": True,
        "code_edition": "2020",
        "ground_fault": True,
        "metering": False,
        "spd": False,
        "confirmed": True,
        "evidence_claims": [{"id": "synthetic-requirement-control"}],
        "main": {
            "kind": "circuit_breaker",
            "trip_a": 1200,
            "poles": 3,
            "trip_type": "LSIG",
            "maximum_trip_a": 1200,
        },
        "feeders": [
            {
                "id": "F1",
                "trip_a": 600,
                "poles": 3,
                "trip_type": "LSI",
                "maximum_trip_a": 600,
            },
            {
                "id": "F2",
                "trip_a": 225,
                "poles": 3,
                "trip_type": "LI",
                "maximum_trip_a": 250,
            },
        ],
        "constraints": {"enclosure": "NEMA1", "line_load_connection": "opposite_side"},
    }


def first(req=None):
    return configure(req or requirements())["configurations"][0]


def check(design, key, device_id=None):
    return next(
        r
        for r in design["rule_results"]
        if r["rule_id"] == key
        and (device_id is None or r.get("device_id") == device_id)
    )


def bus_main_requirements(*, reuse=False, frame_a=2000):
    """Synthetic ratings control, never a real-project accuracy fixture."""
    req = requirements()
    req["service_rating_a"] = 2000
    req["main"].update(trip_a=1600, frame_a=frame_a, maximum_trip_a=1600)
    if reuse:
        req["main"]["reuse_existing"] = True
    return req


def test_1600at_2000af_main_is_not_rejected_by_2000a_board_bus():
    req = bus_main_requirements()
    out = configure(req)
    assert len(out["configurations"]) == 3
    for design in out["configurations"]:
        main = next(d for d in design["devices"] if d["role"] == "main")
        assert (main["trip_a"], main["frame_a"]) == (1600, 2000)
        assert design["sections"][0]["bus_a"] is None
        assert any(q["key"] == "through_bus_a" for q in design["unresolved_questions"])
        # A source-rated frame absent from this catalog subset remains unknown.
        assert main["catalog_variant_id"] is None
        assert not design["has_known_rule_violations"]
        assert not design["fully_configured"]


def test_supported_main_below_bus_keeps_catalog_variant_without_false_floor():
    design = first(bus_main_requirements(frame_a=2500))
    main = next(d for d in design["devices"] if d["role"] == "main")
    assert main["trip_a"] == 1600 and main["catalog_variant_id"] == "RJ-2500-main"
    assert not design["has_known_rule_violations"]


def test_explicit_minimum_main_ampacity_is_a_separate_input_constraint():
    req = bus_main_requirements(frame_a=2500)
    req["main"]["required_ampacity_a"] = {
        "value": 1800,
        "confirmed": True,
        "source_claim_ids": ["synthetic-minimum-main"],
    }
    out = configure(req)
    assert not out["configurations"]
    result = check(out["rejected_configurations"][0], "input.main_required_ampacity")
    assert result["status"] == "fail"
    assert result["authority"] == "source_requirement"
    assert "synthetic-minimum-main" in result["source_claim_ids"]
    assert not result["source_refs"]  # No invented manufacturer/code citation.


def test_explicit_main_minimum_is_rechecked_after_design_edits():
    req = bus_main_requirements(frame_a=2500)
    req["main"].update(rated_current_a=2000, required_ampacity_a=1800)
    design = first(req)
    main = next(d for d in design["devices"] if d["role"] == "main")
    main["rated_current_a"] = 1600
    design["fully_configured"] = True
    result = validate_configuration(design, req)
    assert check(result, "input.main_required_ampacity")["status"] == "fail"
    assert not result["fully_configured"]


def test_bus_and_minimum_do_not_fill_missing_actual_main_rating():
    req = bus_main_requirements(frame_a=2500)
    req["main"].pop("trip_a")
    req["main"]["required_ampacity_a"] = 1600
    design = first(req)
    main = next(d for d in design["devices"] if d["role"] == "main")
    assert main["rated_current_a"] is None and main["trip_a"] is None
    assert check(design, "input.main_required_ampacity")["status"] == "unknown"
    assert not design["fully_configured"]


@pytest.mark.parametrize("value", ["1800", -1, True])
def test_invalid_typed_main_minimum_is_retained_as_unknown(value):
    req = bus_main_requirements(frame_a=2500)
    req["main"]["required_ampacity_a"] = value
    design = first(req)
    question = next(q for q in design["unresolved_questions"] if q["key"] == "main.required_ampacity_a")
    assert question["requirement_value"] == value
    assert not design["fully_configured"]


def test_existing_main_has_no_new_product_or_catalog_envelope():
    req = bus_main_requirements(reuse=True)
    design = first(req)
    main = next(d for d in design["devices"] if d["role"] == "main")
    assert main["reuse_existing"] is True and main["supplied"] is False
    for field in ("manufacturer", "family", "part_number", "catalog_variant_id", "catalog_sccr_ka"):
        assert main[field] is None
    assert main["frame_a"] == 2000 and main["trip_a"] == 1600
    section = design["sections"][0]
    assert all(section[k] is None for k in ("width_in", "depth_in", "height_in"))
    assert section["catalog_frame_height_in"] is None
    assert check(design, "input.main_reuse_existing")["status"] == "pass"
    assert any(q["key"] == "main.dimensions" for q in design["unresolved_questions"])
    assert not design["fully_configured"]


def test_existing_source_product_and_citations_are_retained_without_catalog_lookup():
    req = bus_main_requirements(reuse=True)
    refs = [{"source_id": "synthetic-existing", "sha256": "0" * 64, "page": 1, "bbox": [.1, .2, .3, .4]}]
    req["main"].update(
        manufacturer="Synthetic existing maker", family="Synthetic existing family",
        part_number={"value": "SYNTHETIC-RETAINED", "confirmed": True, "source_claim_ids": ["existing-part"]},
        interrupting_ka=35, source_refs=refs,
    )
    design = first(req)
    main = next(d for d in design["devices"] if d["role"] == "main")
    assert main["manufacturer"] == "Synthetic existing maker"
    assert main["family"] == "Synthetic existing family"
    assert main["part_number"] == "SYNTHETIC-RETAINED"
    assert main["interrupting_ka"] == 35 and main["catalog_sccr_ka"] is None
    assert main["source_refs"] == refs and "existing-part" in main["source_claim_ids"]
    assert check(design, "input.main_reuse_existing")["source_refs"] == refs


@pytest.mark.parametrize("field,value", [
    ("manufacturer", "invented maker"), ("family", "invented family"),
    ("part_number", "invented part"), ("frame", "RJ"), ("frame_a", 2500),
    ("catalog_variant_id", "RJ-2500-main"), ("catalog_sccr_ka", 65),
    ("supplied", True), ("reuse_existing", False),
])
def test_edit_cannot_turn_reused_main_into_new_or_invented_product(field, value):
    req = bus_main_requirements(reuse=True)
    design = first(req)
    next(d for d in design["devices"] if d["role"] == "main")[field] = value
    result = validate_configuration(design, req)
    assert check(result, "input.main_reuse_existing")["status"] == "fail"


def test_supplied_new_conflict_with_reuse_is_retained_as_question():
    req = bus_main_requirements(reuse=True)
    req["main"]["supplied"] = True
    design = first(req)
    main = next(d for d in design["devices"] if d["role"] == "main")
    assert main["supplied"] is None
    assert any(q["key"] == "main.supplied" for q in design["unresolved_questions"])
    assert not design["fully_configured"]


def test_previous_configurator_results_require_explicit_reconfiguration():
    req = requirements()
    design = first(req)
    design.pop("configurator_version")
    result = validate_configuration(design, req)
    assert check(result, "input.configurator_version")["status"] == "fail"


def test_unsupported_feeder_reuse_requirement_does_not_disappear():
    req = requirements()
    req["feeders"][0]["reuse_existing"] = True
    design = first(req)
    assert any(q["key"] == "F1.reuse_existing" for q in design["unresolved_questions"])


def test_cited_data_pages_versions_and_review_history():
    p = load_rule_pack()
    assert p["id"] == "schneider-qed2-ul891-2026.07-v1"
    assert p["review_history"][-1]["status"] == "unapproved"
    assert (
        p["sources"]["schneider-2700CT1101-2026"]["sha256"]
        == "3004a4513f7b9ad2a60eaf49f933a90e032f6a23c369dd2dec1e4ec037d8b2c8"
    )
    assert {r["page"] for d in p["devices"] for r in d["source_refs"]} == {54, 63, 64}
    validate_rule_pack(p)


def test_deterministic_catalog_packing_is_proposed_with_unknowns_preserved():
    req = requirements()
    original = deepcopy(req)
    out = configure(req, model_id="order-model", model_version="req-3")
    assert out == configure(req, model_id="order-model", model_version="req-3")
    assert req == original and len(out["configurations"]) == 3
    for d in out["configurations"]:
        assert d["state"] == "proposed" and d["model_version"] == "req-3"
        assert (
            not d["fully_configured"]
            and not d["engineering_approved"]
            and not d["production_released"]
        )
        dev = {x["id"]: x for x in d["devices"]}
        assert (dev["F1"]["frame"], dev["F1"]["frame_a"]) == ("LJ", 600)
        assert (dev["F2"]["frame"], dev["F2"]["frame_a"]) == ("JJ", 250)
        assert all(
            x["part_number"] is None and x["interrupting_ka"] is None
            for x in dev.values()
        )
        assert all(
            x["catalog_sccr_ka"] >= 65 and x["quantity"] == 1 for x in dev.values()
        )
        pos = [
            p
            for s in d["sections"]
            if s["kind"] == "distribution"
            for p in s["positions"]
        ]
        assert {p["mounting_space_in"] for p in pos} == {4.5, 6}
        assert not d["has_known_rule_violations"]
        assert (
            len(d["topology"]["connections"]) == 3
            and d["topology"]["state"] == "proposed"
        )


def test_wrapped_values_and_dataclass_preserve_claim_ids():
    req = requirements()
    req["voltage_v"] = RequirementValue(480, True, ("voltage-claim",))
    req["feeders"][0]["trip_a"] = {
        "value": 600,
        "confirmed": True,
        "source_claim_ids": ["F1-trip"],
    }
    d = first(req)
    assert (
        "F1-trip"
        in next(x for x in d["devices"] if x["id"] == "F1")["source_claim_ids"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("voltage_v", 601),
        ("service_rating_a", 5001),
        ("current_type", "DC"),
        ("available_fault_ka", 201),
        ("short_time_withstand_ka", 25),
    ],
)
def test_known_scope_failures_return_no_valid_options(field, value):
    req = requirements()
    req[field] = value
    out = configure(req)
    assert out["configurations"] == [] and out["rejected_configurations"]


def test_unsupported_main_and_missing_dimensions_stay_unknown():
    req = requirements()
    req["main"] = {"kind": "fused_switch", "trip_a": 1200, "poles": 3}
    d = first(req)
    assert d["sections"][0]["width_in"] is None and d["sections"][0]["depth_in"] is None
    assert d["topology"]["connections"] == [] and not d["fully_configured"]


def test_dense_feeders_respect_bus_factor_as_well_as_space():
    req = requirements()
    req["feeders"] = [
        {
            "id": f"F{i}",
            "trip_a": 600,
            "poles": 3,
            "trip_type": "LSI",
            "maximum_trip_a": 600,
        }
        for i in range(8)
    ]
    options = configure(req)["configurations"]
    option42 = next(
        d
        for d in options
        if any(s.get("section_type_id") == "iline-42-2000" for s in d["sections"])
    )
    dist = [s for s in option42["sections"] if s["kind"] == "distribution"]
    assert len(dist) == 2 and sum(len(s["positions"]) for s in dist) == 8


@pytest.mark.parametrize(
    "edit",
    ["overlap", "overflow", "side", "trip", "capacity", "sccr", "delete", "main_width"],
)
def test_revalidation_ignores_pass_flags_and_catches_structural_edits(edit):
    req, d = requirements(), first()
    dist = next(s for s in d["sections"] if s["kind"] == "distribution")
    device = next(x for x in d["devices"] if x["id"] == "F1")
    if edit == "overlap":
        dist["positions"][1]["start_in"] = 0
    elif edit == "overflow":
        dist["positions"][0]["start_in"] = 100
    elif edit == "side":
        dist["positions"][0]["side"] = "right"
    elif edit == "trip":
        device["trip_a"] = 800
    elif edit == "capacity":
        device["frame_a"] = 1200
    elif edit == "sccr":
        device["catalog_sccr_ka"] = 200
    elif edit == "delete":
        d["devices"] = [x for x in d["devices"] if x["id"] != "F1"]
    elif edit == "main_width":
        d["sections"][0]["width_in"] = 1
    d.update(rule_results=[], fully_configured=True, engineering_approved=True)
    out = validate_configuration(d, req)
    assert (
        out["has_known_rule_violations"]
        and not out["fully_configured"]
        and not out["engineering_approved"]
    )


def test_priors_cannot_restore_an_option_rejected_by_physical_limit():
    req = requirements()
    req["constraints"]["max_width_in"] = 72
    out = configure(
        req,
        priors=[
            {
                "id": "wide",
                "approved_job": True,
                "source_ref": {"job": "past"},
                "preferred_section_type": "iline-54-3000",
                "weight": 100,
            }
        ],
    )
    assert (
        len(out["configurations"]) == 1
        and out["configurations"][0]["sections"][1]["width_in"] == 42
    )
    assert len(out["rejected_configurations"]) == 2


def test_source_prior_changes_only_rank_of_feasible_options():
    out = configure(
        requirements(),
        priors=[
            {
                "id": "habit",
                "approved_job": True,
                "source_ref": {"job": "past"},
                "preferred_section_type": "iline-48-2000",
                "weight": 0.5,
            }
        ],
    )
    assert out["configurations"][0]["sections"][1]["width_in"] == 48
    assert all(not c["has_known_rule_violations"] for c in out["configurations"])


def test_arc_trigger_uses_actual_trip_capability_not_frame_or_lowered_setting():
    req = requirements()
    req["main"].update(trip_a=900, rated_current_a=1200)
    assert (
        "triggered"
        in check(first(req), "NEC240.87-2020-2023-trigger", "main")["reason"]
    )
    del req["main"]["maximum_trip_a"]
    assert (
        "highest" in check(first(req), "NEC240.87-2020-2023-trigger", "main")["reason"]
    )


@pytest.mark.parametrize("edition", [None, "2023", "2026"])
def test_unverified_ground_fault_code_edition_is_unknown(edition):
    req = requirements()
    req["code_edition"] = edition
    assert check(first(req), "NEC230.95-2020-trigger")["status"] == "unknown"


def test_ground_fault_boundaries_and_missing_context():
    req = requirements()
    assert check(first(req), "NEC230.95-2020-trigger")["status"] == "pass"
    req["voltage_to_ground_v"] = 150
    assert check(first(req), "NEC230.95-2020-trigger")["status"] == "not_applicable"
    req["voltage_to_ground_v"] = None
    assert check(first(req), "NEC230.95-2020-trigger")["status"] == "unknown"


@pytest.mark.parametrize(
    "bad", ["no_citation", "negative_mount", "bad_capacity", "duplicate_id"]
)
def test_edited_rule_data_requires_citations_and_consistent_types(bad):
    pack = load_rule_pack()
    if bad == "no_citation":
        pack["devices"][0]["source_refs"] = []
    elif bad == "negative_mount":
        pack["devices"][0]["mounting_space_in"] = -1
    elif bad == "bad_capacity":
        pack["devices"][0]["maximum_a"] = 9999
    else:
        pack["devices"].append(deepcopy(pack["devices"][0]))
    with pytest.raises(ValueError):
        validate_rule_pack(pack)


def test_unambiguous_ids_and_bounds_required():
    req = requirements()
    req["feeders"][0]["id"] = "main"
    with pytest.raises(ValueError):
        configure(req)
    with pytest.raises(ValueError):
        configure(requirements(), max_options=4)


def test_unsupported_provided_requirements_and_unconfirmed_wrappers_are_retained():
    req = requirements()
    req["bus_material"] = {
        "value": "copper",
        "source_claim_ids": ["material-source"],
        "confirmed": True,
    }
    req["main"]["mounting"] = {"value": "drawout", "source_claim_ids": ["mount-source"]}
    req["feeders"][0]["trip_a"] = {
        "value": 600,
        "confirmed": False,
        "source_claim_ids": ["trip-source"],
    }
    questions = first(req)["unresolved_questions"]
    assert any(
        q["key"] == "bus_material" and q["source_claim_ids"] == ["material-source"]
        for q in questions
    )
    assert any(
        q["key"] == "main.mounting" and q["requirement_value"] == "drawout"
        for q in questions
    )
    assert any(q["key"] == "F1.trip_a.confirmation" for q in questions)


def test_fault_requirement_cannot_be_weakened_by_a_lower_minimum_rating():
    req = requirements()
    req["available_fault_ka"] = 100
    result = configure(req)
    # This subset has no >=100kA feeder variant at480V; it does not use65kA.
    for d in result["configurations"]:
        assert all(
            x["catalog_sccr_ka"] is None or x["catalog_sccr_ka"] >= 100
            for x in d["devices"]
        )
        assert not d["fully_configured"]


def test_unknown_grounding_does_not_mean_ground_fault_rule_is_inapplicable():
    req = requirements()
    req["grounding"] = "unknown"
    assert check(first(req), "NEC230.95-2020-trigger")["status"] == "unknown"


def test_topology_endpoint_edits_are_revalidated():
    req = requirements()
    d = first(req)
    d["topology"]["connections"][0]["to_bus_id"] = "unrelated-bus"
    result = validate_configuration(d, req)
    assert result["has_known_rule_violations"]


def test_unbound_enclosure_dimensions_stay_null_and_standard_minima_stay_cited():
    req = requirements()
    req["constraints"] = {}
    d = first(req)
    assert all(s["depth_in"] is None and s["height_in"] is None for s in d["sections"])
    assert all(s["catalog_minimum_depth_in"] is not None for s in d["sections"])
    assert not d["fully_configured"] and not d["has_known_rule_violations"]


def test_old_requirements_or_rule_bytes_cannot_reuse_a_validation():
    req = requirements()
    d = first(req)
    changed = deepcopy(req)
    changed["voltage_to_ground_v"] = 120
    assert validate_configuration(d, changed)["has_known_rule_violations"]
    pack = load_rule_pack()
    pack["version"] = "1.0.1"
    assert validate_configuration(d, req, pack)["has_known_rule_violations"]
