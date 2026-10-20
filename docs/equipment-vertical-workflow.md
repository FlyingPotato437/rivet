# Equipment work: compile, correct, check, review

Rivet reads an order's controlled requirements, original native drawing and bill of materials. It derives an exact edit plan, changes new draft copies, independently checks the saved files, and places the corrected package in an unsent Outlook draft. Original files stay unchanged.

The running demonstration uses fictional equipment and invented part identifiers. It is not a claim of validation on a manufacturer's production packages.

## The executed loop

1. Ingestion saves drawing/BOM revisions and their original hashes. An equipment-enabled order gets a source-bound comparison and typed model version.
2. A ready, unambiguous literal correction schedules an `equipment_corrections` durable job. Missing native files, unreadable sources, conflicting requirements, missing devices, unsupported requests and impossible data constraints stop automatic preparation.
3. The worker applies source-derived values and native design changes in new copies. The declared CAD adapter now moves and resizes devices, reconnects and reroutes actual conductor paths, and adds or removes devices and wires. The equipment BOM follows explicit inventory changes; the saved DXF produces independently checked wiring and placement sheets. No arbitrary paths, edit selectors or replacement values come from the browser.
4. Rivet reopens the serialized DXF and workbook. A PDF preview is rendered from the saved DXF. The ordinary independent checker reads all three outputs and compares them against the baseline and explicit requirements. Both drawing and BOM must support the requested value; unrequested drift also blocks completion.
5. A new native draft layout and workbook review sheet identify the files as drafts requiring engineering approval. Originals are preserved. Only source-authorized modelspace changes are made; unrequested device geometry, terminals, incidence, native blocks and drawing annotations are checked for drift.
6. The Outlook handoff checks the current order, source hashes, file hashes and latest imported email before writing. For threaded replies it also reads the live Microsoft conversation before creation, each attachment and finalization, so a newer email that has not yet been ingested stops the handoff. It creates an unsent reply in an unambiguous existing thread, or an unaddressed new draft for a file-only order. Existing human drafts are preserved. Turning off automatic Outlook drafts still permits file preparation and an explicit handoff.
7. Microsoft-stored attachment bytes are downloaded and checked against the prepared output hashes before Rivet reports the package ready. Unknown provider outcomes are durably recorded; retrying cannot blindly create another draft or attachment.
8. The PM reviews and sends from Outlook. Background observation records a confirmed send and whether the actual sent attachments match the prepared files. This produces an outcome record, never engineering approval or a training accuracy label.

Returned native-change sources own their captured email's work. The automatic
text-reply path defers before generation and checks ownership again before
Outlook writes, including for already-ready generic replies. It cannot win a
race and leave a text-only draft in place of the checked files. The same gate
avoids redundant file-only response packets. Pending, supported and unsupported
native returns keep that ownership; an unrelated later status email on the same
order does not inherit it. Existing human drafts remain protected.

The event investigator also defers to native-owned returns instead of starting
a redundant language-model investigation. Its per-comment fan-out excludes only
the exact native request evidence; manual investigations retain all comments.
An earlier automatic investigation can be shown as superseded only when its
captured equipment fingerprint matches an independently verified native bundle
and every source/output hash is still current. The original run and failure
details remain in history. Manual failures, different packages and stale files
retain their attention state.

The dashboard shows the completed package first, its saved-file checks, and one Outlook action. Original issues remain labelled as original-source findings. The timeline and Activity retain evidence and history.

## Claude's twelve-part plan: actual implementation boundary

| Part | Implemented | Remaining depth |
| --- | --- | --- |
| 1. Equipment core | Typed per-device specified/requested/baseline/build observations plus a source-bound native graph of transformed terminals, conductor paths and measured bodies | Hidden device internals, assembly ownership and complete arbitrary manufacturer models |
| 2. Drawings to model | Actual DXF device bodies, terminal markers, wire polylines, explicit junctions and enclosure geometry; bounded visible-PDF outlines, labelled contacts, conductor networks and schematic revision differences; XLSX/CSV BOMs | General symbol/glyph recognition, cross-sheet connectivity, scans, DWG or EPLAN. Public drawings expose measured coverage gaps |
| 3. Reconciliation | Exact device identities, native endpoint incidence, drawing/BOM agreement, measured routing/layout differences; source-cited company-reviewed alias groups preserve original tags and block same-source collisions | Applying aliases throughout native edit compilation and all BOM selectors; reviewed complete production inventories |
| 4. Rules | Existing data/native geometry checks plus immutable company-reviewed equals, allowed-set, range, required-device, body-bound and actual terminal-pair constraints; pinned versions, revocation and source-currentness gates before Outlook | Jurisdiction/code interpretation, protection coordination, thermal/3D clearance and validated manufacturer production rule libraries |
| 5. Comments to changes | Cited change schedules compile into eight typed native operations: move, resize, rewire, reroute, add/remove device, add/remove wire | General ambiguous natural language, handwritten design intent and unspecified engineering choices |
| 6. Revision checking | Actual saved-file geometry/topology comparison, per-change source anchors, protected-entity drift checks and same-scale native view; original PDF page images with exact traced-ink overlays | Proprietary sheet formats, generalized schematic interpretation and independently established full-sheet coverage |
| 7. Model to outputs | Real edited DXF, regenerated drawing PDF, Excel equipment BOM and measured wire/layout schedules; independently checked unsent Outlook handoff | Proprietary manufacturer templates, AutoCAD Electrical/EPLAN authoring and gated commercial/release integrations |
| 8. Outcome learning | Package → request → native edit → independent recheck → file hashes → Outlook handoff → observed send outcome | Engineer-approved design/outcome labels and evaluated engineering prediction models |
| 9. Customer memory | Existing tenant-private approved context plus explicit company-reviewed rule profiles evaluated on actual saved CAD/BOM outputs | Automatic evidence-backed proposals from customer history; evaluated engineer-approved outcome learning |
| 10. Historical onboarding | Existing mail/document/SharePoint ingestion and deterministic compilation of supported packages | Reviewed twelve-month imports, identity mapping and manufacturer-specific conventions |
| 11. Product surface | Outcome-first Today and Order timeline; real native before/after evidence in the existing correction drawer; Outlook remains the delivery channel | Manufacturer usability testing on production workflows |
| 12. Simplification | Today, Orders, Activity and Settings remain primary; legacy quote/bid/catalog surfaces stay feature-flagged | Further removal after dependency and pilot review |

The remaining items require manufacturer-reviewed examples, native templates and domain validation. A successful fictional demonstration does not establish arbitrary electrical-design correctness.

## Company conventions and PDF source traces

Company conventions are proposed in Settings and reviewed as an entire immutable
profile by an authenticated team administrator. A proposal cannot supply its own
approval, reviewer or tenant. Each rule/alias cites a quoted passage on an owned
PDF page; the service checks registered and actual source hashes, revision,
active state, page and any supplied crop. Approval creates a separate version.
Revocation is append-only. An order explicitly pins one reviewed version.

The correction plan and prepared bundle include that binding's signature.
Before Outlook writes, Rivet reopens the saved DXF/BOM and evaluates applicable
company rules against their actual geometry, terminal incidence and cited field
observations. Missing applicability, conflicting facts, failed rules, withdrawn
guides and revoked profiles block handoff. Explicitly inapplicable rules do not
become approvals or block unrelated work. A binding change queues a fresh plan;
superseded background plans stop without writing mail. Existing drafts remain
protected. This is company review, not professional engineering certification.

The additional labelled fictional order `RVT-NATIVE-261018-16` demonstrates a
relay relocation, retained terminal pair, explicit device alias and two passing
company rules. The normal worker prepared its files and saved a ninth unsent
Outlook draft. Reproduce with `scripts/seed_company_rules_demo.py`; an existing
demo order is left unchanged. Its guide and review are explicitly fictional.

`model_service.py` incorporates bounded vector PDF reports into the typed
equipment projection. PDF coordinates remain page points, never millimetres.
The existing equipment drawer has one collapsed Drawing evidence view for
original pages, source/page selection and source-bound conductor highlighting.
Unsupported symbols, hidden labels, missing pins, outlined glyphs and incomplete
coverage stay unknown. The independent corpus, real primary-source PDFs and
measured limitations are documented in [pdf-drawing-corpus.md](pdf-drawing-corpus.md).

## Local operation and verification

The normal worker handles correction jobs alongside the existing ingestion lanes. For isolated local work, `.venv/bin/python -m backend.worker --lane equipment` runs correction jobs and known-draft outcome reconciliation without activating unrelated mail or model jobs.

`scripts/seed_equipment_correction_demo.py --organization-id …` creates one isolated, explicitly fictional correctable order. It does not send messages or approve/release equipment.

Key implementation: `backend/equipment/{model,rules,corrections,workflow,outlook}.py`. Routes live in `backend/equipment/api.py`. The current typed model is available at `GET /api/orders/{id}/equipment/model`; correction plans, artifacts and Outlook handoff live under `/api/orders/{id}/equipment/corrections`.

Pure file/model tests are in `tests/test_equipment_corrections.py` and `tests/test_equipment_model.py`. Authenticated workflow and retry tests are in `tests/test_equipment_workflow.py`; native Graph placement/outcome tests are in `tests/test_equipment_outlook.py`. Database suites must use an isolated `rivet_test_*` database.


## Native design adapter and scenario evidence (2026-10-18)

`design_requests.py` reads exact cited change instructions from returned PDFs.
`native_model.py` reconstructs actual transformed terminal coordinates, body
polygons, conductors and explicit junction incidence. Identity metadata names
objects; it does not prove that conductor endpoints touch the claimed terminals.
`native_edits.py` searches bounded orthogonal routes, applies native changes, then
reopens the serialized drawing and checks the full requested result and untouched
entities. Crossing wires without junctions remain electrically separate. Hidden
continuity through relays or terminal-strip jumpers is not invented.

`native_bom.py` reconciles actual device additions/removals against exact BOM
rows. Additional metadata can come only from an unambiguous existing same-part
template or an explicit native attribute. `native_schedules.py` writes and rereads
wire IDs, endpoints, net identities, measured centreline lengths, coordinates and
dimensions from the actual saved DXF. CSV sources become a new Excel draft when
the native workflow needs these schedules; the original CSV is retained.

The labelled-fictional corpus is `output/native-revision-scenarios/manifest.json`:
14 scenarios and 119 source files. Seven exercise actual rewiring, relocation,
addition with new conductors, removal, combined changes, resizing and rerouting.
Seven cover collisions, contradictory changes, missing terminals, stale BOMs,
out-of-bounds positions, deletion with live wires and endpoint/geometry disagreement.
Expected native drawings, BOMs and graphs are authored independently of edit code.
Manufacturer reference material and its limited use are recorded in the manifest.
This is a reproducible engineering workflow test, not production/customer validation.

## Live proof and repeat revisions

The October 18 live run created eight separate **unsent, unaddressed** drafts in
the authorized demo Microsoft 365 mailbox. Each contains the actual edited DXF,
Excel BOM and generated PDF. The read-only reconciliation report is
`output/native-revision-scenarios/review-ready/live-outlook-audit.json`.
It records the eight order IDs, actual Outlook links, independent saved-file
checks and verification of all 24 Microsoft-stored attachment hashes. It does
not record an engineering approval or a customer send.

The real browser check opened the protected checked PDF and the corresponding
Outlook draft, including its editable subject/body and all three attachments.
The dashboard comparison renders the saved native graph at a common scale.
The desktop screenshots are in `output/native-revision-scenarios/`.

`tests/test_native_sequential.py` runs two full corrections sequentially. The
second revision consumes the first revision's actual saved DXF and workbook,
retains its earlier rewiring and all historical worksheet cells, applies the new
relay movement and independently checks the new routes and schedules against
the new saved drawing hash. No predicted graph is substituted for the prior
output files.

The eighth live order (`RVT-NATIVE-261018-15`) consumes the actual first
rewiring package, retains those wire connections, moves and resizes the relay,
and saves the follow-up files in a real unsent Outlook draft. Its PDF has one
full vector drawing sheet and a separate labelled equipment schedule. Setup is
reproducible with `scripts/seed_native_followup_demo.py`; it is a no-upload
no-op after that labelled fictional order exists.

`tests/test_native_mail_intake.py` exercises ordinary EML import and real parse
jobs, without assigning attachment roles or starting the correction task. It
verifies that returned change schedules (including arbitrary attachment names)
queue the native job, change actual saved wire endpoints and create an unsent
threaded reply with matching attachment bytes. Unsupported instructions remain
classified as requested work and explicitly block automatic correction.

Repeated dashboard reads may cache pure parsing and comparison results only
after rechecking current source and artifact byte hashes. A changed file,
registration or plan invalidates the cache; cached success never authorizes a
different package.
