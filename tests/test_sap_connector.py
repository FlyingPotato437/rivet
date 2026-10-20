"""SAP sales-order connector: exact, read-only, cited; no live SAP calls in tests."""

import base64
import json

import httpx
import pytest

from backend.connected_context.providers import (
    ConnectorError,
    read_sap,
    sap_sandbox_binding,
)

BASE = "https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap/API_SALES_ORDER_SRV"
ORDER = {
    "d": {
        "SalesOrder": "1",
        "SalesOrderType": "OR",
        "SoldToParty": "17100001",
        "PurchaseOrderByCustomer": "PO-77",
        "SalesOrderDate": "/Date(1475971200000)/",
        "RequestedDeliveryDate": "/Date(1476057600000)/",
        "TotalNetAmount": "52.65",
        "TransactionCurrency": "USD",
        "OverallSDProcessStatus": "C",
        "OverallTotalDeliveryStatus": "C",
        "DeliveryBlockReason": "",
        "LastChangeDateTime": "/Date(1476057600000+0000)/",
        "to_Item": {
            "results": [
                {
                    "SalesOrderItem": "10",
                    "Material": "TG11",
                    "SalesOrderItemText": "Trading Good 11",
                    "RequestedQuantity": "1",
                    "RequestedQuantityUnit": "PC",
                    "NetAmount": "52.65",
                    "TransactionCurrency": "USD",
                }
            ]
        },
    }
}


def binding(**extra):
    return {**sap_sandbox_binding("1"), **extra}


def transport(seen, body=ORDER, status=200):
    def handle(request):
        seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handle)


def test_reads_one_exact_order_with_the_key_in_a_header(monkeypatch):
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    seen = []
    [resource] = read_sap(binding(), "SAP-SANDBOX-1", transport(seen))
    request = seen[0]
    assert request.method == "GET"
    assert request.headers["APIKey"] == "fake-key"
    assert "fake-key" not in str(request.url)
    assert request.url.path.endswith("/A_SalesOrder('1')")
    text = base64.b64decode(resource.content_base64).decode()
    assert "SAP S/4HANA sales order 1" in text
    assert "Order date: 2016-10-09" in text
    assert "10 | material TG11 | Trading Good 11 | qty 1 PC" in text
    assert resource.name == "SAP-sales-order-1.txt"
    assert resource.facts == {}  # nothing compared unless an administrator maps it
    assert resource.lifecycle == "unknown"
    assert "connectivity demonstration only" in text
    assert "not customer scope" in text


def test_mapped_fields_become_comparable_facts(monkeypatch):
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    [resource] = read_sap(
        binding(
            base_url="https://sap.example.test/API_SALES_ORDER_SRV",
            allowed_hosts=["sap.example.test"],
            fact_fields={"order.customer_reference": "PurchaseOrderByCustomer"},
        ),
        "JOB-1",
        transport([]),
    )
    assert resource.facts == {"order.customer_reference": "PO-77"}


def test_same_order_gives_the_same_fingerprint(monkeypatch):
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    a = read_sap(binding(), "SAP-SANDBOX-1", transport([]))[0]
    b = read_sap(binding(), "SAP-SANDBOX-1", transport([]))[0]
    assert a.sha256 == b.sha256


@pytest.mark.parametrize(
    "extra,message",
    [
        ({"base_url": "http://sandbox.api.sap.com/x"}, "approved HTTPS host"),
        ({"base_url": "https://evil.example.com/x"}, "approved HTTPS host"),
        ({"sales_order": "1' or '1"}, "exact SAP sales order"),
    ],
)
def test_rejects_unapproved_hosts_and_free_text_orders(monkeypatch, extra, message):
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    with pytest.raises(ConnectorError, match=message):
        read_sap(binding(**extra), "SAP-SANDBOX-1", transport([]))


def test_missing_key_wrong_order_and_errors_fail_closed(monkeypatch):
    monkeypatch.delenv("RIVET_SAP_API_KEY", raising=False)
    with pytest.raises(ConnectorError, match="API key"):
        read_sap(binding(), "SAP-SANDBOX-1", transport([]))
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    other = {"d": {**ORDER["d"], "SalesOrder": "2"}}
    with pytest.raises(ConnectorError, match="different sales order"):
        read_sap(binding(), "SAP-SANDBOX-1", transport([], other))
    with pytest.raises(ConnectorError, match="HTTP 401"):
        read_sap(binding(), "SAP-SANDBOX-1", transport([], {}, 401))


def test_sandbox_cannot_be_used_as_a_real_orders_scope(monkeypatch):
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    seen = []
    with pytest.raises(ConnectorError, match="cannot be used as another order"):
        read_sap(binding(), "DEMO-OUTLOOK-001", transport(seen))
    assert seen == []
    with pytest.raises(ConnectorError, match="cannot become equipment facts"):
        read_sap(
            binding(fact_fields={"CB-12.trip_unit": "SalesOrderType"}),
            "SAP-SANDBOX-1",
            transport(seen),
        )
    assert seen == []


@pytest.mark.parametrize(
    "expanded",
    [None, {}, {"__deferred": {}}, {"results": [], "__next": "https://other.example"}],
)
def test_missing_or_paginated_items_are_not_presented_as_complete(
    monkeypatch, expanded
):
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    body = {"d": {**ORDER["d"], "to_Item": expanded}}
    with pytest.raises(ConnectorError, match="incomplete|invalid"):
        read_sap(binding(), "SAP-SANDBOX-1", transport([], body))


def test_redirect_does_not_forward_credentials(monkeypatch):
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    seen = []

    def redirect(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example.test"})

    with pytest.raises(ConnectorError, match="HTTP 302"):
        read_sap(binding(), "SAP-SANDBOX-1", httpx.MockTransport(redirect))
    assert len(seen) == 1


@pytest.mark.parametrize(
    "mapping", [["SalesOrder"], {"": "SalesOrder"}, {"fact": "x" * 161}]
)
def test_bad_production_mapping_is_rejected(monkeypatch, mapping):
    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    with pytest.raises(ConnectorError, match="mapping is invalid"):
        read_sap(
            binding(
                base_url="https://sap.example.test/API_SALES_ORDER_SRV",
                allowed_hosts=["sap.example.test"],
                fact_fields=mapping,
            ),
            "JOB-1",
            transport([]),
        )


def test_verification_saves_separate_evidence_without_credentials(
    monkeypatch, tmp_path
):
    from scripts.verify_sap_sandbox import verify

    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-private-key")
    org = "6151b990-82f1-57d4-a559-df777cb047af"
    seen = []
    output = tmp_path / "verification"
    report = verify("1", output, org, transport(seen))
    assert report["job_number"] == "SAP-SANDBOX-1"
    assert report["engineering_evidence"] is False
    assert report["status"] == "verified"
    assert [request.method for request in seen] == ["GET"]
    config = json.loads((output / "standalone-binding.example.json").read_text())
    [job] = config["organizations"][org]["jobs"]
    assert job == "SAP-SANDBOX-1"
    assert config["organizations"][org]["jobs"][job]["auto_prepare_reply"] is False
    assert all(
        "fake-private-key" not in file.read_text()
        for file in output.rglob("*")
        if file.is_file()
    )


def test_failed_verification_does_not_create_success_artifacts(monkeypatch, tmp_path):
    from scripts.verify_sap_sandbox import verify

    monkeypatch.setenv("RIVET_SAP_API_KEY", "fake-key")
    output = tmp_path / "verification"
    with pytest.raises(ConnectorError, match="HTTP 403"):
        verify("1", output, transport=transport([], {}, 403))
    assert not output.exists()
