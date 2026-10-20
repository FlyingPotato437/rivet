"""Synthetic bus-selection and source-system integrity controls, not design accuracy."""

from copy import deepcopy

import pytest
from test_engineering_configurator import check, requirements

from backend.engineering.configurator import (
    RequirementValue,
    configure,
    load_rule_pack,
    validate_configuration,
    validate_rule_pack,
)


@pytest.fixture(autouse=True)
def database():
    yield


def explicit_bus_pack():
    pack = load_rule_pack()
    # This deliberately synthetic extension tests enumeration handling. It is
    # not added to or attributed to the real manufacturer rule data.
    pack["sources"]["synthetic-through-bus"] = {"document": "Synthetic test enumeration"}
    pack["scope"]["through_bus_standard_ratings_a"] = [1200, 2000, 3000]
    pack["scope"]["source_refs"] = [{"source_id": "synthetic-through-bus", "page": 1}]
    return pack


@pytest.mark.parametrize("service", [600, 1000, 1200, 2000])
def test_cited_range_alone_never_becomes_a_discrete_bus_choice(service):
    req = requirements()
    req["service_rating_a"] = service
    for design in configure(req)["configurations"]:
        assert design["sections"][0]["bus_a"] is None
        assert design["topology"]["bus_a"] is None
        assert design["topology"]["connections"]
        assert design["system"]["service_rating_a"] == service
        assert check(design, "qed2.through_bus")["status"] == "unknown"
        question = next(q for q in design["unresolved_questions"] if q["key"] == "through_bus_a")
        assert question["source_refs"] and not design["fully_configured"]


@pytest.mark.parametrize("service,expected", [(600, 1200), (1000, 1200), (1200, 1200), (1600, 2000), (2500, 3000)])
def test_explicit_synthetic_bus_choices_select_smallest_adequate_rating(service, expected):
    req = requirements()
    req["service_rating_a"] = service
    pack = explicit_bus_pack()
    for design in configure(req, rule_pack=pack)["configurations"]:
        assert design["sections"][0]["bus_a"] == expected
        assert design["topology"]["bus_a"] == expected
        assert design["system"]["service_rating_a"] == service
        assert check(design, "qed2.through_bus")["status"] == "pass"
        assert check(design, "qed2.through_bus")["source_refs"][0]["source_id"] == "synthetic-through-bus"
        assert not any(q["key"] == "through_bus_a" for q in design["unresolved_questions"])


@pytest.mark.parametrize("value", [1000, 6500, True, "2000"])
def test_edited_bus_outside_cited_scope_or_with_invalid_type_fails(value):
    req = requirements()
    design = configure(req)["configurations"][0]
    design["sections"][0]["bus_a"] = design["topology"]["bus_a"] = value
    result = validate_configuration(design, req)
    assert check(result, "qed2.through_bus")["status"] == "fail"
    assert result["has_known_rule_violations"] and not result["fully_configured"]


def test_in_range_edited_bus_without_discrete_catalog_support_stays_unknown():
    req = requirements()
    design = configure(req)["configurations"][0]
    design["sections"][0]["bus_a"] = design["topology"]["bus_a"] = 1800
    result = validate_configuration(design, req)
    assert check(result, "qed2.through_bus")["status"] == "unknown"
    assert not result["fully_configured"]


@pytest.mark.parametrize("edit", ["below-service", "not-enumerated", "mismatched-section"])
def test_edited_explicit_bus_is_revalidated_independently_of_breaker(edit):
    req = requirements()
    req["service_rating_a"] = 1600
    pack = explicit_bus_pack()
    design = configure(req, rule_pack=pack)["configurations"][0]
    value = 1200 if edit == "below-service" else 1800 if edit == "not-enumerated" else 3000
    design["topology"]["bus_a"] = value
    if edit != "mismatched-section":
        design["sections"][0]["bus_a"] = value
    result = validate_configuration(design, req, pack)
    assert check(result, "qed2.through_bus")["status"] == "fail"


def test_main_rating_and_required_minimum_do_not_set_through_bus_capacity():
    req = requirements()
    req["service_rating_a"] = 1000
    req["main"].update(trip_a=600, required_ampacity_a=600, maximum_trip_a=600)
    design = configure(req, rule_pack=explicit_bus_pack())["configurations"][0]
    assert design["topology"]["bus_a"] == 1200
    assert next(d for d in design["devices"] if d["role"] == "main")["trip_a"] == 600
    assert check(design, "input.main_required_ampacity")["status"] == "pass"


@pytest.mark.parametrize("values", [[1000], [7000], [2000, 2000], [True], ["2000"]])
def test_malformed_or_out_of_range_discrete_bus_pack_is_rejected(values):
    pack = explicit_bus_pack()
    pack["scope"]["through_bus_standard_ratings_a"] = values
    with pytest.raises(ValueError, match="through-bus ratings"):
        validate_rule_pack(pack)


def test_system_scalars_and_each_source_binding_are_preserved_without_defaults():
    req = requirements()
    ref = {"source_id": "synthetic-input", "page": 2, "sha256": "a" * 64}
    req["voltage_v"] = {"value": 480, "confirmed": True, "source_claim_ids": ["voltage-claim"], "source_refs": [ref]}
    req["service_rating_a"] = RequirementValue(1000, True, ("service-claim",))
    req.pop("frequency_hz")
    req.pop("wires")
    original = deepcopy(req)
    design = configure(req)["configurations"][0]
    system = design["system"]
    assert system["voltage_v"] == 480 and system["service_rating_a"] == 1000
    assert system["frequency_hz"] is None and system["wires"] is None
    assert system["available_fault_ka"] is None
    assert set(system["source_claim_ids"]) == {"voltage-claim", "service-claim"}
    assert system["source_refs"] == [ref]
    assert system["provenance"]["voltage_v"]["source_refs"] == [ref]
    assert system["provenance"]["service_rating_a"]["source_claim_ids"] == ["service-claim"]
    assert req == original
    system["source_refs"][0]["page"] = 9
    assert req["voltage_v"]["source_refs"][0]["page"] == 2


@pytest.mark.parametrize("edit", ["voltage", "missing", "invented-source"])
def test_edited_or_missing_system_cannot_pass_revalidation(edit):
    req = requirements()
    design = configure(req)["configurations"][0]
    if edit == "voltage":
        design["system"]["voltage_v"] = 600
    elif edit == "missing":
        design.pop("system")
    else:
        design["system"]["source_refs"].append({"page": 99})
    result = validate_configuration(design, req)
    assert check(result, "input.system_binding")["status"] == "fail"
    assert not result["fully_configured"]


def test_unknown_edited_device_identity_fails_validation_without_inventing_a_requirement():
    req = requirements()
    design = configure(req)["configurations"][0]
    device = next(device for device in design["devices"] if device["id"] == "F1")
    device["id"] = "unbound-client-device"
    for section in design["sections"]:
        for position in section["positions"]:
            if position["device_id"] == "F1":
                position["device_id"] = device["id"]
    result = validate_configuration(design, req)
    failure = check(result, "input.device_requirement_binding", device["id"])
    assert failure["status"] == "fail" and failure["source_refs"] == []
    assert result["has_known_rule_violations"] and not result["fully_configured"]
    assert any(question["key"] == device["id"] + ".requirement_binding" for question in result["unresolved_questions"])


def test_explicit_quoted_source_nameplate_and_part_text_survive_as_unapproved_proposal():
    req = requirements()
    refs = [{"source_id": "synthetic-device-text", "page": 3, "sha256": "b" * 64}]
    for key, value in {"nameplate": '"LOAD A — RETAIN THIS TEXT"', "part_number": '"SOURCE-SKU-123"'}.items():
        req["feeders"][0][key] = {"value": value, "confirmed": True, "source_claim_ids": ["source-" + key], "source_refs": refs}
    design = configure(req)["configurations"][0]
    device = next(device for device in design["devices"] if device["id"] == "F1")
    assert device["nameplate"] == req["feeders"][0]["nameplate"]["value"]
    assert device["part_number"] == req["feeders"][0]["part_number"]["value"]
    assert refs[0] in device["source_refs"]
    assert {"source-nameplate", "source-part_number"} <= set(device["source_claim_ids"])
    assert not any(question["key"] == "F1.nameplate" for question in design["unresolved_questions"])
    assert any(question["key"] == "F1.exact_product" for question in design["unresolved_questions"])
    assert not design["fully_configured"] and not design["engineering_approved"]


def test_missing_device_text_is_not_filled_from_tag_or_catalog_frame():
    design = configure(requirements())["configurations"][0]
    assert all(device["nameplate"] is None and device["part_number"] is None for device in design["devices"])


@pytest.mark.parametrize("field", ["nameplate", "part_number"])
def test_edited_device_source_text_cannot_override_unknown_or_explicit_requirement(field):
    req = requirements()
    req["feeders"][0][field] = "EXPLICIT SOURCE TEXT"
    design = configure(req)["configurations"][0]
    next(device for device in design["devices"] if device["id"] == "F1")[field] = "CHANGED CLIENT TEXT"
    result = validate_configuration(design, req)
    assert check(result, "input.device_source_text", "F1")["status"] == "fail"
    assert result["has_known_rule_violations"] and not result["fully_configured"]


@pytest.mark.parametrize("reuse", [False, True])
def test_explicit_main_kind_and_citation_survive_new_or_reused_selection(reuse):
    req = requirements()
    ref = {"source_id": "synthetic-main-type", "page": 4}
    req["main"]["kind"] = {"value": "circuit_breaker", "confirmed": True, "source_claim_ids": ["source-main-kind"], "source_refs": [ref]}
    if reuse:
        req["main"]["reuse_existing"] = True
    design = configure(req)["configurations"][0]
    main = next(device for device in design["devices"] if device["role"] == "main")
    assert main["kind"] == "circuit_breaker"
    assert ref in main["source_refs"] and "source-main-kind" in main["source_claim_ids"]
    assert main["catalog_variant_id"] is None if reuse else main["catalog_variant_id"] is not None


def test_missing_device_kind_is_not_filled_from_selected_breaker_catalog():
    req = requirements()
    req["main"].pop("kind")
    design = configure(req)["configurations"][0]
    assert all(device["kind"] is None for device in design["devices"])
    assert not design["fully_configured"]


def test_edited_source_device_kind_fails_validation_instead_of_losing_type():
    req = requirements()
    design = configure(req)["configurations"][0]
    next(device for device in design["devices"] if device["role"] == "main")["kind"] = None
    result = validate_configuration(design, req)
    assert check(result, "input.device_source_text", "main")["status"] == "fail"
    assert result["has_known_rule_violations"] and not result["fully_configured"]
