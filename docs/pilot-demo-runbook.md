# Larson pilot demo — frozen rehearsal

Use **PDU-DEMO-261019-5 / Portable PDU · rehearsal 5**. All equipment, requirements, part numbers and customer messages are **fictional demo data**. This is a controlled demonstration of the existing product, not a claim of validated performance on Larson documents.

## Before the call

- Keep the local API, PostgreSQL and HTTPS workspace running. Use **https://localhost:5179**, signed into the owner workspace. The HTTP public demo session cannot see this order.
- Open the prepared order, its received Rev A email, its unsent corrected reply, and the local missed-change proof. The saved links and files are in `output/pilot-demo-20261019/README.md`.
- Keep the two-minute backup MP4 ready. It is an **edited, captioned walkthrough of actual product captures**, not a continuous recording or a timed automation benchmark.
- Do not reset the order or trigger new generation during the call. The two independent rehearsals are already saved as runs 5 and 6.

## Five-minute click path

| Step | Open / click | What to show and say |
| --- | --- | --- |
| 1 · Incoming work | Received Outlook email: **Job PDU-DEMO-261019-5 / Rev A returned comments / FICTIONAL DEMO** | Open the attached returned markup. “This is a fictional portable distribution unit: 75 kVA, 480 V to 208Y/120 V. The baseline drawing, native CAD, BOM and customer requirements were preloaded.” |
| 2 · Read the comments | Prepared order → **Drawing and comments** → revision filter **Rev A** → C4 transformer nameplate → **View source** | Show three typed comments and two handwritten-style notes. The handwritten source is on page 4. Confidence is a model estimate, not measured accuracy. Unclear graphical marks stay visible for review. |
| 3 · Check the actual revision | Open **rehearsal-5/missed-change-proof.pdf** | “Rev B fixes four requests but still labels CB-13 SPARE. The customer requirement says COMMISSIONING TOOLS.” The proof contains actual Rev A/B PDF crops; the revision check also reads the native drawing attributes and BOM. |
| 4 · Correct and verify | Return to the order → **View corrected files** | Show the saved DXF nameplate and Excel cell G7, both COMMISSIONING TOOLS. One supported mismatch became zero after reopening the saved files. Originals remain unchanged. This does not verify geometry, protection coordination, compliance or release. |
| 5 · Deliver the work | **Open corrected draft**, or the prepared Outlook reply tab | Show **[Draft]**, “This message hasn't been sent,” and the DXF, XLSX and PDF attachments. A person sends it. The PDF is a native review preview, not a certified reissue of the three-sheet source package. |
| 6 · Export the log | Order → **Order options** → **Downloads** → **Excel comment log** / **PDF comment log** | Both exports were downloaded and inspected. The record includes source locations, transcription confidence and unresolved marks. |
| 7 · Post-sales question | Order → **Ask Rivet about this order** | Ask: “What primary main breaker trip rating did we agree with the customer?” The saved answer is **CB-01, 125 A**, citing customer requirements R1, page 1, §2.1; Rev A showed 150 A trip on a 150 A frame. Open the citation. A fresh question calls the model; the answer and source capture are already in the backup. |

## What the two fresh rehearsals proved

| Measure | Run 5 | Run 6 |
| --- | --- | --- |
| Actual Microsoft 365 capture and order match | Rev A and Rev B | Rev A and Rev B |
| Expected comments found | 3/3 typed; 2/2 handwritten-style | 3/3 typed; 2/2 handwritten-style |
| Declared equipment/circuit identities observed | 7/7 | 7/7 |
| Before correction | 33 supported checks: 5 verified, 27 unchanged, 1 remaining | Same |
| After correction | 6 verified, 27 unchanged, 0 remaining, 0 unknown | Same |
| Real unsent, threaded Outlook reply | 3 attachments | 3 attachments |
| Cited post-sales answer | 125 A; dashboard UI checked | 125 A; conversation service checked |

These are bounded synthetic checks, **not real-document extraction accuracy**. “33 checks” does not establish complete equipment extraction. The log retains additional cover-email and graphical-placeholder records; finding all five expected comments does not mean perfect precision. One cloud currently produces two reviewable graphical placeholders. No production release was inferred.

## What was fixed

- Batching now preserves completed groups and stops later pages after a failed reader call. Ambiguous KAC text no longer silently becomes a kA rating.
- Unresolved governing-spec conflicts remain unverified instead of becoming definite correction failures.
- Valid XLSX files without an optional worksheet dimension element now stream within bounds instead of crashing.
- Reused sheet identifiers no longer override one exact job match; conflicting strong identifiers still require review.
- Parsed native DXF no longer blocks an otherwise source-text-grounded reply. Customer questions are included in retrieval, rather than losing them behind attachment boilerplate.
- Restored the existing PDF comment-log export in the download menu. No UI redesign or new autonomy was added.

## Boundaries and screens to avoid

- This was **controlled orchestration of existing services**. A global background worker was not started, so do not present it as an unattended end-to-end autopilot test.
- Email PDF/XLSX capture was real. Baseline CAD/specification and Rev B DXF were supplied locally through the existing import path. No Larson ERP or drafting vault connection is demonstrated.
- Corrected literal values came from the explicit fictional specification, not from automatically accepting handwritten instructions. Native CAD edits are supported attribute edits; geometry is unchanged. Manufacturer CAD auto-fixing is still unproven.
- The AI reply is conservative where evidence is absent. The final corrected-file handoff is a separate draft and does not contain the earlier AI reply's inline citations; source evidence remains in Rivet.
- Avoid **global Today**, earlier trial orders 1–4, **Requirements / Configure / generated Drawings / Release**, and model-application experiments. They are outside the rehearsed path. Do not click Approve, Generate, Release or Send during this walkthrough.
- Outlook sign-in and local networking can still interrupt a live session. Use the prepared reply tab or backup. Do not bypass a browser security warning.

## Reproduction and freeze

Final validation: **2,445 backend tests passed, 4 skipped; 196 frontend checks passed; public, workspace and Outlook builds passed.** Existing dependency deprecations and the workspace chunk-size warning remain nonblocking. Backend tests used an offline API key and did not consume credits. The recorded API allowance for the rehearsals is approximately **$0.71**, including a conservative $0.40 reservation for the dashboard question; this is not an invoice total and is within the authorized $3.

The fixture builders are in `scripts/pilot_demo/`. `rehearse.py` has `seed`, `message`, `capture`, `message-b`, `revision`; `finish.py` has `vision`, `reply`, `correct`, `export`, `question`. Each requires a new `--run` number. Seed uses the bundled artifact runtime; Microsoft phases require the existing authorized demo connection. Message phases create self-addressed fixture drafts; a person sends them in Outlook. These commands are operator tools, not a new product onboarding flow. Do not run a fresh rehearsal without a model budget and mailbox authorization.

Originals, result JSON, source hashes, exports, screenshots and final drafts are preserved locally under `output/pilot-demo-20261019/`; originals and generated outputs stay out of Git. Test/build logs and the API allowance ledger are under `.data/pilot-demo-20261019/`. The rollback branch is `pilot-demo-stable`.

**Freeze:** no further product changes unless something on this exact demo path breaks. Read `docs/pilot-security.md` before receiving confidential pilot data.
