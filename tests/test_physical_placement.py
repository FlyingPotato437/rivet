"""Synthetic search and save controls; not manufacturer acceptance results."""

import ezdxf
import pymupdf
import pytest

from backend.engineering.cad_snapshot import snapshot_dxf
from backend.engineering.comment_redesign import comments_from_pdfs, file_hash
from backend.engineering.physical_move import verify_geometry
from backend.engineering.physical_placement import (
    _free_intervals,
    build_search,
    check_saved_placement,
    search_placements,
)


@pytest.fixture(autouse=True)
def database():
    yield


def case(tmp_path):
    d = ezdxf.new("R2010")
    m = d.modelspace()
    views = []
    for n, coordinate in enumerate(("face_lateral", "height")):
        y = n * 40
        frame = m.add_lwpolyline(
            [(0, y), (10, y), (10, y + 20), (0, y + 20)], close=True
        )
        objects = [
            m.add_line((11, y + 8), (13, y + 8)),
            m.add_line((13, y + 8), (13, y + 10)),
            m.add_arc((12, y + 9), 0.5, 10, 130),
        ]
        obstacle = m.add_lwpolyline(
            [(-4, y + 7), (-0.5, y + 7), (-0.5, y + 11), (-4, y + 11)], close=True
        )
        views.append(
            {
                "id": f"view{n}",
                "placement_coordinate": coordinate,
                "frame_locator": frame.dxf.handle,
                "handles": [e.dxf.handle for e in objects],
                "obstacle_locators": [obstacle.dxf.handle],
                "target_side": "negative_x",
                "review_bbox": [-5, y - 2, 15, y + 22],
            }
        )
    source = tmp_path / "source.dxf"
    d.saveas(source)
    markup = tmp_path / "comments.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().add_freetext_annot(
            pymupdf.Rect(10, 10, 300, 100), "Move cabinet onto the other face."
        )
        pdf.save(markup)
    sources = [
        {
            "id": "review",
            "name": markup.name,
            "path": str(markup),
            "role": "markup",
            "sha256": file_hash(markup),
        }
    ]
    comment = comments_from_pdfs(sources)[0]
    plan = {
        "schema": "rivet.physical-move.v1",
        "mode": "engineering_trial",
        "source_sha256": file_hash(source),
        "comment_id": comment["id"],
        "comment_text": comment["text"],
        "views": views,
        "missing_engineer_inputs": [
            {
                "field": "clearance",
                "question": "Confirm the required service clearance.",
            }
        ],
    }
    return source, plan, sources


def test_exact_interval_search_finds_a_sliver_a_coarse_grid_misses():
    assert _free_intervals(0, 10, [[0, 4.123], [4.124, 10]]) == [[4.123, 4.124]]
    assert _free_intervals(0, 10, [[-1, 10]]) == []


def test_search_ranks_three_distinct_nonoverlapping_choices_and_rechecks_saved_files(
    tmp_path,
):
    source, plan, sources = case(tmp_path)
    report = build_search(source, plan, sources, tmp_path / "search")
    assert len(report["ranked_candidates"]) == 3
    assert len(report["saved_candidates"]) == 3
    assert all(
        x["geometry"]["passed"] and x["placement"]["passed"]
        for x in report["saved_candidates"]
    )
    assert all(
        x["geometry"]["entities_checked"] == 6 for x in report["saved_candidates"]
    )
    assert not report["engineering_approved"] and not report["production_released"]
    assert file_hash(source) == plan["source_sha256"]
    score = [c["minimum_mapped_edge_gap"] for c in report["ranked_candidates"]]
    assert score == sorted(score, reverse=True)
    # Search results must match the saved file, not merely their intended offsets.
    c = report["ranked_candidates"][0]
    saved = tmp_path / "search/candidate-1/Cabinet-relocation-UNAPPROVED.dxf"
    cad = ezdxf.readfile(saved)
    e = cad.entitydb[plan["views"][1]["handles"][0]]
    e.translate(0, 30, 0)
    bad = tmp_path / "corrupt.dxf"
    cad.saveas(bad)
    assert not check_saved_placement(snapshot_dxf(bad), plan, report, c)["passed"]
    axes = {h: 5 for v in plan["views"] for h in v["handles"]}
    offsets = {
        h: c["translations"][v["id"]] for v in plan["views"] for h in v["handles"]
    }
    assert not verify_geometry(source, bad, axes, offsets)["passed"]


def test_crowded_face_returns_zero_not_a_least_bad_drawing(tmp_path):
    source, plan, sources = case(tmp_path)
    d = ezdxf.readfile(source)
    obstacle = d.entitydb[plan["views"][0]["obstacle_locators"][0]]
    obstacle.set_points([(-4, 0), (-0.5, 0), (-0.5, 20), (-4, 20)])
    d.saveas(source)
    plan["source_sha256"] = file_hash(source)
    report = build_search(source, plan, sources, tmp_path / "search")
    assert report["status"] == "no_feasible_mapped_placement"
    assert not report["ranked_candidates"] and not report["saved_candidates"]
    assert not list((tmp_path / "search").glob("**/*.dxf"))
    assert report["views"][0]["minimum_span_shortfall"] == pytest.approx(2)
    assert report["views"][0]["free_translation_intervals"] == []


def test_no_guessed_projection_or_drifted_source(tmp_path):
    source, plan, _sources = case(tmp_path)
    plan["views"][1]["placement_coordinate"] = "face_lateral"
    with pytest.raises(ValueError, match="distinct"):
        search_placements(snapshot_dxf(source), plan)
    plan["views"][1]["placement_coordinate"] = "height"
    plan["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="another native source"):
        search_placements(snapshot_dxf(source), plan)
