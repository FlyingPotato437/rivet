"""Run the read-only transformer prototype on an explicitly selected local order.

The operator supplies an existing private evaluation state file with the tenant,
user and order identity. No auto-selection, API call, CAD edit or email action.
"""

import argparse
import csv
import json
from html import escape
from pathlib import Path

from backend.domain.service import get
from backend.engineering.phase_change import PhaseScenario
from backend.engineering.phase_change_service import for_order
from backend.identity import Identity, identity_scope
from backend.storage.db import Session
from backend.storage.models import Order


def render(report):
    e = lambda v: escape(str(v))
    refs = lambda ids: "; ".join(
        f"{c['evidence']['name']} · p. {c['evidence']['page']}"
        for c in report["claims"]
        if c["id"] in ids
    )
    body = "".join(
        f"<p><b>{e(f['code'])}</b><br>{e(f['detail'])}</p>" for f in report["findings"]
    )
    claims = "".join(
        f"<tr><td>{e(c['field'])}</td><td>{e(json.dumps(c['value']))}</td><td>{e(c['evidence']['name'])}<br>p. {c['evidence']['page']}</td></tr>"
        for c in report["claims"]
    )
    comparisons = "".join(
        f"<tr><td>{e(c['winding'])}<br>{c['printed_rating_mva']:g} MVA</td><td>{c['printed_current_a']:,.2f} A</td><td>{c['if_rating_is_total_bank']['current_a']:,.2f} A</td><td>{c['if_rating_is_each_single_phase_unit']['current_a']:,.2f} A</td><td>Basis needs confirmation<br>{e(refs(c['claim_ids']))}</td></tr>"
        for c in report["rating_comparisons"]
    )
    blockers = "".join(
        f"<li><b>{e(b['code'])}</b>: {e(b['request'])}</li>" for b in report["blockers"]
    )
    return f"""<!doctype html><meta charset="utf-8"><title>Rivet · phase-change prototype</title>
<style>body{{max-width:1120px;margin:40px auto;padding:0 24px;font:15px/1.6 system-ui;color:#24202c}}h1{{font-size:30px}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:12px;border:1px solid #ddd;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{background:#f1eef6}}.notice{{padding:16px;background:#f1eef6}}li{{margin-bottom:10px}}@media print{{body{{margin:0;max-width:none}}tr{{break-inside:avoid}}}}</style>
<p>rivet. / Engineering prototype</p><h1>Transformer phase-change preflight</h1>
<p>{e(report["order_number"])} · Source fingerprint {report["fingerprint"][:12]}</p>
<p class="notice"><b>Analysis only. Native file changes are blocked.</b><br>These are conditional calculations and printed claims, not a conversion design or engineering approval. No file or Outlook draft was changed.</p>
<h2>What the supplied package says</h2>{body}
<h2>Independent rating-basis check</h2><p>Same printed MVA, two interpretations. The first uses total balanced three-phase bank kVA and line-to-line voltage. The second uses per-unit kVA and line-to-neutral voltage for a single-phase unit in the stated wye bank. Neither interpretation is adopted as approved.</p>
<table><tr><th>Winding / rating</th><th>Printed current</th><th>If MVA is total bank</th><th>If MVA is each unit</th><th>Finding / sources</th></tr>{comparisons}</table>
<p>Formula reference: <a href="https://www.se.com/us/en/faqs/FA101694/">Schneider transformer current calculations</a>. Independent inverse squared-power arithmetic is checked; physical design and protection are unverified.</p>
<h2>Inputs and implementation still needed</h2><ul>{blockers}</ul>
<p>A revised drawing is optional to start. It is useful as an independent benchmark withheld from the agent when testing a proposed change.</p>
<h2>Printed claims with source locations</h2><table><tr><th>Field</th><th>Observed value</th><th>Source</th></tr>{claims}</table>
<p>Prototype reads selectable printed text; raster equipment, winding connectivity and manufacturer design rules are not verified. Full hashes, page rectangles, assumptions and arithmetic are in phase-report.json. Software controls are not manufacturer accuracy results.</p>"""


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    state = json.loads(args.state.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    args.out.chmod(0o700)
    with (
        identity_scope(Identity(state["organization_id"], state["user_id"])),
        Session() as s,
    ):
        order = get(s, Order, state["order_id"])
        report = for_order(s, order)
        scenarios = {}
        # An explicit what-if only. Do not silently select a capacity/voltage
        # from unconfirmed manufacturer claims or claim it is a customer request.
        for target in ("single_phase", "split_phase", "two_phase", "three_phase"):
            scenarios[target] = for_order(s, order, PhaseScenario(target=target))
    (args.out / "phase-report.json").write_text(json.dumps(report, indent=2))
    (args.out / "phase-scenarios.json").write_text(json.dumps(scenarios, indent=2))
    (args.out / "phase-report.html").write_text(render(report))
    with (args.out / "rating-comparison.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Winding",
                "Printed MVA",
                "Printed A",
                "If MVA is total bank A",
                "If MVA is each unit A",
                "Status",
            ]
        )
        for c in report["rating_comparisons"]:
            writer.writerow(
                [
                    c["winding"],
                    c["printed_rating_mva"],
                    c["printed_current_a"],
                    c["if_rating_is_total_bank"]["current_a"],
                    c["if_rating_is_each_single_phase_unit"]["current_a"],
                    c["classification"],
                ]
            )
    print(
        json.dumps(
            {
                "status": report["status"],
                "printed_claims": len(report["claims"]),
                "rating_comparisons": len(report["rating_comparisons"]),
                "files_changed": report["files_changed"],
                "report": str(args.out / "phase-report.html"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
