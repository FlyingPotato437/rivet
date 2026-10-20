# Equipment catalog evidence

This is a small, provenance-bound knowledge base for reconciliation. It is not a complete product catalog, a compatibility checker, or permission to change an equipment value. A catalog claim says what a **bound catalog product** can be. It does not establish what was installed, its actual adjustable trip setting, or whether a drawing is approved/released.

## Verified sources

| Manufacturer | Primary source | Curated scope | Provenance |
| --- | --- | --- | --- |
| Schneider Electric / Square D | [0611CT1001 R09/24](https://download.schneider-electric.com/files?p_Doc_Ref=0611CT1001&p_EnDocType=Catalog&p_File_Name=0611CT1001.pdf) | Complete unit-mount H/J thermal-magnetic and H/J/L electronic numbers actually listed in selected tables; AC UL 489 interrupting ratings | Physical PDF pp. 10–12, 27, 29, 41–44, 52. Original local in `.data/equipment-catalogs/`; SHA-256 in registry |
| Eaton | [HFD2225L](https://www.eaton.com/us/en-us/skuPage.HFD2225L.html), [HFD3200L](https://www.eaton.com/us/en-us/skuPage.HFD3200L.html) and [CA08100005E](https://www.eaton.com/content/dam/eaton/products/electrical-circuit-protection/molded-case-circuit-breakers/mccb-catalog-v4-t2-ca08100005e.pdf) | Two separately verified complete Series C F-frame products. Stored kA only at the listed 240/480 Vac conditions; family capacity from FD/HFD table | Product-specifications sections; catalog printed V4-T2-226/242. Verified through official web/indexed content; direct download failed, so no original-byte hash is asserted |
| Siemens | [BQD/CQD data sheet CBDS-BCQD-0520](https://cache.industry.siemens.com/dl/files/274/109792274/att_1051362/v1/SIE_SS_MCCB_BQD-CQD.pdf) | Exact BQD/CQD and BQD6/CQD6 numbers in both catalog tables; poles/current; conditional UL/CSA AIR | Physical PDF pp. 1–2. Original local in `.data/equipment-catalogs/`; SHA-256 in registry |

Structured registry: `backend/equipment/evidence/catalogs.json`. Each returned claim/constraint contains its source URL, physical page or named section, document edition and hash where downloaded. Catalog PDF originals remain outside git.

## Important range correction

The general characteristics tables in 0611CT1001 state the following AC ranges:

| Bound family / variant | Catalog range | Capacity |
| --- | --- | --- |
| PowerPacT H | 15–150 A | 150 A |
| PowerPacT J | 70–250 A | 250 A |
| PowerPacT L400 | 70–400 A | 400 A |
| PowerPacT L600 | 200–600 A | 600 A |

Therefore **J = 150–250 A** and **L = 250–600 A** are not generally valid constraints. Some narrower thermal-magnetic selection tables do start J at 150 A; that does not override the broader electronic family range. Bare **L** does not select L400 vs L600. DC variants have different tables and are deliberately unsupported by this first decoder.

Legacy schedule letters such as **LI** and **MJ** are preserved unchanged. Neither a Square D-looking drawing nor a frame letter establishes that an old breaker belongs to modern PowerPacT. There is no implicit LI→L alias or catalog-based correction of a scanned value.

## Lookup contract

`lookup_product(manufacturer=None, family=None, part_number=None, frame=None, voltage_v=None, current_type="AC", *, connection_system=None, rating_standard="UL489")`

The result contains `claims`, `constraints`, `applicability`, `unknowns`, `conflicts`, and the unmodified input. It has no side effects and performs no network requests at runtime.

- A complete, exact known catalog number can bind its manufacturer/family. A conflicting supplied manufacturer/family blocks lookup.
- A bare frame needs explicit verified manufacturer **and** family. Constraints remain conditional on that binding.
- Unknown suffixes are not stripped. Unknown product numbers do not become a plausible neighboring number.
- `frame_a` is capacity; `rated_current_a` is a fixed thermal-magnetic product rating; `sensor_a` is the electronic sensor rating. None becomes an observed `trip_a` setting.
- Interrupting kA requires an exact supported voltage/current type/rating standard. No interpolation from 480 V to 460 V, no AC-to-DC reuse, and no IEC Icu/UL AIR substitution.
- Siemens 480Y/277 and 600Y/347 slash ratings require the matching grounded-wye system. BQD6/CQD6 are CSA-only in this sheet; default UL lookup does not silently grant their ratings.
- Siemens frame ampacity and trip technology were not independently curated from this two-page sheet and remain unknown.
- The catalogs constrain candidate equipment values; a conflict requires review. Catalog knowledge is not a second observation of the source drawing.

The tests exercise decoding, rejected suffixes, family binding, voltage/standard conditions, source provenance and the difference between sensor and pickup. These are **contract controls**, not real-document extraction accuracy. No frozen reader, answer key or source document was changed to improve a score.

## Deliberate limits

This initial subset covers 349 Schneider table numbers, 136 Siemens table numbers and 2 Eaton exact products. It excludes unknown accessories, mission-critical variants, DC-specific parts, IEC ratings and legacy I-Line families. More products can be added only from reviewed, versioned primary sources with their applicability conditions. A full manufacturer export/BOM should carry complete catalog numbers and the relevant product family; otherwise the reconciliation engine must retain uncertainty.
