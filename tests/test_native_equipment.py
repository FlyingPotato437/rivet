"""Independent native fixtures with actual bodies, terminal points and wires."""

import hashlib
import logging

import ezdxf
import pytest

from backend.equipment import native_edits as edits
from backend.equipment.native_model import inspect_native, set_xdata


def fixture_drawing(path):
    document = ezdxf.new("R2010")
    document.units = 4
    body = document.blocks.new("RECT")
    polygon = body.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True)
    set_xdata(polygon, ["body"])
    for name, point in (
        ("L", (0, 10)),
        ("R", (20, 10)),
        ("T", (10, 20)),
        ("B", (10, 0)),
    ):
        terminal = body.add_point(point)
        set_xdata(terminal, ["terminal", name])
    boundary = document.modelspace().add_lwpolyline(
        [(0, 0), (300, 0), (300, 200), (0, 200)], close=True
    )
    set_xdata(boundary, ["enclosure", "PANEL"])
    for tag, point in (("A", (30, 80)), ("B", (130, 80)), ("C", (230, 80))):
        device = document.modelspace().add_blockref("RECT", point)
        for key, value in (
            ("EQUIPMENT_TAG", tag),
            ("PART_NUMBER", "PN-1"),
            ("QUANTITY", "1"),
        ):
            device.add_attrib(key, value, point)
        set_xdata(device, ["device", tag, "relay", "20", "20"])
    for identity, start_tag, end_tag, points, net in (
        ("W1", "A", "B", [(50, 90), (130, 90)], "NET-1"),
        ("W2", "B", "C", [(150, 90), (230, 90)], "NET-2"),
    ):
        wire = document.modelspace().add_lwpolyline(
            points, dxfattribs={"layer": "WIRING"}
        )
        set_xdata(wire, ["wire", identity, start_tag, "R", end_tag, "L", net])
    document.modelspace().add_text(
        "Unrelated manufacturer note", dxfattribs={"height": 3}
    ).set_placement((10, 180))
    document.saveas(path)
    return path


@pytest.fixture
def drawing(tmp_path):
    return fixture_drawing(tmp_path / "panel.dxf")


def request(kind, text, **values):
    return {
        "id": hashlib.sha256(text.encode()).hexdigest()[:20],
        "kind": kind,
        **values,
        "evidence": {
            "source_id": "returned-native-request",
            "sha256": "a" * 64,
            "page": 1,
            "text": text,
        },
    }


def by_tag(graph, tag):
    return next(device for device in graph["devices"] if device["tag"] == tag)


def by_wire(graph, identity):
    return next(wire for wire in graph["wires"] if wire["id"] == identity)


def test_actual_geometry_establishes_terminal_incidence_and_physical_extents(drawing):
    graph = inspect_native(drawing)
    assert graph["status"] == "verified"
    assert by_tag(graph, "B")["bounds"] == [130, 80, 150, 100]
    assert by_wire(graph, "W1")["actual_from"] == "A:R"
    assert by_wire(graph, "W1")["actual_to"] == "B:L"
    assert by_wire(graph, "W1")["terminal_ids"] == ["A:R", "B:L"]
    document = ezdxf.readfile(drawing)
    device = next(
        d
        for d in document.modelspace().query("INSERT")
        if d.get_attrib_text("EQUIPMENT_TAG") == "C"
    )
    device.dxf.rotation = 90
    rotated = drawing.parent / "rotated.dxf"
    document.saveas(rotated)
    result = by_tag(inspect_native(rotated), "C")
    assert result["bounds"] == [210, 80, 230, 100]
    assert next(t for t in result["terminals"] if t["name"] == "L")["position"] == [
        220,
        80,
    ]


def test_move_updates_actual_attached_paths_and_saved_readback(drawing, tmp_path):
    original = drawing.read_bytes()
    move = request(
        "move_device", "MOVE B TO X=140 Y=120", device_tag="B", to=[140, 120]
    )
    plan = edits.plan_native_changes(drawing, [move])
    assert plan["status"] == "ready", [
        c for c in plan["checks"] if c["status"] != "pass"
    ]
    result = edits.apply_native_changes(drawing, plan, tmp_path / "draft.dxf")
    assert result["status"] == "verified"
    assert by_tag(result["after"], "B")["position"] == [140, 120]
    assert by_wire(result["after"], "W1")["actual_to"] == "B:L"
    assert by_wire(result["after"], "W2")["actual_from"] == "B:R"
    assert (
        by_wire(result["before"], "W1")["points"]
        != by_wire(result["after"], "W1")["points"]
    )
    assert drawing.read_bytes() == original
    assert {item["kind"] for item in result["diff"]["changes"]} >= {
        "device_geometry",
        "reroute",
    }


@pytest.mark.parametrize(
    ("point", "issue"), [([40, 80], "collision"), ([290, 190], "bounds")]
)
def test_collision_and_enclosure_escape_block_move(drawing, point, issue):
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "move_device",
                f"MOVE B TO X={point[0]} Y={point[1]}",
                device_tag="B",
                to=point,
            )
        ],
    )
    assert plan["status"] == "blocked"
    assert any(c["status"] == "fail" and issue in c["id"] for c in plan["checks"])


def test_resize_changes_real_body_scale_and_transformed_terminals(drawing, tmp_path):
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "resize_device",
                "RESIZE B WIDTH=30 HEIGHT=40",
                device_tag="B",
                width=30,
                height=40,
            )
        ],
    )
    assert plan["status"] == "ready", [
        c for c in plan["checks"] if c["status"] != "pass"
    ]
    result = edits.apply_native_changes(drawing, plan, tmp_path / "resize.dxf")
    assert by_tag(result["after"], "B")["bounds"] == [130, 80, 160, 120]
    assert by_wire(result["after"], "W1")["points"][-1] == [130, 100]


def test_move_routes_around_body_and_unrelated_terminals(drawing, tmp_path):
    document = ezdxf.readfile(drawing)
    for tag, point in (("D", (90, 115)), ("E", (30, 120))):
        device = document.modelspace().add_blockref("RECT", point)
        device.add_attrib("EQUIPMENT_TAG", tag, point)
        set_xdata(device, ["device", tag, "relay", "20", "20"])
    document.saveas(drawing)
    assert inspect_native(drawing)["status"] == "verified"
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "move_device", "MOVE B TO X=140 Y=120", device_tag="B", to=[140, 120]
            )
        ],
    )
    assert plan["status"] == "ready", [
        c for c in plan["checks"] if c["status"] != "pass"
    ]
    result = edits.apply_native_changes(drawing, plan, tmp_path / "routed.dxf")
    assert by_wire(result["after"], "W1")["terminal_ids"] == ["A:R", "B:L"]
    assert by_wire(result["after"], "W2")["terminal_ids"] == ["B:R", "C:L"]
    assert not [c for c in result["checks"] if c["status"] != "pass"]


def test_explicit_rewire_changes_topology_and_rejects_missing_pin(drawing, tmp_path):
    route = [[150, 90], [150, 120], [250, 120], [250, 90]]
    change = request(
        "rewire",
        "REWIRE W2 FROM B:R TO C:R VIA 150,90 150,120 250,120 250,90",
        wire_id="W2",
        from_terminal="B:R",
        to_terminal="C:R",
        points=route,
    )
    plan = edits.plan_native_changes(drawing, [change])
    assert plan["status"] == "ready", [
        c for c in plan["checks"] if c["status"] != "pass"
    ]
    result = edits.apply_native_changes(drawing, plan, tmp_path / "rewired.dxf")
    assert by_wire(result["after"], "W2")["actual_to"] == "C:R"
    assert by_wire(result["after"], "W2")["points"] == route
    missing = request(
        "rewire",
        "REWIRE W2 FROM B:R TO C:MISSING",
        wire_id="W2",
        from_terminal="B:R",
        to_terminal="C:MISSING",
    )
    assert edits.plan_native_changes(drawing, [missing])["status"] == "blocked"


def test_reroute_preserves_endpoints_and_rejects_diagonal(drawing, tmp_path):
    route = [[50, 90], [50, 60], [130, 60], [130, 90]]
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "reroute",
                "REROUTE W1 VIA 50,90 50,60 130,60 130,90",
                wire_id="W1",
                points=route,
            )
        ],
    )
    assert plan["status"] == "ready"
    result = edits.apply_native_changes(drawing, plan, tmp_path / "rerouted.dxf")
    assert by_wire(result["after"], "W1")["points"] == route
    diagonal = edits.plan_native_changes(
        drawing,
        [
            request(
                "reroute",
                "REROUTE W1 VIA 50,90 80,110 130,90",
                wire_id="W1",
                points=[[50, 90], [80, 110], [130, 90]],
            )
        ],
    )
    assert diagonal["status"] == "blocked"


def test_add_and_remove_are_real_entities_with_explicit_template_and_connections(
    drawing, tmp_path
):
    add = request(
        "add_device",
        "ADD D BLOCK RECT AT X=80 Y=140 PART_NUMBER=PN-X QUANTITY=1",
        device_tag="D",
        block_name="RECT",
        at=[80, 140],
        attributes={"PART_NUMBER": "PN-X", "QUANTITY": "1"},
    )
    plan = edits.plan_native_changes(drawing, [add])
    assert plan["status"] == "ready"
    result = edits.apply_native_changes(drawing, plan, tmp_path / "added.dxf")
    assert by_tag(result["after"], "D")["attributes"]["PART_NUMBER"] == "PN-X"
    assert by_tag(result["after"], "D")["terminals"]
    remove = request("remove_device", "REMOVE B", device_tag="B")
    assert edits.plan_native_changes(drawing, [remove])["status"] == "blocked"
    safe = edits.plan_native_changes(
        drawing,
        [
            remove,
            request("remove_wire", "REMOVE W1", wire_id="W1"),
            request("remove_wire", "REMOVE W2", wire_id="W2"),
        ],
    )
    assert safe["status"] == "ready"
    removed = edits.apply_native_changes(drawing, safe, tmp_path / "removed.dxf")
    assert {d["tag"] for d in removed["after"]["devices"]} == {"A", "C"}
    assert not removed["after"]["wires"]


def test_crossing_without_dot_is_not_a_short_but_explicit_junction_is(drawing):
    document = ezdxf.readfile(drawing)
    for tag, point in (("D", (80, 20)), ("E", (80, 140))):
        device = document.modelspace().add_blockref("RECT", point)
        device.add_attrib("EQUIPMENT_TAG", tag, point)
        set_xdata(device, ["device", tag, "relay", "20", "20"])
    wire = document.modelspace().add_line((90, 40), (90, 140))
    set_xdata(wire, ["wire", "CROSS", "D", "T", "E", "B", "OTHER-NET"])
    cross = drawing.parent / "cross.dxf"
    document.saveas(cross)
    graph = inspect_native(cross)
    assert graph["status"] == "verified", [
        c for c in graph["checks"] if c["status"] != "pass"
    ]
    assert any(
        c["position"] == [90, 90] and not c["connected"] for c in graph["crossings"]
    )
    dot = document.modelspace().add_point((90, 90))
    set_xdata(dot, ["junction", "J1", "NET-1"])
    document.saveas(cross)
    graph = inspect_native(cross)
    assert graph["status"] == "blocked"
    assert any(c["id"] == "short:junction:J1" for c in graph["checks"])


def test_source_values_cannot_be_forged_and_metadata_cannot_fake_attachment(drawing):
    forged = request(
        "move_device", "MOVE B TO X=140 Y=120", device_tag="B", to=[140, 130]
    )
    assert edits.plan_native_changes(drawing, [forged])["status"] == "blocked"
    document = ezdxf.readfile(drawing)
    wire = next(iter(document.modelspace().query("LWPOLYLINE[layer=='WIRING']")))
    wire.set_points([(50, 91), (130, 91)])
    changed = drawing.parent / "lying-metadata.dxf"
    document.saveas(changed)
    result = inspect_native(changed)
    assert by_wire(result, "W1")["actual_to"] is None
    assert any(
        c["status"] == "fail" and c["id"].startswith("attachment:")
        for c in result["checks"]
    )


def test_saved_corruption_is_detected_and_unrequested_note_drift_is_blocked(
    drawing, tmp_path, monkeypatch
):
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "move_device", "MOVE B TO X=140 Y=120", device_tag="B", to=[140, 120]
            )
        ],
    )
    original_inspect = edits.inspect_native

    def corrupt(path):
        document = ezdxf.readfile(path)
        wire = next(iter(document.modelspace().query("LWPOLYLINE[layer=='WIRING']")))
        wire.set_points([(50, 90), (130, 91)])
        document.saveas(path)
        return original_inspect(path)

    monkeypatch.setattr(edits, "inspect_native", corrupt)
    output = tmp_path / "corrupted.dxf"
    with pytest.raises(edits.NativeEditError, match="independent"):
        edits.apply_native_changes(drawing, plan, output)
    assert not output.exists()
    monkeypatch.setattr(edits, "inspect_native", original_inspect)
    output = tmp_path / "valid.dxf"
    valid = edits.apply_native_changes(drawing, plan, output)
    document = ezdxf.readfile(output)
    next(
        iter(document.modelspace().query("TEXT"))
    ).dxf.text = "Unexpected changed instruction"
    document.saveas(output)
    drift = edits.verify_native_changes(valid["before"], inspect_native(output), plan)
    assert drift["status"] == "blocked"


def test_saved_attribute_label_movement_and_wire_style_drift_are_blocked(
    drawing, tmp_path
):
    change = request(
        "move_device", "MOVE B TO X=140 Y=120", device_tag="B", to=[140, 120]
    )
    plan = edits.plan_native_changes(drawing, [change])
    output = tmp_path / "presented.dxf"
    result = edits.apply_native_changes(drawing, plan, output)
    doc = ezdxf.readfile(output)
    device = doc.entitydb[by_tag(result["after"], "B")["handle"]]
    device.get_attrib("PART_NUMBER").dxf.insert = (10, 10, 0)
    doc.saveas(output)
    assert (
        edits.verify_native_changes(result["before"], inspect_native(output), plan)[
            "status"
        ]
        == "blocked"
    )
    doc = ezdxf.readfile(drawing)
    wire = next(iter(doc.modelspace().query("LWPOLYLINE[layer=='WIRING']")))
    wire.set_points([(50, 90), (90, 90), (70, 90), (130, 90)])
    doc.saveas(output)
    graph = inspect_native(output)
    assert any(
        c["id"] == "self_path:W1" and c["status"] == "fail" for c in graph["checks"]
    )


def test_explicit_annotation_ownership_moves_and_removes_only_linked_labels(
    drawing, tmp_path
):
    doc = ezdxf.readfile(drawing)
    label = (
        doc.modelspace()
        .add_text("W1", dxfattribs={"height": 2})
        .set_placement((90, 93))
    )
    set_xdata(label, ["wire_label", "W1"])
    doc.modelspace().add_text(
        "W1 engineering note: retain", dxfattribs={"height": 2}
    ).set_placement((20, 175))
    doc.saveas(drawing)
    before = inspect_native(drawing)
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "move_device", "MOVE B TO X=140 Y=120", device_tag="B", to=[140, 120]
            )
        ],
    )
    result = edits.apply_native_changes(drawing, plan, tmp_path / "labels-moved.dxf")
    assert (
        result["after"]["annotations"][0]["position"]
        != before["annotations"][0]["position"]
    )
    removal = edits.plan_native_changes(
        drawing, [request("remove_wire", "REMOVE W1", wire_id="W1")]
    )
    removed = edits.apply_native_changes(
        drawing, removal, tmp_path / "labels-removed.dxf"
    )
    assert not removed["after"]["annotations"]
    saved = ezdxf.readfile(tmp_path / "labels-removed.dxf")
    texts = [e.dxf.text for e in saved.modelspace().query("TEXT")]
    assert "W1" not in texts
    assert "W1 engineering note: retain" in texts


def test_added_wire_is_actual_routed_entity_with_source_named_label(drawing, tmp_path):
    changes = [
        request(
            "add_device",
            "ADD D BLOCK RECT AT X=80 Y=140 PART_NUMBER=PN-X QUANTITY=1",
            device_tag="D",
            block_name="RECT",
            at=[80, 140],
            attributes={"PART_NUMBER": "PN-X", "QUANTITY": "1"},
        ),
        request(
            "add_wire",
            "ADD W3 FROM C:T TO D:R NET_ID=NEW",
            wire_id="W3",
            from_terminal="C:T",
            to_terminal="D:R",
            net_id="NEW",
        ),
    ]
    plan = edits.plan_native_changes(drawing, changes)
    assert plan["status"] == "ready", [
        c for c in plan["checks"] if c["status"] != "pass"
    ]
    result = edits.apply_native_changes(drawing, plan, tmp_path / "new-wire.dxf")
    assert by_wire(result["after"], "W3")["actual_to"] == "D:R"
    assert (
        next(a for a in result["after"]["annotations"] if a["owner_id"] == "W3")["text"]
        == "W3"
    )


def test_routing_complexity_is_bounded_and_blocks_cleanly(drawing, monkeypatch):
    monkeypatch.setattr(edits, "MAX_ROUTING_WORK", 0)
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "move_device", "MOVE B TO X=140 Y=120", device_tag="B", to=[140, 120]
            )
        ],
    )
    assert plan["status"] == "blocked"
    assert any("complexity budget" in c["reason"] for c in plan["checks"])


def test_explicit_rewire_cannot_silently_touch_an_unused_third_terminal(drawing):
    doc = ezdxf.readfile(drawing)
    device = doc.modelspace().add_blockref("RECT", (70, 50))
    device.add_attrib("EQUIPMENT_TAG", "D", (70, 50))
    set_xdata(device, ["device", "D", "relay", "20", "20"])
    doc.saveas(drawing)
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "rewire",
                "REWIRE W1 FROM A:R TO B:L VIA 50,90 50,50 130,50 130,90",
                wire_id="W1",
                from_terminal="A:R",
                to_terminal="B:L",
                points=[[50, 90], [50, 50], [130, 50], [130, 90]],
            )
        ],
    )
    assert plan["status"] == "blocked"
    assert any(c["id"] == "incidental_connection:W1" for c in plan["checks"])


def test_metadata_replacement_is_single_section_and_preserves_vendor_data(
    drawing, caplog
):
    from ezdxf.lldxf.tagwriter import TagCollector

    doc = ezdxf.readfile(drawing)
    doc.appids.new("MANUFACTURER")
    wire = next(iter(doc.modelspace().query("LWPOLYLINE[layer=='WIRING']")))
    wire.set_xdata("MANUFACTURER", [(1000, "vendor-native-metadata")])
    values = ["wire", "W1", "A", "R", "B", "L", "NET-1"]
    with caplog.at_level(logging.INFO, logger="ezdxf"):
        set_xdata(wire, values)
        values[-1] = "EXPLICIT-NEW-NET"
        set_xdata(wire, values)
        set_xdata(wire, values)
    assert not [r for r in caplog.records if "Duplicate XDATA appid" in r.message]
    collector = TagCollector()
    wire.export_dxf(collector)
    names = [tag.value for tag in collector.tags if tag.code == 1001]
    assert names.count("RIVET") == names.count("MANUFACTURER") == 1
    assert [t.value for t in wire.get_xdata("MANUFACTURER")] == [
        "vendor-native-metadata"
    ]


def test_native_preview_never_mutates_or_duplicates_source_metadata(drawing, caplog):
    from backend.drafting.preview import drawing_svg
    from backend.equipment.native_model import entity_signature

    doc = ezdxf.readfile(drawing)
    original = {e.dxf.handle: entity_signature(e) for e in doc.modelspace()}
    disk_bytes = drawing.read_bytes()
    with caplog.at_level(logging.INFO, logger="ezdxf"):
        first = drawing_svg(doc, title="Native source")
        second = drawing_svg(doc, title="Native source")
    assert first == second
    assert original == {e.dxf.handle: entity_signature(e) for e in doc.modelspace()}
    assert drawing.read_bytes() == disk_bytes
    assert not [r for r in caplog.records if "Duplicate XDATA appid" in r.message]


def test_stale_source_or_existing_destination_cannot_be_overwritten(drawing, tmp_path):
    plan = edits.plan_native_changes(
        drawing,
        [
            request(
                "move_device", "MOVE B TO X=140 Y=120", device_tag="B", to=[140, 120]
            )
        ],
    )
    existing = tmp_path / "keep.dxf"
    existing.write_text("keep")
    with pytest.raises(edits.NativeEditError):
        edits.apply_native_changes(drawing, plan, existing)
    assert existing.read_text() == "keep"
    drawing.write_bytes(drawing.read_bytes() + b"\n")
    with pytest.raises(edits.NativeEditError, match="changed"):
        edits.apply_native_changes(drawing, plan, tmp_path / "stale.dxf")
