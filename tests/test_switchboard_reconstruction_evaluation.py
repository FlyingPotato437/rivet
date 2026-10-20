"""Software controls only; none count as real reconstruction accuracy."""
import hashlib
import json

import pytest

from scripts.evaluate_switchboard_reconstruction import load_customer_documents, score_case, score_requirements


@pytest.fixture(autouse=True)
def database():
    # Scorer controls are pure and must not rebuild any shared test database.
    yield


def _key():
    return {"kind": "real_public", "package_id": "control", "split": "development",
        "requirements": [{"id": "order.voltage", "subject_id": "order", "key": "voltage_v", "value": 480}],
        "customer_device_inventory": [{"id": "F1", "features": {"trip_a": 600, "frame_a": 800}}],
        "approved_reconstruction_eligible": False, "approved_reference": None,
        "ineligibility_reason": "No approved manufacturer result."}


def test_input_only_case_does_not_claim_approved_reconstruction():
    result = score_case(_key(), {"requirements": {"items": [{"key": "voltage_v", "subject_id": "order", "value": 480}]},
        "design": {"devices": [{"id": "F1", "trip_a": 600, "frame_a": 800}]}})
    assert result["requirements_extraction"]["match"] == 1
    assert result["customer_inventory_compliance"]["match"] == 1
    assert result["approved_reconstruction"]["status"] == "not_applicable"


def test_partial_extraction_and_wrong_values_are_separate():
    result = score_case(_key(), {"requirements": {"items": [{"key": "voltage_v", "value": 208}]},
        "design": {"devices": [{"id": "F1", "trip_a": 60}]}})
    assert result["requirements_extraction"]["differ"] == 1
    assert result["customer_inventory_compliance"]["feature_accuracy"] == {
        "required": 2, "match": 0, "differ": 1, "missing": 1, "accuracy": 0.0}


def test_conflicting_extraction_cannot_select_correct_candidate_using_key():
    result = score_requirements(_key(), {"items": [
        {"key": "voltage_v", "value": 208}, {"key": "voltage_v", "value": 480}]})
    assert result["match"] == 0
    assert result["differ"] == 1


def test_inventory_does_not_match_wrong_identity_by_rating_or_position():
    result = score_case(_key(), {"design": {"devices": [{"id": "F2", "trip_a": 600, "frame_a": 800}]}})
    assert result["customer_inventory_compliance"]["missing"] == 1
    assert result["customer_inventory_compliance"]["extra_devices"] == ["F2"]


def test_missing_reference_approval_proof_disables_edit_estimate():
    key = _key()
    key["approved_reconstruction_eligible"] = True
    key["approved_reference"] = {"devices": key["customer_device_inventory"]}
    assert score_case(key, {})["approved_reconstruction"]["status"] == "not_applicable"


def test_bounded_edit_estimate_is_not_drafter_hours_or_cad_clicks():
    key = _key()
    key["approved_reconstruction_eligible"] = True
    key["approved_reference"] = {"approval_source_refs": [{"page": 1}],
        "devices": [{"id": "F1", "features": {"trip_a": 600, "frame_a": 800}, "placement": {"section_id": "S1"}}],
        "device_inventory_complete": True}
    scored = score_case(key, {"design": {"devices": [{"id": "F1", "trip_a": 60, "frame_a": 80, "section_id": "S2"}]}})
    edits = scored["approved_reconstruction"]["bounded_logical_edit_estimate"]
    assert (edits["lower"], edits["upper"]) == (1, 3)
    assert edits["bounded_reference_only"] is True


def test_manufacturer_reference_cannot_enter_source_reader(tmp_path):
    with pytest.raises(ValueError, match="customer-side"):
        load_customer_documents({"sources": [{"id": "bad", "path": "bad.pdf", "role": "manufacturer_reference"}]}, tmp_path / "manifest.json")


def test_source_hash_mismatch_stops_before_extraction(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_customer_documents({"sources": [{"id": "bad", "path": "source.pdf", "role": "specification", "sha256": "0" * 64}]}, tmp_path / "manifest.json")


def test_scans_are_visible_in_coverage(tmp_path):
    import pymupdf
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    docs = load_customer_documents({"sources": [{"id": "scan", "path": "scan.pdf", "role": "customer_one_line",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "pages": [1]}]}, tmp_path / "manifest.json")
    assert docs[0]["coverage"][0]["state"] == "scanned"
    assert docs[0]["spans"] == []


def test_approval_output_is_an_error_signal():
    result = score_case(_key(), {"design": {"engineering_approved": True}})
    assert result["approval_claim_violation"] is True


def test_synthetic_case_cannot_enter_real_score():
    key = _key()
    key["kind"] = "synthetic"
    with pytest.raises(ValueError, match="Synthetic"):
        score_case(key, {})


def test_boolean_is_not_numeric_true():
    key = _key()
    key["requirements"][0].update(key="spd", value=True)
    result = score_requirements(key, {"items": [{"key": "spd", "value": 1}]})
    assert result["differ"] == 1


def test_explicit_schema_aliases_do_not_count_as_extraction_errors():
    key = _key()
    key["requirements"] = [{"id": "enclosure", "key": "enclosure", "value": "NEMA1"},
        {"id": "barrier", "key": "section_barriers", "value": True}]
    result = score_requirements(key, {"items": [{"key": "enclosure_type", "value": "Type 1"},
        {"key": "inter_section_barriers", "value": True}]})
    assert result["match"] == 2
    wrong = score_requirements(key, {"items": [{"key": "enclosure_type", "value": "Type 3R"}]})
    assert wrong["differ"] == 1


def test_configurator_placeholder_is_not_counted_as_extracted_device():
    key = _key()
    key["customer_device_inventory"] = [{"id": "main", "features": {"trip_a": 1600}}]
    result = score_case(key, {"design": {"devices": [{"id": "main", "trip_a": None}]}})
    assert result["customer_inventory_compliance"]["identities_found"] == 1
    assert result["source_extracted_device_inventory"]["identities_found"] == 0
    result = score_case(key, {"requirements": {"items": [{"subject_id": "order", "key": "main.trip_a", "value": 1600}]}})
    assert result["source_extracted_device_inventory"]["match"] == 1


def test_source_pipeline_prediction_is_frozen_before_labels_open(tmp_path, monkeypatch):
    from scripts import evaluate_switchboard_reconstruction as runner
    source = tmp_path / "source.pdf"
    source.write_bytes(b"Synthetic scorer control, not real benchmark data")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"packages": [{"id": "control", "kind": "real_public", "split": "development",
        "sources": [{"id": "s", "role": "specification", "path": "source.pdf", "sha256": digest}], "answer_key": "key.json"}]}))
    (tmp_path / "key.json").write_text(json.dumps(_key()))
    output = tmp_path / "output"
    original_read = runner._read

    def observed_read(path):
        if str(path).endswith("key.json"):
            assert (output / "prediction-frozen.json").is_file()
        return original_read(path)

    def source_only(_package, _manifest, _output, **flags):
        assert flags["read_text"] and flags["vision"]
        assert not (output / "score.json").exists()
        return {"requirements": {}, "answer_key_used_as_input": False, "source_hashes": {"s": digest}}

    monkeypatch.setattr(runner, "_read", observed_read)
    monkeypatch.setattr(runner, "run_customer_case", source_only)
    result = runner.main(["--manifest", str(manifest), "--package", "control", "--run", "--read-text", "--vision", "--output", str(output)])
    assert result["source_only_run_declared"] is True
    with pytest.raises(ValueError, match="already frozen"):
        runner.main(["--manifest", str(manifest), "--package", "control", "--run", "--output", str(output)])


def test_native_baseline_rejects_accidentally_enabled_model_readers(tmp_path):
    from scripts.evaluate_switchboard_reconstruction import main
    with pytest.raises(SystemExit):
        main(["--manifest", str(tmp_path / "not-opened.json"), "--package", "control", "--run-native", "--vision", "--output", str(tmp_path / "out")])


@pytest.mark.parametrize("read_text", [False, True])
def test_drawing_note_reading_follows_explicit_semantic_reader_flag(tmp_path, monkeypatch, read_text):
    from scripts import evaluate_switchboard_reconstruction as runner
    from backend.engineering import requirements, readers, text_reader, configurator

    monkeypatch.setattr(runner, "_implementation_snapshot", lambda _output: (tmp_path, {}))
    monkeypatch.setattr(runner, "load_customer_documents", lambda *_args: [])
    monkeypatch.setattr(requirements, "extract_requirements", lambda *_args, **_kwargs: {"items": []})
    monkeypatch.setattr(requirements, "to_config_inputs", lambda _requirements: {})
    monkeypatch.setattr(configurator, "configure", lambda *_args, **_kwargs: {"configurations": []})
    monkeypatch.setattr(text_reader, "read_customer_text", lambda current, *_args, **_kwargs: (current, []))
    observed = []

    def drawing(current, _documents, **kwargs):
        observed.append(kwargs["read_notes"])
        return current, []

    monkeypatch.setattr(readers, "read_customer_drawings", drawing)
    runner.run_customer_case({"id": "synthetic-control", "sources": []}, tmp_path / "manifest.json",
        tmp_path / "output", read_text=read_text, vision=True)
    assert observed == [read_text]


def test_logical_edit_lower_bound_counts_disjoint_devices():
    key = _key()
    key["approved_reconstruction_eligible"] = True
    key["approved_reference"] = {"approval_source_refs": [{"page": 1}], "device_inventory_complete": True,
        "devices": [{"id": "F1", "features": {"trip_a": 600}}, {"id": "F2", "features": {"trip_a": 400}, "placement": {"section_id": "S1"}}]}
    result = score_case(key, {"design": {"devices": [{"id": "F1", "trip_a": 60}, {"id": "F2", "trip_a": 400, "section_id": "S2"}]}})
    assert result["approved_reconstruction"]["bounded_logical_edit_estimate"]["lower"] == 2


def _local_identity_key(page=1):
    ref = {"sha256": "a" * 64, "page": page, "source_id": "source"}
    key = _key()
    key["requirements"] = [{"id": "7.trip", "subject_id": "7", "key": "trip_a", "value": 600, "source_refs": [ref]}]
    key["customer_device_inventory"] = [{"id": "7", "features": {"trip_a": 600}, "source_refs": [ref]}]
    return key


def _local_item(subject, value, *, page=1, sha="a" * 64):
    return {"id": subject + "-claim", "subject_id": subject, "key": "trip_a", "value": value,
        "display_tag": "7", "source_refs": [{"sha256": sha, "page": page, "source_id": "source"}]}


def test_unique_literal_display_tag_is_matched_only_with_source_page_lineage():
    key = _local_identity_key()
    prediction = {"requirements": {"items": [_local_item("opaque-source-node", 600)]},
        "design": {"devices": [{"id": "opaque-source-node", "trip_a": 600}]}}
    result = score_case(key, prediction)
    assert result["requirements_extraction"]["match"] == 1
    assert result["requirements_extraction"]["false_alarms"] == 0
    assert result["source_extracted_device_inventory"]["match"] == 1
    assert result["customer_inventory_compliance"]["match"] == 1


def test_repeated_literal_circuit_on_same_page_cannot_choose_correct_rating():
    prediction = {"requirements": {"items": [_local_item("left-position", 600), _local_item("right-position", 60)]},
        "design": {"devices": [{"id": "left-position", "trip_a": 600}, {"id": "right-position", "trip_a": 60}]}}
    result = score_case(_local_identity_key(), prediction)
    assert result["requirements_extraction"]["missing"] == 1
    assert result["requirements_extraction"]["identity_ambiguities"][0]["candidate_subject_ids"] == ["left-position", "right-position"]
    assert result["source_extracted_device_inventory"]["identities_found"] == 0
    assert result["customer_inventory_compliance"]["identities_found"] == 0


def test_repeated_label_on_different_page_uses_original_page_not_row_order():
    items = [_local_item("first-page-position", 60, page=1), _local_item("second-page-position", 600, page=2)]
    result = score_case(_local_identity_key(page=2), {"requirements": {"items": list(reversed(items))},
        "design": {"devices": [{"id": "first-page-position", "trip_a": 60}, {"id": "second-page-position", "trip_a": 600}]}})
    assert result["requirements_extraction"]["match"] == 1
    assert result["source_extracted_device_inventory"]["match"] == 1
    assert result["customer_inventory_compliance"]["match"] == 1


def test_matching_display_tag_cannot_borrow_identity_from_different_source_hash():
    result = score_case(_local_identity_key(), {"requirements": {"items": [_local_item("foreign-position", 600, sha="b" * 64)]},
        "design": {"devices": [{"id": "foreign-position", "trip_a": 600}]}})
    assert result["requirements_extraction"]["missing"] == 1
    assert result["source_extracted_device_inventory"]["missing"] == 1
    assert result["customer_inventory_compliance"]["missing"] == 1


def test_generated_display_tag_without_requirement_lineage_is_not_identity():
    key = _local_identity_key()
    result = score_case(key, {"design": {"devices": [{"id": "unsupported-position", "display_tag": "7", "trip_a": 600,
        "source_refs": key["customer_device_inventory"][0]["source_refs"]}]}})
    assert result["customer_inventory_compliance"]["missing"] == 1


def test_source_local_subject_reused_across_pages_cannot_merge_device_values():
    result = score_case(_local_identity_key(), {"requirements": {"items": [
        _local_item("corrupt-shared-id", 600, page=1), _local_item("corrupt-shared-id", 60, page=2)]},
        "design": {"devices": [{"id": "corrupt-shared-id", "trip_a": 600}]}})
    assert result["requirements_extraction"]["match"] == 1
    # The malformed identity cannot supply inventory lineage across two pages.
    assert result["source_extracted_device_inventory"]["missing"] == 1
    assert result["customer_inventory_compliance"]["missing"] == 1


def test_duplicate_design_ids_do_not_allow_last_value_to_hide_conflict():
    result = score_case(_key(), {"design": {"devices": [{"id": "F1", "trip_a": 60}, {"id": "F1", "trip_a": 600, "frame_a": 800}]}})
    assert result["customer_inventory_compliance"]["missing"] == 1
    assert result["customer_inventory_compliance"]["duplicate_device_ids"] == ["F1"]


def test_inventory_reference_may_bind_an_explicit_printed_source_identifier():
    key = _local_identity_key()
    key["customer_device_inventory"][0].update(id="functional-main", source_identifier="7")
    result = score_case(key, {"requirements": {"items": [_local_item("opaque-position", 600)]}})
    assert result["source_extracted_device_inventory"]["match"] == 1
    # This reference-only scoring correspondence never mutates source identity.
    assert result["source_extracted_device_inventory"]["rows"][0]["id"] == "functional-main"


def test_legacy_exact_identifier_cannot_borrow_a_drawing_main_from_generic_spec():
    key = _local_identity_key()
    key["requirements"] = []
    key["customer_device_inventory"][0].update(id="main")
    result = score_case(key, {"requirements": {"items": [{"key": "main.trip_a", "subject_id": "order", "value": 600,
        "source_refs": [{"sha256": "b" * 64, "page": 1}]}]},
        "design": {"devices": [{"id": "main", "trip_a": 600}]}})
    assert result["source_extracted_device_inventory"]["missing"] == 1
    assert result["customer_inventory_compliance"]["missing"] == 1


def test_expected_source_specific_extraction_does_not_use_other_source_value():
    key = _local_identity_key()
    rows = [{"subject_id": "7", "key": "trip_a", "value": 600, "source_refs": [{"sha256": "a" * 64, "page": 1}]},
        {"subject_id": "7", "key": "trip_a", "value": 60, "source_refs": [{"sha256": "b" * 64, "page": 1}]}]
    result = score_requirements(key, {"items": rows})
    assert result["match"] == 1
    assert result["rows"][0]["observed"] == [600]


def test_exact_identifier_without_required_original_anchor_is_not_found():
    result = score_requirements(_local_identity_key(), {"items": [{"subject_id": "7", "key": "trip_a", "value": 600}]})
    assert result["missing"] == 1


def test_historical_reference_requires_extraction_only_mode(tmp_path):
    from scripts.evaluate_switchboard_reconstruction import run_customer_case
    with pytest.raises(ValueError, match="new purchase design"):
        run_customer_case({"id": "control", "source_authority": "historical_reference_only"}, tmp_path / "manifest.json", tmp_path)


def test_extraction_only_never_calls_configurator_or_generator(tmp_path, monkeypatch):
    from scripts import evaluate_switchboard_reconstruction as runner
    from backend.engineering import requirements, configurator, generation
    monkeypatch.setattr(runner, "_implementation_snapshot", lambda _out: (tmp_path, {}))
    monkeypatch.setattr(runner, "load_customer_documents", lambda *_args: [])
    monkeypatch.setattr(requirements, "extract_requirements", lambda *_args, **_kwargs: {"items": []})
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Historical observations must not configure/generate")
    monkeypatch.setattr(configurator, "configure", forbidden)
    monkeypatch.setattr(generation, "generate_package", forbidden)
    result = runner.run_customer_case({"id": "control", "sources": [], "source_authority": "historical_reference_only"},
        tmp_path / "manifest.json", tmp_path / "out", extraction_only=True)
    assert result["design"] is None and result["artifacts"] is None and result["configuration"] is None
    assert score_case(_key(), result)["approved_reconstruction"]["status"] == "not_applicable"


def _drawing_prediction(properties=None):
    return {"drawing_reports": [{"source_id": "source", "sha256": "a" * 64, "pages": [{"page": 1,
        "reading": {"objects": [{"key": "position", "raw_tag": "7", "kind": "prepared_device",
            "position_state": "prepared", "bbox": [0.1, 0.1, 0.2, 0.2], "properties": properties or []}]}}]}]}


def test_raw_source_position_is_located_even_without_a_numeric_value():
    key = _local_identity_key()
    key["customer_device_inventory"][0]["features"] = {}
    result = score_case(key, _drawing_prediction())
    assert result["raw_source_position_inventory"]["identities_found"] == 1
    assert result["source_extracted_device_inventory"]["identities_found"] == 0


def test_raw_source_units_do_not_turn_sccr_or_wrong_voltage_units_into_interrupting():
    key = _local_identity_key()
    key["customer_device_inventory"][0]["features"] = {"interrupting_ka": 42}
    result = score_case(key, _drawing_prediction([{"feature": "interrupting_ka", "value": 42000, "unit": "A"}]))
    assert result["raw_source_position_inventory"]["feature_accuracy"]["match"] == 1
    result = score_case(key, _drawing_prediction([{"feature": "assembly_sccr_ka", "value": 42, "unit": "kA"}]))
    assert result["raw_source_position_inventory"]["feature_accuracy"]["missing"] == 1
    result = score_case(key, _drawing_prediction([{"feature": "interrupting_ka", "value": 42, "unit": "V"}]))
    assert result["raw_source_position_inventory"]["feature_accuracy"]["missing"] == 1


def test_raw_source_conflicting_properties_stay_unknown():
    result = score_case(_local_identity_key(), _drawing_prediction([
        {"feature": "trip_a", "value": 600, "unit": "A"}, {"feature": "trip_a", "value": 60, "unit": "A"}]))
    assert result["raw_source_position_inventory"]["feature_accuracy"]["missing"] == 1
