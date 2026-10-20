"""Synthetic model-local file identity controls; no source accuracy claim."""

import csv
import hashlib
import json
from pathlib import Path

import ezdxf
import pytest

from backend.drafting.service import DraftingError
from backend.engineering.generation import generate_package
from backend.equipment.native_bom import _attributes, _index, verify_inventory
from backend.equipment.readers import clean_tag, read_source
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    yield  # File-only controls never connect to/reset a database.


def tag(model="synthetic-model", device="main"):
    payload = json.dumps(
        [str(model), str(device)], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "model:" + hashlib.sha256(payload).hexdigest()[:32]


def attrs(model="synthetic-model", device="main", version="1"):
    return [
        ("EQUIPMENT_TAG", tag(model, device)),
        ("IDENTITY_SCOPE", "model_local"),
        ("MODEL_ID", model),
        ("MODEL_VERSION", version),
        ("MODEL_DEVICE_ID", device),
        ("SOURCE_LABEL", ""),
        ("FRAME_A", "1600"),
        ("TRIP_A", "1200"),
        ("PART_NUMBER", "SYNTHETIC-PART"),
        ("QUANTITY", "1"),
    ]


def dxf(tmp_path, pairs=None, *, duplicate=False):
    document = ezdxf.new("R2010")
    document.blocks.new("SYNTHETIC_DEVICE")
    for index in range(2 if duplicate else 1):
        insert = document.modelspace().add_blockref("SYNTHETIC_DEVICE", (index * 20, 0))
        for key, value in pairs or attrs():
            insert.add_attrib(key, value)
    path = tmp_path / "synthetic-model-local.dxf"
    document.saveas(path)
    return path


def bom(tmp_path, rows=None):
    pairs = attrs()
    path = tmp_path / "synthetic-model-local.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([key.lower() for key, _ in pairs])
        writer.writerows(rows or [[value for _, value in pairs]])
    return path


def read(path):
    return read_source(
        SourceInput(
            id="synthetic-file",
            name=path.name,
            path=str(path),
            role="drawing" if path.suffix == ".dxf" else "bom",
            revision="TEST",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    )


def test_bound_unlabelled_device_is_read_by_opaque_model_identity_without_inventing_source_label(
    tmp_path,
):
    for path in (dxf(tmp_path), bom(tmp_path)):
        report, facts = read(path)
        assert report["observed_device_tags"] == [tag()]
        assert not report["source_local_identities"]
        assert report["model_local_identities"][tag()]["model_device_id"] == "main"
        for fact in facts:
            assert fact["model_local_identity"] is True
            assert fact["source_local_identity"] is False
            assert fact["identity_scope"] == "model_local"
            assert fact["model_device_id"] == "main"
            assert (
                fact["model_id"] == "synthetic-model" and fact["model_version"] == "1"
            )
            assert not fact["evidence"]["source_label"]
            assert fact["evidence"]["physical_identity_verified"] is False
    assert verify_inventory(dxf(tmp_path), bom(tmp_path))["status"] == "verified"


def test_existing_enterprise_tag_cleaner_is_unchanged():
    assert clean_tag("main") is None
    assert clean_tag(tag()) is None
    assert clean_tag("drawing:" + "a" * 32) is None
    assert clean_tag("cb-1") == "CB-1"


@pytest.mark.parametrize(
    "missing",
    ["EQUIPMENT_TAG", "IDENTITY_SCOPE", "MODEL_ID", "MODEL_VERSION", "MODEL_DEVICE_ID"],
)
def test_each_model_binding_attribute_is_required_on_its_own_block(tmp_path, missing):
    path = dxf(tmp_path, [(key, value) for key, value in attrs() if key != missing])
    report, facts = read(path)
    assert not report["observed_device_tags"] and not facts
    assert any(w["code"] == "unbound_model_local_identity" for w in report["warnings"])
    with pytest.raises(DraftingError, match="model-local inventory ID"):
        _attributes(ezdxf.readfile(path))


@pytest.mark.parametrize(
    "change",
    [
        ("MODEL_ID", "foreign-model"),
        ("MODEL_DEVICE_ID", "foreign-device"),
        ("MODEL_DEVICE_ID", ""),
        ("MODEL_VERSION", ""),
        ("MODEL_VERSION", "UNKNOWN"),
        ("IDENTITY_SCOPE", "enterprise"),
        ("EQUIPMENT_TAG", "model:" + "c" * 32),
        ("EQUIPMENT_TAG", "MODEL:" + "c" * 32),
    ],
)
def test_wrong_hash_foreign_model_empty_identity_or_scope_is_rejected(tmp_path, change):
    changed = [
        (key, change[1] if key == change[0] else value) for key, value in attrs()
    ]
    report, facts = read(dxf(tmp_path, changed))
    assert not facts and not report["observed_device_tags"]


@pytest.mark.parametrize(
    "extra",
    [
        [("MODEL_ID", "synthetic-model")],
        [("MODEL_VERSION", "1")],
        [("MODEL_DEVICE_ID", "main")],
        [("EQUIPMENT_TAG", tag())],
        [("TAG", tag())],
        [("TAG", "CB-1")],
    ],
)
def test_duplicate_or_contradictory_binding_on_a_block_is_rejected(tmp_path, extra):
    path = dxf(tmp_path, attrs() + extra)
    report, facts = read(path)
    assert not facts and not report["observed_device_tags"]
    with pytest.raises(DraftingError, match="model-local inventory ID"):
        _attributes(ezdxf.readfile(path))


def test_binding_is_not_forward_filled_between_bom_rows(tmp_path):
    first = [value for _, value in attrs()]
    second = [value for _, value in attrs(device="second")]
    second[2] = ""  # No MODEL_ID on this row.
    path = bom(tmp_path, [first, second])
    report, facts = read(path)
    assert report["observed_device_tags"] == [tag()]
    assert {fact["device_tag"] for fact in facts} == {tag()}
    assert any(
        w["code"] == "unbound_model_local_identity" and w["row"] == 3
        for w in report["warnings"]
    )
    with path.open(newline="") as stream:
        rows = list(csv.reader(stream))
    with pytest.raises(DraftingError, match="model-local inventory ID"):
        _index(rows)


def test_duplicate_model_object_blocks_and_bom_rows_fail_closed(tmp_path):
    report, facts = read(dxf(tmp_path, duplicate=True))
    assert (
        not facts
        and not report["observed_device_tags"]
        and not report["model_local_identities"]
    )
    assert any(
        w["code"] == "duplicate_model_local_identity" for w in report["warnings"]
    )
    path = bom(tmp_path, [[value for _, value in attrs()]] * 2)
    report, facts = read(path)
    assert not facts and not report["observed_device_tags"]
    with pytest.raises(DraftingError, match="unique"):
        verify_inventory(dxf(tmp_path), path)


def test_unicode_model_and_device_use_exact_utf8_json_hash_without_case_or_whitespace_aliases(
    tmp_path,
):
    model, device = "modèle α", "主开关"
    report, facts = read(dxf(tmp_path, attrs(model=model, device=device)))
    assert report["observed_device_tags"] == [tag(model, device)]
    assert facts[0]["model_device_id"] == device and facts[0]["model_id"] == model
    assert tag(model, device) != tag(model.upper(), device)


def test_same_canonical_id_cannot_merge_foreign_models_or_revisions_in_native_inventory(
    tmp_path,
):
    native = dxf(tmp_path)
    foreign = [value for _, value in attrs(model="foreign-model")]
    assert verify_inventory(native, bom(tmp_path, [foreign]))["status"] == "blocked"
    revision = [value for _, value in attrs(version="2")]
    assert verify_inventory(native, bom(tmp_path, [revision]))["status"] == "blocked"


def test_actual_generated_unlabelled_main_reads_back_as_model_instance(tmp_path):
    model = {
        "model_id": "generated-synthetic",
        "model_version": "3",
        "sections": [],
        "devices": [
            {
                "id": "main",
                "tag": None,
                "role": "main",
                "frame_a": 1600,
                "trip_a": 1200,
                "pole_count": 3,
                "part_number": "SYNTHETIC-PART",
                "nameplate": "TEST LABEL",
            }
        ],
    }
    package = generate_package(model, output_dir=tmp_path / "generated")
    for kind in ("one_line_dxf", "bom_xlsx"):
        path = Path(
            next(file["path"] for file in package["artifacts"] if file["kind"] == kind)
        )
        report, facts = read(path)
        assert report["observed_device_tags"] == [tag("generated-synthetic", "main")]
        assert facts and all(
            fact.get("model_device_id") == "main" and fact.get("model_local_identity")
            for fact in facts
        )
