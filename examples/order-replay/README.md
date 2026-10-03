# Fictional order replay: Larkspur DC-04

This small replay is for **one engineer-to-order low-voltage switchgear package**, MSB-01. Every customer, manufacturer, part number, acceptance and rating is fictional. These files are tests, not a design certification or commercial offer.

The PDFs contain selectable text. The markup PDF also contains a genuine FreeText annotation for comment 7 and an empty cloud annotation that must remain an unresolved visual-review item.

## Replay sequence

1. Add `01-project-specification.pdf`, `02-customer-purchase-order.pdf`, and `03-accepted-exception.pdf`.
2. Add `04-approval-drawing-Rev-B.pdf`. The obligation ledger should use the PO's **85 kA** requirement over the spec's **65 kA**, and the accepted exception's **aluminum** bus over the spec's **copper**.
3. Add `05-customer-markups-Rev-B.pdf`. There are **11 numbered comments**: seven explicit drawing deviations, one request conflicting with the accepted exception, two already-supported values, and one unclear lifting-eye question. The empty cloud is additional unreadable evidence, not an invented twelfth comment.
4. Inspect responses and proposed actions. The PM can decide how to handle the copper-bus request; accepting a response must not rewrite the source obligation or release a failing package.
5. Add `04-approval-drawing-Rev-C.pdf`. Six attribute values change. Comment 7 is claimed “fixed,” but the drawing still states **65 kA**; the PO still requires **85 kA**, so the check must remain failing.
6. Add the existing BOM, supplier PO and nameplate PDFs (`07`–`09`) to expose downstream values that still refer to Rev B. Track propagation separately; approval of the drawing must not invent revised downstream evidence.
7. Export the response matrix and BOM delta. Only accepted response text belongs in the response column. The BOM delta should show the actual Rev B→C changes, and no supplier cost or margin.

This replay does **not** show a production-ready order: the SCCR, unclear comment/cloud and accepted-exception conflict need disposition. A label saying “fixed” cannot close them.

The seven mechanically comparable Rev B deviations are a fixture expectation, **not** a measured accuracy claim on a real past order. The overall check count also includes evidence, reconciliation and engineering-rule findings and may differ from the comment count.

See `expectations.json` for the exact expected values, roles, aliases, revision changes and review cases. Regenerate with:

```sh
uv run python scripts/make_order_replay.py
```
