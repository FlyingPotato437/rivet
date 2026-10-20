import json

import pytest
from fastapi import HTTPException
from test_company_context import ORG_B, identity
from test_connected_context import setup

from backend.connected_context import diagnostics, service
from backend.storage.db import Session


def test_connection_status_reflects_retrieved_sources_and_tenant(monkeypatch, tmp_path):
    with identity():
        oid, job, _, path = setup(monkeypatch, tmp_path)
        config = json.loads(path.read_text())
        connectors = next(iter(config["organizations"].values()))["jobs"][job][
            "connectors"
        ]
        sap = connectors[0]
        sap.update(
            adapter="sap_odata",
            mode="live",
            base_url="https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap/API_SALES_ORDER_SRV",
            sales_order="1",
            api_key_env="RIVET_TEST_SECRET",
        )
        path.write_text(json.dumps(config))
        with Session() as s:
            assert diagnostics.sap_status(s)["profiles"][0]["status"] == "pending"
        service.synchronize(oid)
        with Session.begin() as s:
            profile = diagnostics.sap_status(s)["profiles"][0]
            assert profile["environment"] == "SAP sandbox"
            assert profile["source_status"] == "current"
            assert profile["documents"] == 1
            assert profile["resources"][0]["sha256"]
            assert (
                diagnostics.integration_health(s, "erp")["status"] == "sandbox_verified"
            )
            assert (
                diagnostics.integration_health(s, "sharepoint")["status"] == "demo_only"
            )
            assert "RIVET_TEST_SECRET" not in str(profile)
            assert diagnostics.check_sap(s, oid)["status"] == "queued"
        from backend.connected_context import providers

        def unavailable(binding, job):
            raise providers.ConnectorError("Source unavailable (HTTP 503).")

        monkeypatch.setattr(providers, "fetch", unavailable)
        service.synchronize(oid)
        with Session() as s:
            assert (
                diagnostics.integration_health(s, "erp")["status"]
                == "attention_required"
            )
            assert (
                diagnostics.sap_status(s)["profiles"][0]["source_status"]
                == "unavailable"
            )
    with identity(ORG_B), Session.begin() as s:
        assert not diagnostics.sap_status(s)["profiles"]
        with pytest.raises(HTTPException) as error:
            diagnostics.check_sap(s, oid)
        assert error.value.status_code == 404
    with identity(role="org:member"), Session() as s:
        with pytest.raises(HTTPException) as error:
            diagnostics.sap_status(s)
        assert error.value.status_code == 403
