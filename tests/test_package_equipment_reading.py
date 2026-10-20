"""Pure orchestration controls. Generated pages never count as real accuracy."""

import hashlib
import json
from types import SimpleNamespace

import pymupdf
import pytest

from backend.equipment import package_reading, visual_reader, visual_revision


@pytest.fixture(autouse=True)
def database():
    """No database is used or rebuilt by this file."""
    yield


def pdf_source(tmp_path, name, pages=1, *, side="target", role="drawing"):
    path = tmp_path / (name + ".pdf")
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    doc.close()
    return {
        "id": name,
        "name": path.name,
        "path": path.name,
        "role": role,
        "revision": "As supplied",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "side": side,
    }


def package(*sources, **extra):
    return {"id": "synthetic-orchestration-control", "sources": list(sources), **extra}


@pytest.fixture
def adapters(monkeypatch):
    calls = SimpleNamespace(read=[], visual=[], revision=[])

    def read(source):
        calls.read.append(source.id)
        return {"id": source.id, "status": "partial", "observed_bom_rows": []}, []

    def visual(source, pages, output, **kwargs):
        calls.visual.append((source.id, list(pages), kwargs))
        return {
            "pages": [
                {
                    "source_id": source.id,
                    "page": p,
                    "status": "read",
                    "requires_human_verification": True,
                }
                for p in pages
            ],
            "devices": [],
            "observed_bom_rows": [],
            "connections": [],
            "sheet_identities": [],
        }

    def revision(before, after, output, **kwargs):
        calls.revision.append((before.id, after.id, kwargs))
        return {"revision_checks": [], "errors": [], "engineering_verified": False}

    def bounded(function, *args, deadline, **kwargs):
        # The real subprocess wrapper has its own tests. Keep this orchestration
        # boundary synchronous so pure adapters are deterministic and picklable.
        if package_reading.time.monotonic() >= deadline:
            return {
                "status": "timed_out",
                "error_type": "TimeBudget",
                "elapsed_seconds": 0.0,
            }
        try:
            return {
                "status": "completed",
                "result": function(*args, **kwargs),
                "elapsed_seconds": 0.0,
            }
        except Exception as exc:  # noqa: BLE001 - mirrors the worker boundary.
            return {
                "status": "failed",
                "error_type": type(exc).__name__,
                "elapsed_seconds": 0.0,
            }

    monkeypatch.setattr(package_reading, "_call_with_budget", bounded, raising=False)
    monkeypatch.setattr(package_reading, "read_source", read)
    monkeypatch.setattr(visual_reader, "read_visual_equipment", visual)
    monkeypatch.setattr(visual_revision, "compare_revision_sources", revision)
    return calls


def test_all_hashes_checked_before_first_reader_or_provider(tmp_path, adapters):
    first = pdf_source(tmp_path, "first")
    changed = pdf_source(tmp_path, "changed")
    changed["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="no provider request"):
        package_reading.read_package_equipment(
            package(first, changed),
            tmp_path / "manifest.json",
            tmp_path / "output",
            vision=True,
        )
    assert adapters.read == adapters.visual == adapters.revision == []
    assert not (tmp_path / "output/equipment-reading.json").exists()


def test_page_limit_retains_every_unprocessed_physical_page(tmp_path, adapters):
    source = pdf_source(tmp_path, "long", pages=9)
    result = package_reading.read_package_equipment(
        package(source),
        tmp_path / "manifest.json",
        tmp_path / "out",
        vision=True,
        visual_page_limit=8,
    )
    assert [(sid, pages) for sid, pages, _ in adapters.visual] == [
        ("long", list(range(1, 9)))
    ]
    coverage = result["predictions"]["pages"]
    assert [p["page"] for p in coverage] == list(range(1, 10))
    assert coverage[-1]["status"] == "not_processed_budget"
    assert coverage[-1]["requires_human_verification"] is True
    assert result["errors"][0]["stage"] == "visual_equipment"


def test_elapsed_budget_prevents_provider_and_records_pages(
    tmp_path, adapters, monkeypatch
):
    source = pdf_source(tmp_path, "elapsed", pages=3)
    clock = {"now": 100.0}
    monkeypatch.setattr(package_reading.time, "monotonic", lambda: clock["now"])
    previous_read = package_reading.read_source

    def slow_reader(s):
        result = previous_read(s)
        clock["now"] = 110.0
        return result

    monkeypatch.setattr(package_reading, "read_source", slow_reader)
    result = package_reading.read_package_equipment(
        package(source),
        tmp_path / "manifest.json",
        tmp_path / "out",
        vision=True,
        time_budget=5,
    )
    assert adapters.visual == []
    assert [p["status"] for p in result["predictions"]["pages"]] == [
        "not_processed_budget"
    ] * 3
    assert result["timings"]["equipment_and_revision_wall_seconds"] == 10.0


def test_native_table_row_does_not_establish_whole_page_coverage(
    tmp_path, adapters, monkeypatch
):
    source = pdf_source(tmp_path, "schedule", pages=2, role="bom")

    def known_table(s):
        return {
            "id": s.id,
            "status": "partial",
            "observed_bom_rows": [
                {
                    "source_id": s.id,
                    "page": 1,
                    "row": 1,
                    "fields": {"quantity": 2},
                    "confidence": "source_text",
                }
            ],
        }, []

    monkeypatch.setattr(package_reading, "read_source", known_table)
    result = package_reading.read_package_equipment(
        package(source), tmp_path / "manifest.json", tmp_path / "out", vision=False
    )
    assert adapters.visual == []
    coverage = result["predictions"]["pages"]
    assert [(p["source_id"], p["page"], p["status"]) for p in coverage] == [
        ("schedule", page, "requires_visual_reading") for page in (1, 2)
    ]
    assert all(p["requires_human_verification"] is True for p in coverage)
    assert (
        result["source_reports"][0]["observed_bom_rows"][0]["fields"]["quantity"] == 2
    )


def test_explicit_pairs_override_file_order_without_guessing(tmp_path, adapters):
    a = pdf_source(tmp_path, "before-a", side="baseline")
    b = pdf_source(tmp_path, "before-b", side="baseline")
    x = pdf_source(tmp_path, "after-x")
    y = pdf_source(tmp_path, "after-y")
    pairs = [
        {"baseline_source_id": b["id"], "target_source_id": x["id"]},
        {"baseline_source_id": a["id"], "target_source_id": y["id"]},
    ]
    result = package_reading.read_package_equipment(
        package(a, x, b, y, revision_pairs=pairs),
        tmp_path / "manifest.json",
        tmp_path / "out",
    )
    assert [(before, after) for before, after, _ in adapters.revision] == [
        ("before-b", "after-x"),
        ("before-a", "after-y"),
    ]
    assert len(result["revision_reports"]) == 2
    assert not any(e["stage"] == "revision_pairing" for e in result["errors"])


def test_ambiguous_file_sets_never_pair_by_name_or_page_order(tmp_path, adapters):
    a = pdf_source(tmp_path, "sheet-a-old", side="baseline")
    b = pdf_source(tmp_path, "sheet-b-old", side="baseline")
    x = pdf_source(tmp_path, "sheet-a-new")
    result = package_reading.read_package_equipment(
        package(a, b, x), tmp_path / "manifest.json", tmp_path / "out"
    )
    assert adapters.revision == []
    assert any(
        e["stage"] == "revision_pairing" and "explicit" in e["message"]
        for e in result["errors"]
    )


def test_unique_issue_pair_is_explicitly_compared(tmp_path, adapters):
    old = pdf_source(tmp_path, "old", side="baseline")
    new = pdf_source(tmp_path, "new")
    package_reading.read_package_equipment(
        package(new, old), tmp_path / "manifest.json", tmp_path / "out"
    )
    assert [(a, b) for a, b, _ in adapters.revision] == [("old", "new")]


def test_partial_observations_stay_unknown_and_human_unverified(
    tmp_path, adapters, monkeypatch
):
    source = pdf_source(tmp_path, "uncertain", pages=2)
    row = {
        "source_id": source["id"],
        "page": 1,
        "row": 1,
        "fields": {"trip_a": None},
        "cell_confidence": {"trip_a": "low"},
        "requires_human_verification": True,
        "engineering_verified": False,
    }
    wire = {
        "source_id": source["id"],
        "page": 1,
        "from": {"device_tag": None, "pin": "1"},
        "to": {"device_tag": "86T", "pin": "A1"},
        "status": "unknown_endpoint",
        "geometry_verified": False,
    }

    def partial(s, pages, output, **kwargs):
        return {
            "pages": [
                {
                    "source_id": s.id,
                    "page": 1,
                    "status": "partial",
                    "requires_human_verification": True,
                },
                {
                    "source_id": s.id,
                    "page": 2,
                    "status": "failed",
                    "error_type": "UnreadablePage",
                    "requires_human_verification": True,
                },
            ],
            "observed_bom_rows": [row],
            "connections": [wire],
            "devices": [],
            "sheet_identities": [],
        }

    monkeypatch.setattr(visual_reader, "read_visual_equipment", partial)
    result = package_reading.read_package_equipment(
        package(source), tmp_path / "manifest.json", tmp_path / "out", vision=True
    )
    predictions = result["predictions"]
    assert predictions["bom_rows"] == [row]
    assert predictions["connections"] == [wire]
    assert len(result["errors"]) == 2
    assert {e["page"] for e in result["errors"]} == {1, 2}
    assert predictions["producer"]["engineering_verified"] is False
    assert (
        predictions["requires_human_verification"]
        is result["requires_human_verification"]
        is True
    )
    output = tmp_path / "out/equipment-reading.json"
    assert output.stat().st_mode & 0o777 == 0o600
    assert (
        json.loads(output.read_text())["predictions"]["bom_rows"][0]["fields"]["trip_a"]
        is None
    )


def test_revision_budget_exposes_skipped_comparison(tmp_path, adapters, monkeypatch):
    old = pdf_source(tmp_path, "old", side="baseline")
    new = pdf_source(tmp_path, "new")
    monkeypatch.setattr(package_reading.time, "monotonic", lambda: 100.0)
    result = package_reading.read_package_equipment(
        package(old, new), tmp_path / "manifest.json", tmp_path / "out", time_budget=0
    )
    assert adapters.revision == []
    assert any(e["stage"] == "visual_revision" for e in result["errors"])


def test_duplicate_source_ids_fail_before_provider(tmp_path, adapters):
    first = pdf_source(tmp_path, "first")
    second = pdf_source(tmp_path, "second")
    second["id"] = first["id"]
    with pytest.raises(ValueError, match="[Dd]uplicate|unique"):
        package_reading.read_package_equipment(
            package(first, second),
            tmp_path / "manifest.json",
            tmp_path / "out",
            vision=True,
        )
    assert adapters.read == adapters.visual == adapters.revision == []


def test_missing_pair_source_fails_before_provider(tmp_path, adapters):
    current = pdf_source(tmp_path, "current")
    pairs = [{"baseline_source_id": "missing", "target_source_id": current["id"]}]
    with pytest.raises(ValueError):
        package_reading.read_package_equipment(
            package(current, revision_pairs=pairs),
            tmp_path / "manifest.json",
            tmp_path / "out",
            vision=True,
        )
    assert adapters.read == adapters.visual == adapters.revision == []


def test_provider_failure_becomes_visible_page_failure(tmp_path, adapters, monkeypatch):
    source = pdf_source(tmp_path, "provider-fault", pages=2)

    def unavailable(*args, **kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(visual_reader, "read_visual_equipment", unavailable)
    result = package_reading.read_package_equipment(
        package(source), tmp_path / "manifest.json", tmp_path / "out", vision=True
    )
    assert result["errors"]
    coverage = result["predictions"]["pages"]
    assert {p["page"] for p in coverage} == {1, 2}
    assert all(p["status"] != "read" for p in coverage)
    assert all(p["requires_human_verification"] is True for p in coverage)
    assert not result["predictions"]["devices"]


def test_page_budget_smaller_than_eight_still_processes_allowed_pages(
    tmp_path, adapters
):
    source = pdf_source(tmp_path, "small-budget", pages=5)
    result = package_reading.read_package_equipment(
        package(source),
        tmp_path / "manifest.json",
        tmp_path / "out",
        vision=True,
        visual_page_limit=3,
    )
    assert [(sid, pages) for sid, pages, _ in adapters.visual] == [
        ("small-budget", [1, 2, 3])
    ]
    coverage = result["predictions"]["pages"]
    assert {p["page"] for p in coverage} == {1, 2, 3, 4, 5}
    assert [p["page"] for p in coverage if p["status"] == "not_processed_budget"] == [
        4,
        5,
    ]


def test_missing_provider_page_records_are_not_silently_dropped(
    tmp_path, adapters, monkeypatch
):
    source = pdf_source(tmp_path, "missing-coverage", pages=2)

    def incomplete(s, pages, output, **kwargs):
        return {
            "pages": [
                {
                    "source_id": s.id,
                    "page": 1,
                    "status": "read",
                    "requires_human_verification": True,
                }
            ]
        }

    monkeypatch.setattr(visual_reader, "read_visual_equipment", incomplete)
    result = package_reading.read_package_equipment(
        package(source), tmp_path / "manifest.json", tmp_path / "out", vision=True
    )
    coverage = result["predictions"]["pages"]
    assert {p["page"] for p in coverage} == {1, 2}
    assert next(p for p in coverage if p["page"] == 2)["status"] == "not_returned"
    assert any(e.get("error_type") == "MissingPageRecords" for e in result["errors"])
