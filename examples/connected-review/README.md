# Connected review example

These are **fictional text documents**, deliberately small enough to inspect. They exercise the same upload, parsing, matching and review code as an ordinary order. They are not engineering drawings or proof of drawing interpretation. The separately documented public PDF demo covers native PDF annotations and scanned regions.

## Load the files

Create an order, then upload the five files listed under `initial_documents` in `manifest.json`. Set the listed document role and revision when uploading. Keep `drawing-E-10-rev-C.txt` for the revision step.

For the existing authenticated local practice team, run this with the API and worker running:

```sh
uv run python -m scripts.seed_connected_demo
```

The script prints the order URL. It reuses that team's example order and preserves reviews on reruns. It cannot seed a production deployment. No response, approval, completed task, recipient or sent message is fabricated.

## Review the results

1. Open the order's **Work queue** after processing finishes. Comment 07 has an explicit `CB-12` / `E-10` reference. Open its evidence and inspect the matching rationale. A source match does not mark the comment technically resolved.
2. Comment 08 mentions `MSB-2`, present on two sheets. Confirm the appropriate location yourself; the system must not silently select one. Comment 11 lacks a usable drawing reference and should remain unresolved, with a clarification draft for review.
3. Use the queue to review a proposed action, edit it if necessary, and accept or skip it. Inspect the activity entry and undo an eligible internal action. Later edits protect the record from stale undo.
4. Assign the PM, drafting, production and commercial owners in coordination settings. Review the resubmittal and release checks. A task or linked document is not evidence that an engineer has implemented a change.
5. Add `drawing-E-10-rev-C.txt` as a drawing at revision C. Compare it with revision B. The trip-unit reference changes from `TU-2500-DEMO` to `TU-3000-DEMO`; the production BOM still lists the earlier reference. Record only changes you have verified and keep production review open until someone checks the affected documents.
6. Use Rivet's sidebar to ask which items still need review. Answers should cite order evidence and never silently apply changes. Inspect Activity to follow recorded links, routing and drafts without leaving the order.

Weekly summaries and notices are drafts. Configure recipients and explicitly review a notice before sending. Nothing in this example approves an engineering decision or contacts another person.
