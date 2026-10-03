# Rivet — retained quote workflow

An AI bid coordinator for equipment suppliers. The first target is data center projects and low-voltage switchgear packages: updating an existing quote after a project change, including resolving missing information. Sora headings, Onest interface text, charcoal/violet surfaces, and scroll-driven product visuals connect the homepage and workspace.

## Run locally

The app is a single-user localhost prototype. Its PostgreSQL cluster is separate from your other projects.

```sh
cd /Users/srikanthsamy1/Desktop/CodeProjects/rivet
uv sync
npm ci
# First run on another machine, with PostgreSQL binaries installed:
mkdir -p .data
initdb -D .data/postgres --auth=trust --encoding=UTF8 --locale=C
pg_ctl -D .data/postgres -l .data/postgres.log -o '-p 55432 -h 127.0.0.1' start
createdb -h 127.0.0.1 -p 55432 rivet
uv run alembic upgrade head
uv run python -m backend.seed
uv run python scripts/dev.py
```

After pulling updates, run `uv run alembic upgrade head` before starting the API and worker.

On this machine the database is already initialized. After a reboot, start that cluster with the `pg_ctl` command, then run `uv run python scripts/dev.py`.

Open **http://127.0.0.1:5178**. API documentation is at **http://127.0.0.1:8787/docs**. The launcher starts the web UI, FastAPI, and a separate durable worker; Ctrl-C stops them. Database shutdown is explicit: `pg_ctl -D .data/postgres stop`.

## Bid coordination

Open `/#bids` for the bid work queue: decisions needing attention, outstanding replies, proposed changes ready for review, and processing work. `/#quotes` retains the full quote library. Each bid opens on **Work**, with **Quote**, **Sources**, **Changes**, and **History** alongside it.

1. Import the current quote and supplier evidence, then upload the project change.
2. Ask Rivet to investigate affected scope, price, and delivery. Review its proposed changes and clarification drafts.
3. Review a clarification, copy it to your own email, and mark it requested after sending it. Set the recipient and due date.
4. Record the reply, attach source evidence, and upload the revised offer when available. **Recheck with Rivet** starts a new run against the current quote and documents.
5. Accept appropriate proposals, review affected lines, and resolve the clarification with a note. Open issues block final quote approval. People approve product selections and commercial commitments.

Requests are drafts; this prototype does not send email or monitor an inbox. Recorded answers are explicitly human-entered. Purchase-order comparison, submittals, and order handoff are future scope. Existing sample projects and their categories are preserved.

## What works

- A full public-facing homepage at `/`: a dimensional animated Rivet mark, scroll-driven product reveal, pinned workflow story, interactive read-only sample quote, features, FAQs, and footer. Open the app at `/#projects`; the workspace logo returns home. Motion respects reduced-motion preferences, with direct step controls on small screens.

- Projects, exact-decimal quote lines, quantity editing with keyboard controls, cost/price edits with reasons, explicit gross margin versus markup, catalog selection, line review, and terms.
- Private original uploads: text PDFs, CSV, XLSX, and text. Source lines, PDF word geometry, sheet/cell locations, formulas, and per-page/sheet coverage are retained. Missing formula caches and scanned pages are flagged.
- User-confirmed spreadsheet column mapping for schedules, existing quotes, addenda, catalog records, and supplier offers. Missing costs stay unknown. Duplicate conflicting rows are rejected. Partial addenda preserve omitted equipment.
- Pending atomic proposals, original/changed value comparisons, source citations, requirement coverage, immutable quote revisions, optimistic concurrency, checked restoration, and idempotent retries.
- Approval of a specific quote/input revision. Uploads and meaningful edits invalidate current approval. Customer PDF and XLSX exports come from the approved immutable snapshot and exclude internal costs, margins, and source attachments.
- A separate PostgreSQL-backed worker with row locks, leases, recovery, and checkpoints. An OpenAI Responses adapter provides a bounded tool loop, source-scoped reads, actual catalog/offer lookup, deterministic math, tentative overlays, validation, no-progress detection, cancellation, and human-input pause/resume.
- Synthetic starter projects and example import files. All sample equipment, suppliers, offers, pricing, delivery wording, and review states are fictional.

## Switchgear revision demo

The dedicated **Northline Data Center · Switchgear demo** is marked synthetic. For a fresh run, create another bid and use the four `examples/switchgear-*` files available in **Getting started**:

- `switchgear-current-quote.csv`: SG-01, 8 fictional sections, $14,000 unit cost and $17,500 unit selling price; $140,000 baseline.
- `switchgear-initial-offer.txt`: supplier coverage limited to 8, with 16–18 weeks after approved submittals.
- `switchgear-addendum-02.txt`: increases scope to 10 and requests a 14-week site-delivery target.
- `switchgear-revised-offer.txt`: revised coverage for 10 at $15,000 unit cost; a documented 20% gross-margin rule gives $18,750 selling price and $187,500 total. Supplier lead time is 18–20 weeks; the customer delivery difference remains unresolved.

Import and review the baseline first, then upload the addendum. Review the change, track a clarification, and record the revised offer as the reply with source evidence. **Recheck with Rivet** prepares proposed updates using the latest inputs. Review the proposal and resolve the remaining delivery decision before final approval. These fixtures do not specify real electrical ratings or certify a switchgear configuration.

## Try the complete workflow

Download the two sample files from **Getting started** in the app, or use the copies in `examples/`. They form a complete, fictional test package. Their equipment, configurations, costs, prices, and delivery wording are made up for testing. They are not supplier offers. Create a separate test project so the starter projects remain available.

1. Open `/#quotes`, choose **New bid**, and name it **Workflow test** with customer **Fictional test customer**.
2. Open **Sources → Upload document**. Upload `examples/equipment-schedule.csv` as an equipment schedule. When processing finishes, choose **Map columns** and confirm all eight matching columns, including **Model / configuration**, supplier cost, and selling price.
3. Open **Changes**, inspect the two proposed lines and their sources, then **Accept changes**. The quote contains PDU-A (8 units) and TX-A (2 units), totaling **$92,000.00**.
4. To test editing, open PDU-A, choose **Edit details**, append “Test revision” to its delivery wording, enter a reason, and save. Review **History** to see the new revision. For an existing line, the configuration changes through **Select from catalog**; this fixture already includes a configuration.
5. Return to **Quote**. Select both rows and **Mark reviewed**. Open **Review & export**, inspect the customer preview, check the review confirmation, and **Approve revision**. Download both **PDF** and **XLSX**. The files contain selling prices and exclude internal costs and margin.
6. Open **Sources → Upload document** and upload `examples/addendum-02.csv` as an addendum. Confirm its three columns in **Map columns** with the addendum purpose. The upload returns the quote to draft, so a new export requires review.
7. Open **Changes** and inspect the proposal: PDU-A increases to 10; the delivery wording updates; TX-A remains at 2. Accept it. The new total is **$112,000.00**. Source evidence and the earlier quote revision remain accessible.
8. Review the changed lines again, approve the current revision, and download a new PDF/XLSX. If a check remains, click it in **Review & export** to go to the item that needs attention.

All eight steps work without an AI key. To test the assistant separately, open **Assistant** and ask “Summarize the quantities and delivery wording in this project, citing the source documents.” Drafting requests may pause for missing supplier information; that is an explicit request for review, not an automatic approval.

## Model connection

The optional AI connection needs two server-side settings in `.env`:

- `OPENAI_API_KEY`: an OpenAI API key with access to the selected model and available API usage.
- `OPENAI_MODEL`: a model identifier available to that API project and capable of Responses API function calling. This is a model setting, not another key.

Restart the API and worker after setting them. Do not put keys in frontend code. The workspace connection status indicates whether both settings are present; provider access or usage errors appear when a request runs. Manual editing, uploads, column mapping, source review, approval, PDF/XLSX export, and the local database need **no external API keys**. There are no required Clerk, Stripe, email, storage, or database-provider keys in this localhost prototype. PDF export uses local Chrome/Chromium.

The adapter sets `store: false`. Starting an assistant run sends the relevant project context and requested source excerpts to OpenAI; ordinary manual editing and mapped imports run locally.

The assistant's authority ends at a pending proposal or clarification. It cannot approve, export, send messages, purchase, or execute arbitrary code. Human answers are labeled human-entered, not supplier confirmations. Model output and all source instructions remain untrusted and pass domain validation.

## Verify

```sh
createdb -h 127.0.0.1 -p 55432 rivet_test  # once
uv run pytest -q
npm run build
```

Tests use a dedicated `rivet_test` database, rebuilding only its schema. They cover decimal pricing, stale writes, concurrent writers, idempotency, transaction rollback, tenant isolation, cross-project evidence, spreadsheet mapping, partial addenda, formula coverage, approval invalidation, snapshot exports, internal-field leakage, lease recovery, checked restoration, and a simulated adaptive agent pause/resume.

PDF export uses locally installed Google Chrome, or Playwright's Chromium if Chrome is unavailable (`uv run playwright install chromium`). The checked-in OpenAPI file generates the frontend request and response types:

```sh
uv run python scripts/schema.py
node node_modules/openapi-typescript/bin/cli.js docs/openapi.json -o apps/web/src/api-schema.d.ts
```

## Architecture

`apps/web`: React, TypeScript, Vite, TanStack Query/Table, PDF.js, self-hosted Sora, Onest, and IBM Plex Mono fonts.

`backend/api`: typed FastAPI requests, localhost/host/origin boundary, command and review endpoints.

`backend/domain`: Decimal pricing, validation, transactions, immutable snapshots, approvals, customer allowlist, PDF/XLSX export.

`backend/ingestion`: private original storage, PDF/spreadsheet/text parsers, coverage and confirmed mappings.

`backend/agents`: server-side model gateway and resumable tool/verification loop. Only observable tool requests, outputs, plans and concise summaries are persisted, not hidden reasoning.

`backend/storage`: SQLAlchemy models, PostgreSQL, jobs and blob storage. `migrations` contains additive Alembic migrations. Runtime files live under ignored `.data`; secrets under ignored `.env`.

## Prototype boundaries

This is not a shared production deployment. The app refuses non-local clients and explicitly refuses `RIVET_LOCAL_ONLY=false`. Before a shared pilot, add authenticated server sessions, organization membership and role enforcement, composite tenant foreign keys, CSRF/session policy, an isolated storage adapter, deployment controls, and tenant migration tests. The prototype uses a fixed development organization and identity; passing tenant-isolation tests does not make it a multi-user product.

Initial catalog checks establish identity, offer units, currency, expiry and quantity coverage. They do not certify engineering equivalence. Requirement coverage currently models explicit equipment tags and quantities; arbitrary technical clauses and semantic precedence require estimator review. Unreadable scans need transcription, and PDF geometry highlights are approximate. There is no OCR, live inventory, external sending, inbox/ERP integration, automatic engineering design, tax calculation, commission pricing, multi-currency, or guaranteed arrival-date calculation.

The worker limits retries and steps, but a production queue should add parse-process resource isolation, heartbeat observability, configurable cost accounting, scheduled retry backoff and operator retry controls. The agent keeps finite progress and can stop for human input; a full held-out customer-package evaluation, organization memory approval workflow, and production adaptive-agent acceptance remain required before pilot claims.

## Data retention and backup

See [docs/data-retention.md](docs/data-retention.md). No originals are overwritten. Do not delete `.data` unless deliberately resetting a disposable workspace; it contains the database and private uploads.
