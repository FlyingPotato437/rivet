"""Synthetic controls for physical editing; not real-manufacturer accuracy."""

import json
from copy import deepcopy

import ezdxf
import pymupdf
import pytest

from backend.engineering.comment_redesign import comments_from_pdfs, file_hash
from backend.engineering.physical_move import build_trial, verify_geometry


@pytest.fixture(autouse=True)
def database():
    yield


def sample(tmp_path):
    cad = ezdxf.new("R2010")
    m = cad.modelspace()
    views = []
    for index, y in enumerate((0, 30)):
        frame = m.add_lwpolyline(
            [(0, y), (10, y), (10, y + 10), (0, y + 10)], close=True
        )
        objects = [
            m.add_line((12, y + 2), (14, y + 2)),
            m.add_line((14, y + 2), (14, y + 5)),
            m.add_arc((13, y + 3), 0.5, 35, 160),
            m.add_circle((13, y + 4), 0.4),
        ]
        obstacle = m.add_lwpolyline(
            [(-4, y + 1), (-2, y + 1), (-2, y + 6), (-4, y + 6)], close=True
        )
        views.append(
            {
                "id": f"view{index}",
                "frame_locator": frame.dxf.handle,
                "handles": [e.dxf.handle for e in objects],
                "obstacle_locators": [obstacle.dxf.handle],
                "target_side": "negative_x",
                "review_bbox": [-7, y - 2, 17, y + 12],
            }
        )
    path = tmp_path / "source.dxf"
    cad.saveas(path)
    markup = tmp_path / "markups.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.add_freetext_annot(
            pymupdf.Rect(10, 10, 300, 120), "Move control cabinet to H1/X1 side."
        )
        pdf.save(markup)
    sources = [
        {
            "id": "review",
            "name": "markups.pdf",
            "path": str(markup),
            "sha256": file_hash(markup),
            "role": "markup",
        }
    ]
    comment = comments_from_pdfs(sources)[0]
    plan = {
        "schema": "rivet.physical-move.v1",
        "mode": "engineering_trial",
        "source_sha256": file_hash(path),
        "comment_id": comment["id"],
        "comment_text": comment["text"],
        "views": views,
        "missing_engineer_inputs": [
            {
                "field": "mounting_location",
                "value": None,
                "question": "Where may the cabinet mount without intersecting existing accessories?",
            }
        ],
    }
    return path, sources, plan


def test_saved_geometry_changes_and_overlap_are_separate(tmp_path):
    source, sources, plan = sample(tmp_path)
    result = build_trial(source, plan, sources, tmp_path / "result")
    assert result["write"]["preservation_verified"]
    assert result["saved_geometry"]["entities_checked"] == 8
    assert result["saved_geometry"]["passed"]
    assert result["layout"]["mapped_target_faces_reached"]
    assert not result["layout"]["projected_envelope_check_passed"]
    assert len(result["layout"]["views"]) == 2
    assert all(v["projected_envelope_overlaps"] for v in result["layout"]["views"])
    assert not result["engineering_approved"]
    assert not result["production_released"]
    assert not result["bom"]["supplied"]
    assert file_hash(source) == plan["source_sha256"]
    saved = tmp_path / "result/Cabinet-relocation-UNAPPROVED.dxf"
    d = ezdxf.readfile(saved)
    assert len(d.modelspace()) == len(ezdxf.readfile(source).modelspace())
    after = d.entitydb[plan["views"][0]["handles"][0]]
    assert list(after.dxf.start) == [-2, 2, 0]
    assert list(after.dxf.end) == [-4, 2, 0]
    assert (
        "projected_envelope_overlaps"
        in json.loads((tmp_path / "result/result.json").read_text())["layout"]["views"][
            0
        ]
    )


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_source",
        "duplicate",
        "shared",
        "missing_frame",
        "wrong_comment",
        "empty_inputs",
        "paper_space",
        "nonplanar",
        "custom_data",
    ],
)
def test_unmapped_or_unsupported_edits_fail_closed(tmp_path, fault):
    source, sources, plan = sample(tmp_path)
    if fault == "wrong_source":
        plan["source_sha256"] = "0" * 64
    elif fault == "duplicate":
        plan["views"][0]["handles"].append(plan["views"][0]["handles"][0])
    elif fault == "shared":
        plan["views"][0]["handles"][0] = "OTHER/BLOCK"
    elif fault == "missing_frame":
        plan["views"][0]["frame_locator"] = "MISSING"
    elif fault == "wrong_comment":
        plan["comment_text"] = "Unrelated request"
    elif fault == "empty_inputs":
        plan["missing_engineer_inputs"] = []
    else:
        d = ezdxf.readfile(source)
        e = d.entitydb[plan["views"][0]["handles"][0]]
        if fault == "paper_space":
            e.dxf.paperspace = 1
        elif fault == "nonplanar":
            e.dxf.start = (12, 2, 5)
        elif fault == "custom_data":
            d.appids.new("CUSTOM")
            e.set_xdata("CUSTOM", [(1000, "paired geometry")])
        d.saveas(source)
        plan["source_sha256"] = file_hash(source)
    with pytest.raises(ValueError):
        build_trial(source, plan, sources, tmp_path / "result")
    assert not (tmp_path / "result/result.json").exists()


def test_saved_checker_catches_wrong_edit_and_leftover_view(tmp_path):
    source, sources, plan = sample(tmp_path)
    build_trial(source, plan, sources, tmp_path / "result")
    saved = tmp_path / "result/Cabinet-relocation-UNAPPROVED.dxf"
    d = ezdxf.readfile(saved)
    e = d.entitydb[plan["views"][1]["handles"][0]]
    e.dxf.end = (14, 32, 0)  # leave one old endpoint in the second view
    corrupt = tmp_path / "corrupt.dxf"
    d.saveas(corrupt)
    axes = {h: 5 for view in plan["views"] for h in view["handles"]}
    result = verify_geometry(source, corrupt, axes)
    assert not result["passed"]
    assert any("reflection" in e or "length" in e for e in result["errors"])


def test_bom_presence_never_becomes_unverified_bom_success(tmp_path):
    source, sources, plan = sample(tmp_path)
    bom = tmp_path / "bom.csv"
    bom.write_text("Part,Quantity\nCABINET,1\n")
    sources.append(
        {
            "id": "bom",
            "name": bom.name,
            "path": str(bom),
            "sha256": file_hash(bom),
            "role": "bom",
        }
    )
    result = build_trial(source, plan, sources, tmp_path / "result")
    assert result["bom"]["supplied"]
    assert not result["bom"]["checked"]


def test_repeated_run_cannot_overwrite_source_or_result(tmp_path):
    source, sources, plan = sample(tmp_path)
    build_trial(source, plan, sources, tmp_path / "result")
    with pytest.raises(ValueError, match="Output must be new"):
        build_trial(source, plan, sources, tmp_path / "result")
    revised = deepcopy(plan)
    saved = tmp_path / "result/Cabinet-relocation-UNAPPROVED.dxf"
    revised["source_sha256"] = file_hash(saved)
    with pytest.raises(ValueError, match="source tank face"):
        build_trial(saved, revised, sources, tmp_path / "again")


def dimension_dependency(source, plan):
    from ezdxf.entities import factory
    from ezdxf.lldxf.extendedtags import ExtendedTags

    d = ezdxf.readfile(source)
    edge = d.entitydb[plan["views"][0]["handles"][0]]
    dim = d.modelspace().add_linear_dim(
        base=(0, -1), p1=(0, 0), p2=(14, 0), text="185.0"
    )
    dim.render()
    handle = d.entitydb.next_handle()
    tags = f"""0
DIMASSOC
5
{handle}
330
{d.rootdict.dxf.handle}
100
AcDbDimAssoc
330
{dim.dimension.dxf.handle}
90
1
70
0
71
0
1
AcDbOsnapPointRef
72
1
331
{edge.dxf.handle}
73
2
91
0
40
1.0
10
0
20
0
30
0
75
0
"""
    assoc = factory.load(ExtendedTags.from_text(tags), d)
    d.entitydb.add(assoc)
    d.objects.add_object(assoc)
    edge.append_reactor_handle(handle)
    d.saveas(source)
    plan["source_sha256"] = file_hash(source)
    return {
        "association_handle": handle,
        "dimension_handle": dim.dimension.dxf.handle,
        "reference_handles": [edge.dxf.handle],
        "reason": "Overall width annotation needs recalculation against the approved location.",
    }


def test_associated_dimension_must_be_explicit_and_stays_unresolved(tmp_path):
    source, sources, plan = sample(tmp_path)
    dependency = dimension_dependency(source, plan)
    with pytest.raises(ValueError, match="explicit deferred mapping"):
        build_trial(source, plan, sources, tmp_path / "unmapped")
    plan["deferred_dimension_associations"] = [dependency]
    result = build_trial(source, plan, sources, tmp_path / "declared")
    assert result["saved_geometry"]["passed"]
    assert not result["annotation_check"]["passed"]
    assert result["annotation_check"]["preserved_dependencies_verified"]
    stale = result["annotation_check"]["stale_dimensions"][0]
    assert stale["display_override"] == "185.0"
    assert stale["dimension_handle"] == dependency["dimension_handle"]
    assert stale["status"] == "stale_dimension_requires_revision"
    assert not result["engineering_approved"]
    assert file_hash(source) == plan["source_sha256"]


def test_mismapped_dimension_is_not_an_escape_for_unknown_associations(tmp_path):
    source, sources, plan = sample(tmp_path)
    dependency = dimension_dependency(source, plan)
    dependency["reference_handles"] = ["NOT-THE-CABINET"]
    plan["deferred_dimension_associations"] = [dependency]
    with pytest.raises(ValueError, match="does not match"):
        build_trial(source, plan, sources, tmp_path / "bad")


def test_grouped_obstacle_is_measured_without_claiming_complete_clearance(tmp_path):
    source, sources, plan = sample(tmp_path)
    view = plan["views"][1]
    view["obstacle_groups"] = [
        {"id": "existing-mechanism", "locators": view.pop("obstacle_locators")}
    ]
    result = build_trial(source, plan, sources, tmp_path / "result")
    check = result["layout"]["views"][1]
    assert check["obstacle_envelopes_checked"] == 1
    assert check["projected_envelope_overlaps"][0]["locator"] == "existing-mechanism"
    assert check["obstacle_coverage"] == "explicitly_mapped_only"
    assert not result["layout"]["complete_collision_check"]


def test_empty_obstacle_list_is_not_evidence_of_clearance(tmp_path):
    source, sources, plan = sample(tmp_path)
    for view in plan["views"]:
        view["obstacle_locators"] = []
    result = build_trial(source, plan, sources, tmp_path / "result")
    assert all(v["obstacle_envelopes_checked"] == 0 for v in result["layout"]["views"])
    assert not result["layout"]["complete_collision_check"]
    assert not result["layout"]["physical_clearance_verified"]
