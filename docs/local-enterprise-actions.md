# Local enterprise action execution

This is an executable integration harness, not a production SAP/SharePoint/vault/
Teams connection. It makes real HTTP requests to a loopback service, persists
enterprise records in SQLite, writes the actual released file bytes to a local
object store, and returns verifiable receipts. Every response is labelled
`local_http_demo`. It cannot connect to a non-loopback URL or execute a
non-synthetic order. It never sends customer mail.

## What runs

| Approved package | Local HTTP effects | Meaning and boundaries |
| --- | --- | --- |
| Kickoff | SAP draft sales order; SharePoint job folder | Exact approved PO lines. No production acceptance or billing. |
| Returned revision | SAP proposed commercial change | Preserves the proposed change separately. It does not infer customer price acceptance or post billing. |
| Purchasing | Buyer handoff in a local Teams channel; internal material milestone | The exact money-gate actor must be a named configured buyer. All package gates must be approved. The date is earliest material availability; production, test, transit and a customer ship-date commitment are not inferred. |
| Release | Publish exact approved bytes to local SharePoint and vault; update local SAP production status | Current customer/engineering signoff and the completed revision proof are checked. SAP release waits for matching SharePoint and vault manifests. The real Rivet Order is not marked production released by this simulator. |

These are eight effects across four approved stages. The adapter boundary is a
typed `Command` plus approved source, package and file hashes. A future actual
vendor adapter must translate that intent to the customer's authorized APIs and
return equivalent reconciliation evidence. Pointing a configuration variable at
a real vendor is deliberately unsupported.

## Approval-to-worker flow

Automatic execution defaults off. When `RIVET_LOCAL_ACTIONS_AUTOMATIC=true`, the
organization is explicitly allowlisted and the service is configured, the final
human gate appends a durable `workflow_actions` job. No HTTP write happens in
the approval request. The ordinary worker checks the exact package and evidence
again, then executes it. It never receives an approval tool or signs a decision.

The executor checks the real authenticated team-administrator role for this
isolated harness, the source's physical bytes, all immutable artifact hashes,
the exact approvals and their source snapshots, the package fingerprint and its
presence in an OrderRevision with a matching checksum. Execution attempts and
receipts are separate append-only OrderEvents; they do not modify signed
Order.state, Order.version or the snapshot checksum.

The worker checks its lease before delivery. A partial execution retains the
effects already applied and retries after ten seconds, up to three worker
attempts. An exhausted job needs intervention. A source change or revoked
configuration prevents further execution. An explicit endpoint can retry the
approved intent after the connection is repaired; it cannot grant approval.

## Idempotency and failure recovery

Every action has a stable identity derived from organization, order, package,
fingerprint and action type. The remote store commits its resource change and
receipt in the same SQLite transaction. A lost response is reconciled by querying
that identity before retrying. Repeating an execution, even after the Rivet
transaction or process was lost, reuses the existing receipt and does not
duplicate an ERP proposal or channel post. A different intent at the same
identity is rejected. Remote resource versions prevent blind overwrites.

Publication writes the exact approved bytes under their SHA-256; the receipt
records file identity, hash and byte count. A missing, modified or unreleased
source blocks publication. Partial publication is explicit, rather than being
reported as a successful distributed transaction.

## Reproduce the whole proof

Use a dedicated existing `rivet_test_*` PostgreSQL database and an isolated data
directory. The command refuses a live database and requires an explicit
fictional-human-decision flag. It creates a normal realistic local order,
performs the eleven-comment completion scenario, and lets the ordinary worker
consume the approval outbox. It starts an ephemeral private loopback server,
generates its token in memory and stops that server when done. SQLite state,
published files and the JSON proof remain on disk.

```sh
RIVET_DATABASE_URL=postgresql+psycopg://localhost:55432/rivet_test_actions_demo \
RIVET_AUTH_MODE=local RIVET_LOCAL_ONLY=true \
RIVET_DATA_DIR="$PWD/.data/local-http-proof" \
.venv/bin/python -m backend.workflow_actions.replay \
  --simulate-human-decisions \
  --server-database .data/local-http-proof/enterprise.sqlite \
  --output .data/demo-readiness/local-http-actions-replay.json
```

PDF fixture authoring requires ReportLab and pypdf in the selected runtime. An
existing `RIVET_PDF_PYTHON` may select the bundled artifact Python when necessary.
The replay does not mutate the live API/worker environment or a mailbox.

## Run a persistent local simulator

The standalone server binds only `127.0.0.1`. Set a private token of at least
24 characters and `RIVET_LOCAL_ACTIONS_ORGANIZATIONS` to an explicit comma-separated
UUID allowlist before running:

```sh
.venv/bin/python -m backend.workflow_actions.local_server \
  --database .data/local-enterprise/enterprise.sqlite --port 5188
```

Configure the Rivet API/worker with these server-owned variables:

- `RIVET_LOCAL_ACTIONS_URL`: the exact `http://127.0.0.1:5188` origin.
- `RIVET_LOCAL_ACTIONS_TOKEN`: the private simulator token, kept out of chat/logs.
- `RIVET_LOCAL_ACTIONS_ORGANIZATIONS`: explicit organization UUIDs.
- `RIVET_LOCAL_ACTIONS_BUYERS`: JSON object mapping those UUIDs to lists of exact
  authenticated buyer IDs. A display name or a generic writer role is not enough.
- `RIVET_LOCAL_ACTIONS_AUTOMATIC`: optional `true`; default `false`.

Rivet routes are tenant scoped:

- `GET /api/orders/{id}/workflow-actions`: operational attempt history.
- `POST /api/orders/{id}/workflow-actions/{package_id}/execute`: explicit retry
  with `expected_version` and a recorded `reason`; administrator only.

The local server requires its private bearer token for `/v1/state/...` and
`/v1/actions/...`. Its unauthenticated health check only reports that this is a
local simulator, never that a vendor connection exists. Fault injection is
available only to tests through the server object's state, not through an HTTP
administration endpoint.

## Still needed for real integrations

- **SAP:** a company test system, the correct sales-order/change/release APIs,
  scoped write credentials/communication arrangement, actual material/price
  mappings, company approval roles and post-write reconciliation. The public
  read-only SAP sandbox proves connectivity, not any of these write grants.
- **SharePoint:** specifically authorized selected site/library create/publish
  permissions, version/retention rules and mapping to the real job folders. The
  current selected-site read permission cannot create folders or publish files.
- **Drawing vault:** the actual PDM/CAD product, authorized test repository,
  checkout/check-in semantics, exact revision/release rules and engineering
  signatory identities.
- **Teams:** an approved app/workflow endpoint in a specific internal channel,
  scoped send permission and durable delivery reconciliation. A local channel
  row is not a delivered Teams message.
- **Schedule:** the company's authoritative scheduling system and approved
  production/test/transit inputs. A six-week supplier lead time alone is not a
  promised customer delivery date.

No customer automatic sending or Mail.Send permission is added by this harness.
