"""Order isolation and immutable-record integration for the read-only prototype."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.engineering.api import phase_change
from backend.identity import Identity, identity_scope
from tests.test_records import create, upload


def test_phase_preflight_is_order_scoped_read_only_and_version_checked(client):
    order = create(client)
    upload(client, order, "phase.txt", b"Comment 1: Confirm transformer phase.")
    before = client.get(f"/api/orders/{order['id']}").json()
    record_before = client.get(f"/api/orders/{order['id']}/record").json()
    result = client.get(f"/api/orders/{order['id']}/engineering/phase-change")
    assert result.status_code == 200, result.text
    report = result.json()
    body = {
        "expected_version": report["order_version"],
        "target": "three_phase",
        "supply": "single_phase",
        "total_kva": 75,
        "terminal_voltage_v": 208,
        "balanced_load": True,
    }
    result = client.post(
        f"/api/orders/{order['id']}/engineering/phase-change/analyze", json=body
    )
    assert result.status_code == 200, result.text
    assert result.json()["scenario_calculations"]
    assert not result.json()["native_execution_supported"]
    assert client.get(f"/api/orders/{order['id']}").json() == before
    assert client.get(f"/api/orders/{order['id']}/record").json() == record_before
    body["expected_version"] += 1
    assert (
        client.post(
            f"/api/orders/{order['id']}/engineering/phase-change/analyze", json=body
        ).status_code
        == 409
    )
    assert (
        client.get(
            "/api/orders/00000000-0000-0000-0000-000000000001/engineering/phase-change"
        ).status_code
        == 404
    )
    # The API does not accept paths, write switches or an approval bypass.
    body["path"] = "/etc/passwd"
    assert (
        client.post(
            f"/api/orders/{order['id']}/engineering/phase-change/analyze", json=body
        ).status_code
        == 422
    )
    with identity_scope(Identity(str(uuid4()), "Another manufacturer", "org:admin")):
        with pytest.raises(HTTPException) as denied:
            phase_change(order["id"])
        assert denied.value.status_code == 404
