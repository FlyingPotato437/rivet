# SAP sandbox connectivity proof

This optional check calls SAP's actual Business Accelerator Hub sandbox. It is
separate from the switchgear scenario: SAP's generic sales order `1` is not the
accepted scope for `DEMO-OUTLOOK-001`. The main demo continues to use the clearly
labelled local ERP and vault alongside live Outlook and SharePoint.

The connector is implemented and covered by deterministic transport tests. A
successful current run of the verification command below is required before
calling the SAP connection live. Having the code or an API key is not proof that
the API call worked.

## Current verification and product integration

The October 15 verification succeeded against SAP's real sandbox sales order
`1`, including its line items. The authorized team's separate `SAP-SANDBOX-1`
workspace order now stores that retrieved source, its timestamp and SHA-256.
The main breaker demo's ERP/vault mappings remain local and separate.

Workspace Settings now shows SAP environment, check status, retrieved document
count and an evidence link. **Check connection** enqueues the same durable source
refresh used by the worker; it does not create a parallel or write-enabled API
path. `GET /api/integrations/sap` returns this scoped status and
`POST /api/integrations/sap/check` accepts an authorized `order_id`. Both require
an administrator. The general integration summary reflects saved SAP/SharePoint
checks instead of hardcoded “not connected” labels.

## Get the sandbox key

1. Open the official [Sales Order API page](https://api.sap.com/api/API_SALES_ORDER_SRV/overview)
   and sign in with a SAP Universal ID. Register if needed; this does not require
   provisioning an enterprise S/4HANA tenant for the sandbox test.
2. Open **Try Out**, choose **Sales Order Header**, and select the GET operation
   for one sales order. SAP's tutorial uses sales order `1`.
3. Run it with environment **Sandbox**. Confirm that the response is successful.
4. Choose **Show API Key** in the API banner, or find it under the signed-in
   account's **Settings**. Store the key as server-only `RIVET_SAP_API_KEY` in the
   ignored root `.env`, or in the process environment. Do not use a `VITE_` key.

The key is sent in the `APIKey` header. It is never added to a URL, a document,
the browser bundle, or the saved verification report. This adapter only uses
GET, and does not follow redirects with the credential.

## Verify without changing the running demo

From the repository root:

```sh
.venv/bin/python scripts/verify_sap_sandbox.py
```

The command reads one exact order and its expanded items from
`https://sandbox.api.sap.com/s4hanacloud/sap/opu/odata/sap/API_SALES_ORDER_SRV`,
then saves the retrieved text and a timestamped, hashed verification report in
`.data/sap-sandbox/`. No database, existing source binding, Outlook message, or
SAP record is changed. A nonzero exit or `not_verified` result means the live
connection has not been demonstrated.

The saved text explicitly says **SAP sandbox sample data**. It is useful as
additional API-connectivity proof, rather than as a source in the breaker
conflict. Show it separately and say: "This is a real read-only SAP sandbox call;
the breaker scenario uses our local ERP adapter. A customer's ERP requires
authorized access and an exact order mapping."

## Optional separate workspace order

To save a standalone configuration example for an authorized organization:

```sh
.venv/bin/python scripts/verify_sap_sandbox.py \
  --organization-id YOUR_RIVET_ORGANIZATION_UUID
```

The script writes `standalone-binding.example.json` alongside the proof. It does
not activate or merge it. The only permitted workspace job number for this
sandbox sample is `SAP-SANDBOX-1`; another sample number uses the corresponding
`SAP-SANDBOX-<number>` name. If adding the example to an existing server config,
merge only that job entry under the correct organization. Do not replace the
live `DEMO-OUTLOOK-001` connectors or its SharePoint binding.

Keep automatic customer replies off for this connectivity-only order. The
connector refuses to attach sandbox resources to any other job and refuses
equipment fact mappings on sandbox data. Its lifecycle remains `unknown`; SAP
sales status codes do not establish engineering approval or production release.

For a real customer SAP instance, this API-key sandbox adapter is not a complete
production authentication solution. Configure the customer's authorized SAP
communication arrangement, credentials, exact order mapping, business field
mapping, and lifecycle interpretation as a separate deployment step. Never use
sandbox status or material codes as customer approval facts.

## Verification and official references

The pure adapter tests do not need a database or live API key:

```sh
.venv/bin/python -m pytest --noconftest tests/test_sap_connector.py -q
```

They cover GET-only access, credential location, exact-order validation,
deterministic source hashes, host restrictions, redirects, missing and paginated
items, malformed mappings, and separation of SAP samples from customer orders.

- [SAP: trying APIs in the sandbox](https://help.sap.com/docs/SAP_API_BUSINESS_HUB/e56a6c50d31541ea826021dc8e721a53/6a86b2433feb43899a9639d080dba752.html)
- [SAP: Sales Order sandbox example and APIKey header](https://learning.sap.com/courses/developing-with-sap-build-process-automation/encapsulating-the-apis-of-remote-systems-using-actions-projects_d102638b-94d7-4c2e-9547-bb344e9aa9de)
- [SAP: local API calls](https://developers.sap.com/tutorials/hcp-abh-test-locally?from=explore-sap-apis-in-the-business-accelerator-hub)
