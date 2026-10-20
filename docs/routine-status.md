# Routine order-status replies

The normal mailbox preparation loop now recognizes a small set of status-only
questions before invoking a model. It uses exact human-reviewed status facts and
creates a cited, unsent `ReplyDraft`; the existing Outlook placement worker can
put that draft in the captured customer thread. There is no Graph send call or
new Microsoft permission in this implementation.

## Authority and facts

- `GET/PUT /api/routine-status/policy` reads or changes versioned team policy.
  A team administrator must change it. `auto_send` defaults to false; enabling
  it requires exact recipient addresses, without domain patterns or wildcards.
- `POST /api/routine-status/orders/{id}/facts` records a named company status
  approver's decision against exact source bytes and the current order version.
  Synthetic orders allow their team admin to make explicitly fictional decisions.
- A fact is `confirmed_ship_date` or `material_available_date`, a date, an expiry
  within seven days, source IDs, a review reason, and `expected_version`. The
  structured source must contain that exact key/value, the exact `job_number`,
  and `status: confirmed`. Incoming email/markups cannot establish the fact.
- `status_approvers` names the actual authorized identities. Fact age defaults to
  24 hours and can be configured between one hour and seven days. Changed bytes,
  source metadata/text, expiry, or a changed order invalidate prepared responses.
- `GET /api/routine-status/orders/{id}` exposes the policy, approved fact snapshots,
  and actual preparation/simulation history. A protected `/prepare` route also
  supports a specific captured receipt; it never accepts a caller-chosen recipient.

A material availability date is described only as material availability, explicitly
not a shipment commitment. It cannot answer a ship-date question alone. A passed
ship date, material availability after a ship date, a delay, new promise, price,
engineering, release/approval question, attachments, or unrecognized extra wording
requires a person. This is deliberately a closed grammar, not a claim of general
email understanding or universal coverage.

## Local simulation, off by default

A policy-enabled exact recipient may enqueue one durable local outbox job. The
transport still defaults to disabled. Only `RIVET_ROUTINE_STATUS_TRANSPORT=local_simulation`
with an explicit absolute `RIVET_ROUTINE_STATUS_SIMULATOR_DB` path can choose the
SQLite acceptance ledger. Execution additionally requires a synthetic order.
These flags were not enabled on any live service. There is no production sender.

The outbox freezes the recipient, email/thread identity, order version, fact hashes,
policy version, and exact response. It commits intent before the effect and checks
currentness again immediately before execution. A stable receipt identity prevents
duplicates. After a process failure or uncertain transport response, future worker
attempts perform receipt lookup only; a missing receipt never justifies a resend.
After three inconclusive reconciliations the worker leaves the item needing
attention. Simulator acceptance is labelled `accepted_simulation`; it never marks
a real customer email sent or records fabricated learning.

## What this does not claim

This does not yet import a company's authoritative schedule automatically, add a
schedule-mapping UI, infer approval from a file name, or send production customer
mail. A pilot needs its actual approved schedule/material source mapped to these
typed facts, named approval authority, and tested freshness rules. A future live
sender would need explicit separate setup, permission review, and sent-mail
reconciliation; an enabled policy alone cannot authorize or create that connector.
