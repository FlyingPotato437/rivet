"""The pilot runner must reject changed sources before any model/engine work."""

import json
import sys
from copy import deepcopy
from hashlib import sha256

import pytest

from scripts import pilot_first24


def authorized_package():
    sources = [{"id": "drawing", "sha256": "a" * 64}]
    return {
        "kind": "real_private",
        "sources": sources,
        "model_processing": {
            "external_ai_allowed": True,
            "authorization_reference": "SCC evaluation approval reference (synthetic test)",
            "recorded_by": "operator-test",
            "recorded_at": "2026-11-08T12:00:00+00:00",
            "source_hashes": {"drawing": "a" * 64},
        },
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("external_ai_allowed", False),
        ("external_ai_allowed", "true"),
        ("authorization_reference", " "),
        ("recorded_by", ""),
        ("recorded_at", "2026-11-08T12:00:00"),
        ("recorded_at", None),
        ("source_hashes", {"drawing": "b" * 64}),
    ],
)
def test_private_remote_reading_requires_explicit_source_bound_receipt(key, value):
    package = authorized_package()
    package["model_processing"][key] = value
    with pytest.raises(ValueError, match="No package was sent to a model"):
        pilot_first24.validate_remote_processing(package, requested=True)


def test_remote_receipt_cannot_extend_to_extra_files_or_replace_redaction():
    package = authorized_package()
    before = deepcopy(package)
    pilot_first24.validate_remote_processing(package, requested=True)
    assert package == before
    package["sources"].append({"id": "new-bom", "sha256": "b" * 64})
    with pytest.raises(ValueError, match="not authorized"):
        pilot_first24.validate_remote_processing(package, requested=True)
    package.pop("model_processing")
    package["redaction_review"] = {"confirmed": True}
    with pytest.raises(ValueError, match="Redaction confirmation alone"):
        pilot_first24.validate_remote_processing(package, requested=True)
    pilot_first24.validate_remote_processing(package, requested=False)
    pilot_first24.validate_remote_processing({"kind": "real_public"}, requested=True)


def test_conflicting_model_flags_fail_before_reading_manifest_or_running_engines(
    monkeypatch,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pilot_first24",
            "--manifest",
            "does-not-exist.json",
            "--package",
            "private",
            "--output-dir",
            "unused",
            "--vision",
            "--predictions",
            "saved.json",
        ],
    )
    with pytest.raises(ValueError, match="saved predictions or a fresh vision"):
        pilot_first24.main()


def test_duplicate_source_ids_cannot_hide_an_unapproved_file():
    package = authorized_package()
    package["sources"].insert(0, {"id": "drawing", "sha256": "b" * 64})
    # A collapsed dictionary would still match the approved final entry.
    with pytest.raises(ValueError, match="not authorized"):
        pilot_first24.validate_remote_processing(package, requested=True)


@pytest.mark.parametrize("flag", ["--vision", "--drawing-vision"])
def test_private_cli_blocks_unapproved_remote_work_before_any_output_or_engine(
    tmp_path, monkeypatch, flag
):
    data = b"tag,rating\nCB-01,125\n"
    (tmp_path / "bom.csv").write_bytes(data)
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "rivet.real-packages.v1",
                "redaction_review": {"confirmed": True},
                "packages": [
                    {
                        "id": "private-test",
                        "kind": "real_private",
                        "sources": [
                            {
                                "id": "bom",
                                "path": "bom.csv",
                                "name": "bom.csv",
                                "sha256": sha256(data).hexdigest(),
                                "role": "bom",
                                "revision": "A",
                                "side": "target",
                            }
                        ],
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pilot_first24",
            "--manifest",
            str(manifest),
            "--package",
            "private-test",
            "--output-dir",
            str(tmp_path / "unused"),
            flag,
        ],
    )
    monkeypatch.setattr(
        pilot_first24,
        "prepare_output",
        lambda *a, **k: pytest.fail("No engine/output before authorization"),
    )
    with pytest.raises(ValueError, match="external AI processing is not authorized"):
        pilot_first24.main()
    assert not (tmp_path / "unused").exists()


def test_changed_private_source_stops_before_vision_or_engine(tmp_path, monkeypatch):
    original = tmp_path / "drawing.pdf"
    original.write_bytes(b"changed source bytes")
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "rivet.real-packages.v1",
                "redaction_review": {"confirmed": True},
                "packages": [
                    {
                        "id": "pilot-001",
                        "kind": "real_private",
                        "sources": [
                            {
                                "id": "drawing-001",
                                "path": "drawing.pdf",
                                "name": "drawing.pdf",
                                "sha256": sha256(
                                    b"original approved bytes"
                                ).hexdigest(),
                                "role": "drawing",
                                "revision": "A",
                                "side": "target",
                            }
                        ],
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pilot_first24",
            "--manifest",
            str(manifest),
            "--package",
            "pilot-001",
            "--output-dir",
            str(tmp_path / "result"),
            "--vision",
        ],
    )
    monkeypatch.setattr(
        pilot_first24,
        "_run",
        lambda *a, **k: pytest.fail("Must reject before engine/model work"),
    )
    with pytest.raises(ValueError, match="No package was sent to a model"):
        pilot_first24.main()
    assert not (tmp_path / "result").exists()


def test_findings_do_not_turn_unlabelled_or_missing_baseline_into_accuracy():
    report = {
        "coverage": {
            "physical_pages": 200,
            "equipment_pages_attempted": 200,
            "equipment_pages_processed": 20,
            "equipment_pages_unreadable": 180,
        },
        "equipment_model": {"observed_device_count": 0},
        "errors": [],
    }
    text = pilot_first24.findings(
        report, {"id": "pilot", "sources": [{"side": "target"}]}, 10
    )
    assert "independent reference labels are missing" in text
    assert "No baseline was provided" in text
    assert "readable: 20; unreadable: 180" in text
    assert "Observed equipment model devices: 0" in text


def test_private_run_rejects_existing_child_symlinks_before_writing(
    tmp_path, monkeypatch
):
    private = tmp_path / ".data"
    private.mkdir()
    monkeypatch.setattr(pilot_first24, "PRIVATE_DATA_ROOT", private)
    outside = tmp_path / "shared"
    outside.mkdir()
    destination = private / "old-run"
    destination.mkdir()
    (destination / "vision").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="fresh output directory"):
        pilot_first24.prepare_output(destination, private=True)
    assert not list(outside.iterdir())
    fresh = pilot_first24.prepare_output(private / "new-run", private=True)
    pilot_first24.write_output(fresh / "summary.txt", "private", private=True)
    assert fresh.stat().st_mode & 0o777 == 0o700
    assert (fresh / "summary.txt").stat().st_mode & 0o777 == 0o600


def test_private_output_does_not_follow_leaf_symlink(tmp_path):
    original = tmp_path / "do-not-overwrite"
    original.write_text("retained")
    link = tmp_path / "summary.txt"
    link.symlink_to(original)
    with pytest.raises(OSError):
        pilot_first24.write_output(link, "private", private=True)
    assert original.read_text() == "retained"
