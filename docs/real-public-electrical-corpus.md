# Real public electrical source collection

The additional collection contains **15 distinct official project/record
packages, 28 unchanged PDF originals and 2,291 physical pages**. Thirteen
packages concern equipment procurement or manufacturer approval/review; one is
an engineering-study RFP and one is a mixed operation-and-maintenance record.
Addenda and rebids from one project count once. No synthetic control, catalogue
page or Autodesk training dataset contributes to that project count.

The originals and their acquisition manifest are local research data under
`.data/real-document-corpus/new-official/`, outside Git. The manifest records
publisher URL, final URL, SHA-256, page count, document type and provenance.
Public access is not treated as a redistribution licence.

## Sources

| Project | Published material | Limits |
| --- | --- | --- |
| Hopewell Water Renewal | 2022 switchgear bid and IFB 05-24 reissue | Reissued procurement documents; matching corrected manufacturer sheets are not established |
| IDEA Public Schools | Electrical switchgear upgrade specifications | Bid requirements, not a completed returned shop-drawing cycle |
| JEA Arlington East WRF | Switchgear replacement specifications | Requirements and submittal procedures |
| Fremont | Metal-enclosed switchgear purchase contract | Bid requirements |
| San Marcos Rattler Substation | Transformer T1 procurement | Bid requirements |
| New York OGS Altona | Switchgear replacement addendum and attached drawings | Actual bid changes, not a claimed customer design approval |
| UNC Charlotte | Grid automation notice and five addenda | Procurement questions, terms, existing-equipment photos and changed bid dates; not five independent projects |
| Tri-County Technical College | Anderson switchboard original bid, rebid and drawing | One real project; raster drawing needs visual interpretation |
| Austin Energy | Transformer specification and two addenda | One real procurement cycle |
| Austin Water | Multi-tap transformer scope, drawings and bid | One real procurement package |
| Petersburg | Scow Bay Generator No. 2 switchgear bid and addendum | Addendum changes the bid deadline; it does not demonstrate an equipment-design change |
| Danville | Pole-mount transformer bid and scanned Q&A addendum | Actual procurement clarification |
| Kansas Docking building | Schneider Electric / Square D approval drawings and returned review | Actual manufacturer BOM/schedules, drawings, marked comments and review stamps |
| Hanford A6 feeders | Switchgear-building engineering-study RFP | Related study, not a manufacturer submittal package |
| NY DEC Dollinger | O&M record with a GE supplier transmittal appendix | Mixed record; not a complete modern switchgear review cycle |

Run `PYTHONPATH=. .venv/bin/python scripts/acquire_real_electrical_packages.py`
to fetch missing originals. Previously recorded hashes are checked before reuse;
a changed original requires explicit review and repinning. Four additional
candidate sources were not acquired: Durham and two Maine PDFs returned HTTP
403; the historical Birmingham VA attachment returned HTTP 404. They are
recorded as failures and excluded from the counts.

## Independently labelled Kansas scope

The [official Kansas archive](https://kslegislature.gov/li_2016/b2015_16/committees/ctte_jt_bldg_constr_1/documents/testimony/20160202_51.pdf)
contains a 266-page binder. Its pinned SHA-256 is
`3c3a61b0d8715880272813295f24df64350fe3033f5765bb71b4be4d466afc4d`.
The original remains intact.

The bounded evaluation includes unchanged copies of original pages 241-246,
the complete **SWITCHBOARD H3** subpackage. The circuit schedule has nine rows:
one incoming busway connection, four prepared spaces and four populated
breakers. Circuits 3, 4, 7 and 8 are the populated JL 200 A, JL 250 A, HL 30 A
and LI 600 A devices, respectively. Circuits 1, 2, 5 and 6 are HL prepared
spaces. No fictional CB tags are assigned to the local circuit numbers.

It also includes original pages 194 and 252: two dated issues of the same
Control Center No. 1 one-line, drawing **O29528680-009-01, sheet 1 of 2**.
The May 30 issue visibly adds the connected section-4 transition to the
existing MCC compared with the May 15 issue. Both title-block revision fields
are dashes. A revision letter, causal customer instruction and corrected
post-review manufacturing release are not invented.

Every evaluation copy records its parent hash and physical-page map. A rendered
pixel comparison confirmed that copying each page preserved its appearance.
The copies are one project's bounded evaluation views, not additional real
projects. The full binder remains a separate scalability source; success on
eight selected pages cannot establish coverage of all 266.

The preliminary reference key is
`benchmarks/real-packages/keys/kansas-docking-switchboard.json`, registered in
`benchmarks/real-packages/packages.json`. Labels were visually transcribed
before running Rivet predictions and need an engineer/manufacturer second
review. Exact printed-tag presence, physical position counts, PDF schedule
rows, comment extraction and revision checks remain separate measurements.

## Native vendor samples and remaining access gaps

Two genuine official Autodesk University datasets were acquired separately:

- [Automatic Drawing Creation, 2018](https://www.autodesk.com/autodesk-university/class/Automatic-Drawing-Creation-AutoCAD-Electrical-2018): three DWGs, a WDP project and an AEPX file.
- [Cables and Harnessing, 2014](https://www.autodesk.com/autodesk-university/class/Cables-and-Harnessing-AutoCAD-Electrical-2014): 35 DWG drawings/symbols, a WDP project, WDT mapping and two MDB catalogues. Its nested ZIP repeats project content and is not counted as another dataset.

Original ZIPs, hashes and complete archive-member inventories are under
`.data/real-document-corpus/official-native-exports/`. Archives were inspected
without executing code. These are vendor-authored training samples, not real
customer orders or generated BOM/wire exports. No CAD application was invoked
and no drawing conversion or write validation is claimed.

Neither ZIP, including the nested archive, contains a CSV, XLS, XLSX or PDF
component/wire report. The separately downloaded two-page Autodesk Electrical
quick reference documents symbol attributes; it is not an exported project
report. Downloading a sample or report PDF and generating a fresh report from a
native project are separate capabilities. The latter has not been demonstrated.

A local check on October 18 found no AutoCAD Electrical or EPLAN application in
`/Applications` or `~/Applications`, and no matching exporter, DWG converter,
Wine, QCAD or LibreCAD executable on the command path. Autodesk Fusion is
present; it does not establish Electrical export capability. No runnable,
licensed native exporter has been verified. The checked official
[AutoCAD specialized-toolset requirements](https://help.autodesk.com/view/ACD/2026/ENU/?caas=caas%2Fsfdcarticles%2Fsfdcarticles%2FSystem-requirements-for-AutoCAD-2026-including-Specialized-Toolsets.html)
and [EPLAN requirements](https://eplan.help/en-us/infoportal/content/swreqs/Content/htm/SwReqs_k_win10_2025.htm)
describe Windows environments. No commercial converter was installed.

Autodesk's [website terms](https://www.autodesk.com/company/legal-notices-trademarks/access-use/website-terms-of-use)
and [copyright policy](https://www.autodesk.com/company/legal-notices-trademarks/intellectual-property/copyright)
do not establish an open licence for these ZIPs. Public download availability
does not establish permission to modify, redistribute, train models on or use
them in commercial benchmarks. Applicable additional terms or permission must
be established before those uses; the registry contains source metadata only.

[EPLAN's official sample-project instructions](https://www.eplan.com/int/en/main-guided-installation/guided-installation-platform-2026/single-user-license-update-installation/first-steps/open-sample-project.html)
refer to supplied application sample data. Its
[supplier-guideline document](https://www.eplan.com/content/dam/eplan/website/global/downloads/industries/operator/plant-operator-tips-for-creating-supplier-guidelines.pdf)
describes basic-project downloads after login. No direct public EPLAN native
project/report export was acquired. No authentic switchgear-design XLSX BOM
has been obtained in this collection; pricing sheets and PDF schedules are not
relabeled as one.

## Reproducible metadata

`benchmarks/real-packages/acquisition-catalog.json` records all 15 project/record
packages and 28 original URLs, hashes, page counts and document types.
`benchmarks/real-packages/native-vendor-sources.json` records the vendor archive
hashes, complete member inventories, report-format findings, checked local
exporter scope and official rights/platform references. These files contain no
original drawings, archives or extracted document text.

Run `PYTHONPATH=. .venv/bin/python scripts/snapshot_real_corpus_metadata.py`
after acquisition to regenerate those registries. It verifies each local
original against its pinned hash and refuses to snapshot changed bytes. The
bounded evaluation manifest and human reference keys remain separate from this
acquisition inventory.

Acquisition proves provenance and readable source bytes. It does not establish
Rivet's extraction accuracy, engineering correctness or manufacturer readiness.

## Adjacent electrical BOM check

One additional project is recorded separately from the 15-package collection:
Alaska Railroad's passenger-car wireless installation. The official
[procurement page](https://www.alaskarailroad.com/corporate/procurement/solicitations)
links a [one-page BOM](https://www.alaskarailroad.com/sites/default/files/procurement/Passenger_Car_Wireless_Bill_of_Materials.pdf)
and a [23-page IFC drawing set](https://www.alaskarailroad.com/sites/default/files/procurement/ARRC_WIRELESS_WIFI_IFC.pdf).
These are adjacent transit electrical installation documents, not a
custom-switchgear manufacturer review cycle.

The ten BOM rows were visually labelled in
`benchmarks/real-packages/keys/alaska-passenger-wireless.json` before predictions.
The complete scope is that one-page table; the IFC device inventory is
unlabelled. Item numbers 1-10 are identifiers, EA/FT are units, and no purchase
quantity is established: the title expressly says quantity varies per car.

The BOM is an actual PDF even though its metadata title ends in `.xlsx`.
The state-notice attachment URL returned a 245-byte rejected-request HTML
page. No original Excel workbook, worksheets, merged headers or formulas were
obtained. The legitimate publisher copy was retained unchanged; no PDF-to-Excel
conversion is presented as original data. Provenance and hashes are in
`benchmarks/real-packages/adjacent-bom-sources.json`. This is the fifth evaluated
project, with its adjacent-domain and PDF-only limits recorded in the manifest.
