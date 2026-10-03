# Public-document validation

Validated locally on October 1–2, 2026. These are selected pages from actual public equipment submittals, not generated demo drawings. The test results apply to these excerpts; they are not a claim of accuracy across all packages.

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

The prior compact question-bar UI, Rivet icon, keyboard shortcut, and prompt suggestions are retained. The assistant has an answer-only tool. Server-side validation rejects citations outside the supplied order context. Asking a question cannot close a comment, record an approval, change equipment, or send a message.

## Repeat the checks

```sh
uv run --with pypdf python scripts/prepare_public_documents.py
RIVET_PUBLIC_DOCUMENTS=1 uv run pytest tests/test_public_documents.py -q -s
RIVET_PUBLIC_DOCUMENTS=1 uv run pytest -q
```

The preparation script fails if a publisher changes an original file's expected hash; inspect the new original before updating the manifest. Tests run against the isolated `rivet_test` database, never the user's order database.

To inspect these sources with real authentication, run `uv run python -m scripts.setup_demo` with the API and worker running, then restart Rivet. The sign-in screen offers **Try the demo**. Setup creates a separate Clerk development user and organization and adds **Alachua · switchgear drawing review** and **AVTA · scanned review** through the normal upload service and durable parser. Re-running preserves edits and avoids duplicate imports. No existing tenant data is copied. Comments start open and unreviewed; responses entered during verification are explicitly labeled as demo tests. Publisher links and excerpt-to-original page mappings appear in the source viewer.

## Current limits

Handwriting, flattened markups, scan text and drawing geometry still require human interpretation. Native annotation author names are preserved as stored in the PDF, including usernames; they are not verified identities. Explicit drawing targets are confirmed by the PM. Unnumbered flattened review prose is not guaranteed to split into separate comments. Text extraction may contain duplicated or poorly ordered text layers, so originals remain the authority.

The tested record flow also covers saved EML intake and attachments, later revisions, original-text preservation, version conflicts, responses, changes, approval snapshots, token-scoped local sharing, revocation, email notice drafts and Excel/PDF exports. Clerk authentication and isolated teams are connected and demo sign-in has been exercised in the browser. Live inbox forwarding and email delivery still require receiving access, a domain, sender, and webhook configuration. Remotely accessible sharing requires hosting. Automatic email sending is deliberately excluded.
