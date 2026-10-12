# Inbox-first pilot adoption

Rivet starts with a returned drawing and produces a reviewable comment log and the files a PM already sends. The workspace is the detailed source review, not a prerequisite for the customer to participate.

## Pilot path

1. A Rivet administrator grants the pilot team access. The public marketing site remains a pilot-request site.
2. The PM tries a sample or uploads a marked-up PDF / saved `.eml` email. Keep sample data explicitly labelled. Do not require confidential customer files to see the workflow.
3. Intake uses supplied job identifiers and asks the PM to confirm unresolved matches. A missing job number must not silently attach a drawing to an unrelated order.
4. Rivet extracts the comments it can read and preserves the original document, annotation/source location, and any stated author or timestamp. Unreadable or ambiguous content requires review.
5. The comment log highlights revision review cues: new/repeated comments, previously open items, material recorded after an approved record, and explicit possible scope-change wording. These are not findings of engineering noncompliance, proven missed drafting work, or contractual change-order entitlement.
6. The PM checks the source, accepts or edits prepared work, records responses and hands off drafting work.
7. The PM downloads the comment spreadsheet, response PDF, drafting list, or email review brief. They select recipients and send from their own email application. Existing Rivet send-notice functionality still requires a separate explicit human send action.
8. The next returned revision is added to the same order. A comment being absent from a later PDF does not prove the underlying drawing was corrected.

The “under five minutes” onboarding and “80% in the inbox” statements are pilot targets, not measured product claims. Time from upload to export and time spent correcting extraction should be measured on redacted real orders before publishing those promises.

## Implemented delivery surface

- `GET /api/orders/{id}/record/digest` builds a current review brief from the tenant-scoped record and revision-review projection.
- `GET /api/orders/{id}/record/digest.eml?expected_version=...` downloads the reviewed version as a multipart plain-text/HTML draft. A changed record returns a conflict so the PM can review the refreshed brief first.
- The message has no `To`, `Cc`, `Bcc`, or `From` header. Downloading creates no delivery, approval, or new record version. It does not call Resend.
- Counts separate comments from flags: one comment may have more than one review flag. Current-revision comment counts do not sum all historical revisions.
- Comment links use the order ID and a validated comment ID from that order. Every link remains subject to team authentication. No public share token is minted.
- Hosted brief links require `RIVET_WORKSPACE_URL`. Set this to the private pilot workspace's exact HTTPS origin, not the marketing-only deployment. Only local development can fall back to a configured allowed origin and use loopback HTTP. If missing/invalid, the brief omits links rather than inventing a public destination. A CORS-allowed marketing origin is not assumed to host an accessible workspace.
- HTML escapes all document-derived content. Subject newlines are removed. The record source is treated as evidence, never as instructions to send messages or change order values.
- The in-app preview identifies the brief as automatically prepared and a draft. It uses text rendering, not arbitrary HTML injection.

## Forwarding that exists today

The Resend integration provides a **unique address per team**, created only after the receiving domain is configured and provider receiving access is checked. Forwarded mail enters the same matching queue as uploads. Exact job/revision matches can import automatically; uncertain matches wait for confirmation. Existing per-order addresses remain supported. An address in a mockup such as `orders@rivet-usa.com` is not a provisioned shared team mailbox.

Before using forwarding in a pilot:

1. Configure the production API, tenant authentication, persistent storage, database, and worker.
2. Configure a receiving domain and the provider DNS records. Store `RESEND_API_KEY` and `RESEND_WEBHOOK_SECRET` on the server, never in the web bundle.
3. Configure the signed Resend `email.received` webhook. Creating an address does not prove DNS or webhook delivery is functioning.
4. Enable the team forwarding address from the Inbox. Only an enabled saved address is displayed. A team administrator can pause forwarding while retaining saved emails. Per-order forwarding remains available in Documents.
5. Forward a nonconfidential test email with a PDF, verify that the worker imports it once, and inspect its source and attachment mapping. Retry the event to verify deduplication.
6. Confirm rejection of an invalid signature and that an alias belonging to another team cannot route content into the current team.

Local “Check inbox” is a manual fallback using the latest 100 provider messages. It is not a complete historical mailbox sync. The UI states this limit. The setup screen reports **configured** separately from verified delivery.

## Integration truth table

| Tool                  | Current pilot capability                                    | What is not connected                                                         |
| --------------------- | ----------------------------------------------------------- | ----------------------------------------------------------------------------- |
| PDF / Bluebeam return | File upload and available PDF annotation/text extraction    | Visual engineering validation or guaranteed handwritten-text extraction       |
| Email                 | Saved EML import; team and per-order forwarding after setup | Outlook mailbox discovery and continuous Graph sync                           |
| Excel                 | Downloadable `.xlsx` comment log                            | Live workbook synchronization or editing a customer's workbook                |
| Outlook               | Manual forward or saved email import; downloadable draft    | OAuth mailbox adapter, continuous sync, automatic reply handling              |
| SharePoint            | Upload a downloaded source file                             | Drive browsing, subscriptions, version tracking, writing files back           |
| SAP                   | User-supplied order/job identifiers                         | Reading orders, changing scope, pricing, production or commercial commitments |

`GET /api/integrations/adoption` supplies these statuses to Getting started and Workspace settings. It returns the saved enabled team address and per-order address counts for the current team, not secret keys or invented addresses. Existing environment values alone never make Outlook, SharePoint, or SAP appear connected. `GET /api/integrations/team-inbox` shows the real alias, configuration state and recent import results. Administrator-only POST actions enable, check or pause the address.

## Later Outlook connector

This is an implementation plan, not a live adapter:

- Register an Entra application and add a consent flow tied to the Rivet organization and the explicit mailbox selected by its administrator. Use encrypted credentials and a disconnect/revoke path.
- For a shared/delegated mailbox read flow, Microsoft documents delegated `Mail.Read.Shared`. That permission does **not** support subscriptions on shared/delegated folders. Shared-mailbox change notifications require the appropriate application permission, such as `Mail.Read`, plus tenant-admin setup and a mailbox access restriction. Do not request `Mail.Send` to implement read-only intake. [Microsoft shared-mailbox guidance](https://learn.microsoft.com/en-us/graph/outlook-share-messages-folders).
- Persist subscription lifecycle, renewal, replay/deduplication, retry state, and reconciliation so a missed webhook cannot silently lose a revision. Microsoft explicitly documents lifecycle notifications and renewal requirements. [Outlook change notifications](https://learn.microsoft.com/en-us/graph/outlook-change-notifications-overview).
- Define which mailbox/folder is watched and expose the last successful sync, pending/failed imports, and a retry. Do not claim “connected” until consent and a real read check succeed.
- Stage correspondence and attachments in a tenant-scoped intake queue. Treat job-number, PO, drawing-number, and thread matches as independent evidence; require a person when they disagree. Keep provider IDs for idempotency and the original evidence for audit.

## Later SharePoint and SAP adapters

Use customer-selected locations for SharePoint and read access first. Microsoft Selected permissions require both consent and explicit resource assignment; a token alone does not grant access to all selected resources. [Selected permissions overview](https://learn.microsoft.com/en-us/graph/permissions-selected-overview).

SAP scope must be defined with the customer's administrator for their specific product/version and order workflow. Begin with a read-only order reference and a stable external ID. Do not simulate a generic SAP connection, or give the agent a tool that can release production or commit a commercial change.

## Release checks

The delivery tests cover current-revision counts, duplicate flags on one comment, escaping hostile markup, header-newline isolation, foreign-reference rejection, tenant-scoped reads, no-send behavior, configured-origin links, and honest integration states. A real pilot must additionally validate provider DNS/delivery and the end-to-end worker, which unit tests and an API-key presence check cannot establish.
