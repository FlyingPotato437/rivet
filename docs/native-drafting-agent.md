# Native drafting through the ordinary email agent

`backend/records/cad_preparation.py` connects native draft preparation to the
regular record agent. The event worker can read an incoming returned PDF,
inspect registered native drawing/BOM files, prepare actual copies and include
their exact change/task references in a lifecycle revision-review package.
This does not need a special replay-only CAD call or a new dashboard form.

## Executed workflow

1. Existing email intake retains the EML and parses the returned PDF annotations
   into evidence-linked comments on the order.
2. `inspect_order_cad` reads the actual current DXF block handles, equipment tags,
   supported attribute values and BOM rows. It returns their source IDs, hashes
   and a strict preparation schema.
3. `prepare_order_cad` accepts owned document IDs and exact hashes. Each edit
   cites a read, current returned comment. Its replacement literal must actually
   occur in that comment. Paths, approval status and edit allowlists are chosen
   by the server, never by the model.
4. The existing native-file adapter creates new DXF/XLSX or CSV copies, reopens
   and checks them, and writes before/after views plus a hash manifest. Originals
   are preserved. A layout request becomes an existing-entity task with a real
   anchor and bounds; no geometry is moved.
5. The saved CAD result returns `comment_plans` that the normal
   `prepare_lifecycle_work` tool can use in `revision_review`. Every field edit
   references a specific actual change ID; every layout task references its
   actual task ID. Preparing the package does not resolve those comments.

The event is append-only. Repeating the same request reuses its current bundle.
Changed native bytes, metadata, comment evidence or output hashes withhold stale
bundles. Native file paths must resolve inside the order's server blob store,
including when a symlink happens to contain identical bytes.

## Boundaries

- Native files must already be registered to the authorized order, such as by
  an authorized vault source. General email/upload ingestion currently does not
  accept DXF as an arbitrary attachment. PDFs are evidence, not editable CAD.
- Supported CAD fields are `TRIP_UNIT` and `NAMEPLATE` on an exact unique
  `INSERT` equipment block. Supported BOM literal fields are `part_number`,
  `description` and `quantity`. The native adapter also checks the exact row,
  expected old value, formula restrictions and unique field identity.
- This version requires both a registered native drawing and structured BOM,
  and at least one field edit. A standalone layout-only bundle is not exposed.
- The native source and target revisions must be distinct. Files have bounded
  sizes and spreadsheet expansion/dimensions; malformed inputs become explicit
  blockers.
- An explicit customer value may be staged in a draft; it does not establish
  technical correctness or resolve a conflicting governing specification.
  Engineering and commercial gates remain separate. No release, publication,
  geometry mutation, external vault write or customer-send authority is added.
- The preparation agent can also prepare revision checks, release packages and
  closeout packages. Existing actual-resolution, exact human-approval and
  released-file prerequisites still apply; preparing a release package never
  itself releases production.

## Verification

Run in a disposable, dedicated test database; the fixture recreates its tables:

```sh
RIVET_TEST_DATABASE_URL=postgresql+psycopg://localhost:55432/rivet_test_actions_security \
  .venv/bin/pytest -q tests/test_cad_agent.py tests/test_drafting_revision.py tests/test_lifecycle_agent.py
```

The integration case imports a real EML and annotated PDF, uses the ordinary
scheduled event job and tool dispatcher, verifies three edits in the serialized
DXF/XLSX bytes, verifies unchanged source files and geometry, and stages a real
revision-review package with open comments and no decisions. Provider choices
are scripted in this test; no live model was used and it is not proof of general
autonomous drawing interpretation. Separate cases reject invented values,
unread evidence, approval/geometry fields, stale hashes, another order's native
source and an outside symlink; reuse/currentness and malformed workbook cases
are also covered.
