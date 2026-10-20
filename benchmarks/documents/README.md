# Public document benchmark

35 original public submittal/review packages, 691 physical pages. Includes
electrical switchboard/switchgear packages and other MEP shop drawings. These
are public evaluation sources, not Rivet customers or pilot traction.

`sources.json` pins each publisher URL, original-byte SHA-256, page count and
provenance. Originals are downloaded into ignored local storage, not redistributed
in Git. A publisher changing its file causes a hash failure and requires review.

Run `uv run python -m scripts.benchmark_public_documents --fetch`.
The same check runs in CI on pushes and pull requests.

The real PDF ingestion function is exercised against each complete package.
An independent PyMuPDF read provides a native annotation-text oracle. The gate
requires every native annotation text entry to survive with a page and usable
rectangle, every physical page to have a coverage entry, and every image-only
page to carry an explicit limitation. It exposed cyclic annotation references
and malformed PDF text encodings; ingestion now falls back to an independent
annotation parser for those cases.

Latest run: **1,753 / 1,753 native annotation text entries retained**, no invalid
anchors and no image-only pages silently accepted. Entries include stamps and
graphical labels; this is not a claim that 1,753 actionable comments were found.

## Vision smoke check

A live check used AVTA's original physical page 79 (handwritten switchgear
review) and Paraje page 3 (unmarked scanned product data). It recovered both
readable electrical notes on the AVTA page, located them on the page, flagged
the ambiguous reviewer name, and found no invented review comment on the
negative control. The small reference set is in `vision-gold.json`.

This is a two-page smoke check, not a broad accuracy estimate. Vision reads the
review-markup layer; it does not verify engineering or certify complete OCR of
the underlying drawing. Model confidence remains a model estimate.

## Remaining pilot measurements

The report deliberately leaves overall visual-comment recall, answer accuracy
and PM edits as null. They require independently labelled pages, source-backed
expected answers, and actual reviewed/sent responses. Add blinded, permissioned
pilot material when available. Keep public originals, generated stress variants
and private customer packages in separate provenance groups; never count a
rendered copy as another independent real package.
