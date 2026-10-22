# Reply with Rivet in Outlook

The Outlook add-in is a small review surface for the selected message. It is separate from the mailbox synchronization service. A running task pane does **not** mean background inbox monitoring is connected.

## Implemented flow

1. The background worker captures relevant mail, matches its order, reads attachments and connected evidence, and prepares a cited response. Opening the pane is not needed to start this work.
2. With Autopilot enabled and scoped Microsoft mailbox write access, Rivet places the response and eligible released drawings in an **unsent native Outlook reply**, preserving the original thread and subject. The PM edits and sends in Outlook.
3. **Reply with Rivet** remains an optional inspection surface. It identifies the selected message and shows preparation, saved draft status, citations, conflicts and supporting files. For ambiguous order matches it offers a concise Yes/No choice; missing or conflicting references still need an explicit answer.
4. If a native draft already exists, the pane opens that exact item. It does not insert another copy or maintain a second editable version. Message identity and saved placement are rechecked immediately before opening.
5. If native automatic placement is unavailable, the pane provides an explicit manual fallback. **Insert draft in Outlook** saves the review and revalidates evidence and selected files before calling Office.js. It never sends. Existing native or uncertain placements block duplicate insertion on both client and server.
6. Only cited, released drawing-vault files are recommended automatically. Other documents remain an explicit PM choice. Internal ERP material and unreleased drawings are not automatically attached to customer replies.
7. Autopilot records whether its draft was sent unchanged, edited, or removed. Current, cited sent replies can become examples for the **same order** by team policy; stale evidence and broader customer/company guidance require review. This is saved-context retrieval, not shared model training.

There is no customer-send API in the task pane or Autopilot. A reply is not proof of engineering approval or production release. Background capture, worker availability, Microsoft permission scope and connected-source configuration remain separate prerequisites.

## Local preview

Start the normal Rivet API and web development server, then open:

`http://127.0.0.1:5178/outlook/taskpane.html`

The standalone browser explicitly says **Preview · Outlook not connected**. It allows selecting an existing order and calls the same generation and review endpoints. It cannot access a user's inbox or open a threaded Outlook reply. Authentication and tenant scoping use the same `Authentication`, `WorkspaceAccess`, and bearer-token wrapper as the workspace; there is no add-in auth bypass or secret in the manifest.

## Build isolation

From the repository root:

```sh
npm run build
npm run build:workspace
npm run build:outlook
```

These produce the marketing-only `apps/web/dist`, the authenticated full workspace in `apps/web/dist-workspace`, and the authenticated add-in in `apps/web/dist-outlook` with entry `outlook/taskpane.html`. The regular public site still excludes workspace and Clerk code. Follow [private-workspace-deployment.md](private-workspace-deployment.md) to configure the separate workspace host used by source and order links.

Use a separate add-in deployment with the settings in `apps/web/vercel.outlook.json` (Vercel does not select this alternate file automatically). Its project root is `apps/web`, build command is `VITE_API_URL= npm run build:outlook`, and output directory is `dist-outlook`. Deploy it under an approved HTTPS origin. Keep `VITE_API_URL` empty and proxy `/api/*` to the authenticated API on the same browser origin; this is required by persistent Outlook sessions. Configure `VITE_WORKSPACE_URL` for the private full workspace. The checked-in rewrite targets `https://api.rivet-usa.com`; the release preparer in [private-workspace-deployment.md](private-workspace-deployment.md) generates it for another backend origin. Allow the actual add-in origin in backend CORS and in the relevant Clerk application settings.

The existing marketing deployment sends `X-Frame-Options: DENY`; do not reuse that header on the task-pane deployment. The dedicated configuration limits embedding to Microsoft Outlook/Microsoft 365 origins with `frame-ancestors`. Confirm the exact production client's ancestor origins and the tenant's policies when testing. Don't remove frame protections from the public marketing site.

## Manifest and HTTPS

The checked-in `apps/web/public/outlook/manifest.xml` is a development manifest targeting **https://localhost:5178**. It is not a claim that an add-in host is already deployed or trusted locally. Generate a manifest for the actual HTTPS origin before deployment:

```sh
.venv/bin/python scripts/build_outlook_manifest.py --origin https://YOUR-VERIFIED-ADDIN-HOST
npm run build:outlook
```

The generator rejects HTTP origins, paths, credentials, and query strings. The manifest requests only Office `ReadItem`, uses Mailbox 1.9 for the asynchronous reply API, supports a pinned reading pane through nested VersionOverrides 1.1, and contains no Graph credentials or tokens. Base64 reply attachments additionally require Mailbox 1.15; older clients can explicitly use a body-only reply and never silently lose selected attachments. Pinning is a client capability, not continuous background execution.

For a local Outlook sideload, configure a trusted local HTTPS certificate. Vite's Outlook mode reads the certificate paths from `RIVET_OUTLOOK_HTTPS_KEY` and `RIVET_OUTLOOK_HTTPS_CERT`; keep keys outside the repository. Run the existing dev command with `--mode outlook`. The regular HTTP server must not already occupy port 5178. Microsoft's development-certificate tooling can provision a trusted certificate for the developer machine; installing trust is an explicit setup step.

Once the HTTPS host is reachable, validate and sideload:

```sh
npx --yes office-addin-manifest validate apps/web/public/outlook/manifest.xml
```

In Outlook, open Microsoft's [sideload dialog](https://aka.ms/olksideload), then **My add-ins → Add a custom add-in → Add from File** and select the generated manifest. A tenant administrator may restrict custom add-ins. Test on a real licensed Microsoft 365 mailbox before presenting this as connected.

## Live acceptance gates

### Local Outlook web acceptance — October 12, 2026

A licensed Microsoft 365 test mailbox completed a real loop in Chrome with the
development HTTPS host. The test used one explicitly authorized, self-addressed
fictional email and two small UTF-8 text attachments; it was not a customer drawing
benchmark. Scheduled Graph capture created the order from its explicit job number,
parsed both files, and automatically staged three coordination clarification drafts.

The actual Outlook task pane authenticated the private owner, matched the selected
email, and generated a reply using `gpt-6-luna`. It correctly quoted the fixture's
trip-unit value, requested the missing marked location, and did not claim a
production release. After review and a signature edit, Office.js opened a native
reply with one deliberately selected attachment. Read-only Graph verification
confirmed the draft flag, original-thread header, intended recipient, and a
byte-identical attachment. The reply was left **unsent**. Local proof and identifiers
are retained under the ignored `.data/outlook-live-test/` directory.

This does not certify all Outlook clients or production readiness. The local Chrome/Outlook web panel now preserves authentication across panel
close/reopen and a full Outlook refresh (verified October 14). The same behavior
still needs testing on each production Outlook client; no bearer tokens are stored
in browser storage or public URLs.
Internal update delivery remains off. Hosted workers, production Clerk credentials,
effective Microsoft mailbox scope, long-running capture/recovery and representative
PDF/scanned-document cases remain pilot gates. The remaining checks below are not
implied by this one successful fixture.

- Sign in through Clerk inside the actual Outlook host and select a team. Modal/third-party-cookie behavior must be verified on the pilot client's Outlook version. If blocked, implement the supported Office dialog authentication flow; do not substitute an unscoped token or disable authentication.
- Confirm the connector has durably imported a real test email and attachment, and the context endpoint matches it within the correct tenant.
- Generate, edit, approve, and open a native reply. Verify original sender/thread, signature behavior, recipients, and that **nothing is sent until the user sends in Outlook**.
- Select one supporting document, verify its revision and warning, and confirm the native reply includes the exact original bytes. Test replacement/deletion before compose and an older Outlook client; either must block rather than substitute or omit the attachment.
- Pin the pane and switch messages during generation and review. Results must reset and never be inserted into a different message.
- Alter the underlying evidence before approval. Verify the backend rejects an outdated review.
- Test an unknown job, inaccessible order, duplicate email, unavailable connector, no selected message, unsupported client, long draft, and provider failure.
- Submit an optional team example, verify it is pending administrator review, and verify other teams cannot retrieve it.

Automated boundary tests live in `tests/frontend-outlook.test.mjs`. They cover HTML escaping, UTF-8 size limits, unsupported clients, host failures, captured-message requirements, and wrong-thread protection. `tests/frontend-record-source.test.mjs` verifies cited sources open only evidence from the linked order and select the associated comment. They do not substitute for a live Outlook test.

## Supporting documents and grounded reply preparation

`POST /api/reply-drafts` snapshots source context from the authenticated team's
matched order. The captured message supplies the question even when the PM supplies
no custom prompt. Deterministic BM25 lexical ranking retrieves related documents
and history within explicit context budgets. This is retrieval, not training or
proof that every engineering drawing was inspected. The result states how many
source excerpts and comments were included.

The prompt and structured context distinguish customer requests, recorded human
responses, and record approvals. Comments amended after an approval are labeled
separately from unchanged approved-record comments. An older approval never silently
approves a new request. Relevant customer memory is eligible only through the
company-context service's explicit customer binding, source verification, expiry,
approval and conflict checks. A historical requirement is not a current-order fact;
a standing preference is conditional guidance, never authority for a commitment.

Draft responses include an `attachment_candidates` inventory from the current order
only. The model does not choose files for automatic sharing. Deterministic checks can recommend cited, released drawing-vault originals. The PM can select additional original
supporting files and approves those selections with the response via
`attachment_ids` on `POST /api/reply-drafts/{id}/review`. Internal/commercial and
historical material carries explicit review warnings. Inactive, unavailable,
unprocessed or unsupported originals cannot be selected.

Before using the reviewed response, the authenticated pane requests
`POST /api/reply-drafts/{id}/compose` with its expected evidence hash and current
Internet message ID. The server rechecks the order association, approval, current
evidence and company guidance, plus each selected file's exact original SHA-256,
size, type and saved metadata version. It returns the reviewed body and selected
Base64 bytes with `Cache-Control: no-store`. There are no public download URLs,
bearer tokens in links, or permanent attachment sharing grants.

Microsoft supports Base64 `ReplyFormAttachment` with `type: "base64"`, `base64file`,
`name`, and `inLine: false` starting in Mailbox 1.15. Rivet requires that capability
when attachments are selected; unsupported clients must explicitly continue with
no selected files or attach the originals themselves. It never silently omits an
approved file. The `file` URL mechanism is intentionally unused because Exchange
requires an unauthenticated URL. See Microsoft's
[ReplyFormAttachment specification](https://learn.microsoft.com/en-us/javascript/api/outlook/office.replyformattachment?view=outlook-js-preview).

Rivet caps the bundle at five files, 10 MB per file and 20 MB total. These are Rivet
limits below Outlook's documented web/new-Windows size ceiling; a pilot's Exchange
configuration may impose stricter limits. Supported formats are PDF, UTF-8 TXT/CSV,
XLSX, DOCX, PNG and JPEG when they are already processed originals in the order.
Type checks do not constitute malware scanning or an engineering verification.
[Microsoft's attachment limits](https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/limits-for-activation-and-javascript-api-for-outlook-add-ins)
still apply in the actual host.

Opening a reply does not send it or prove delivery. The live acceptance test must
verify the original thread and recipient, every selected filename and byte-identical
attachment, unsupported-client behavior, file changes after approval, and nothing
sent until the PM presses Send in Outlook. Automated tests use synthetic originals
and mocked providers; they do not claim live Microsoft connectivity.

## Official references

- [Office initialization and browser preview behavior](https://learn.microsoft.com/en-us/office/dev/add-ins/develop/initialize-add-in)
- [Outlook MessageRead reply APIs and requirements](https://learn.microsoft.com/en-us/javascript/api/outlook/office.messageread?view=outlook-js-preview)
- [Pinnable task panes and ItemChanged handling](https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/pinnable-taskpane)
- [Manifest and HTTPS requirements](https://learn.microsoft.com/en-us/office/dev/add-ins/develop/add-in-manifests)
- [VersionOverrides 1.1 nesting](https://learn.microsoft.com/en-us/javascript/api/manifest/versionoverrides-1-1-mail?view=common-js-preview)
- [Sideloading an Outlook add-in](https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/sideload-outlook-add-ins-for-testing)

## Outlook session persistence (October 14)

The task pane uses the existing Clerk sign-in for its first connection. After a
verified Clerk bearer request, the API sets an opaque `__Host-rivet_outlook` cookie
with `Secure`, `HttpOnly`, `SameSite=None`, `Partitioned`, and `Path=/`. Only its
SHA-256 digest is stored in `outlook_sessions` (migration 0019). The expiry is the
earlier of seven days or the originating Clerk session expiry; reopening never
extends it. The API checks the originating Clerk session and current organization
membership on every cookie-authenticated request. Revocation, account tasks,
changed active teams, and lost membership fail closed. Clerk outages return a
retryable error rather than signing the user in unchecked.

This requires an HTTPS task-pane origin with a same-origin `/api` reverse proxy.
Cookie authentication is explicitly opt-in and requires same-origin Fetch Metadata,
an approved origin, and the Outlook request header. CORS credentials remain disabled.
Ordinary workspace requests continue to use Clerk bearer tokens. Unsupported or
expired cookie sessions return to the existing sign-in flow. Office.js initializes
before authentication so Outlook can initialize a reopened frame normally.

Verified locally: close/reopen and full Outlook reload without another sign-in,
with successful authenticated source and draft lookups afterward. Regression tests
cover revocation, expiry, team/user mismatch, membership removal, role changes,
forged cookies, CSRF, and verification-provider outages.
