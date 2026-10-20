# Connected Outlook workflow

## What is real and what is simulated

The installed Outlook add-in and Microsoft mailbox capture are real. Rivet's API,
worker, parsing, source retrieval, comparisons, citations, AI generation and native
Outlook draft/attachment handoff use the actual application path.

The reproducible starter uses three independent read-only HTTP services on
loopback, backed by editable fictional originals. In the configured Microsoft 365
demo, the specification connector now reads an actual PDF from the private
**Rivet Integration Demo** SharePoint site through Microsoft Graph. The ERP and
drawing vault remain local services; they are not SAP or Autodesk Vault connections.
The UI identifies each source as a live connection or local demo.

An optional [SAP sandbox connectivity check](sap-sandbox.md) reads an actual
generic sales order from SAP's API. That sample is isolated as `SAP-SANDBOX-1`;
it does not replace the fictional breaker order's local ERP scope. Only call
this optional connection live after its verification command succeeds.

The live SharePoint setup was verified on October 12, 2026. Rivet has application
`Sites.Selected` consent and a `read` grant on the demo site only. Reading the
tenant root site returns HTTP 403. The uploaded fictional PDF was retrieved
byte-for-byte through Rivet's actual connector, including its revision, lifecycle
and equipment metadata. Graph Explorer was used temporarily to assign the site
grant; its consent was revoked immediately afterward.

No CAD editing, ERP writes, customer sending or engineering approval happens here.

## Run locally

From the repository root, with the existing database, API, worker, authenticated
workspace and HTTPS Outlook add-in configured as in `outlook-addin.md`:

1. Generate sources bound to your Rivet organization and exact job number:

   ```sh
   uv run --with reportlab python scripts/build_connected_demo.py \
     --organization-id YOUR_RIVET_ORGANIZATION_UUID \
     --job DEMO-OUTLOOK-001 --output .data/connected-demo
   ```

   This writes fictional files, `catalog.json` and `connections.json`. It does not
   edit Rivet's database. Regeneration replaces those demo files; use a different
   output directory to preserve custom scenarios.

2. Set `RIVET_CONNECTED_CONTEXT_CONFIG` in the server `.env` to the absolute path
   of `.data/connected-demo/connections.json`. Never expose it through a Vite env
   variable. Run `uv run alembic upgrade head` (includes migration 0017).

3. Run the independent sources:

   ```sh
   uv run python scripts/start_connected_demo.py
   ```

4. Restart the API and worker so they load the configuration. Keep the HTTPS
   frontend running. Existing configured orders are picked up automatically;
   newly captured mail triggers retrieval once its order match is confirmed.

5. Open the fictional returned email in Outlook and choose **Reply with Rivet**.
   The demo configuration enables `auto_prepare_reply`: after capture and parsing,
   the worker prepares a response for the latest matched email on this order.
   Opening the pane recovers the current prepared response; you can also select
   **Prepare reply** or **Regenerate** on demand. Inspect conflicts and evidence,
   choose the released drawing PDF,
   review the response, then open the unsent reply in Outlook. No Send action is
   automated. The detailed order link opens the same cited originals in Rivet.

## The demonstration

The customer asks about CB-12 on E-10 Rev C, a missing red-marked item, and production
release. The accepted ERP scope records TU-2500-DEMO and excludes remote indication.
The approved specification requires TU-3000-DEMO and remote indication. Released
drawing B shows TU-2500-DEMO. Drawing C shows TU-3000-DEMO but is a draft. The ERP
production state is HOLD pending engineering reconciliation.

The reply should explain the conflicting trip-unit records, ask for the missing
markup location, and state that the records do not establish production release.
The attachment chooser permits released drawing B, blocks vault draft C, and keeps
internal ERP scope out of customer attachments. Drawing release and production
release are separate facts. All product identifiers and records are fictional.

## Architecture and boundaries

- Source bindings are server-controlled and scoped to an exact Rivet organization
  and job. A model cannot choose arbitrary URLs or another company's documents.
- Each source returns its original bytes, hash, identity, business revision,
  lifecycle and optional normalized facts. Originals are parsed into Rivet's
  evidence store with provenance. Repeated retrieval does not duplicate files.
- Mapped values are compared deterministically. This demonstrates reconciliation
  of explicitly supplied fields, not universal engineering understanding or
  automatic extraction of every drawing attribute.
- Old versions are retained for history but retired from new reply context.
  Snapshot hashes cover source content, metadata and configuration. A changed,
  stale or unavailable source prevents approval of a reply based on old context.
- Connected sources are checked after intake, periodically (120 seconds), and
  immediately before a requested reply. Snapshots older than five minutes are
  stale. A source outage is visible and generation pauses.
- Background replies are explicitly enabled per job by `auto_prepare_reply`.
  They use a durable, duplicate-safe job keyed to the captured message and evidence
  state. The worker prepares the latest actionable captured message per thread within the recent-mail window. With Autopilot and scoped mailbox write access enabled, it places an unsent native reply automatically. The PM edits and sends in Outlook; Rivet never sends.
- Autopilot recommends only cited, released drawing-vault originals. Additional supporting attachments can be selected by the PM. Current file hashes and access
  are rechecked before Outlook handoff. Rivet does not decide who receives an
  internal document or send the email itself.
- The current selected-source limit is 30 PDF/TXT resources per connector,
  12 MB per original and 24 MB per adapter JSON response. This is a local pilot
  implementation, not a whole-company crawl.

## Reproduce other scenarios

Edit the fictional originals and their matching `facts` in `catalog.json` to test
different scope values. The service computes content hashes on retrieval. Change
a lifecycle from draft to released only when intentionally testing that state.
Remove a file to test unavailable-source handling. Use another organization or
job binding to test isolation; unbound jobs receive no connected source records.
Restore files after outage tests. Never point these unauthenticated loopback demo
services at real confidential originals or expose their ports publicly.

## Configure a real SharePoint resource

Use adapter `microsoft_graph`, kind `sharepoint`, mode `live`, an exact authorized
tenant ID, and a list of selected drive/item IDs in the same server-side binding.
Each item maps `revision_field`, `lifecycle_field`, `lifecycle_map` and `family`.
Optional `fact_fields` maps a comparison key (for example `CB-12.trip_unit`) to
the internal name of a SharePoint column (for example `TripUnit`). Values come
from that item's actual column data. Empty or absent columns do not create facts;
multi-value objects are rejected. PDF text remains available as cited evidence
whether or not structured columns are configured.
Configure explicit `download_hosts` for the tenant's Graph download redirect.
The existing server Microsoft certificate credential is reused; it does not grant
SharePoint access by itself. Have the tenant administrator grant the narrow
selected-site/resource access required for this pilot first. This change requires
separate authorization and is not performed by the demo setup script.

For the existing demo, the specification is stored at
`https://rivetusa.sharepoint.com/sites/RivetIntegrationDemo`. Its library columns
are `EngineeringRevision`, `ReviewStatus`, `TripUnit`, and `RemoteIndication`.
These values were entered from the fictional source; this does not demonstrate
automatic extraction of structured equipment facts from arbitrary drawings.
The PDF text is parsed separately for source-grounded reply generation.
The ignored local `connections.json` contains the actual drive/item binding.
Do not regenerate into that directory unless you intend to replace the live
binding with the starter's local specification service.

The reader checks the item eTag before and after download, refuses unexpected
download hosts, and never forwards the Graph bearer token to a download URL.
An unmapped business lifecycle stays unknown; Microsoft file version numbers do
not establish engineering approval. ERP/vault vendor adapters can implement the
read-only protocol once the actual products and authorized test systems are known.

References: [Microsoft Graph file downloads](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0),
[selected SharePoint permissions](https://learn.microsoft.com/en-us/graph/permissions-selected-overview).

## Verification

Local verification on October 12, 2026: the existing authorized fictional email
in the real Outlook mailbox was matched to `DEMO-OUTLOOK-001`. A background
`gpt-6-luna` reply cited email, ERP, specification-library and vault evidence.
Opening the add-in recovered that prepared reply without another generation
request. After review, the native unsent Outlook reply contained released drawing
B and specification revision 2 as PDF attachments. No email was sent. Repeated
source checks retained one automatic draft for the unchanged evidence state.

156 backend tests and 14 frontend checks passed, as did the Outlook, private
workspace and marketing builds. Screenshots and the saved response are in the
ignored `.data/connected-demo` directory. The development Clerk session may need
sign-in again when the Outlook pane is closed/reopened; leave the authenticated
pane open while filming. Production add-in SSO remains a separate rollout task.

After replacing the local specification binding with real SharePoint, 49 focused
connected-context, attachment and Microsoft certificate tests passed. The worker
automatically prepared a fresh reply with 13 citations and three connected-source
conflicts. The Outlook pane recovered it without a manual generation request.
Following PM review, the native unsent reply contained the actual downloaded
`Rivet-Demo-Specification-26-24-13-Rev-2.pdf` and released drawing B; internal ERP
scope and draft drawing C remained unavailable as customer attachments. The
SharePoint source link also opened the retrieved PDF in the order workspace.
No new email was sent during this check.

Live proof is saved as `outlook-live-sharepoint-reply.png`,
`sharepoint-live-library.png`, `graph-access-revoked.png`, and
`live-sharepoint-response.json` in that ignored directory. The saved response
reports partial context retrieval (40 of 55 excerpts, with all four recorded
comments); it must not be represented as an exhaustive engineering review.

`tests/test_connected_context.py` exercises tenant/job isolation, automatic and
confirmed intake, duplicate delivery, source changes, source outages, stale drafts,
released/draft attachment handling, configuration removal, invalid source hashes,
redirect rejection, and the Graph item's lifecycle/download/etag boundaries.
The existing reply, mailbox and agent suites cover the surrounding application.
