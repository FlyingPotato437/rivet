# Streamlined order workflow

Verified locally on November 8, 2026. Branch: `streamlined-order-flow-20261108`.

## Default workflow

The default order page has three views:

1. **Customer requests**: original documents, extracted requests, findings, and links to saved edits or the full comment log.
2. **Corrected files**: supported drawing/BOM outputs, revision checks, located before/after excerpts, and downloads. Partial or unsupported work remains explicitly incomplete.
3. **Outlook reply**: the existing draft, sent reply, or source thread, with accurate delivery state. An Outlook thread link is not presented as a saved draft.

Today groups work into Needs your input, Ready for review, Working, and Waiting. Multiple decisions on one order share one row; individual decisions remain in the drawer. Specialist tools, history, and order properties remain under More tools and order details. Explicit engineering-stage, source, comment, approval, and event links still work.

## Background handoff

Automatic mailbox reconciliation now schedules supported native file corrections after source parsing. Read-only ownership checks do not create work. Each job carries the receipt that owns the files, preventing a later unrelated email on the order from receiving those attachments. A newer message in the same conversation blocks a stale handoff.

Queued legacy unbound jobs can be upgraded in place. An exact receipt-bound job takes precedence if both exist. Already-running legacy work exits before an Outlook write when a native email owner exists.

Sent, discarded, superseded, pending, and ambiguous provider outcomes do not offer duplicate creation. Known-safe provider rejections can be retried explicitly. Saving a reply is not engineering approval, and this workflow never sends it.

## Verification

| Check | Result |
| --- | --- |
| Related backend workflow suite | 136 passed; 6 dependency warnings |
| Frontend suite | 221 passed |
| TypeScript | Passed |
| Workspace production build | Passed; existing large-chunk warning remains |
| Outlook production build | Passed |
| Desktop browser | Requests, source PDFs, saved edit links, comparison previews, file checks, and draft/sent state verified |
| Live Outlook | Existing unsent fictional PDU reply opened directly in compose with DXF, XLSX, and PDF attachments; no duplicate or send |
| Real supplied CAD | Request-to-saved-edit and before/after excerpts verified; partial extraction and unresolved engineering requests remain visible |

The backend suite covers redesign, automatic replies, document Outlook drafts, native ownership, native Outlook workflow, equipment Outlook, and freshness. This was not the entire backend suite and is not an engineering-accuracy benchmark. Mobile layout was not visually verified because the browser viewport override did not take effect.

Private evidence is in `.data/flow-audit-20261108/`, including mailbox/readback reports and `order-workflow.png`. These files and customer originals are not committed.

## Remaining limits

- Complex equipment geometry and circuit changes require manufacturer rules and engineering inputs. Native text edits do not establish those changes were made.
- An existing scanned evaluation still has one failed coverage item. Its successful responses do not establish complete handwriting extraction.
- If file preparation succeeds but Outlook saving fails, the current prepared-bundle gate does not automatically requeue delivery. The explicit save/retry action remains available for safe rejections; ambiguous writes require checking the existing reply first.
- The usual demo mailbox was checked successfully with zero provider errors. A second workspace connection remains in initial sync and is not claimed to be monitoring.
- An old Chrome tab lost its session during workspace switching. A fresh tab restored the signed-in workspace without changing authentication configuration. Session persistence across every workspace transition is not proven.

No email was sent, no engineering release approved, and no new credits purchased during this verification. Internal notification sending and follow-up drafting were disabled in the resumed local worker.
