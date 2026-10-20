"""Explicit server-side bindings. No model-selected URLs or arbitrary browsing."""

import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from backend.identity import current_org

MAX_BYTES = 12 * 1024 * 1024


class ConnectorError(ValueError):
    pass


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_id: str = Field(min_length=1, max_length=240)
    job_number: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=240)
    revision: str = Field(default="", max_length=80)
    lifecycle: str = Field(default="unknown", max_length=80)
    family: str = Field(default="", max_length=120)
    content_base64: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_url: str = Field(default="", max_length=2000)
    facts: dict[str, str] = Field(default_factory=dict)

    def content(self):
        try:
            data = base64.b64decode(self.content_base64, validate=True)
        except ValueError as exc:
            raise ConnectorError("Source content encoding is invalid.") from exc
        if not data or len(data) > MAX_BYTES:
            raise ConnectorError(
                "Source exceeds the 12 MB connector limit or is empty."
            )
        if hashlib.sha256(data).hexdigest() != self.sha256:
            raise ConnectorError("Source content does not match its fingerprint.")
        if Path(self.name).name != self.name or Path(self.name).suffix.lower() not in {
            ".pdf",
            ".txt",
        }:
            raise ConnectorError("This connector accepts PDF and text originals only.")
        if len(self.facts) > 40 or any(
            len(k) > 160 or len(v) > 500 for k, v in self.facts.items()
        ):
            raise ConnectorError("Source metadata exceeds the supported limit.")
        return data


def configuration():
    path = os.getenv("RIVET_CONNECTED_CONTEXT_CONFIG", "")
    if not path:
        return {}
    try:
        result = json.loads(Path(path).read_text())
        if not isinstance(result.get("organizations"), dict):
            raise TypeError()
        return result
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ConnectorError(
            "Connected source configuration needs administrator attention."
        ) from exc


def bindings(job_number, organization_id=None):
    org = (
        configuration()
        .get("organizations", {})
        .get(organization_id or current_org(), {})
    )
    # Exact, administrator-confirmed order mapping; no fuzzy cross-company search.
    job = org.get("jobs", {}).get(job_number)
    if not job:
        return []
    result = job.get("connectors", [])
    keys = [x.get("key") for x in result]
    if len(result) > 8 or len(set(keys)) != len(keys):
        raise ConnectorError("Connected source bindings must have unique keys.")
    for item in result:
        if item.get("kind") not in {"erp", "sharepoint", "vault"} or not item.get(
            "key"
        ):
            raise ConnectorError("Unknown connected source type.")
    return result


def _json(client, url, headers=None):
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code != 200:
            raise ConnectorError(f"Source unavailable (HTTP {response.status_code}).")
        parts, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > 24 * 1024 * 1024:
                raise ConnectorError("Connector response exceeds the size limit.")
            parts.append(chunk)
        return json.loads(b"".join(parts))


def read_adapter(binding, job_number, transport=None):
    """Rivet's read-only adapter protocol, backed by real independent local services."""
    base = binding["base_url"].rstrip("/")
    parsed = urlsplit(base)
    local = binding.get("mode") == "local_demo"
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConnectorError("Invalid configured source URL.")
    if local:
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ConnectorError("Local demo connectors must use loopback HTTP.")
    elif parsed.scheme != "https":
        raise ConnectorError("External adapters require HTTPS.")
    headers = {}
    if binding.get("token_env"):
        token = os.getenv(binding["token_env"], "")
        if not token:
            raise ConnectorError("Connector credential is not configured.")
        headers["Authorization"] = "Bearer " + token
    with httpx.Client(
        timeout=20, follow_redirects=False, transport=transport
    ) as client:
        payload = _json(
            client,
            base + "/v1/orders/" + quote(job_number, safe="") + "/resources",
            headers,
        )
    if (
        payload.get("job_number") != job_number
        or payload.get("kind") != binding["kind"]
    ):
        raise ConnectorError("The source returned a different order or system.")
    if not isinstance(payload.get("resources"), list) or len(payload["resources"]) > 30:
        raise ConnectorError("The source inventory is invalid or exceeds 30 files.")
    resources = [Resource.model_validate(item) for item in payload["resources"]]
    _validate(resources, job_number)
    return resources


def _validate(resources, job_number):
    if not resources:
        raise ConnectorError("No records found for the configured order.")
    identities = [r.external_id for r in resources]
    if len(set(identities)) != len(identities):
        raise ConnectorError("The source returned duplicate resource identities.")
    for resource in resources:
        if resource.job_number != job_number:
            raise ConnectorError("Source job number does not match this order.")
        resource.content()


def _sharepoint_facts(item, fields):
    """Compare only administrator-mapped SharePoint columns, never invented values."""
    mapping = item.get("fact_fields", {})
    if (
        not isinstance(mapping, dict)
        or len(mapping) > 40
        or any(
            not isinstance(key, str)
            or not key
            or len(key) > 160
            or not isinstance(column, str)
            or not column
            or len(column) > 160
            for key, column in mapping.items()
        )
    ):
        raise ConnectorError("SharePoint fact column mapping is invalid.")
    facts = {}
    for key, column in mapping.items():
        value = fields.get(column)
        if value is None or value == "":
            continue
        if not isinstance(value, (str, int, float, bool)):
            raise ConnectorError("SharePoint fact column must contain a single value.")
        text = str(value).strip()
        if len(text) > 500:
            raise ConnectorError("SharePoint fact column exceeds the supported limit.")
        if text:
            facts[key] = text
    return facts


def read_sharepoint(binding, job_number, transport=None):
    """Read exact configured Graph items. Live consent is separate from configuration.

    Each item maps a drive/file and its business revision/lifecycle explicitly.
    Microsoft file-version numbers are never interpreted as engineering approval.
    Content redirects may carry preauthenticated download URLs; never forward the
    Graph bearer token to the download host.
    """
    from backend.mailbox.graph import credential_fields

    tenant = os.getenv("RIVET_MS_TENANT_ID", "")
    client_id = os.getenv("RIVET_MS_CLIENT_ID", "")
    if not tenant or binding.get("tenant_id") != tenant or not client_id:
        raise ConnectorError("SharePoint tenant is not explicitly authorized.")
    items = binding.get("items", [])
    if not items or len(items) > 30:
        raise ConnectorError(
            "Configure between one and thirty SharePoint source items."
        )
    with httpx.Client(
        timeout=20, follow_redirects=False, transport=transport
    ) as client:
        token_url = f"https://login.microsoftonline.com/{quote(tenant, safe='')}/oauth2/v2.0/token"
        response = client.post(
            token_url,
            data={
                "client_id": client_id,
                "grant_type": "client_credentials",
                "scope": "https://graph.microsoft.com/.default",
                **credential_fields(client_id, token_url),
            },
        )
        if response.status_code != 200:
            raise ConnectorError("Microsoft rejected SharePoint authentication.")
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        resources = []
        for item in items:
            base = (
                "https://graph.microsoft.com/v1.0/drives/"
                + quote(item["drive_id"], safe="")
                + "/items/"
                + quote(item["item_id"], safe="")
            )
            meta = _json(
                client,
                base
                + "?$select=id,name,eTag,webUrl,size&$expand=listItem($expand=fields)",
                headers,
            )
            if meta.get("id") != item["item_id"]:
                raise ConnectorError("SharePoint returned a different source item.")
            if meta.get("size", MAX_BYTES + 1) > MAX_BYTES:
                raise ConnectorError("SharePoint original exceeds the size limit.")
            with client.stream("GET", base + "/content", headers=headers) as download:
                if download.status_code in {301, 302, 303, 307, 308}:
                    location = download.headers.get("Location", "")
                    host = urlsplit(location)
                    allowed = binding.get("download_hosts", [])
                    if (
                        host.scheme != "https"
                        or host.hostname not in allowed
                        or host.username
                        or host.password
                    ):
                        raise ConnectorError(
                            "SharePoint download host is not approved in this connector."
                        )
                    data = _download(client, location)
                elif download.status_code == 200:
                    data = _bytes(download)
                else:
                    raise ConnectorError(
                        f"SharePoint file unavailable (HTTP {download.status_code})."
                    )
            after = _json(client, base + "?$select=id,eTag", headers)
            if not meta.get("eTag") or after.get("eTag") != meta["eTag"]:
                raise ConnectorError(
                    "SharePoint file changed during retrieval. Retry synchronization."
                )
            fields = meta.get("listItem", {}).get("fields", {})
            revision = str(fields.get(item.get("revision_field", ""), ""))
            state = str(fields.get(item.get("lifecycle_field", ""), ""))
            lifecycle = item.get("lifecycle_map", {}).get(state, "unknown")
            resources.append(
                Resource(
                    external_id=item["drive_id"] + ":" + meta["id"],
                    job_number=job_number,
                    name=meta["name"],
                    revision=revision,
                    lifecycle=lifecycle,
                    family=item.get("family", ""),
                    facts=_sharepoint_facts(item, fields),
                    source_url=meta.get("webUrl", ""),
                    content_base64=base64.b64encode(data).decode(),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
    _validate(resources, job_number)
    return resources


def _bytes(response):
    parts, size = [], 0
    for part in response.iter_bytes():
        size += len(part)
        if size > MAX_BYTES:
            raise ConnectorError("Original exceeds the connector size limit.")
        parts.append(part)
    return b"".join(parts)


def _download(client, url):
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise ConnectorError("SharePoint original could not be retrieved.")
        return _bytes(response)


SAP_HOSTS = {"sandbox.api.sap.com"}
SAP_SANDBOX_BASE = (
    "https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap/API_SALES_ORDER_SRV"
)


def sap_sandbox_binding(sales_order="1"):
    """A separate connectivity demonstration, never equipment-order evidence."""
    return {
        "key": "sap-sandbox",
        "kind": "erp",
        "adapter": "sap_odata",
        "mode": "live",
        "label": "SAP sandbox · read-only · sample order",
        "base_url": SAP_SANDBOX_BASE,
        "sales_order": str(sales_order),
        "source_url": "https://api.sap.com/api/API_SALES_ORDER_SRV/overview",
    }


def _sap_date(value):
    """OData v2 dates arrive as /Date(ms)/; keep only the calendar day."""
    import re
    from datetime import UTC, datetime

    match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d+)?\)/", str(value or ""))
    if not match:
        return str(value or "")
    return datetime.fromtimestamp(int(match.group(1)) / 1000, UTC).date().isoformat()


def read_sap(binding, job_number, transport=None):
    """Read one administrator-configured SAP sales order through SAP's OData API.

    Read-only GET of an exact order number; the response is rendered into a cited
    text original. Commercial or production facts are only compared when an
    administrator maps them explicitly ("fact_fields"); nothing is inferred.
    """
    import re

    base = binding.get("base_url", "").rstrip("/")
    parsed = urlsplit(base)
    allowed = set(binding.get("allowed_hosts", [])) | SAP_HOSTS
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConnectorError("SAP connector requires an approved HTTPS host.")
    order = str(binding.get("sales_order", ""))
    if not re.fullmatch(r"[0-9A-Za-z]{1,10}", order):
        raise ConnectorError("Configure an exact SAP sales order number.")
    sandbox = parsed.hostname == "sandbox.api.sap.com"
    if sandbox and job_number != "SAP-SANDBOX-" + order:
        raise ConnectorError(
            "SAP sandbox sample data belongs only to SAP-SANDBOX-"
            + order
            + "; it cannot be used as another order's scope."
        )
    if sandbox and binding.get("fact_fields"):
        raise ConnectorError("SAP sandbox sample fields cannot become equipment facts.")
    mapping = binding.get("fact_fields") or {}
    if (
        not isinstance(mapping, dict)
        or len(mapping) > 40
        or any(
            not isinstance(fact, str)
            or not fact
            or len(fact) > 160
            or not isinstance(field, str)
            or not field
            or len(field) > 160
            for fact, field in mapping.items()
        )
    ):
        raise ConnectorError("SAP fact field mapping is invalid.")
    key = os.getenv(binding.get("api_key_env", "RIVET_SAP_API_KEY"), "")
    if not key:
        raise ConnectorError("SAP API key is not configured on the server.")
    url = (
        base
        + "/A_SalesOrder('"
        + quote(order, safe="")
        + "')?$expand=to_Item&$format=json"
    )
    with httpx.Client(
        timeout=20, follow_redirects=False, transport=transport
    ) as client:
        payload = _json(client, url, {"APIKey": key, "Accept": "application/json"})
    head = payload.get("d") if isinstance(payload, dict) else None
    if not isinstance(head, dict) or str(head.get("SalesOrder")) != order:
        raise ConnectorError("SAP returned a different sales order.")
    expanded = head.get("to_Item")
    if not isinstance(expanded, dict) or expanded.get("__next"):
        raise ConnectorError(
            "SAP order items are missing or paginated; the source is incomplete."
        )
    items = expanded.get("results")
    if (
        not isinstance(items, list)
        or len(items) > 200
        or any(not isinstance(item, dict) for item in items)
    ):
        raise ConnectorError("SAP sales order items are invalid.")
    lines = [
        f"SAP S/4HANA sales order {order}",
        f"Source: {binding.get('label', 'SAP')} (read-only OData API_SALES_ORDER_SRV)",
        f"Order type: {head.get('SalesOrderType', '')}",
        f"Sold-to party: {head.get('SoldToParty', '')}",
        f"Customer reference: {head.get('PurchaseOrderByCustomer', '')}",
        f"Order date: {_sap_date(head.get('SalesOrderDate'))}",
        f"Requested delivery: {_sap_date(head.get('RequestedDeliveryDate'))}",
        f"Net value: {head.get('TotalNetAmount', '')} {head.get('TransactionCurrency', '')}",
        f"Overall status: {head.get('OverallSDProcessStatus', '')}",
        f"Delivery status: {head.get('OverallTotalDeliveryStatus', '')}",
        f"Delivery block: {head.get('DeliveryBlockReason', '') or 'none'}",
        "",
        "Items:",
    ]
    if sandbox:
        lines[2:2] = [
            "SAP SANDBOX SAMPLE DATA - connectivity demonstration only.",
            "This is not customer scope, a breaker order, or evidence of engineering approval.",
        ]
    for item in items:
        lines.append(
            "  {} | material {} | {} | qty {} {} | net {} {}".format(
                item.get("SalesOrderItem", ""),
                item.get("Material", ""),
                item.get("SalesOrderItemText", ""),
                item.get("RequestedQuantity", ""),
                item.get("RequestedQuantityUnit", ""),
                item.get("NetAmount", ""),
                item.get("TransactionCurrency", ""),
            )
        )
    data = ("\n".join(lines) + "\n").encode()
    facts = {}
    for fact, field in mapping.items():
        value = head.get(field)
        if isinstance(value, (str, int, float)) and str(value).strip():
            if len(str(value).strip()) > 500:
                raise ConnectorError("SAP fact field exceeds the supported limit.")
            facts[fact] = str(value).strip()
    resource = Resource(
        external_id="sap:sales-order:" + order,
        job_number=job_number,
        name=f"SAP-sales-order-{order}.txt",
        revision=str(_sap_date(head.get("LastChangeDateTime")) or ""),
        lifecycle="unknown" if sandbox else binding.get("lifecycle", "unknown"),
        family="SO-" + order,
        facts=facts,
        source_url=binding.get("source_url", ""),
        content_base64=base64.b64encode(data).decode(),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    _validate([resource], job_number)
    return [resource]


def fetch(binding, job_number):
    try:
        if binding.get("adapter") == "microsoft_graph":
            return read_sharepoint(binding, job_number)
        if binding.get("adapter") == "sap_odata":
            return read_sap(binding, job_number)
        if binding.get("adapter") == "rivet_readonly_v1":
            return read_adapter(binding, job_number)
        raise ConnectorError("Unsupported source adapter.")
    except ConnectorError:
        raise
    except Exception as exc:
        # Provider response bodies, credentials and URLs never enter the UI/log.
        raise ConnectorError(
            "The connected source could not be read or validated."
        ) from exc
