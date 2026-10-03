# Order evidence attributes

The replay reader supports **34 explicit attributes** for low-voltage switchgear packages. It reads assertions from the existing vector-PDF text layer and persisted text/row spans. It also reads PDF annotation objects. It does not certify equipment, infer an engineering design, or turn an unreadable cloud into a conclusion.

Each extracted fact carries a device identifier, one canonical attribute, a typed value, canonical unit, persisted source IDs, and the source page/region when available. The deterministic order engine decides precedence and checks; this reader does not.

| Canonical attribute | Type / unit | Example |
| --- | --- | --- |
| rated_voltage_v | number / V | 480 |
| frequency_hz | number / Hz | 60 |
| phases | integer | 3 |
| wires | integer | 4 |
| main_bus_a | number / A | 3200 |
| neutral_bus_percent | number / % | 100 |
| short_circuit_ka | number / kA | 85 |
| short_time_ka | number / kA | 65 |
| short_time_duration_s | number / s | 0.5 |
| main_breaker_frame_a | number / A | 3200 |
| main_breaker_trip_a | number / A | 3000 |
| main_breaker_poles | integer | 3 |
| main_breaker_interrupting_ka | number / kA | 100 |
| feeder_breaker_frame_a | number / A | 800 |
| feeder_breaker_trip_a | number / A | 600 |
| enclosure_type | text | NEMA 3R |
| bus_material | text | aluminum |
| bus_plating | text | silver |
| cable_entry | text | top |
| control_voltage_v | number / V | 120 |
| metering | text | multifunction |
| communications_protocol | text | Modbus TCP |
| surge_protection | Boolean | yes |
| ground_fault_protection | Boolean | yes |
| arc_resistant | Boolean | no |
| seismic_qualification | text | Project-specific review required |
| enclosure_width_mm | number / mm | 2400 |
| enclosure_height_mm | number / mm | 2200 |
| enclosure_depth_mm | number / mm | 1000 |
| quantity | integer | 1 |
| manufacturer | text | Fictional Aster Equipment |
| catalog_number | text | ASTER-LV-3200-DEMO |
| nameplate_text | text | MSB-01 / 480 V / 85 kA |
| lead_time_weeks | number / weeks | 28 |

The numeric examples above are synthetic test data, not engineering recommendations.

## Supported evidence forms

Use an explicit device and field label. Canonical keys and a bounded set of natural labels are supported:

```text
MSB-01 | rated_voltage_v: 480 V
MSB-01 | Main bus rating: 3200 A
Device: MSB-01
Control voltage: 120 V
Alias: MSB Main -> MSB-01
Alias: 52-M1 -> MSB-01
Alias: BOM line 3 -> MSB-01
Comment 7 | MSB-01 | short_circuit_ka: 85 kA | Match the purchase order.
```

The aliases are explicit evidence; similar-looking tags are not silently merged. Numeric conversions are bounded: kV↔V, kA↔A, ms↔s, and mm/cm/m/inch→mm. A range such as `65–85 kA`, a compound rating such as `480/277 V`, an unknown unit, or a qualifier such as `provisional` remains unresolved. Numeric values without a suffix use the canonical field's unit. Text preserves the source wording; the reader does not equate a standard designation with actual certification.

Document roles are read from explicit metadata, a declared document-role header, or a conservative filename classifier. Role classification alone is not an approval or signature. Recognized roles are specification, purchase order, accepted exception, drawing, markup, BOM, supplier PO, nameplate, and quote. Unknown roles require review.

## Coverage and limitations

- A vector PDF's text regions remain approximate line bounds; the original PDF is retained.
- PDF callouts/free-text annotations preserve their own annotation ID and exact annotation rectangle. Polygon/cloud objects without text are marked for review.
- Numbered comments split into distinct items. Following lines attach to the comment with every supporting source ID retained.
- A comment or a drafter's “fixed” statement is not drawing evidence. Rev C must contain the changed attribute for its check to pass.
- Scans, unsupported pages, unresolved formulas and unclassified documents remain explicit review work. There is no silent OCR/vision fallback.
- Arbitrary prose extraction, CAD/DWG attribute ingestion, standards certification and external inbox watching are outside this reader. Uploads with unfamiliar labeling may need clearer source text or a human-reviewed fact workflow.

The fixture bundle in `examples/order-replay/` includes the source PDFs and a machine-readable expected outcome. Regenerate it with `uv run python scripts/make_order_replay.py`.
