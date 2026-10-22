"""Independent planar geometry controls, not manufacturer design accuracy."""

import math

import ezdxf
import pytest

from backend.engineering.cad_geometry import (
    GeometryBudgetError,
    curve_points,
    transform_curve,
)
from backend.engineering.cad_snapshot import snapshot_dxf


@pytest.fixture(autouse=True)
def database():
    yield


def test_planar_scaled_block_is_located_but_nonplanar_children_are_not(tmp_path):
    doc = ezdxf.new()
    block = doc.blocks.new("body")
    block.add_line((1, 2, 0), (3, 2, 0))
    block.add_line((1, 2, 1), (3, 2, 1))
    doc.modelspace().add_blockref(
        "body", (10, 20), dxfattribs={"xscale": 2, "yscale": 3, "zscale": 0.08}
    )
    path = tmp_path / "scaled.dxf"
    doc.saveas(path)
    result = snapshot_dxf(path)
    assert len(result["placed_lines"]) == 1
    assert result["placed_lines"][0]["start"] == pytest.approx([12, 26, 0])
    assert result["placed_lines"][0]["end"] == pytest.approx([16, 26, 0])
    assert result["omissions"]["nonplanar_line"] == 1
    assert "unsupported_z_transform" not in result["omissions"]


@pytest.mark.parametrize("bulge", [1.0, -1.0, 0.25, -0.5, 2.0])
def test_polyline_bulges_have_exact_endpoints_and_lie_on_independent_circle(bulge):
    from ezdxf.math import bulge_to_arc

    item = {
        "kind": "LWPOLYLINE",
        "vertices": [{"x": 0, "y": 0, "bulge": bulge}, {"x": 10, "y": 0}],
        "closed": False,
    }
    points, error = curve_points(item)
    center, _, _, radius = bulge_to_arc((0, 0), (10, 0), bulge)
    assert points[0] == (0, 0) and points[-1] == (10, 0)
    assert all(
        math.hypot(x - center.x, y - center.y) == pytest.approx(radius)
        for x, y in points
    )
    assert error > 0
    if abs(bulge) == 1:
        assert (sum(y for _, y in points) < 0) == (bulge > 0)


def test_nonuniform_transform_error_bound_encloses_true_ellipse():
    item = {"kind": "CIRCLE", "x": 2, "y": 3, "radius": 7}
    matrix = (2.0, 0.3, -0.1, 0.5, 10.0, -20.0)
    path = transform_curve(item, matrix)
    x0, y0, x1, y1 = path["bbox_with_chord_error"]
    for i in range(2000):
        theta = i / 2000 * 2 * math.pi
        x = 2 + 7 * math.cos(theta)
        y = 3 + 7 * math.sin(theta)
        px, py = (
            matrix[0] * x + matrix[1] * y + matrix[4],
            matrix[2] * x + matrix[3] * y + matrix[5],
        )
        assert x0 <= px <= x1 and y0 <= py <= y1


def test_curve_budgets_and_bad_radius_are_explicit():
    circle = {"kind": "CIRCLE", "x": 0, "y": 0, "radius": 5}
    with pytest.raises(GeometryBudgetError):
        curve_points(circle, 10)
    with pytest.raises(ValueError):
        curve_points({**circle, "radius": float("inf")})


def test_sparse_bulges_stay_attached_to_the_correct_vertex(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_lwpolyline([(0, 0, 0), (10, 0, 1), (20, 0, 0)], format="xyb")
    path = tmp_path / "bulges.dxf"
    doc.saveas(path)
    result = snapshot_dxf(path)
    curve = result["placed_paths"][0]
    assert [v["bulge"] for v in curve["native"]["vertices"]] == [0, 1, 0]
    assert all(abs(y) < 1e-10 for x, y in curve["points"] if x < 10)
    assert any(y < -4 for x, y in curve["points"] if x > 10)
