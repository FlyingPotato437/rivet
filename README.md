# Rivet

An evidence-first equipment quoting workspace. Rivet has its own product identity, with Lumina-inspired dark surfaces, crisp Geist typography, restrained lighting, and quiet motion. It is not a copy of Memorable's landing page.

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

On this machine the database is already initialized. After a reboot, start that cluster with the `pg_ctl` command, then run `uv run python scripts/dev.py`.

Open **http://127.0.0.1:5178**. API documentation is at **http://127.0.0.1:8787/docs**. The launcher starts the web UI, FastAPI, and a separate durable worker; Ctrl-C stops them. Database shutdown is explicit: `pg_ctl -D .data/postgres stop`.

## What works

- A full public-facing homepage at `/`: a dimensional animated Rivet mark, scroll-driven product reveal, pinned workflow story, interactive read-only sample quote, features, FAQs, and footer. Open the app at `/#projects`; the workspace logo returns home. Motion respects reduced-motion preferences, with direct step controls on small screens.

- Projects, exact-decimal quote lines, quantity editing with keyboard controls, cost/price edits with reasons, explicit gross margin versus markup, catalog selection, line review, and terms.
- Private original uploads: text PDFs, CSV, XLSX, and text. Source lines, PDF word geometry, sheet/cell locations, formulas, and per-page/sheet coverage are retained. Missing formula caches and scanned pages are flagged.
- User-confirmed spreadsheet column mapping for schedules, existing quotes, addenda, catalog records, and supplier offers. Missing costs stay unknown. Duplicate conflicting rows are rejected. Partial addenda preserve omitted equipment.
- Pending atomic proposals, original/changed value comparisons, source citations, requirement coverage, immutable quote revisions, optimistic concurrency, checked restoration, and idempotent retries.
- Approval of a specific quote/input revision. Uploads and meaningful edits invalidate current approval. Customer PDF and XLSX exports come from the approved immutable snapshot and exclude internal costs, margins, and source attachments.
- A separate PostgreSQL-backed worker with row locks, leases, recovery, and checkpoints. An OpenAI Responses adapter provides a bounded tool loop, source-scoped reads, actual catalog/offer lookup, deterministic math, tentative overlays, validation, no-progress detection, cancellation, and human-input pause/resume.
- Synthetic starter projects and example import files. All sample equipment, suppliers, offers, pricing, delivery wording, and review states are fictional.

## Model connection

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env` and restart the API/worker. Both remain server-side. This machine's existing authorized OpenAI key was reused without printing it. The adapter sets `store: false`. Starting an assistant run sends the relevant project context and requested source excerpts to OpenAI; ordinary manual editing and mapped imports run locally.

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

`apps/web`: React, TypeScript, Vite, TanStack Query/Table, PDF.js, self-hosted Geist fonts.

`backend/api`: typed FastAPI requests, localhost/host/origin boundary, command and review endpoints.

`backend/domain`: Decimal pricing, validation, transactions, immutable snapshots, approvals, customer allowlist, PDF/XLSX export.

`backend/ingestion`: private original storage, PDF/spreadsheet/text parsers, coverage and confirmed mappings.

`backend/agents`: server-side model gateway and resumable tool/verification loop. Only observable tool requests, outputs, plans and concise summaries are persisted, not hidden reasoning.

`backend/storage`: SQLAlchemy models, PostgreSQL, jobs and blob storage. `migrations` contains the initial Alembic migration. Runtime files live under ignored `.data`; secrets under ignored `.env`.

## Prototype boundaries

This is not a shared production deployment. The app refuses non-local clients and explicitly refuses `RIVET_LOCAL_ONLY=false`. Before a shared pilot, add authenticated server sessions, organization membership and role enforcement, composite tenant foreign keys, CSRF/session policy, an isolated storage adapter, deployment controls, and tenant migration tests. The prototype uses a fixed development organization and identity; passing tenant-isolation tests does not make it a multi-user product.

Initial catalog checks establish identity, offer units, currency, expiry and quantity coverage. They do not certify engineering equivalence. Requirement coverage currently models explicit equipment tags and quantities; arbitrary technical clauses and semantic precedence require estimator review. Unreadable scans need transcription, and PDF geometry highlights are approximate. There is no OCR, live inventory, external sending, inbox/ERP integration, automatic engineering design, tax calculation, commission pricing, multi-currency, or guaranteed arrival-date calculation.

The worker limits retries and steps, but a production queue should add parse-process resource isolation, heartbeat observability, configurable cost accounting, scheduled retry backoff and operator retry controls. The agent keeps finite progress and can stop for human input; a full held-out customer-package evaluation, organization memory approval workflow, and production adaptive-agent acceptance remain required before pilot claims.

## Data retention and backup

See [docs/data-retention.md](docs/data-retention.md). No originals are overwritten. Do not delete `.data` unless deliberately resetting a disposable workspace; it contains the database and private uploads.
