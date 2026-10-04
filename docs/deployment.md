# Deploy Rivet

Rivet has a Vite frontend, a FastAPI service, a durable worker, PostgreSQL, and
uploaded files. Deploy the frontend to Vercel. Run the backend services on a
Linux server using the included Docker Compose configuration. This is a
single-server deployment, not a highly available cluster.

The repository contains the configuration; it does not provision infrastructure,
create DNS records, or supply production credentials.

## What runs where

| Component | Deployment | Persistence |
| --- | --- | --- |
| React/Vite | Vercel, repository root `./` | Static build |
| HTTPS ingress | Caddy, server ports 80/443 | Certificate volume |
| FastAPI | Docker `api` service | Shared `documents` volume |
| Background processing | Docker `worker` service | Same database and `documents` volume |
| PostgreSQL 17 | Docker `postgres` service | `postgres` volume |
| Schema migration | Docker `migrate` job | Runs before API and worker |

The API and worker must see the **same files at the same path**. Separate
ephemeral disks lose uploads and prevent the worker from reading API uploads.
For multiple backend hosts, implement shared object storage before separating
them. Do not run the worker as a Vercel function.

The image installs locked Python dependencies and Playwright Chromium, including
its Linux libraries, for PDF exports. It runs as an unprivileged user. The build
context excludes `.env` files, the local database, uploads, and frontend source.
Postgres and port 8787 are private to Docker; only Caddy publishes ports.

## 1. Choose the two public addresses

For example:

- Frontend: `https://app.your-domain.com` (or a stable Vercel production address)
- API: `https://api.your-domain.com`

Point the API hostname to your server. Allow inbound TCP 80 and 443; Caddy uses
the hostname to obtain and renew its TLS certificate. Allow UDP 443 if using
HTTP/3. Keep PostgreSQL and the API's internal port off the public firewall.

Use a stable frontend hostname for Clerk and the API allowlist. An arbitrary
Vercel preview URL is not automatically authorized. To test a preview, explicitly
add its exact HTTPS origin to the API configuration and the appropriate Clerk
configuration, or use a separate staging backend and Clerk instance.

Provider references: [Vercel Vite deployments](https://vercel.com/docs/frameworks/frontend/vite),
[Vercel monorepos](https://vercel.com/docs/monorepos), and
[Playwright browser dependencies](https://playwright.dev/python/docs/browsers).

## 2. Configure production authentication

Create/configure the **production instance** of the existing Clerk application.
Enable Organizations and configure your frontend domain and sign-in methods in
Clerk. Complete its domain/DNS requirements. Use production keys from the same
instance; the local development keys and one-click demo are not production
credentials.

On the server set `CLERK_SECRET_KEY=sk_live_…` and
`VITE_CLERK_PUBLISHABLE_KEY=pk_live_…`. Despite the latter's historical name, this
deployment reads it from the API environment and returns the public value from
`/api/auth/config`; **neither Clerk key needs to be configured in Vercel**.
Never give Vercel the secret key, database URL, model key, or Resend key.

The production API refuses to start with local authentication, development
Clerk keys, the development demo enabled, missing exact origin/host allowlists,
or an unspecified persistent data path. API access checks Clerk's issuer,
authorized party, session, active organization, and role. CORS preflight requests
do not need a token; private records still do.

## 3. Start the backend

Install Docker Engine and Docker Compose on your server, then clone this
repository. From the repository root:

```sh
cp deploy/.env.example .env.production
chmod 600 .env.production
openssl rand -hex 32
```

Paste that generated value into `POSTGRES_PASSWORD` and fill the remaining
required settings in `.env.production`. Use the hex password as generated;
the bundled deployment rejects passwords shorter than 64 hexadecimal characters
because punctuation in a raw password can corrupt the database connection URL.

```dotenv
POSTGRES_PASSWORD=<generated-hex-value>
RIVET_API_HOST=api.your-domain.com
RIVET_ALLOWED_HOSTS=api.your-domain.com,127.0.0.1,localhost
RIVET_ALLOWED_ORIGINS=https://app.your-domain.com
CLERK_SECRET_KEY=<production-secret-key>
VITE_CLERK_PUBLISHABLE_KEY=<matching-production-publishable-key>
```

Keep `127.0.0.1` in the host allowlist for the container health check. Origins are
comma-separated exact HTTPS origins, with no paths or wildcards; hosts contain
hostnames only. Compose sets `RIVET_ENV=production`, `RIVET_AUTH_MODE=clerk`,
`RIVET_LOCAL_ONLY=false`, `RIVET_DEMO_ENABLED=false`, the internal database URL,
and `/var/lib/rivet` as the persistent data path.

Validate without printing secrets, then build and start:

```sh
docker compose --env-file .env.production -f compose.production.yml config --quiet
docker compose --env-file .env.production -f compose.production.yml up -d --build
docker compose --env-file .env.production -f compose.production.yml ps -a
curl --fail https://api.your-domain.com/api/health
```

The migration container should exit successfully. API, worker, database, and
proxy should remain running. `health` checks database access; it does not prove
that the worker is processing jobs. Verify processing with an upload below.

To inspect problems:

```sh
docker compose --env-file .env.production -f compose.production.yml logs --tail=100 migrate api worker proxy
```

Do not post unredacted environment output or logs containing document content.
The full `docker compose config` output includes resolved secrets; use
`config --quiet` for validation.

## 4. Deploy the frontend on Vercel

Import `FlyingPotato437/rivet` and use:

| Vercel setting | Value |
| --- | --- |
| Framework preset | Vite |
| Root Directory | `./` |
| Node.js Version | `24.x` |
| Install Command | `npm ci --include=dev` |
| Build Command | `npm run build` |
| Output Directory | `apps/web/dist` |
| Environment variable | `VITE_API_URL=https://api.your-domain.com` |

The root `vercel.json` supplies these settings and overrides framework detection.
Do not use the FastAPI preset or `vite build` directly at the repository root.
The root npm script runs the frontend workspace build, and the install command
includes Vite and TypeScript even when production dependency pruning is enabled.

An existing project rooted at `apps/web` is also supported by its own
`vercel.json` (output `dist`, install from the repository lockfile). Keep access
to files outside that directory enabled. Prefer `./` for new imports.

Use Node 24 for the frontend build. The locked PDF viewer requires Node 22.13+
or 24+, so Node 20 does not satisfy all dependencies. Node 24 is an available
[Vercel Node.js version](https://vercel.com/docs/functions/runtimes/node-js/node-js-versions).

Both configurations also set basic security response headers. `VITE_API_URL` is a **public, build-time value**. It must be an origin,
without `/api`, a query, credentials, or a fragment. Redeploy the frontend after
changing it. A configured HTTP API origin is rejected on Vercel. The public
marketing page can build and load without an API URL, but sign-in, order work,
and pilot form submissions require the hosted API. Without it, the form reports
an error and never pretends a request was saved.

The application uses hash routes, so `/#orders` and `/#shared/<token>` need no
SPA rewrite. API requests, PDF source reads, and downloads are routed directly
to the configured backend. Clerk bearer tokens are sent to that API only;
cross-site cookies and redirected authenticated fetches are disabled.

## 5. Optional connections

### Rivet questions

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in the **server** environment, then
recreate API and worker. Choose a model enabled for that key. Uploading and
reviewing comments, responses, approvals, and exports work without these values.
The assistant needs them to answer questions.

### Email forwarding and notices

Follow [accounts and email setup](accounts-email-setup.md). Configure a Resend
key with receiving access, a receiving domain, and a verified sending address.
Set `RESEND_API_KEY`, `RESEND_RECEIVING_DOMAIN`, `RIVET_EMAIL_FROM`, and
`RESEND_WEBHOOK_SECRET` only on the server. Register the receiving webhook at:

```text
https://api.your-domain.com/api/webhooks/resend
```

Enable `email.received`; the webhook verifies the provider signature before
queuing intake. Receiving also needs the worker. Delivery requires explicit
review and send; uploading documents does not send anything. Domain verification
and an actual forwarding test are still required after deploying the code.

## 6. Verify before inviting a customer

Use your own account and a test organization with public or synthetic files:

1. Sign in through the deployed frontend, create/select a team, and reload.
2. Create an order and upload a marked-up PDF. Confirm queued processing
   completes and original text, author, date, and page are retained.
3. Open a cited source PDF in the main workspace, return with **Back to record**,
   save a reviewed response, and export both Excel and PDF. Open **Rivet** with
   Cmd/Ctrl+J and confirm the conversation persists while switching order tabs.
4. Approve the reviewed record and open its share link in a signed-out browser.
   Confirm source access works and subsequent working edits do not alter it.
5. Sign in with a separate team and confirm the first team's orders are absent.
6. If configured, ask a source-linked question and verify a forwarded email
   arrives on the intended order. Test notice sending only to your own address.
7. Restart API and worker and confirm documents and records remain available.

The local **Try the demo** account uses Clerk development authentication and is
deliberately unavailable in production. Create real production users and teams;
invite demo viewers to a separate team containing only demo documents.

Automated checks, run locally against the dedicated test database:

```sh
uv run pytest -q
node --test tests/frontend-api-routing.test.mjs
npm run build
```

Passing these checks is not a completed deployment. TLS issuance, production
Clerk sign-in, the container's Chromium PDF export, live email delivery, and
durability need the checks above on the actual host.

## Updates, backups, and recovery

Back up **both** the PostgreSQL database and the `documents` volume. The database
alone cannot recover uploaded originals. Keep encrypted backups off the server
and test restoring them into an isolated deployment before relying on them.
Use a maintenance window and stop API and worker while taking a coordinated
backup, or use a backup system that guarantees a consistent recovery point.

Before an update, take that backup. Pull the desired Git commit and use the same
`docker compose ... up -d --build` command. The migration job runs before API
and worker start. If a migration fails, inspect it and restore a known-good
version/backup; don't manually mark it successful. A code rollback alone is not
a database rollback.

`docker compose ... down` retains named volumes. **Do not use `down -v`** on a
deployment containing data. Changing `POSTGRES_PASSWORD` in the environment
does not change the password of an existing PostgreSQL volume; rotate the
database role and application settings together.

For a different container host, configure the same production variables from
`deploy/.env.example`, run `python -m scripts.container_entrypoint migrate` once,
then run the `api` and `worker` roles separately against the same database and
shared persistent volume. The current blob implementation does not support
independent per-service filesystems.

## Pilot waitlist

Apply migration `0006` with the other migrations before enabling the landing
page form. `POST /api/pilot-requests` accepts public opt-ins; it validates fields,
requires contact consent, deduplicates normalized email addresses, checks origins,
and limits the intake to 100 new requests per hour. A honeypot filters basic bots.
Inquiries are independent of workspace tenants, and there is no public listing.
The existing API backup covers this table. Put the API behind provider-level
rate limiting before a high-traffic campaign; the intake cap is intentionally
small and can be exhausted by unsolicited submissions.

No notification email is sent automatically. Operators with server/database
access can export requests into a new private CSV file:

```sh
python scripts/export_pilot_requests.py --output /private/path/rivet-pilot-requests.csv
```

Use a private directory outside the repository for contact exports. The file
uses owner-only permissions and neutralizes spreadsheet formulas. Follow up
manually with opted-in contacts. An optional frontend `VITE_PILOT_EMAIL` adds an
email fallback if saving a form fails; use a real monitored address and rebuild.
