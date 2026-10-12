# Public-document validation

The original public-source validation below was performed locally on October 1–2, 2026. These are selected pages from actual public equipment submittals, not generated demo drawings. The October 11 regression scenarios use deliberately fictional generated files to exercise cases missing from those excerpts. Neither set establishes extraction accuracy across every customer package.

## Sources and results

| Primary publisher | Original package | Tested original PDF pages | Result |
| --- | --- | --- | --- |
| [City of Alachua](https://www.cityofalachua.com/DocumentCenter/View/1321/Addendum-Number-1-w-Attachments?bidId=) | Addendum with switchgear shop drawings, 193 pages | 33, 40, 45, 47, 48, 59, 67 | All 13 native review callouts preserved as separate reviewable records, with their original text, annotation author/date, page and bounding box. |
| [Antelope Valley Transit Authority](https://www.avta.com/downloads/meetings/bod/2018/042418-agenda.pdf) | April 24, 2018 board package with switchgear submittal reviews, 198 pages | 77–80 | Two image-only pages flagged for manual transcription, with direct page citations. Original page 79 contains handwritten review notes; they are not falsely reported as extracted text. |

PDF page numbers above are positions in the original file, not printed drawing sheet numbers. Excerpts have their own consecutive PDF pages. `.data/validation/manifest.json` records that mapping, publisher URLs, original sizes and SHA-256 hashes. Original PDFs and excerpts remain local and are ignored by Git.

## Defects found and corrected

- A numbered specification inside an annotated drawing was being treated as a reviewer comment. On pages with native annotations, the record reader now uses that review layer instead of underlying numbered drawing notes.
- Unreadable pages were flagged without an actionable source area. They now receive a persisted full-page citation and an explicit source page, so the PM can open the correct original immediately.
- Numbered comments using `1.)` are accepted. Continuation text stops at a page boundary instead of absorbing the next sheet's content.
- Oversized packages are rejected before persisting a failed import. Files are limited to 20 MB and PDFs to 150 pages. Both full public packages exceed the page limit; the AVTA file also exceeds the size limit. Split large packages into named sections.
- Comment labels such as A2 and A10 now sort numerically in the workspace.

## Question bar

A live question against the Alachua order asked about the PCR plan. Rivet cited the two applicable native annotations, distinguished their conditional wording from approval, identified missing responses and target drawing links, and kept the record unchanged. The browser citation opened the correct excerpt page and highlighted the original annotation area.

Question mode has an answer-only tool. Server-side validation rejects citations outside the supplied order context. Asking a question cannot close a comment, record an approval, change equipment, or send a message. The separate coordination agent can investigate evidence and stage internal work for review; that capability does not make a question an approval or a request to send.

## October 11 document scenarios

`tests/test_record_scenarios.py` exercises the real upload endpoints, durable parse worker, record projection, and Excel export. Files are generated during the tests; no customer files or model calls are needed. Assertions cover the following scenarios; live workspace checks and final run totals are recorded separately below.

| Scenario | Required behavior |
| --- | --- |
| 100-page native PDF | All 100 physical pages are processed, including the last. This tests a normal-size long package within the 150-page limit. It is a functional check, not a throughput benchmark. |
| Annotation on p. 2 referring to E-10 on p. 10 | The original annotation remains on p. 2. An exact automatic reference points to the actual E-10 text span on p. 10, retains automatic provenance, and does not mark the comment reviewed. Excel keeps source and target pages separate. |
| Comment 7 repeated in Rev B and Rev C | The two comments have separate document/source identities. A response recorded against Rev B is preserved and never copied as a resolution of Rev C. |
| Repeated upload | The same project, filename, bytes, kind, and intake metadata reuse the existing document without another parse job or comment set. A different explicit revision, another order, or another parent email retains its own provenance. |
| Ambiguous unstructured email | The plain-text body remains one unresolved record with its original wording. “Looks fine” is not inferred to mean approval, and unclear references stay unconfirmed. |
| CSV and XLSX | Source rows/cells remain inspectable. Equipment rows are not reviewer comments. A formula without a cached result is flagged rather than evaluated or assigned a guessed value. |
| Forwarded unsupported attachment | The email remains available internally, supported attachments are imported, and the omitted filename appears in source warnings. Attachments from separate parent messages remain separate. |
| PDF form fields and popup containers | Widget, Popup, and Link objects do not become standalone reviewer comments. The actual parent annotation retains its text and evidence. |
| Flattened, unnumbered marked-up PDF | If there is extractable text but no native annotation or recognizable numbered comment, the document displays a manual-review coverage warning. Rivet does not invent comments or claim there are no issues. |
| Native and image-only pages in one PDF | Coverage stays page-specific. The image-only page has a full-page source and an unreviewed transcription item. |
| Cropped and rotated pages | Text and annotation coordinates use the visible MediaBox/CropBox intersection, matching the PDF viewer. Fixtures check 0°, 90°, 180°, and 270°, with both zero and nonzero page origins against independently calculated coordinates. Unsupported geometry falls back to full-page manual review. |

`tests/test_record_shared_email.py` additionally checks that public email-source downloads contain only frozen, approved text excerpts. They must not expose raw MIME recipient headers or embedded attachments. Authenticated access to an original email is separate.

The alignment implementation is in `backend/ingestion/pdf_geometry.py`. Existing stored sources created before this normalization are not silently rewritten. The ignored `.data/qa/rivet-cropped-rotated-fictional.pdf` is a manual browser fixture: its pale square is the intended annotation area. It is not a customer document.

## October 11 end-to-end verification

The final local run passed **196 backend tests**, including the four public-document checks and 17 additional document scenarios, plus **8 frontend API-routing checks**. `npm run build` completed successfully. These are observed functional results, not a measured accuracy claim or a production readiness certification.

The signed-in browser checks exercised:

- Public Alachua annotations, exact source-page navigation after manually changing pages, PDF zoom, and excerpt-to-original page provenance.
- A real GPT-6 Luna investigation of comment 4, “See E-085.” The agent distinguished the annotation itself from evidence of a drawing sheet, prepared a specific request for the missing sheet/revision, cited the original page, and stopped with changes awaiting human review. Its task persisted across panel navigation and reload without starting another run.
- A separate fictional connected order, where an agent read drawing evidence and prepared a clarification; uploading Rev C made older proposals stale instead of silently applying them. Comparing Rev B/C, following a changed line, and returning preserved the selected pair.
- A fresh fictional order created through the UI, EML import, human response/review, an approval snapshot, recipient-view sharing, sanitized email-source download, and link revocation. Private MIME recipients were absent from the shared excerpt. No customer message was sent.
- Real authenticated XLSX and PDF exports; the worksheet contents and rendered PDF table were inspected.
- A cropped, rotated fictional PDF whose highlighted annotation matched the intended square in the actual PDF viewer.

These checks used the local API, worker, database, development authentication, and public or explicitly fictional files. Production API hosting, production authentication configuration, remote sharing, live forwarding, and live email delivery remain separate deployment checks.

## Repeat the checks

```sh
uv run --with pypdf python scripts/prepare_public_documents.py
RIVET_PUBLIC_DOCUMENTS=1 uv run pytest tests/test_public_documents.py -q -s
uv run pytest tests/test_record_scenarios.py tests/test_record_shared_email.py -q
RIVET_PUBLIC_DOCUMENTS=1 uv run pytest -q
```

The preparation script fails if a publisher changes an original file's expected hash; inspect the new original before updating the manifest. Tests run against the isolated `rivet_test` database, never the user's order database.

To inspect these sources with real authentication, run `uv run python -m scripts.setup_demo` with the API and worker running, then restart Rivet. The sign-in screen offers **Try the demo**. Setup creates a separate Clerk development user and organization and adds **Alachua · switchgear drawing review** and **AVTA · scanned review** through the normal upload service and durable parser. Re-running preserves edits and avoids duplicate imports. No existing tenant data is copied. Comments start open and unreviewed; responses entered during verification are explicitly labeled as demo tests. Publisher links and excerpt-to-original page mappings appear in the source viewer.

## Supported input and limits

| Input | Support and boundary |
| --- | --- |
| PDF | Up to 20 MB and 150 pages. Selectable text and native annotation objects are read. Image-only pages and empty graphical markups need manual transcription. An encrypted or invalid PDF is rejected. |
| CSV | UTF-8 text, including a byte-order mark. Rows and column values are preserved. The reader is not a general adapter for every vendor's comment-log columns. |
| XLSX | Sheet, row, cell, and formula references are preserved. Cached formula values may be read; Rivet does not calculate formulas. Missing caches require review. Workbooks over 80 MB expanded size and oversized sheets are rejected. Legacy XLS files are not supported. |
| TXT | UTF-8 text. Recognizable numbered comments retain source lines; arbitrary prose is not guaranteed to split into review items. |
| EML | A plain-text MIME body, metadata, and up to 20 supported attachments, within the 20 MB message limit. Unstructured body text stays one record. HTML-only bodies are explicitly unsupported; no HTML-to-text fallback is claimed. |
| Forwarded email | Requires configured Resend receiving access, domain, and verified webhook. Supported attachments follow the same parser. Unsupported files remain in the private original and produce visible warnings. Direct EML upload rejects unsupported attachments before import. |
| Other formats | MSG, DWG/CAD, ZIP archives, standalone JPG/PNG, and legacy XLS are not supported input formats. No mailbox/portal connector is implied by EML support. |

There is no OCR, handwriting interpretation, drawing geometry comparison, automatic engineering solution, or compliance certification. A page containing selectable header text and a raster drawing may have readable text without its image content being understood; the original must still be checked. Mixed native/image-only *pages* are covered by the scenario suite; this is not a claim of region-level scan detection on every hybrid page.

Native annotation author names and timestamps are preserved as stored in the PDF, including usernames; they are not verified identities. Exact reference links are coordination aids and still require PM review. Text extraction can be duplicated or poorly ordered, particularly in complex layouts; originals remain the authority. Extracted-text revision comparison cannot prove that drawing geometry changed or that a comment was resolved.

The tested record flow also covers saved EML intake and attachments, later revisions, original-text preservation, version conflicts, responses, changes, approval snapshots, token-scoped local sharing, revocation, email notice drafts and Excel/PDF exports. Clerk authentication and isolated teams are connected and demo sign-in has been exercised in the browser. Live inbox forwarding and email delivery still require receiving access, a domain, sender, and webhook configuration. Remotely accessible sharing requires hosting. Automatic email sending is deliberately excluded.
