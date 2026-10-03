# Accounts and email

## Current setup

Rivet uses Clerk's React SDK and Python backend SDK. The development app is linked to the user-selected Rivet application. The CLI stores its keys in ignored `apps/web/.env.local`; the API loads that file as well as root `.env`. Runtime environment variables take precedence. `/api/auth/config` returns the publishable key, auth mode, and (when explicitly enabled locally) public demo identifiers. It never returns a secret key.

Open `http://127.0.0.1:5178/#orders`, sign up or sign in, then create or select a team. The sidebar has a team switcher and account menu. **Workspace settings → Manage team** opens Clerk's membership and invitation controls. The original local sample workspace is preserved separately; it is never silently assigned to the first account that signs up.

Clerk's development instance is suitable for local testing. Production keys, hosting, domain verification, database/storage deployment, and a public email webhook have not been configured.

## Access rules

- Workspace APIs require a verified Clerk session with an active organization. The issuer and authorized origin must match Rivet. Pending sessions and missing organization membership are rejected.
- Clerk organization IDs map to stable internal tenant UUIDs. Queries, inserts, exports, original files, AI context, and idempotency keys use this verified tenant. Request headers or payloads cannot select a different tenant.
- Members and administrators can work on records and explicitly send reviewed notices. Administrators configure integrations and forwarding addresses. Other roles are read-only until a permission mapping is explicitly added.
- Audit writes use the verified user ID. Source authors, response authors, and historical approver names remain separately recorded facts.
- Workers claim jobs across teams, then establish the job's tenant context before reading or writing records. Browser query caches are replaced when users or teams change.
- Public share links are exceptions to sign-in, authorizing only the frozen snapshot and its included originals. Revocation and expiry still apply.
- With no Clerk keys, development mode is available only on loopback. `RIVET_LOCAL_ONLY=false` cannot enable unauthenticated shared access. Keep the default local boundary until deployment is configured.

## Resend credentials

Set server-only values in root `.env` or deployment secrets; never commit them:

| Variable | Purpose |
| --- | --- |
| `RESEND_API_KEY` | Sending and retrieval. Forwarding requires **Full access**, not a send-only key. |
| `RESEND_RECEIVING_DOMAIN` | Receiving-enabled custom subdomain or the managed receiving domain shown by Resend. Do not include `@`. |
| `RIVET_EMAIL_FROM` | Sender on a verified sending domain, such as `Rivet <notices@example.com>`. |
| `RESEND_WEBHOOK_SECRET` | Signing secret from the webhook configured below. |

Restart API and worker after environment changes. **Workspace settings → Check receiving access** makes a read-only permission check; it does not send mail. A saved API key alone is not presented as a working inbox.

### Local receiving

1. Configure a Full access key and the receiving domain.
2. Open an order's **Documents** tab and create its forwarding address.
3. Forward an email to that address.
4. Select **Check inbox**. This deliberately checks the latest 100 received messages; the UI states that limit. Use webhook delivery for continuous intake.

The app downloads the original MIME email through Resend, validates its envelope recipient against the order address, retains the original, and queues supported attachments for parsing. Messages are deduplicated by provider ID and original bytes. Imported comments remain unreviewed. Unsupported forwarded attachments remain in the original email and are listed in its metadata. The existing 20 MB and 20 attachment limits apply.

### Hosted receiving

After a public HTTPS API is deployed, create a Resend webhook for `email.received` pointing to `/api/webhooks/resend`, then save its signing secret. The handler verifies the raw-body signature and timestamp before routing. Unmatched or ambiguous recipients do not enter an order. A duplicate event does not duplicate documents or work. Localhost cannot receive public webhooks directly; no tunnel is provisioned by this setup.

### Sending

Once a verified sender is configured, open **Approved record**, expand a change notice, and choose **Review & send**. The confirmation shows the exact body and recipient snapshot. It sends no attachments and grants no workspace access to recipients. Each explicit send creates a durable job and delivery record. Duplicate clicks reuse the same delivery. Per-recipient Resend idempotency keys protect retries; attempts older than 23 hours stop for manual review rather than risking another send after Resend's idempotency window.

“Sent” means Resend accepted the email. It does not mean the recipient received or read it. Automatic sending and delivery/open tracking are not enabled. No actual messages are sent by setup or automated tests.

## Verification

`tests/test_auth_email.py` exercises real RS256 verification through Clerk's Python SDK, expired/tampered/wrong-issuer sessions, organization isolation, document downloads, job tenancy, actor spoofing, integration roles, webhook signatures, duplicate intake, and explicit duplicate-safe notice sending. Provider transport is mocked in automated tests. Full live forwarding requires the receiving credentials and domain above.

References: [Clerk React](https://clerk.com/docs/react/getting-started/quickstart), [Clerk Python authentication](https://clerk.com/articles/how-to-add-authentication-to-a-python-backend), [Resend receiving](https://resend.com/docs/dashboard/receiving/introduction), [webhook verification](https://resend.com/docs/webhooks/verify-webhooks-requests).

## Public-document demo

Run `uv run --with pypdf python scripts/prepare_public_documents.py`, then `uv run python -m scripts.setup_demo` while the worker is running. Restart Rivet and select **Try the demo** from the sign-in page. This signs into a real Clerk development user using the reserved test-email flow and activates its separate organization. No password, inbox access, API auth bypass, or public session-minting endpoint is needed.

The setup is repeatable: it reuses the designated demo identity and imports missing public excerpts using the same document service and worker as an ordinary upload. It does not copy another team's records, invent approvals, or reset edits. The `.env` demo flags and IDs stay ignored. Disable the button by setting `RIVET_DEMO_ENABLED=false`. The button is also disabled server-side when the local boundary is removed or either Clerk key is a production key. This shared development identity is for public practice documents only. Use a personal sign-in and a separate team for customer files.

The supported test identity is `rivet-demo+clerk_test@example.com`; Clerk's development-only email code is `424242`. The one-click button completes this flow automatically. See [Clerk test emails](https://clerk.com/docs/guides/development/testing/test-emails-and-phones).
