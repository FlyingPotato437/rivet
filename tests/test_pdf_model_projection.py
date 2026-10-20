"""Visible PDF drawing evidence reaches the typed order model without fake mm."""

from hashlib import sha256
from uuid import uuid4

from test_equipment_api import new_order

from backend.equipment.model import EquipmentModel
from backend.equipment.pdf_corpus import series, write_visible_pdf


def upload(client, oid, path, revision):
    response = client.post(
        f"/api/orders/{oid}/equipment/packages",
        data={"role": "drawing", "revision": revision},
        files=[("files", (path.name, path.read_bytes()))],
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 200, response.text


def test_order_model_contains_real_pdf_nets_and_revision_geometry_but_no_physical_dimensions(client, tmp_path):
    oid = new_order(client)
    before = tmp_path / "control-C.pdf"
    after = tmp_path / "control-D.pdf"
    original = series()
    write_visible_pdf(before, original, "Control schematic revision C")
    changed = series()
    changed["devices"][1]["bbox"] = [450, 170, 570, 290]
    changed["devices"][1]["ports"] = {"A1": [450, 200], "A2": [450, 260]}
    changed["wires"] = [[[170, 200], [450, 200]], [[170, 260], [450, 260]]]
    write_visible_pdf(after, changed, "Control schematic revision D")
    upload(client, oid, before, "C")
    upload(client, oid, after, "D")
    response = client.get(f"/api/orders/{oid}/equipment/model")
    assert response.status_code == 200, response.text
    model = EquipmentModel.model_validate(response.json())
    assert len(model.vector_drawings) == 2
    target = next(d for d in model.vector_drawings if d.side == "target")
    assert target.source.sha256 == sha256(after.read_bytes()).hexdigest()
    assert len(target.connections) == 2
    assert {tuple(c["terminal_ids"]) for c in target.connections} == {
        ("KA-1:A1", "TB-1:1"), ("KA-1:A2", "TB-1:2"),
    }
    ka = next(d for d in model.devices if d.tag == "KA-1")
    assert ka.connectivity.status == "observed"
    assert ka.connectivity.observations[0].source.location["page"] == 1
    assert ka.dimensions.status == "unknown" and not ka.dimensions.observations
    assert model.coverage["pdf_physical_dimensions_verified"] is False
    assert model.coverage["complete_inventory_verified"] is False
    assert model.native is None
    assert model.pdf_revision_check["summary"]["observed_geometry_changes"] > 0
    assert not model.pdf_revision_check["summary"]["complete_revision_verified"]
    assert "/tmp" not in response.text and str(tmp_path) not in response.text
