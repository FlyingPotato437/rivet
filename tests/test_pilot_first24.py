"""The pilot runner must reject changed sources before any model/engine work."""

import json
import sys
from hashlib import sha256

import pytest

from scripts import pilot_first24


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
