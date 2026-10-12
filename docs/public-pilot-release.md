# Public pilot release

The production web build contains only the marketing page and `/pilot` booking page. Authentication, the workspace, document views, shared records, and demo entry points are excluded at compile time. Private app development remains at `http://127.0.0.1:5178/#decisions`.

The public calendar uses the official Cal.com React embed for `srikanth-samy-pfuse0/30min`. No API key is required for this public event. Cal.com manages availability, bookings, invitations, and its embedded form. The fallback link opens the same calendar if third-party content is blocked.

The API separately defaults to closed when `RIVET_ENV=production`: all workspace routes, authentication configuration, share-token routes, intake, and email webhooks return 403. `/api/health` and `/api/pilot-requests` remain available. The closed gate is additive to the existing production authentication and tenant protections.

A later private pilot deployment must deliberately set `RIVET_WORKSPACE_ENABLED=true` and provide production Clerk, PostgreSQL, persistent document storage, worker, and exact CORS/host configuration. It also needs a separate private frontend build/deployment; this public bundle cannot expose the app via an environment switch. Set `RIVET_WORKSPACE_URL` to that private origin for email draft links.

## Website representation

The interactive workflow and email brief are explicitly illustrative sample data. The switchgear image is original generated illustrative artwork, not a photograph of a Rivet/customer facility. No client logos, customer counts, accuracy promises, timing guarantees, or connected Outlook/SAP/SharePoint claims are made.

The initial private workflow supports PDF/EML/TXT intake, exact identifier matching with uncertainty review, annotation-based comment extraction, record-based revision flags, reviewed responses, and exports. Risk flags are triage, not engineering or commercial determinations. See `inbox-first-adoption.md` for integration setup and boundaries.
