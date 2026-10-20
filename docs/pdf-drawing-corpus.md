# Drawing PDF evaluation corpus

This corpus separates real public reference drawings from controlled fictional
circuits. Neither group is a customer order or an approved manufacturing design.

## Reproduce

From the repository root:

```sh
PYTHONPATH=. .venv/bin/python scripts/build_pdf_drawing_corpus.py --download
PYTHONPATH=. .venv/bin/python scripts/evaluate_pdf_drawing_corpus.py
.venv/bin/pytest --noconftest -q tests/test_pdf_source_corpus.py
```

The first command downloads missing official originals. Omit `--download` to
require existing local copies and avoid network access. The evaluation uses no
database, language model, mailbox, or hidden PDF equipment metadata.

Outputs are under `output/public-drawing-corpus/`:

| Location | Content |
| --- | --- |
| `manifest.json` | Official URLs, publisher, original SHA-256, page statistics, manual observations and annotation limits |
| `originals/` | Unmodified official PDFs |
| `evidence/` | Rendered pages and higher resolution evidence crops |
| `independent/` | Ten visible-ink circuit PDFs and separately authored expected results |
| `selected-pages/` | Exact selected pages copied from official originals for bounded evaluation; the originals stay untouched |
| `evaluations/*.report.json` | Actual adapter observations and source anchors |
| `evaluations/summary.json` | Tag, terminal and terminal-net metrics against independent truth |

## Official sources

Retrieved on October 18, 2026 from primary publishers:

| Publisher | Reference | Annotated scope |
| --- | --- | --- |
| Minnesota Department of Transportation | [Generator and transfer-switch wiring](https://edocs-public.dot.state.mn.us/edocs_public/DMResultSet/download?docId=19625901) | Visible terminal/assembly observations; curved conduit is not assumed to be an electrical net |
| Minnesota Department of Transportation | [E-ZPass control wiring](https://edocs-public.dot.state.mn.us/edocs_public/DMResultSet/download?docId=19625887) | Repeated relay types and terminal labels; repeated type labels do not establish unique device tags |
| Minnesota Department of Transportation | [RICWS two-lane sample plans](https://www.dot.state.mn.us/trafficeng/signals/plansheets/ricws2lane.PDF) | Sheet 11 visible cabinet/handhole tags; multiconductor cable routes do not establish individual wire connectivity |
| Schweitzer Engineering Laboratories | [SEL-121F data sheet](https://selinc.com/api/download/2836/) | Page 19, Figure 10: visible coils and six terminals on the direct upper rail; other circuitry remains unannotated |
| Schweitzer Engineering Laboratories | [SEL-251 data sheet](https://selinc.com/api/download/3067) | Page 21 visible trip/close coil labels; the diagram exports labels as outlines rather than selectable text |

The MnDOT [official ITS drawing index](https://www.dot.state.mn.us/freeway-operations/its.html)
provides the drawing context. Publisher copyrights remain in force. Public
availability is not treated as an open redistribution licence; copies here are
research references, not Rivet-owned or customer-provided data.

## Controlled cases

The ten controlled PDFs exercise parallel wires, crossings with and without a
filled junction, a three-terminal branch, an ambiguous undotted branch, a
missing terminal label, duplicate terminal labels, page rotation, a crop box,
and a true CAD-exported PDF with outlined glyphs. The first nine are authored
directly as ordinary PDF text and vectors; the tenth is exported from native
DXF geometry. Expected nets live outside the PDFs and are not generated from
the extractor's predictions.

The evaluator compares terminal-net membership, independent of how a branch
is divided into segments. A stable geometric-contact ID with no readable label
is retained as unscored geometry; it is never counted as an invented pin name.
Public annotations establish only explicitly listed observations, not the
precision of unannotated content or completeness of the whole sheet.

## Current result and practical limit

The October 18 run recovered the manually named nets in controlled cases
1–5, 8 and 9 exactly. Case 6 preserved the readable net and an unnamed geometric
contact; case 7 retained duplicate terminal labels as unknown. Case 10 could
not read its outlined device labels. The undotted branch, missing or duplicate
pins, cropped scope and outlined labels remain explicit unknowns.

The real SEL-121F drawing now has a supported geometric subset: on original
page 19, Figure 10, the adapter reads 29 named terminal markers and traces the
upper rail joining terminals **13, 15, 47, 17, 27 and 37**, including its six
actual filled junction dots. These observations come from visible labels,
flattened polygon circles and conductor strokes. The terminal labels are
unassigned to an enterprise equipment instance: the printed “SEL-121F
(partial)” reference model is not a customer device tag. The coil identities
and the remaining circuits are not established by that rail observation.
For a selected-page evaluation copy, page anchors refer to that copy's local
page; the manifest records its original page and both source hashes.

This is positive evidence for one manually annotated rail, not a real-document
accuracy pass. Whole-sheet precision, total device inventory and completeness
are unknown. The MnDOT exports include invisible render-mode-3 text over
visible outline glyphs; the adapter deliberately excludes that hidden text
from device identity. RICWS tag subsets and SEL-251 outlined labels remain
measured interpretation gaps. Unknown symbols, contact internals, cross-sheet
links and unaccounted vector ink are not filled in by inference.

PDF coordinates remain schematic page points. No physical dimensions,
engineering approval, whole-sheet verification, editable native CAD or
production release is established by this corpus. The focused adapter tests
also exercise covered and hidden labels, clipped forms, obscured wires,
annotation overlays, source-hash changes and bounded geometry work.
