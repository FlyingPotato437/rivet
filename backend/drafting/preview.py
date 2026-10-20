"""Small, dependency-light review views of the edited native CAD and BOM."""

from __future__ import annotations

from html import escape


def drawing_svg(
    document,
    *,
    title: str,
    tasks: list[dict] | None = None,
    highlight_handles: set[str] | None = None,
    highlight_color="#e1f3ea",
) -> str:
    """Show supported source primitives, never invent or regenerate geometry.

    Native DXF remains the engineering artifact. Unsupported primitives are
    counted and disclosed; this review view is not a CAD renderer/certifier.
    """
    pieces: list[str] = []
    skipped: dict[str, int] = {}
    points: list[tuple[float, float]] = []

    def draw(entity):
        kind = entity.dxftype()
        color = "#4f5866"
        if kind == "LINE":
            a, b = entity.dxf.start, entity.dxf.end
            points.extend([(a.x, a.y), (b.x, b.y)])
            pieces.append(
                f'<line x1="{a.x}" y1="{-a.y}" x2="{b.x}" y2="{-b.y}" stroke="{color}" stroke-width=".7"/>'
            )
        elif kind == "LWPOLYLINE":
            # Straight segments only; a bulge is an arc and must not be redrawn straight.
            if any(point[4] for point in entity.get_points()):
                skipped[kind] = skipped.get(kind, 0) + 1
                return
            xy = [(p[0], p[1]) for p in entity.get_points()]
            points.extend(xy)
            if entity.closed and xy:
                xy.append(xy[0])
            coords = " ".join(f"{x},{-y}" for x, y in xy)
            pieces.append(
                f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width=".7"/>'
            )
        elif kind == "CIRCLE":
            c, radius = entity.dxf.center, entity.dxf.radius
            points.extend([(c.x - radius, c.y - radius), (c.x + radius, c.y + radius)])
            pieces.append(
                f'<circle cx="{c.x}" cy="{-c.y}" r="{radius}" fill="none" stroke="{color}" stroke-width=".7"/>'
            )
        elif kind in {"TEXT", "ATTRIB"}:
            p, size = entity.dxf.insert, entity.dxf.height
            points.extend(
                [(p.x, p.y), (p.x + len(entity.dxf.text) * size * 0.65, p.y + size)]
            )
            if entity.dxf.handle in (highlight_handles or set()):
                width = len(entity.dxf.text) * size * 0.65 + 3
                pieces.append(
                    f'<rect x="{p.x - 1.5}" y="{-p.y - size - 1}" width="{width}" height="{size + 3}" rx="1" fill="{highlight_color}"/>'
                )
            pieces.append(
                f'<text x="{p.x}" y="{-p.y}" font-family="Arial,sans-serif" font-size="{size}" fill="#18202b">{escape(entity.dxf.text)}</text>'
            )
        elif kind == "INSERT":
            for child in entity.virtual_entities():
                draw(child)
            for attrib in entity.attribs:
                if not attrib.is_invisible:
                    draw(attrib)
        else:
            skipped[kind] = skipped.get(kind, 0) + 1

    for entity in document.modelspace():
        draw(entity)
    if not points:
        points = [(0, 0), (300, 200)]
    left = min(p[0] for p in points) - 12
    right = max(p[0] for p in points) + 12
    bottom = min(p[1] for p in points) - 12
    top = max(p[1] for p in points) + 12
    for index, task in enumerate(tasks or [], 1):
        x, y, _ = task["anchor_xyz"]
        pieces.append(
            f'<circle cx="{x}" cy="{-y}" r="8" fill="#fff3dc" stroke="#b56f17" stroke-width=".8"/><text x="{x}" y="{-y + 2}" text-anchor="middle" font-family="Arial,sans-serif" font-size="5" fill="#925d12">{index}</text>'
        )
    disclosure = "Supported primitive review; open DXF for engineering review. " + (
        "Unsupported entities: "
        + ", ".join(f"{k} ({v})" for k, v in sorted(skipped.items()))
        if skipped
        else "No unsupported primitives found."
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="720" viewBox="0 0 1120 720" role="img">'
        f'<title>{escape(title)}</title><rect width="1120" height="720" fill="#fafbfd"/>'
        '<rect x="0" y="0" width="1120" height="88" fill="#171b24"/>'
        f'<text x="32" y="38" font-family="Arial,sans-serif" font-size="22" fill="white">{escape(title)}</text>'
        '<text x="32" y="64" font-family="Arial,sans-serif" font-size="12" fill="#c8c1e2">DRAFT REVIEW COPY - ENGINEERING APPROVAL REQUIRED</text>'
        f'<svg x="32" y="110" width="1056" height="546" viewBox="{left} {-top} {right - left} {top - bottom}" preserveAspectRatio="xMidYMid meet">'
        + "".join(pieces)
        + '</svg><line x1="32" y1="674" x2="1088" y2="674" stroke="#d7dbe2"/>'
        f'<text x="32" y="698" font-family="Arial,sans-serif" font-size="11" fill="#687080">{escape(disclosure)}</text></svg>'
    )


def report_html(report: dict) -> str:
    e = escape
    rows = "".join(
        f"<tr><td><strong>{e(change['target'])}</strong><small>{e(change['evidence_id'])}</small></td>"
        f'<td class="before">{e(change["before"])}</td><td class="after">{e(change["after"])}</td>'
        f"<td>{e(change['reason'])}</td></tr>"
        for change in report["changes"]
    )
    tasks = "".join(
        f"<li><strong>{e(task['equipment_tag'])} - {e(task['instruction'])}</strong><p>"
        f"DXF entity {e(task['entity_handle'])}; exact source insertion {e(str(task['anchor_xyz']))}. "
        f"{e(task['reason'])}</p><small>{e(task['evidence_id'])} - Geometry remains unchanged; drafter action required.</small></li>"
        for task in report["layout_tasks"]
    )
    downloads = "".join(
        f'<a href="{e(a["name"], quote=True)}" download>{e(a["name"])}</a>'
        for a in report["artifacts"]
        if a["role"] in {"draft_drawing", "draft_bom"}
    )
    mode = (
        "Fictional local demo"
        if report["provenance"] == "local_demo"
        else "Authorized source upload"
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rivet - draft revision review</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f5f8;color:#242935;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}main{{max-width:1320px;margin:auto;padding:48px 40px 80px}}header{{display:flex;align-items:start;justify-content:space-between;gap:32px;border-bottom:1px solid #d9dce4;padding-bottom:28px}}.brand{{font-size:21px;font-weight:750}}h1{{font-size:34px;letter-spacing:-1px;line-height:1.2;margin:16px 0 10px}}h2{{font-size:19px;margin:36px 0 16px}}p{{color:#656d7b;margin:8px 0}}.status{{border:1px solid #d9c8a5;background:#fff6e6;color:#865d1b;padding:9px 14px;border-radius:7px;white-space:nowrap}}.meta{{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#78818f}}.views{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.views figure{{margin:0;border:1px solid #dce0e7;background:white;border-radius:9px;overflow:hidden}}.views img{{display:block;width:100%;height:auto}}figcaption{{padding:13px 18px;font-weight:600}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{text-align:left;padding:16px;vertical-align:top;border-bottom:1px solid #e0e3ea}}th{{color:#67707d;font-size:12px;font-weight:600}}small{{display:block;color:#78818f;font-size:12px;margin-top:6px}}.before{{color:#94504f}}.after{{color:#2f7958}}ul{{padding-left:24px}}li{{padding:10px 0}}nav{{display:flex;gap:12px;flex-wrap:wrap;margin-top:24px}}a{{color:#5d4b88;border:1px solid #d8d3e4;background:white;padding:10px 16px;border-radius:6px;text-decoration:none}}.note{{padding:16px 20px;background:#e9eaf0;border-radius:7px;margin-top:24px}}@media(max-width:800px){{main{{padding:24px}}header{{display:block}}.status{{display:inline-block;margin-top:16px}}.views{{grid-template-columns:1fr}}table{{font-size:13px}}td,th{{padding:10px}}}}
</style><main><header><div><div class="brand">rivet.</div><div class="meta">{e(mode)} / {e(report["job_number"])}</div><h1>Draft Rev {e(report["target_revision"])} is prepared.</h1><p>{len(report["changes"])} exact text and data edits. {len(report["layout_tasks"])} layout task for drafting.</p></div><div class="status">Engineering approval required</div></header>
<h2>Drawing {e(report["drawing_number"])} - before and after</h2><div class="views"><figure><img src="before.svg" alt="Source drawing before edits"><figcaption>Source Rev {e(report["source_revision"])} - original unchanged</figcaption></figure><figure><img src="after.svg" alt="Draft drawing after text edits with layout callout"><figcaption>Draft Rev {e(report["target_revision"])} - text changes prepared</figcaption></figure></div>
<h2>Prepared changes</h2><table><thead><tr><th>EXACT TARGET / EVIDENCE</th><th>BEFORE</th><th>AFTER</th><th>REASON</th></tr></thead><tbody>{rows}</tbody></table>
<h2>For the drafter</h2><ul>{tasks or "<li>No layout changes requested.</li>"}</ul>
<nav>{downloads}<a href="report.json">Source hashes and edit manifest</a></nav><div class="note">These are proposed edits in new files. Original drawings and BOMs were not modified. No geometry was relocated, no production release was issued, and no external CAD or vault system was changed.</div></main></html>"""
