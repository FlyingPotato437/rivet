# Equipment evidence application profile

Rivet preserves what a source says, then reconciles claims separately. This is a small application profile informed by the standards below. It is **not CIM, IEC 61850, IEC 81346 or ETIM conformance**, and it does not claim that a drawing is correct or released.

## Separate the views

| View | What Rivet represents | Source and boundary |
| --- | --- | --- |
| Physical assembly | Lineup → section → compartment → device, only where a cited source supports containment | These are local manufacturing assembly classes. They are not asserted as exact normative CIM class names. CIM's EquipmentContainer is the conceptual mapping. |
| Electrical topology | Device → Terminal → ConnectivityNode | The [UCAIug CIM topology example](https://cimug.ucaiug.org/KB/Knowledge%20Base/Use_of_the_CIM_Ontology_DistribuTech_2006.pdf) separates conducting equipment, terminals and connectivity nodes. Containment does not establish a wire. A non-conducting relay is not automatically ConductingEquipment. See also the [CIM Modeling Guide](https://cimug.ucaiug.org/Model%20Manager%20Documents/Public/CIM%20Modeling%20Guide_v1.1.pdf). |
| Automation function | LogicalNode → logical_function_of → device | [IEC 61850-7-4](https://webstore.iec.ch/en/publication/66551) specifies logical-node and data-object names for device/function communication. A logical node is not a physical compartment, and this profile does not infer XCBR or protection nodes from a symbol. |
| Function classification | Preserve ANSI/IEEE device function information separately from identity | [IEEE C37.2-2022](https://ieeexplore.ieee.org/document/9984140) identifies functions, including functions in multifunction equipment or software. `52-12` and `BKR-12` therefore remain different identifiers until a project-specific alias is verified. `86T` can carry function 86; embedded text in `1086M` is not parsed as function 86. Eaton's [generator-relay page](https://www.eaton.com/us/en-us/catalog/electrical-circuit-protection/generator-relays.html) independently documents its 86 lockout function. |
| Reference designation | Preserve original designation plus separate function `=`, product `-`, location `+`, type `%` aspects | [IEC 81346-1:2022](https://webstore.iec.ch/en/publication/64021) defines structuring/reference designation principles and introduces the type aspect. Prefix parsing is descriptive, not proof of compliant naming. This does not infer normative object classification letters. |
| Product features | Local typed numerical/logical/enumerated features, explicit unit conversion, unknown unsupported values | [ETIM's classification guidelines](https://www.etim-international.com/wp-content/uploads/2019/07/ETIM-International-Guidelines-V3-2020.pdf) distinguish numeric, range, logical and enumerated alphanumeric data. Rivet follows that pattern without inventing EF/EC/EV/EU identifiers or claiming that this limited profile is a complete ETIM release. Local identifiers/free text are explicitly distinguished from ETIM alphanumeric features. |

## Evidence contract

`rivet.evidence-graph.v1` contains `nodes`, `edges`, `claims`, `rules`, `aliases`, `scope_coverage`, `coverage` and `metadata`. A claim contains:

- `subject_id`, local typed `feature`, normalized `value` and `unit`;
- exact `raw_value` and extractor `raw_confidence`;
- hash-bound `source` with source ID and page/cell/row/CAD entity or XML path;
- extractor name/version/run, `independence_group`, and `derived_from` claim IDs;
- `conditions` including side, role, revision and document lifecycle;
- `normalization.valid/reason` and an `observed`, `proposed`, `approved` or `released` state.

The decision to prefer a value is a separate projection. It does not delete disagreeing claims. A low-confidence or unsupported value can remain null while the exact printed text survives. Every schedule cell is retained, including unrecognised vendor headers. Native, visual and report data keep their original positions; a source without a hash is rejected. Local paths are not part of public source references.

Append helpers return a new graph. Appending the same record is idempotent; replacing an existing evidence ID with a different record is rejected. Original claims survive later authorized lifecycle events. The graph is ordinary JSON, so storage/callers must use these append semantics rather than editing evidence objects directly. Lifecycle transitions require independently checked actor/authority evidence; merely reading “APPROVAL” or “RELEASED” in a title block leaves the reading **observed** and preserves that printed document state only as context.

## Identity and independence

The default entity ID is scoped to the tenant/order/package, source and source-local row/entity. Two row-1 positions, or two equal function numbers, never join merely because they look alike. Cross-document identity joins require a caller-verified, cited identity table. A position-based join additionally requires an established lineup, section and circuit correspondence. Explicit aliases require the same scope, `verified:true`, and source evidence. Candidate aliases do not join devices.

Repeated readings of one original page use the same independence group, regardless of source filename or extractor. The caller can supply original hash/page ancestry for page splits. This is conservative: many correlated observations remain one group, and an unchanged copied page does not create new confidence. Independence means an evidence lineage distinction, not an assumption that two manufacturers' documents were produced independently.

Specification claims become source-linked graph requirements. They are never counted as independent observations of the installed equipment. Requirements can compare a feature to a literal or another subject's feature. Approved/released requirements require explicit authority evidence; an assumed identical-lineup symmetry is not an approved equality rule.

## Existing input adapters

`adapt_sources(...)` and the convenience functions `from_comparison`, `from_equipment_model`, `from_visual_predictions`, and `from_cad_reports` retain original product readers. They produce a new graph alongside the original data. Source context can carry side/role, verified lineup and original-page ancestry. No existing source observations are overwritten.

The CAD adapter uses explicit component, pin and wire declarations. Duplicate pin names and missing endpoints remain unresolved; declarations do not prove physical geometry. Visual connection text with unverified endpoints remains a claim, without invented terminals. Every default completeness flag remains false until a separate evidence-based inventory/coverage check establishes it.

## Deliberately unverified

- Full manufacturer device inventories and wiring topology.
- Standards compliance, protection coordination, safety or manufacturing release.
- Aliases inferred solely from similar numbers, symbols, filenames or model confidence.
- A complete IEC/ETIM licensed classification catalogue.
- The idea that a consistent graph is automatically a correct graph: it can still omit unreadable equipment, which the separate source coverage check must expose.
