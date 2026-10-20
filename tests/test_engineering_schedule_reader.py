"""Generated transport/projection controls, never real extraction accuracy."""

import json
from copy import deepcopy
from hashlib import sha256

import httpx
import pymupdf
import pytest

from backend.engineering.requirements import (
    extract_requirements,
    merge_drawing_readings,
)
from backend.engineering.schedule_reader import (
    ScheduleReading,
    combine_schedule_and_drawing,
    project_schedule,
    read_schedule_evidence,
    transcribe_schedule,
)


@pytest.fixture(autouse=True)
def database():
    yield


def sample():
    columns = [
        {
            "key": "frame",
            "header": "FRAME SIZE",
            "feature": "frame_a",
            "unit": "A",
            "bbox": [0.2, 0.1, 0.4, 0.2],
            "confidence": "high",
        },
        {
            "key": "cont",
            "header": "CONT. CURRENT RATING",
            "feature": "rated_current_a",
            "unit": "A",
            "bbox": [0.4, 0.1, 0.6, 0.2],
            "confidence": "high",
        },
        {
            "key": "ic",
            "header": "INTERRUPT RATING RMS AMP SYMM",
            "feature": "interrupting_ka",
            "unit": "RMS AMP SYMM",
            "bbox": [0.6, 0.1, 0.8, 0.2],
            "confidence": "high",
        },
    ]
    rows = []
    for n in range(3):
        y = 0.3 + n * 0.15
        rows.append(
            {
                "key": f"r{n}",
                "circuit_label": str(n + 1),
                "raw_tag": None,
                "raw_label": str(n + 1),
                "position_state": "prepared" if n == 2 else "populated",
                "state_evidence": "SPACE" if n == 2 else "breaker schedule row",
                "confidence": "high",
                "bbox": [0.1, y, 0.9, y + 0.1],
                "cells": [
                    {
                        "column_key": "frame",
                        "raw_text": "" if n else "400",
                        "value": None if n else "400",
                        "shared_id": "arrow1" if n == 1 else None,
                        "confidence": "high",
                        "bbox": [0.2, y, 0.4, y + 0.1],
                    },
                    {
                        "column_key": "cont",
                        "raw_text": "100" if n < 2 else "",
                        "value": "100" if n < 2 else None,
                        "shared_id": None,
                        "confidence": "high",
                        "bbox": [0.4, y, 0.6, y + 0.1],
                    },
                    {
                        "column_key": "ic",
                        "raw_text": "42,000" if not n else "",
                        "value": "42,000" if not n else None,
                        "shared_id": None,
                        "confidence": "high",
                        "bbox": [0.6, y, 0.8, y + 0.1],
                    },
                ],
            }
        )
    return {
        "tables": [
            {
                "key": "t1",
                "crop_index": 0,
                "title": "CIRCUIT BREAKER SCHEDULE",
                "bbox": [0, 0, 1, 1],
                "columns": columns,
                "rows": rows,
                "shared_cells": [
                    {
                        "key": "arrow1",
                        "column_key": "frame",
                        "raw_text": "400",
                        "value": "400",
                        "value_bbox": [0.2, 0.3, 0.4, 0.4],
                        "bbox": [0.2, 0.3, 0.4, 0.56],
                        "row_keys": ["r0", "r1"],
                        "kind": "continuation_arrow",
                        "evidence": "Visible arrow ends at r1",
                        "confidence": "high",
                    }
                ],
                "enumeration_complete": True,
                "warnings": [],
            }
        ],
        "warnings": [],
    }


def regions():
    return [{"bbox": [0.1, 0.2, 0.5, 0.8], "png": b"test injected provider bytes"}]


def test_explicit_shared_scope_and_header_units_preserve_semantics():
    result = project_schedule(sample(), regions())
    first, second, prepared = result["objects"]
    by = lambda obj: {p["feature"]: p for p in obj["properties"]}
    assert by(first)["interrupting_ka"]["value"] == 42
    assert by(first)["rated_current_a"]["value"] == 100
    assert "trip_a" not in by(first)
    assert by(second)["frame_a"]["value"] == 400
    assert by(prepared)["frame_a"]["value"] is None
    assert prepared["position_state"] == "prepared"
    assert (
        by(second)["frame_a"]["schedule_provenance"]["shared_value"]["kind"]
        == "continuation_arrow"
    )
    assert second["representation_role"] == "schedule_row"
    assert first["bbox"] == pytest.approx([0.14, 0.38, 0.46, 0.44])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_column",
        "duplicate_row",
        "wrong_column",
        "out_of_scope",
        "copied_number",
        "absent_crop",
    ],
)
def test_invalid_shared_associations_fail_closed(mutation):
    data = sample()
    table = data["tables"][0]
    if mutation == "missing_column":
        table["rows"][0]["cells"].pop()
    elif mutation == "duplicate_row":
        table["rows"].append(deepcopy(table["rows"][0]))
    elif mutation == "wrong_column":
        table["shared_cells"][0]["column_key"] = "cont"
    elif mutation == "out_of_scope":
        table["shared_cells"][0]["bbox"][3] = 0.41
    elif mutation == "copied_number":
        table["rows"][1]["cells"][0]["value"] = "400"
    else:
        table["crop_index"] = 1
    with pytest.raises(ValueError):
        project_schedule(data, regions())


def test_low_confidence_unknown_unit_and_plain_blanks_do_not_inherit():
    data = sample()
    data["tables"][0]["columns"][2]["unit"] = None
    data["tables"][0]["columns"][2]["header"] = "INTERRUPT RATING"
    data["tables"][0]["rows"][0]["cells"][0]["confidence"] = "low"
    objects = project_schedule(data, regions())["objects"]
    assert objects[0]["properties"][0]["value"] is None
    assert objects[0]["properties"][2]["value"] is None
    assert objects[1]["properties"][2]["value"] is None


def test_schema_keys_are_not_answer_keys():
    assert "expected" not in str(ScheduleReading.model_json_schema()).lower()


def test_context_note_is_not_an_equipment_table():
    context = regions()
    context[0]["kind"] = "context_notes"
    with pytest.raises(ValueError, match="note crop"):
        project_schedule(sample(), context)


def test_blank_capacity_is_retained_but_cannot_create_a_prepared_feeder():
    from backend.engineering.requirements import to_config_inputs

    data = sample()
    table = data["tables"][0]
    table["columns"][2].update(feature="description", header="REMARKS", unit=None)
    table["rows"][2]["cells"][0].update(raw_text="400", value="400")
    table["rows"][2]["cells"][2].update(raw_text="BLANK", value=None)
    projected = project_schedule(data, regions())
    blank = projected["objects"][2]
    assert blank["position_state"] == "unknown"
    assert blank["equipment_presence"] == "blank_provisions_unknown"
    assert blank["properties"][0]["value"] == 400
    assert not blank["configuration_eligible"]
    report = {
        "source_id": "s",
        "sha256": "a" * 64,
        "version": "fixture",
        "prompt_sha256": "b" * 64,
        "pages": [{"page": 1, "reading": projected}],
    }
    req = merge_drawing_readings(extract_requirements([]), [report])
    assert all(
        not i["configuration_eligible"] for i in req["items"] if i["display_tag"] == "3"
    )
    assert all(f.get("display_tag") != "3" for f in to_config_inputs(req)["feeders"])


def test_empty_ruled_paper_is_retained_for_coverage_not_inventory():
    data = sample()
    row = data["tables"][0]["rows"][2]
    row.update(
        circuit_label=None,
        raw_tag=None,
        raw_label="",
        state_evidence="",
        position_state="unknown",
    )
    for cell in row["cells"]:
        cell.update(raw_text="", value=None, shared_id=None)
    result = project_schedule(data, regions())
    assert len(result["objects"]) == 2
    assert len(result["non_equipment_regions"]) == 1
    assert result["non_equipment_regions"][0]["raw_row"]["key"] == "r2"


@pytest.mark.parametrize(
    "heading,unit",
    [
        ("FRAME HEIGHT (IN)", "IN"),
        ("FRAME WIDTH MM", "MM"),
        ("FRAME DIMENSIONS", None),
        ("FRAME SIZE", "in"),
        ("TRIP TIME SEC", "SEC"),
    ],
)
def test_physical_frames_and_trip_time_never_become_ampacity(heading, unit):
    data = sample()
    data["tables"][0]["columns"][0].update(header=heading, unit=unit)
    assert (
        project_schedule(data, regions())["objects"][0]["properties"][0]["value"]
        is None
    )


def test_immutable_source_model_prompt_crop_cache(tmp_path):
    pdf = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(pdf)
    source = {
        "id": "source",
        "path": str(pdf),
        "sha256": sha256(pdf.read_bytes()).hexdigest(),
    }
    calls = []

    def provider(images, *, model):
        calls.append(model)
        return sample(), {"output_tokens": 10}

    report = read_schedule_evidence(
        source, 1, regions(), output_dir=tmp_path / "out", provider=provider
    )
    again = read_schedule_evidence(
        source, 1, regions(), output_dir=tmp_path / "out", provider=provider
    )
    assert calls == ["gpt-6.1-sol"]
    assert again["pages"][0]["cache_hit"]
    req = merge_drawing_readings(extract_requirements([]), [report])
    ic = next(
        i
        for i in req["items"]
        if i["key"] == "interrupting_ka" and i["value"] is not None
    )
    assert ic["value"] == 42 and ic["unit"] == "kA"
    assert ic["schedule_provenance"]["header"] == "INTERRUPT RATING RMS AMP SYMM"
    assert not ic["confirmed"] and report["production_released"] is False
    changed = regions()
    changed[0]["bbox"][0] = 0.09
    read_schedule_evidence(
        source, 1, changed, output_dir=tmp_path / "out", provider=provider
    )
    assert len(calls) == 2
    source["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        read_schedule_evidence(
            source, 1, regions(), output_dir=tmp_path / "out", provider=provider
        )


@pytest.mark.parametrize(
    "unit,value,expected",
    [("A", 42000, 42), ("kA", 42, 42), ("kAIC", 42, 42), ("MVA", 42, None)],
)
def test_device_interrupting_projection_is_not_assembly_sccr(unit, value, expected):
    raw = {
        "objects": [
            {
                "key": "breaker",
                "kind": "device_symbol",
                "raw_label": "BKR-1",
                "raw_tag": "BKR-1",
                "position_state": "populated",
                "properties": [
                    {
                        "feature": "interrupting_ka",
                        "value": value,
                        "unit": unit,
                        "raw_text": str(value),
                        "confidence": "high",
                        "bbox": [0.1, 0.1, 0.2, 0.2],
                    },
                    {
                        "feature": "assembly_sccr_ka",
                        "value": 65,
                        "unit": "kA",
                        "raw_text": "assembly 65 kA",
                        "confidence": "high",
                        "bbox": [0.1, 0.1, 0.2, 0.2],
                    },
                ],
            }
        ]
    }
    report = {
        "source_id": "s",
        "sha256": "a" * 64,
        "version": "fixture",
        "prompt_sha256": "b" * 64,
        "pages": [{"page": 1, "reading": raw}],
    }
    req = merge_drawing_readings(extract_requirements([]), [report])
    assert len(req["items"]) == 1
    assert req["items"][0]["value"] == expected
    assert req["items"][0]["key"] == "interrupting_ka"
    assert "minimum_interrupting_ka" not in {i["key"] for i in req["items"]}


def test_same_label_control_contact_and_connected_objects_remain_separate():
    schedule = {
        "source_id": "s",
        "sha256": "a" * 64,
        "version": "schedule-v1",
        "prompt_sha256": "b" * 64,
        "pages": [{"page": 1, "reading": project_schedule(sample(), regions())}],
    }
    row = schedule["pages"][0]["reading"]["objects"][0]
    old = deepcopy(row)
    old["key"] = "old_table_row"
    contact = deepcopy(old)
    contact.update(key="contact_elsewhere", bbox=[0.7, 0.1, 0.8, 0.2])
    wired = deepcopy(old)
    wired["key"] = "connected_symbol"
    original = {
        "source_id": "s",
        "sha256": "a" * 64,
        "version": "graph-v1",
        "prompt_sha256": "c" * 64,
        "pages": [
            {
                "page": 1,
                "reading": {
                    "objects": [old, contact, wired],
                    "conductors": [
                        {"from_object": "connected_symbol", "to_object": "bus"}
                    ],
                },
            }
        ],
    }
    result = combine_schedule_and_drawing(original, schedule)
    keys = {o["key"] for o in result["pages"][0]["reading"]["objects"]}
    assert "old_table_row" not in keys
    assert {"contact_elsewhere", "connected_symbol"} <= keys
    assert result["pages"][0]["superseded_observations"][0]["observation"] == old
    assert original["pages"][0]["reading"]["objects"][0] == old
    schedule["sha256"] = "d" * 64
    with pytest.raises(ValueError, match="across originals"):
        combine_schedule_and_drawing(original, schedule)


@pytest.mark.parametrize(
    "status,body", [("completed", True), ("incomplete", False), ("completed", False)]
)
def test_schedule_transport_accepts_only_complete_valid_observations(
    status, body, monkeypatch
):
    import backend.engineering.schedule_reader as reader

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-no-network-key")
    client_type = httpx.Client
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": status,
                "usage": {"output_tokens": 3},
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(sample()) if body else '{"tables":[',
                            }
                        ]
                    }
                ],
            },
        )

    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    if body and status == "completed":
        value, usage = transcribe_schedule(
            [([0, 0, 1, 1], b"source-crop")], model="gpt-6.1-sol"
        )
        assert len(value["tables"][0]["rows"]) == 3
        assert usage["request_settings"]["timeout_seconds"] == 900
    else:
        with pytest.raises(reader.DrawingProviderError):
            transcribe_schedule([([0, 0, 1, 1], b"source-crop")], model="gpt-6.1-sol")
    assert calls[0]["store"] is False
    assert calls[0]["text"]["format"]["strict"] is True
