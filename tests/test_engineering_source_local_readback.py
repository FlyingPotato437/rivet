"""Synthetic generated-ID/readback and input-citation controls, not accuracy."""

import csv
import hashlib
from pathlib import Path

import ezdxf
import pytest

from backend.engineering.configurator import configure
from backend.engineering.generation import generate_package
from backend.equipment.readers import clean_tag, read_source
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    yield


def _read(path):
    return read_source(
        SourceInput(
            id="synthetic-readback",
            name=path.name,
            path=str(path),
            role="bom" if path.suffix in {".xlsx", ".csv"} else "drawing",
            revision="SYNTHETIC",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            lifecycle="proposed",
        )
    )


def _model():
    tags = ["drawing:" + "a" * 32, "drawing:" + "b" * 32]
    return {
        "model_id": "synthetic-source-local-model",
        "model_version": "1",
        "manufacturer": "Synthetic lineup",
        "family": "Synthetic family",
        "sections": [
            {
                "id": "S1",
                "width_in": None,
                "depth_in": None,
                "height_in": None,
                "positions": [],
            }
        ],
        "devices": [
            {
                "id": tag,
                "tag": tag,
                "display_tag": "7",
                "section_label": section,
                "source_local_identity": True,
                "position_state": "prepared" if index == 0 else "unknown",
                "role": "feeder",
                "section_id": "S1",
                "frame_a": 250,
                "trip_a": 225,
                "pole_count": 3,
                "supplied": False if index == 0 else None,
            }
            for index, (tag, section) in enumerate(zip(tags, ("A", "B"), strict=True))
        ],
    }


def test_generated_dxf_and_bom_preserve_two_local_sevens_with_exact_binding(tmp_path):
    model = _model()
    package = generate_package(model, output_dir=tmp_path / "package")
    for kind in ("one_line_dxf", "bom_xlsx"):
        path = Path(next(artifact for artifact in package["artifacts"] if artifact["kind"] == kind)["path"])
        report, facts = _read(path)
        expected = {d["tag"] for d in model["devices"]}
        assert set(report["observed_device_tags"]) == expected
        assert set(report["source_local_identities"]) == expected
        assert {f["device_tag"] for f in facts if f["field"] == "frame_a"} == expected
        for fact in facts:
            assert fact["identity_scope"] == "source_local"
            assert fact["source_local_identity"] is True
            assert fact["evidence"]["source_label"] == "7"
            assert fact["evidence"]["section_label"] in {"A", "B"}
            assert fact["evidence"]["physical_identity_verified"] is False
            assert fact["evidence"]["model_device_id"] == fact["device_tag"]


def _dxf(tmp_path, mutate=None, *, tag=None):
    tag = tag or "drawing:" + "c" * 32
    attrs = [
        ("EQUIPMENT_TAG", tag),
        ("IDENTITY_SCOPE", "source_local"),
        ("MODEL_ID", "synthetic-model"),
        ("MODEL_VERSION", "1"),
        ("MODEL_DEVICE_ID", tag),
        ("SOURCE_LABEL", "7"),
        ("SECTION_LABEL", "A"),
        ("FRAME_A", "250"),
        ("TRIP_A", "225"),
    ]
    if mutate:
        attrs = mutate(attrs)
    drawing = ezdxf.new("R2010")
    drawing.blocks.new("SYNTHETIC_POSITION")
    insert = drawing.modelspace().add_blockref("SYNTHETIC_POSITION", (0, 0))
    for name, value in attrs:
        insert.add_attrib(name, value)
    path = tmp_path / "source-local-control.dxf"
    drawing.saveas(path)
    return path


@pytest.mark.parametrize(
    "missing", ["IDENTITY_SCOPE", "MODEL_ID", "MODEL_VERSION", "MODEL_DEVICE_ID"]
)
def test_opaque_dxf_id_without_each_exact_binding_is_not_an_enterprise_tag(
    tmp_path, missing
):
    path = _dxf(tmp_path, lambda attrs: [(k, v) for k, v in attrs if k != missing])
    report, facts = _read(path)
    assert not facts and not report["observed_device_tags"]
    assert any(w["code"] == "unbound_source_local_identity" for w in report["warnings"])


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong-device",
        "wrong-scope",
        "duplicate-equipment",
        "duplicate-model",
        "contradictory-tag",
    ],
)
def test_conflicting_or_duplicate_generated_identity_is_rejected(tmp_path, mutation):
    def mutate(attrs):
        if mutation == "wrong-device":
            return [
                (k, "drawing:" + "d" * 32 if k == "MODEL_DEVICE_ID" else v)
                for k, v in attrs
            ]
        if mutation == "wrong-scope":
            return [(k, "enterprise" if k == "IDENTITY_SCOPE" else v) for k, v in attrs]
        if mutation == "duplicate-equipment":
            return attrs + [attrs[0]]
        if mutation == "duplicate-model":
            return attrs + [("MODEL_ID", "another-model")]
        return attrs + [("TAG", "CB-1")]

    report, facts = _read(_dxf(tmp_path, mutate))
    assert not facts and not report["observed_device_tags"]


def test_source_local_namespace_is_narrow_and_enterprise_tag_rules_unchanged(tmp_path):
    assert clean_tag("drawing:" + "a" * 32) is None
    assert clean_tag("cb-1") == "CB-1"
    report, facts = _read(_dxf(tmp_path, tag="drawing:not-a-source-hash"))
    assert not facts and not report["observed_device_tags"]
    path = _dxf(
        tmp_path,
        lambda attrs: [
            (k, v)
            for k, v in attrs
            if k
            not in {"IDENTITY_SCOPE", "MODEL_ID", "MODEL_VERSION", "MODEL_DEVICE_ID"}
        ],
        tag="CB-1",
    )
    report, facts = _read(path)
    assert report["observed_device_tags"] == ["CB-1"]
    assert facts and not report["source_local_identities"]


def test_bom_requires_binding_on_each_row_without_forward_fill(tmp_path):
    tags = ["drawing:" + "e" * 32, "drawing:" + "f" * 32]
    path = tmp_path / "synthetic-source-local.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "equipment_tag",
                "frame_a",
                "trip_a",
                "identity_scope",
                "model_id",
                "model_version",
                "model_device_id",
                "source_label",
                "section_label",
            ]
        )
        writer.writerow(
            [
                tags[0],
                250,
                225,
                "source_local",
                "synthetic-model",
                "1",
                tags[0],
                "7",
                "A",
            ]
        )
        writer.writerow([tags[1], 250, 225, "source_local", "", "1", tags[1], "7", "B"])
    report, facts = _read(path)
    assert report["observed_device_tags"] == [tags[0]]
    assert {f["device_tag"] for f in facts} == {tags[0]}
    assert any(
        w["code"] == "unbound_source_local_identity" and w["row"] == 3
        for w in report["warnings"]
    )


def test_input_page_citations_survive_typed_main_and_catalog_selection():
    original = {
        "source_id": "synthetic-customer",
        "sha256": "0" * 64,
        "page": 4,
        "bbox": [0.1, 0.2, 0.3, 0.4],
    }
    req = {
        "service_rating_a": 1200,
        "voltage_v": 480,
        "current_type": "AC",
        "main": {
            "kind": "breaker",
            "trip_a": {"value": 600, "confirmed": True, "source_refs": [original]},
            "poles": 3,
            "trip_type": "LI",
            "source_refs": [original],
        },
        "feeders": [],
        "constraints": {},
    }
    result = configure(req)
    assert result["configurations"]
    main = result["configurations"][0]["devices"][0]
    assert main["source_refs"].count(original) == 1
    assert any(
        ref.get("source_id") != "synthetic-customer" for ref in main["source_refs"]
    )
