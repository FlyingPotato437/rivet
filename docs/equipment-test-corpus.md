# Equipment revision test corpus

The generated corpus exercises changes in actual drawing equipment data and real BOM workbooks. Every project, part identifier and drawing is explicitly fictional. These files are functional test fixtures, not customer validation, engineering approval, or a claim about extraction accuracy on arbitrary packages.

Generate with `uv run python -m scripts.build_equipment_revision_demo`. The files are written to `output/equipment-revision-demo`. Tests may call `backend.equipment.demo_documents.build_demo_corpus(tmp_path)`; this has no database, order, mailbox or model dependency.

## The package

The 17 x 11 inch vector PDF one-line contains an illustrative MSB-1 bus, three schematic breaker branches and per-device data. Native DXFs contain corresponding INSERT blocks and explicit equipment attributes. Each revision has a real XLSX BOM and, unless testing an unknown formula, a CSV equivalent. The requirement schedule is a separate controlled source. All sheets say that they are fictional and not for fabrication.

The baseline lists CB-12, CB-14 and CB-16. The required change replaces CB-12's fictional trip-unit identifier TU-2500 with TU-3000 and changes its nameplate to DATA HALL A. It does not authorize deleting CB-14 or changing another device. TU-* and RVT-* identifiers are invented and imply no real vendor compatibility.

| Scenario | Independent expected result |
| --- | --- |
| C to partial D | The PDF and DXF show CB-12's new trip unit and nameplate, but the BOM still specifies TU-2500. CB-14 disappears from the drawing while remaining in the BOM. An explicit “all corrected” review response must not hide either failure. |
| C to complete E | Both actual drawing and BOM contain the declared required values; all baseline devices remain. This passes the narrow data check, not an engineering or production release. |
| Native DXF pairs | The same partial and complete outcomes must be observed from actual block attributes. |
| Title-block-only revision | Revision letters and filenames change while equipment data stays identical. The requested changes remain unmet. |
| Comments-only claim | A response document contains desired values and says everything was fixed. Unchanged drawing/BOM data must still fail. |
| Conflicting repeated tag | CB-12 appears twice with incompatible trip-unit values. The checker must report ambiguity rather than choose one. |
| Uncached formula | The XLSX trip-unit cell is a real formula without a cached result. It is unknown; the checker must not evaluate or guess it. |
| Image-only drawing | A deliberately low-resolution rasterized PDF has no embedded text. Coverage must be unknown and the package must not pass. |
| Unit normalization | 2.5 kA in a drawing's frame field equals 2500 A in the BOM. It must not produce a spurious change. |
| Unrequested change | CB-16 changes from 120 V to 230 V in both drawing and BOM. Cross-document agreement alone must not hide this drift. |
| Unapproved tag rename | CB-14 becomes CB-114 without an alias decision. The checker must preserve the deleted and added identities instead of silently matching them. |
| CSV equivalent | Complete E must also pass when the BOM is supplied as CSV. |

Expected values and outcomes are hand-declared in the generator and serialized in `manifest.json`. They are not obtained by running the extractor on its own output. The manifest supplies relative file paths, source roles, revision identity, hashes, baseline/target pairs and requirement sources. The consumer must resolve paths against the corpus root.

## Real-world references and limits

General drawing presentation follows conventions visible in the existing public validation corpus: title blocks, approval status, revision identification, one-line branches and equipment callouts. Sources are the [City of Alachua switchgear addendum](https://www.cityofalachua.com/DocumentCenter/View/1321/Addendum-Number-1-w-Attachments?bidId=) and [AVTA April 2018 board package](https://www.avta.com/downloads/meetings/bod/2018/042418-agenda.pdf). No publisher drawings, logos or engineering values are reproduced in the generated package. Their original file hashes and excerpt page mappings are in `.data/validation/manifest.json`.

The known limitations are deliberate: schematic geometry is illustrative, and the corpus does not establish connectivity correctness, protection coordination, physical clearances, standards compliance, billability, or fitness for fabrication. Native CAD attributes and legible PDF text are the tested evidence. A future production benchmark needs manufacturer-reviewed inventories, requirements and paired revisions kept separate from development examples.
