"""Runner controls use generated PDFs and injected observations, never paid calls."""

import json
from hashlib import sha256
from pathlib import Path

import pymupdf
import pytest
from test_engineering_schedule_reader import regions, sample

from scripts import run_switchboard_schedule_extraction as runner


@pytest.fixture(autouse=True)
def database():
    yield


def _manifest(tmp_path, *, pages=1, role="customer_one_line", checksum=None):
    source = tmp_path / "synthetic-original.pdf"
    with pymupdf.open() as pdf:
        for _ in range(pages):
            pdf.new_page()
        pdf.save(source)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "id": "synthetic-control",
                        "kind": "synthetic",
                        "split": "development",
                        "source_authority": "historical_reference_only",
                        "answer_key": "answer-key-do-not-open.json",
                        "sources": [
                            {
                                "id": "source",
                                "role": role,
                                "path": source.name,
                                "sha256": checksum
                                or sha256(source.read_bytes()).hexdigest(),
                                "pages": list(range(1, pages + 1)),
                            }
                        ],
                    }
                ]
            }
        )
    )
    return manifest, source


@pytest.fixture
def located(monkeypatch):
    from backend.engineering import schedule_regions

    calls = []

    def locate(source, page, *, output_dir):
        calls.append((source, page))
        return {"regions": regions(), "diagnostics": [], "status": "regions_located"}

    monkeypatch.setattr(schedule_regions, "locate_schedule_regions", locate)
    return calls


def test_freezes_source_code_and_prediction_without_opening_answer_key(
    tmp_path, monkeypatch, located
):
    manifest, original = _manifest(tmp_path)
    key = tmp_path / "answer-key-do-not-open.json"
    key.write_text("This synthetic trap is not an answer key.")
    output = tmp_path / "new-run"
    original_read_bytes, original_read_text = Path.read_bytes, Path.read_text

    def guarded_bytes(path):
        assert path != key, "An answer-key path must never be opened"
        return original_read_bytes(path)

    def guarded_text(path, *args, **kwargs):
        assert path != key, "An answer-key path must never be opened"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", guarded_bytes)
    monkeypatch.setattr(Path, "read_text", guarded_text)
    provider_calls = []

    def provider(images, *, model):
        assert (output / "implementation-before.json").is_file()
        assert (output / "manifest-frozen.json").is_file()
        assert (output / "source-only-inputs.json").is_file()
        assert not (output / "prediction-frozen.json").exists()
        assert not (output / "score.json").exists()
        assert located[0][0]["path"] != str(original)
        provider_calls.append(model)
        return sample(), {}

    receipt = runner.run_manifest(
        manifest, "synthetic-control", output, provider=provider
    )
    prediction_path = output / "prediction-frozen.json"
    prediction = json.loads(prediction_path.read_text())
    assert provider_calls == ["gpt-6.1-sol"]
    assert receipt["run_status"] == "completed"
    assert (
        receipt["prediction_sha256"] == sha256(prediction_path.read_bytes()).hexdigest()
    )
    assert prediction["requirements"]["items"]
    assert (
        prediction["configuration"]
        is prediction["design"]
        is prediction["artifacts"]
        is None
    )
    assert (
        not prediction["engineering_approved"] and not prediction["production_released"]
    )
    assert prediction["source_authority"] == "historical_reference_only"
    assert prediction["answer_key_opened"] is False
    assert not (output / "score.json").exists()
    frozen = output / "implementation"
    for relative in (
        "backend/engineering/schedule_regions.py",
        "backend/engineering/schedule_reader.py",
        "backend/engineering/requirements.py",
        "backend/equipment/evidence/table_reader.py",
        "backend/equipment/evidence/schema.py",
        "scripts/evaluate_switchboard_reconstruction.py",
        "scripts/run_switchboard_schedule_extraction.py",
    ):
        assert (frozen / relative).is_file()
    bindings = json.loads((output / "source-only-inputs.json").read_text())["sources"]
    assert Path(bindings[0]["frozen_path"]).read_bytes() == original.read_bytes()


def test_existing_output_is_rejected_before_manifest_or_provider(tmp_path):
    output = tmp_path / "preserved-run"
    output.mkdir()
    sentinel = output / "prediction-frozen.json"
    sentinel.write_bytes(b"immutable previous prediction")
    with pytest.raises(ValueError, match="already exists"):
        runner.run_manifest(tmp_path / "must-not-open.json", "control", output)
    assert sentinel.read_bytes() == b"immutable previous prediction"


def test_declared_key_cannot_be_disguised_as_a_source(tmp_path, monkeypatch, located):
    manifest, _ = _manifest(tmp_path)
    payload = json.loads(manifest.read_text())
    key = tmp_path / "answer-key-do-not-open.json"
    payload["packages"][0]["sources"][0]["path"] = key.name
    manifest.write_text(json.dumps(payload))
    read_bytes = Path.read_bytes

    def guarded(path):
        assert path != key, "The answer key must be rejected before any read"
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded)
    with pytest.raises(ValueError, match="answer key cannot"):
        runner.run_manifest(manifest, "synthetic-control", tmp_path / "run")
    assert located == []


@pytest.mark.parametrize("failure", ["hash", "role"])
def test_invalid_originals_fail_before_locator_or_provider(tmp_path, located, failure):
    manifest, _ = _manifest(
        tmp_path,
        checksum="0" * 64 if failure == "hash" else None,
        role="manufacturer_reference" if failure == "role" else "customer_one_line",
    )
    with pytest.raises(
        ValueError, match="hash mismatch" if failure == "hash" else "customer-side"
    ):
        runner.run_manifest(
            manifest,
            "synthetic-control",
            tmp_path / "run",
            provider=lambda *a, **kw: pytest.fail("No provider"),
        )
    assert located == []


def test_page_budget_records_deferred_pages_without_extra_calls(tmp_path, located):
    manifest, _ = _manifest(tmp_path, pages=2)
    calls = []

    def provider(images, *, model):
        calls.append(model)
        return sample(), {}

    receipt = runner.run_manifest(
        manifest, "synthetic-control", tmp_path / "run", max_pages=1, provider=provider
    )
    assert len(calls) == len(located) == 1
    assert receipt["deferred_pages"] == 1


def test_reader_failure_freezes_partial_prediction_and_does_not_retry(
    tmp_path, located
):
    manifest, _ = _manifest(tmp_path, pages=2)
    calls = []

    def provider(images, *, model):
        calls.append(model)
        raise ValueError("Synthetic failed reading")

    output = tmp_path / "run"
    receipt = runner.run_manifest(
        manifest, "synthetic-control", output, provider=provider
    )
    assert len(calls) == len(located) == 1
    assert receipt["run_status"] == "completed_with_reader_errors"
    assert receipt["deferred_pages"] == receipt["reader_errors"] == 1
    prediction = json.loads((output / "prediction-frozen.json").read_text())
    assert prediction["drawing_reports"] == []
    assert prediction["requirements"]["items"] == []


def test_code_change_is_preserved_and_invalidates_run_without_scoring(
    tmp_path, monkeypatch, located
):
    manifest, _ = _manifest(tmp_path)
    helper = tmp_path / "synthetic-helper.py"
    helper.write_text("VERSION = 1\n")
    monkeypatch.setattr(
        runner, "_implementation_files", lambda: {"synthetic-helper.py": helper}
    )

    def provider(images, *, model):
        helper.write_text("VERSION = 2\n")
        return sample(), {}

    output = tmp_path / "run"
    receipt = runner.run_manifest(
        manifest, "synthetic-control", output, provider=provider
    )
    assert receipt["run_status"] == "invalid_implementation_changed"
    assert (
        output / "implementation/synthetic-helper.py"
    ).read_text() == "VERSION = 1\n"
    assert (output / "prediction-frozen.json").is_file()
    assert not (output / "score.json").exists()


def test_cli_requires_explicit_run_before_opening_inputs(tmp_path):
    with pytest.raises(SystemExit):
        runner.main(
            [
                "--manifest",
                str(tmp_path / "not-opened.json"),
                "--package",
                "control",
                "--output",
                str(tmp_path / "out"),
            ]
        )


def test_exclusive_artifact_writer_preserves_existing_bytes(tmp_path):
    path = tmp_path / "frozen.json"
    runner._exclusive_json(path, {"version": 1})
    previous = path.read_bytes()
    with pytest.raises(FileExistsError):
        runner._exclusive_json(path, {"version": 2})
    assert path.read_bytes() == previous
