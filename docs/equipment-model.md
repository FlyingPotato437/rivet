# Drawing and BOM revision checks

Rivet now compares equipment data in original drawings and bills of materials. A comment marked resolved, a response saying “fixed”, or a revised filename cannot establish that equipment changed.

## One order, one check

Open **Drawing and BOM check** in the order timeline. The entry shows verified changes, remaining changes, unverified evidence and unchanged fields. The drawer compares the baseline drawing and BOM with the revised drawing and BOM and the explicit requirement. Expand a finding to see the original PDF location, workbook cell or native DXF block attribute.

Upload or register the originals with a role and revision. Existing order documents with drawing/BOM roles and revision metadata are already available; they do not need to be uploaded again. Normal document ingestion rechecks equipment-enabled orders when a new package arrives. The check runs against the original bytes without requiring an LLM or a separate agent task.

The background record agent receives a small saved-check hint and can call the read-only `inspect_equipment_revision` tool for current, paginated observations. It must inspect this before drawing “fixed” or revision-completion conclusions. Source-span pointers remain distinct from equipment observation and document IDs; source reads are still required before staging cited work.

Explicit historical comparisons stay selected. A newly uploaded revision advances automatic checking. Historical comparisons never authorize release of a newer package.

## Equipment model

Each observation records an explicit device tag, field, normalized value, unit, source role and revision. Its evidence includes the document ID and SHA-256 plus a PDF page/bounding box, spreadsheet sheet/row/cell, or CAD entity handle. These observations form an order-scoped model; different files retain separate observations rather than overwriting each other.

Supported fields: frame current, trip current, trip-unit identifier, part number, quantity, nameplate text, control voltage and interrupting rating. Readers support selectable-text PDFs, native DXF INSERT attributes, and XLSX/CSV BOMs. Numeric units are normalized only where explicit and compatible. Formula cells are not evaluated. Image-only scans and unsupported fields remain unverified.

Requirements and explicit requested changes are kept separate from observed values. Both the actual revised drawing and BOM must support a requested change. The checker also flags missing baseline devices, conflicting tags, unrequested edits and disagreement between drawing and BOM. Unchanged fields remain neutral. No fuzzy tag aliases are accepted.

The overall result is bounded to these supported properties. It does not certify a complete inventory, wiring connectivity, geometry, clearances, protection coordination, regulatory compliance or suitability for fabrication.

## Audit and release

Checks are saved as append-only order events and revisions with their exact input fingerprint and source manifest. That fingerprint includes source roles, revision selection, expected and actual hashes, and the checker schema version. The manifest retains the selected source IDs even when more files are later uploaded to the same revision. Modifying bytes or active requirements invalidates the prior result. An original already registered in a package cannot be reassigned to another role or revision; upload a separate copy to retain history.

Explicitly inactive or superseded specifications remain in the available source list but no longer govern the comparison. Historical drawing and BOM originals stay available for baseline checks.

For equipment-enabled orders, the existing release gate also requires a current saved comparison for the latest package, a readable before/after baseline, explicit requirements, and no remaining or unverified supported checks. Earlier ledger waivers cannot bypass this gate. Passing this comparison never supplies engineering approval or releases an order by itself.

## Reproducible test and demonstration

`backend/equipment/demo_documents.py` generates the independent fictional originals described in [equipment-test-corpus.md](equipment-test-corpus.md): 34 PDF, native DXF, XLSX and CSV files across 12 scenarios. Expected outcomes are declared independently of extraction.

Generate the corpus from the repository root:

```sh
.venv/bin/python -m scripts.build_equipment_revision_demo
```

An explicitly selected local team can receive one isolated fictional order using `scripts.seed_equipment_revision_demo`. It preserves existing orders and does not send email or approve/release equipment. The seeded C→D comparison detects a stale trip unit in the BOM and a breaker absent from the revised drawing, despite an “all corrected” response. C→E verifies both requested edits against actual drawing and BOM values.

Targeted regression suites are `tests/test_equipment_engine.py`, `tests/test_equipment_api.py`, `tests/test_equipment_registration.py` and `tests/frontend-equipment.test.mjs`. Run database tests with `RIVET_TEST_DATABASE_URL` pointing to an isolated `rivet_test_*` database; the fixture rebuilds its tables.

These fictional scenarios demonstrate reproducible behavior, not measured accuracy on customer packages. Manufacturer-reviewed paired revisions and independent inventories are still required for a production accuracy benchmark.
