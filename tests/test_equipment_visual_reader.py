"""Synthetic transport controls, never part of real-file accuracy."""

import hashlib

import pymupdf
import pytest

from backend.equipment.schemas import SourceInput
from backend.equipment.visual_reader import (
    PageEquipment,
    _observations,
    _original_box,
    _scalar,
    read_visual_equipment,
)


def source(tmp_path):
    path = tmp_path / "control.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((30, 30), "Synthetic transport control")
        doc.save(path)
    return SourceInput(
        id="control",
        name=path.name,
        path=str(path),
        role="drawing",
        revision="test",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def empty():
    return PageEquipment(
        tables=[],
        devices=[],
        sheet_identity=[],
        connections=[],
        unknown_regions=[],
        coverage_complete=True,
    )


def test_hash_precedes_provider_and_cache(tmp_path):
    original = source(tmp_path)
    changed = original.model_copy(update={"sha256": "a" * 64})
    with pytest.raises(ValueError, match="source_hash"):
        read_visual_equipment(
            changed,
            [1],
            tmp_path / "cache",
            provider=lambda *a, **kw: pytest.fail("provider called"),
        )
    assert not (tmp_path / "cache").exists()


def test_cache_binds_source_prompt_and_model(tmp_path, monkeypatch):
    original = source(tmp_path)
    calls = []
    monkeypatch.setattr(
        "backend.equipment.visual_reader._images",
        lambda *a: ([([0, 0, 1, 1], b"png")], 0, None),
    )

    def provider(*a, **kw):
        calls.append(kw)
        return empty(), {}

    a = read_visual_equipment(original, [1], tmp_path / "cache", provider=provider)
    b = read_visual_equipment(original, [1], tmp_path / "cache", provider=provider)
    assert len(calls) == 1
    assert not a["pages"][0]["cache_hit"]
    assert b["pages"][0]["cache_hit"]
    assert b["requires_human_verification"]


def test_budget_precedes_api(tmp_path):
    original = source(tmp_path)
    with pytest.raises(ValueError, match="page_budget"):
        read_visual_equipment(
            original,
            [1, 2],
            tmp_path / "cache",
            max_pages=1,
            provider=lambda *a, **kw: pytest.fail("provider called"),
        )


def test_failed_page_is_visible(tmp_path, monkeypatch):
    original = source(tmp_path)
    monkeypatch.setattr(
        "backend.equipment.visual_reader._images",
        lambda *a: ([([0, 0, 1, 1], b"png")], 0, None),
    )

    def provider(*a, **kw):
        raise TimeoutError("No provider body should leak")

    report = read_visual_equipment(original, [1], tmp_path / "cache", provider=provider)
    assert report["status"] == "partial"
    assert report["pages"][0]["error_type"] == "TimeoutError"
    assert "No provider" not in str(report)


def test_unknown_cells_are_not_guessed():
    assert _scalar("trip_a", "200A", "low") is None
    assert _scalar("trip_a", "(100A)", "high") == 100
    assert _scalar("quantity", "3P", "high") is None
    assert _scalar("trip_a", "-", "high") is None
    assert _scalar("trip_a", "200/250A", "high") is None


def test_original_page_coordinates_after_rotation():
    assert _original_box([0.1, 0.2, 0.3, 0.4], 90) == pytest.approx(
        [0.2, 0.7, 0.4, 0.9]
    )
    assert _original_box([0.1, 0.2, 0.3, 0.4], 270) == pytest.approx(
        [0.6, 0.1, 0.8, 0.3]
    )


def test_circuit_position_never_becomes_device_tag(tmp_path):
    original = source(tmp_path)
    transcript = {
        "tables": [
            {
                "bbox": [0, 0, 1, 1],
                "headers": [
                    {"text": "Circuit", "field": "circuit_no"},
                    {"text": "TRIP AMP", "field": "trip_a"},
                ],
                "rows": [
                    {
                        "bbox": [0, 0.2, 1, 0.3],
                        "cells": [
                            {
                                "column": 1,
                                "text": "12",
                                "confidence": "high",
                                "bbox": [0, 0.2, 0.5, 0.3],
                            },
                            {
                                "column": 2,
                                "text": "250A",
                                "confidence": "high",
                                "bbox": [0.5, 0.2, 1, 0.3],
                            },
                        ],
                    }
                ],
            }
        ],
        "devices": [],
    }
    rows, devices = _observations(original, 1, transcript)
    assert rows[0]["fields"] == {"circuit_no": 12, "trip_a": 250}
    assert devices[0]["tag"] is None
    assert devices[0]["source_local_id"] == "page-1-table-1-row-1"
