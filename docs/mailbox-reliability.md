# Microsoft 365 mailbox intake

Rivet's mailbox worker discovers incoming mail, saves a durable receipt, retrieves
the original MIME email, and submits it to the same order-intake pipeline used by
forwarded emails. That pipeline creates or matches an order only when the job and
revision evidence is sufficient; otherwise the receipt points to a confirmation
item. Document parsing and Rivet's existing background preparation follow normal
order processing. Intake does not approve work. A separate, opt-in internal-update
outbox can ask the configured PM to confirm an unknown order/revision or notify
them after preparation finishes; it cannot send customer responses.

## Reliability contract

- Reconcile explicitly configured folders every 120 seconds while the worker is
  running. Notifications are not required for correctness. A future verified
  notification may wake polling early, but it must not replace reconciliation.
- Follow every Graph delta page. A ten-page execution budget saves the next page
  and resumes; it never reports a completed sync merely because a page was full.
- Commit the complete page's receipt ledger and continuation cursor together.
  A malformed page or database rollback leaves the previous checkpoint intact.
- Use Graph immutable message IDs and a unique connection/message key. Duplicate
  events and full reconciliation replays do not create duplicate receipt jobs.
- Discover the next email even if one MIME download or attachment fails. Intake
  has its own lease, attempt count, saved original, retry time and visible error.
- A folder tombstone still attempts MIME capture using the immutable message ID:
  the email may have moved elsewhere in the same mailbox. An original already
  saved before a worker interruption continues through intake even after deletion.
- Honor `Retry-After` for throttling; otherwise use bounded exponential backoff.
  Expired Graph cursors trigger a full reconciliation using the original scope.
- Surface missing content, expired permissions, stalled synchronization, exhausted
  retries and mail removed before capture. A folder synchronized successfully can
  still have individual messages needing attention.

This is a coverage-and-recovery contract, not a guarantee that every email can
always be recovered. Graph is eventually consistent. Mail deleted permanently
before capture, moved to an unmonitored folder, archived into another mailbox, or
outside the configured account is not guaranteed recoverable. Configure every
folder into which rules route relevant mail. Subfolders are not included
automatically. Retention rules and permissions belong in pilot acceptance tests.

## Pilot setup

Use a Microsoft 365 work/school test tenant and a dedicated shared pilot mailbox.
This adapter uses application credentials; it does not implement personal
Outlook.com login or delegated OAuth onboarding yet.

1. Register the Microsoft application and authorize read access to the selected
   mailbox. Original bodies and MIME attachments require `Mail.Read`; metadata-only
   `Mail.ReadBasic` is insufficient for this workflow. Sending is not required.
2. Have the tenant administrator apply an Exchange Online application mailbox
   scope. Prefer Application RBAC with the appropriate scoped role. Entra-wide
   grants and Exchange RBAC permissions are additive: a broad Entra `Mail.Read`
   grant is not narrowed merely by adding an RBAC scope. Verify the effective
   scope with the administrator before confidential mail is used.
3. Set server-only secrets in the deployment secret store:

   - `RIVET_MS_TENANT_ID`: the one authorized Microsoft tenant GUID.
   - `RIVET_MS_CLIENT_ID`: application ID.
   - Prefer `RIVET_MS_CERTIFICATE_PATH` and `RIVET_MS_PRIVATE_KEY_PATH`: server-mounted
     PEM certificate and RSA private key (2048 bits or stronger). Upload only the
     public certificate to Entra. Rivet signs a five-minute PS256 client assertion
     with the certificate's SHA-256 thumbprint. Expired or mismatched pairs fail
     closed; they never fall back to a secret. Rotate before the certificate expires.
   - Alternatively, `RIVET_MS_CLIENT_SECRET`: application credential. Never put
     private keys or secrets in a browser bundle, repository, screenshot, log, or
     API request from the workspace. Set private-key file permissions to owner-only.
   - `RIVET_MS_MAILBOX_ALLOWLIST`: JSON binding each Rivet organization to the
     mailbox it may register and its internal update destination.

Example allowlist (illustrative addresses):

```json
{
  "rivet-organization-id": {
    "pilot@manufacturer.example": {
      "update_recipient": "project-manager@manufacturer.example"
    }
  }
}
```

The simpler `{"rivet-organization-id":["pilot@manufacturer.example"]}` form uses
that mailbox as its own update destination. This server-side allowlist is an
additional application check; it does not replace Microsoft's permission scope.
An update address alone does not enable or authorize automated delivery.

4. Run the migration and register the mailbox as a Rivet team administrator:

```http
POST /api/mailboxes
Content-Type: application/json

{
  "microsoft_tenant_id": "11111111-1111-4111-8111-111111111111",
  "mailbox": "pilot@manufacturer.example",
  "folder_ids": ["inbox"]
}
```

Registration is configuration, not proof of connectivity. The first successful
full reconciliation establishes coverage. Existing mail in each selected folder
is included in that initial pass; there is no silent start-date cutoff.

5. Run `backend.mailbox.service.poll_due_mailboxes()` from the durable worker
   schedule. Folder cursors and message leases live in PostgreSQL and survive
   process restarts. Avoid running a polling batch in a user HTTP request.

## Operations and review

- `GET /api/mailboxes`: scope, last completed sync, retry time, failure counts and
  receipt counts. Cursor tokens and secrets are deliberately omitted.
- `GET /api/mailboxes/{id}/receipts`: source metadata, order/intake links, attempts
  and visible failures. The response is tenant scoped and bounded.
- `POST /api/mailboxes/{id}/reconcile`: request an early reconciliation; keep the
  durable cursor and current leases.
- `PATCH /api/mailboxes/{id}` with `{"enabled":false}`: pause future work.
- `POST /api/mailboxes/{id}/receipts/{receipt_id}/retry`: manually retry an
  unsuccessful receipt after investigating its error. Captured MIME is reused.

After eight unsuccessful processing attempts, a receipt needs manual attention;
it is retained, not discarded. A 410 or `syncStateNotFound` restarts folder
reconciliation and reuses the same idempotent receipt ledger. Source emails remain
historical evidence even if Outlook later deletes them. Draft messages are
excluded from intake until Graph reports that they are no longer drafts.

The lookup `find_receipt_by_message_id(session, internet_message_id,
connection_id=None)` supports a reply add-in. It returns no association when the
ID is absent or ambiguous across monitored mailboxes. The caller must not infer a
different tenant from a browser-supplied organization ID.

## Pilot acceptance gate

Automated tests cover skipped notifications recovered by polling, duplicate
events, all-page traversal and resumable budgets, transaction failure before
checkpoint, throttling, expired cursors, unavailable MIME, unknown orders,
tombstones, tenant isolation and unsafe continuation URLs. Those tests use
synthetic mail and a mocked provider; they do not establish live connectivity.

Before claiming a deployed mailbox loop works, test a customer-approved Microsoft
365 mailbox through: real receipt, attachment capture, order match, preparation,
review, output generation and authorized delivery. Also test folder rules,
attachments over the limit, permission revocation, a stopped/restarted worker,
throttling and a receipt needing manual recovery. Independently monitor worker
uptime and stale folder timestamps. Provider acceptance is not delivery proof.
Subscription lifecycle handling and production delivery verification remain
separate from mailbox registration.

## Opt-in internal PM updates

`backend.mailbox.updates.schedule_and_deliver_updates()` is inert unless
`RIVET_MAILBOX_UPDATES_ENABLED=true`. It also requires `RESEND_API_KEY`, a verified
`RIVET_EMAIL_FROM`, an HTTPS `RIVET_WORKSPACE_URL`, and the connection's internal
PM recipient in the server allowlist. There is no browser-supplied recipient and
no customer-reply send operation.

An unmatched email produces one deterministic confirmation notice with its subject,
sender, observed job/revision references and a link to that exact intake item.
It explicitly says the original was saved but not assigned or processed as an
order. Confirming the item before delivery supersedes the unsent notice. This
notice follows the same opt-in, recipient, retry and delivery rules as work updates.
Use a dedicated pilot mailbox or explicitly selected job folder: this connector
does not yet classify unrelated personal mail or silently discard unknown messages.

For a matched order, the scheduler waits for all order documents to finish parsing and the automatic
agent to reach a final or human-review state. It builds the summary from the
saved record's digest and current proposals. Several incoming emails covered by
one automatic pass produce one PM update for that mailbox, evidence and result.
Failed preparation is reported as needing attention, not as completed work.

The outbox freezes the body, recipient and provider idempotency key before a send
attempt. Retries use exactly the same payload and stop for manual review after
23 hours from the first attempt, inside Resend's documented 24-hour idempotency
window. Changed order evidence, changed company guidance, or a changed allowed
recipient prevents an unsent stale update from being sent. An uncertain earlier
attempt is retained for manual investigation.

Rivet's own notifications carry an internal update identifier. If they return to
a monitored mailbox, the worker matches that identifier, saved recipient, sender
and subject to its outbox and records `excluded_internal_update`. This preserves
the original without starting an email/agent feedback loop.

`GET /api/mailboxes/{id}/updates` shows the frozen text and actual processing
state. `accepted_by_provider` means the provider returned an acceptance ID; it
does not assert delivery, opening, customer approval, or engineering approval.
Delivery webhooks and a real recipient-side test are required to demonstrate
the completed external loop.

Additional tests cover default-off behavior, aggregation, internal-recipient
enforcement, stale company guidance, duplicate notifications, interrupted sends,
the expired idempotency window, confirmation notices, and notification-loop prevention.

Provider reference: [Resend idempotency keys and 24-hour retention](https://resend.com/docs/dashboard/emails/idempotency-keys).

## Microsoft references

- [Delta queries for messages are per-folder and paginated](https://learn.microsoft.com/en-us/graph/delta-query-messages).
- [Delta tokens, replays, eventual consistency and synchronization resets](https://learn.microsoft.com/en-us/graph/delta-query-overview).
- [Immutable IDs and their lifetime](https://learn.microsoft.com/en-us/graph/outlook-immutable-id).
- [Graph throttling and Retry-After](https://learn.microsoft.com/en-us/graph/throttling).
- [Retrieve a message and its MIME content](https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0).
- [Application authentication](https://learn.microsoft.com/en-us/graph/auth-v2-service).
- [Exchange Online Application RBAC](https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac).
