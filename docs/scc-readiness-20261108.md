# SCC evaluation readiness — 8 November 2026

This is an internal readiness record, not a customer security attestation. The first SCC package is a founder-operated technical evaluation. It is not a signed paid pilot or a production deployment.

## Sendable materials

- `output/pdf/Rivet-SCC-Data-Handling.pdf`: two-page description of the verified local environment, operator access, optional OpenAI API processing, proposed retention, and remaining IT review.
- `output/pdf/Rivet-SCC-Package-Checklist.pdf`: one-page request for marked PDF, matching baseline CAD/PDF, BOM if available, and optional held-out engineer revision.
- Editable sources: `docs/scc-evaluation-data-handling.md` and `docs/scc-package-checklist.md`.

Both PDFs were rendered and visually inspected. The email and files have not been sent to SCC. Recent messages checked in the connected Berkeley mailbox did not contain the new package; this does not establish whether another recipient has received it.

## Changes completed

### Private evaluation intake

Uploads reject ambiguous duplicate metadata keys and path-like metadata filenames. Intake records external AI authorization separately from redaction, including the authorization reference, operator, timestamp, and hashes of the exact source set. Uploading starts no model processing.

The private first-day runner rejects fresh remote reading without that source-bound receipt. Changing or adding a source requires new authorization. It also rejects incompatible fresh-vision and saved-prediction options before any engineering reader starts.

This control covers the private pilot intake and `scripts/pilot_first24.py` route. It is not a global authorization retrofit for every legacy workspace or mailbox-processing route. Use the private route for SCC and do not import its package into an automatic mailbox workflow during this evaluation.

### CAD preservation and review coverage

Supported paper-space text and geometry are captured separately from model-space edit targets, including paper-space BLOCKS definitions. Saved-file checks detect unplanned changes to those supported entities and extraction coverage. Unsupported or unlocated layouts make the correction package need information and prevent approval.

The supported correction path remains ASCII DXF with bounded text edits and selected planar geometry. DWG conversion, binary DXF and native EPLAN editing are not configured. The corrected DXF preserves the original structure outside approved edits; the PDF change report is not a complete plotted multi-layout drawing set. Do not present it as one.

The correction engine fingerprint changes with this implementation. Existing saved CAD runs may require a fresh check before approval; do not override stale-run protection to reuse an older demonstration.

### Comment-and-response deliverables

Excel and PDF show response status separately from recorded workflow status. AI-proposed wording does not turn a comment into an engineer-approved resolution. Long PDF comments and responses continue in bounded rows with repeated source, revision and status. Excel retains one row per comment. Long response letters can paginate without dropping their final text.

### Existing Outlook draft reconciliation

A repeated background check could fall through to generic reply preparation after an approved lifecycle draft was already placed in Outlook. Reconciliation now recognizes that existing draft, revalidates its approved sources, and reports it ready without creating another reply. Stale approvals remain blocked and newer customer mail supersedes the proposal. Regression checks use a fake mailbox with no sending operation; no live Outlook delivery was performed in this pass.

### Manufacturer rules

Existing customer profiles were audited and tested: source-cited PDF constraints and aliases are explicitly attached to orders, scoped to the workspace, and revalidated for current evidence and revocation. These rules do not provide automatic drawing-style learning or native EPLAN editing.

## Public-file regression evidence

These are reused public packages with preliminary keys, not unseen manufacturer holdouts. No paid model calls were made. Native comment parsing was exercised with vector inspection and remote vision disabled.

| Package | Pages read by parser | Native comments found / key | False positives | Strict transcription | Time |
| --- | ---: | ---: | ---: | ---: | ---: |
| Alachua/Powell | 71 | 13/13 | 0 | 13/13 | 30.23 s |
| AVTA scanned package | 198 | 0/2 | 0 | 0/2 | 18.92 s |

AVTA needs image reading; this run supplies no handwriting accuracy claim. Neither row measures device extraction, engineering changes, or drawing correctness. Local reports are under `.data/scc-readiness-20261108/public-document-check/`.

## First SCC run

1. Agree authorized contact, NDA if required, transfer method, operator access, AI processing and retention. Use an SCC-approved named-recipient channel; no hosted SCC intake link has been verified.
2. Preserve originals and record revisions, file roles and hashes. Request ASCII DXF alongside DWG. Record missing fonts, external references, proxy objects, absent BOM and EPLAN dependencies.
3. Keep the engineer-completed revision separate if it will be the answer key. Do not use it as an input and then claim a blind redesign result.
4. Create the private intake receipt. Keep external AI permission off until SCC has approved the stated processing. Verify the API project's data-sharing settings before relying on the provider's default no-training policy. A budget allowance from Rivet's founder is not customer data-processing permission.
5. Run local inspection first. Stop for unsupported layouts or uncertain evidence; do not infer missing ratings, parts or engineering approval.
6. With permission and a separately approved spend cap, run remote reading where needed. Score comment location/transcription separately from device/BOM extraction and implemented changes.
7. Inspect originals versus proposed copies and saved-file checks. Export the complete response log with outstanding items visible. Have SCC's engineer judge the result before any production use.
8. Confirm video timing after inspecting the package. Show successful edits and unresolved items in the same walkthrough; keep email drafts unsent.

## Remaining gates

- Actual SCC documents and engineer answer key are needed to measure real accuracy and fit its drawing standards.
- Native DWG/EPLAN editing and a full plotted multi-sheet CAD output remain unsupported in the current correction path.
- Handwriting, dense drawings and arbitrary engineering changes are not certified by software unit tests.
- SCC IT must approve hosting, identities, mailbox/site restrictions, access, backups, deletion and incident handling before connected production use. Current verified evaluation runs on a FileVault-enabled founder Mac.
- No customer email, live package processing, paid API run or production deployment was performed in this hardening pass.

## Verification

Working branch: `scc-pilot-readiness-20261108`. Test logs and visual QA are retained locally under `.data/scc-readiness-20261108/`.

- Frontend: 209 checks passed, none failed.
- Workspace and Outlook builds: both passed. The workspace build retains a non-blocking bundle-size warning.
- Focused private intake and runner tests: 34 passed.
- Focused manufacturer-profile tests: 52 passed.
- Focused CAD snapshot, preservation and redesign tests: 38 passed.
- Focused comment/export tests: 16 passed, including the moderate unbroken-text regression.
- Lifecycle Outlook module: 24 passed, including repeat reconciliation and stale approval.
- Ruff on all changed Python files and `git diff --check`: passed.
- Synthetic visual export QA: 25 comment records across mixed revisions, a 21-page PDF and a one-row-per-comment XLSX. Representative continuation, uncertain-transcription, first and last pages were visually inspected. This is layout evidence, not a real-manufacturer accuracy score.

Final full backend rerun: **2,677 passed, 4 skipped, 0 failed in 1,092.27 seconds (18 minutes 12 seconds)**. The four skips are the explicitly opt-in `tests/test_public_documents.py` group (`RIVET_PUBLIC_DOCUMENTS` was not enabled); they are not counted as passed. The separate current public-package harness results are reported above. The final run includes the previously failing large-format OCR and lifecycle reconciliation tests. Local Python 3.12 was used; the production container still requires its own build and verification.

Results: `.data/scc-readiness-20261108/backend-verified.log`, `backend-verified.xml`, and `verification-summary.json`. No source originals or generated customer material are committed with this change.

The local API was restarted on `127.0.0.1:8787` with the updated code. Its health check returned `ok` with Clerk authentication; unsigned requests to the private checklist and a package manifest returned HTTP 401. The HTTPS Vite app remains on port 5179. No background worker was restarted, no inbox sync was requested, and no hosted deployment was changed.

The initial full backend run had 2,645 passing tests, 9 skipped tests and 2 failures. One exposed the Outlook reconciliation defect fixed above. The other was a 36-by-24-inch synthetic OCR table yielding no rows. Investigation proved that the restricted non-login launch environment omitted `/opt/homebrew/bin`, so `shutil.which("tesseract")` returned `None` despite the binary being installed. Passing isolated runs used a login shell with that path. An intermediate rerun was stopped after confirming the same environmental defect; both logs were retained.

The final test runner and local API launch explicitly include the verified Homebrew paths. No OCR confidence threshold, render bound or production timeout was relaxed, and no extraction-code workaround was added. A missing OCR tool still produces explicit unknown coverage. Production provisioning must install Tesseract and verify it is discoverable in the actual API/worker environment, not only in an interactive shell.

The production Dockerfile previously omitted Tesseract; it now installs `tesseract-ocr` and its English data. The container image was not rebuilt: the installed Docker client could not connect because the local Docker daemon socket was absent. The dependency edit still needs a real image build and smoke check before deployment. This is not evidence of an SCC production deployment.
