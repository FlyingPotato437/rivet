"""What each customer comment asks for, and which ones add scope (change orders).

Automatic and conservative. Every label carries the phrases that produced it, a
person confirms anything commercial, and a cost appears only when a trusted ERP
price row matches. Nothing here invents a price, approves scope or answers a
comment.

Kinds: change (requested drawing/BOM change), question, scope (adds scope that
may be billable), no_action (review stamps), review (unreadable or unclear).
`engineer` is separate: the comment touches ratings, protection, wiring,
substitutions or code compliance, so an engineer decides.
"""

import json
import re
from decimal import Decimal

from fastapi import HTTPException

from backend.domain.pricing import gross_margin, money

NUMBERS = {
    "a": 1, "an": 1, "one": 1, "another": 1, "second": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip

NO_ACTION = re.compile(
    r"^\W*(no exceptions? taken|furnish as (noted|corrected)|reviewed( for general conformance)?"
    r"|approved( as noted)?|date\s*:|signed\b|initials?\b)",
    re.IGNORECASE,
)
UNREADABLE = re.compile(
    r"no extractable text|has no text|manual interpretation|transcri(be|ption) (the|or)"
    r"|visual review required|illegible",
    re.IGNORECASE,
)
SCOPE = [
    (
        re.compile(r"\badd(ing|ed)?\b(?! (to|the note|note))", re.IGNORECASE),
        "adds equipment",
    ),
    (
        re.compile(r"\b(additional|extra|spare)\b", re.IGNORECASE),
        "asks for additional equipment",
    ),
    (
        re.compile(
            r"\b(quote|quotation|price|pricing|extra[- ]price|change order)\b",
            re.IGNORECASE,
        ),
        "asks for pricing",
    ),
    (
        re.compile(
            r"\b(not in|outside( of)?|excluded from|beyond) (the )?(base |accepted |contract )?scope\b|\bexclude[sd]?\b",
            re.IGNORECASE,
        ),
        "refers to excluded scope",
    ),
    (
        re.compile(
            r"\bupgrade\b|\bincrease (the )?(quantity|qty|size|rating)\b", re.IGNORECASE
        ),
        "asks for an upgrade",
    ),
    (
        re.compile(r"\b(option|accessory|accessories)\b", re.IGNORECASE),
        "asks for an option",
    ),
]
ENGINEER = [
    (
        re.compile(
            r"\bUL\s*-?\s*508A?\b|\bUL\b listing|\bNEC\b|\bNFPA\b|\bIEEE\b|\bANSI\b|\bCSA\b",
            re.IGNORECASE,
        ),
        "code or listing",
    ),
    (
        re.compile(
            r"\bSCCR\b|short[- ]circuit|interrupting|withstand|\b\d+(\.\d+)?\s?kA\b",
            re.IGNORECASE,
        ),
        "short-circuit rating",
    ),
    (
        re.compile(
            r"\btrip\b|\bLSIG?\b|ground[- ]fault|coordination|protective device|overcurrent|fus(e|ing)\b",
            re.IGNORECASE,
        ),
        "protection setting",
    ),
    (
        re.compile(
            r"\bsubstitut|\balternate\b|\bor equal\b|\bequivalent\b", re.IGNORECASE
        ),
        "component substitution",
    ),
    (
        re.compile(
            r"\bwir(e|ing)\b|\bAWG\b|\bkcmil\b|\bconductor|\brewire|\breroute",
            re.IGNORECASE,
        ),
        "wiring change",
    ),
    (
        re.compile(r"\b\d+(\.\d+)?\s?(V|VAC|VDC|kV)\b|\bvoltage\b", re.IGNORECASE),
        "voltage",
    ),
    (
        re.compile(
            r"\barc[- ]flash\b|\bgrounding\b|\bbonding\b|\bclearance", re.IGNORECASE
        ),
        "safety clearance or grounding",
    ),
    (
        re.compile(
            r"\b\d+(\.\d+)?\s?HP\b|\bhorsepower\b|\bVFD\b|\bdrive\b|\bmotor\b",
            re.IGNORECASE,
        ),
        "drive or motor sizing",
    ),
]
QUESTION = re.compile(
    r"\?\s*$|^\W*(please\s+)?(confirm|clarify|verify|identify|advise|explain|indicate|"
    r"which|what|why|how|is|are|can|could|does|do|will|should|would)\b",
    re.IGNORECASE,
)
CHANGE = re.compile(
    r"\b(must|shall|change|update|revise|correct|replace|relocate|move|show|provide|"
    r"furnish|delete|remove|rename|label|match|substitute|swap|use)\b",
    re.IGNORECASE,
)
TAG = re.compile(r"\b[A-Z]{1,5}\d*-[A-Z0-9]{1,8}\b|\b[A-Z]{2,5}\d{1,4}[A-Z]?\b")
QUANTITY = re.compile(
    r"\b(add|provide|include|furnish|quote(?:\s+the)?)\s+(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|another|second)\b",
    re.IGNORECASE,
)


def _reasons(patterns, text):
    return [label for pattern, label in patterns if pattern.search(text)]


def _quantity(text):
    m = QUANTITY.search(text)
    if not m:
        return Decimal(1)
    word = m.group(2).lower()
    return Decimal(word) if word.isdigit() else Decimal(NUMBERS.get(word, 1))


def classify(text):
    """Pure text rules. Returns kind, engineer flag, reasons and mentioned tags."""
    text = (text or "").strip()
    tags = sorted(set(TAG.findall(text)))
    if not text or UNREADABLE.search(text):
        return {
            "kind": "review",
            "engineer": False,
            "reasons": ["the mark could not be read"],
            "tags": tags,
        }
    engineer = _reasons(ENGINEER, text)
    if NO_ACTION.search(text) and len(text) < 120:
        return {
            "kind": "no_action",
            "engineer": False,
            "reasons": ["review stamp"],
            "tags": tags,
        }
    scope = _reasons(SCOPE, text)
    if scope:
        kind, reasons = "scope", scope
    elif QUESTION.search(text):
        kind, reasons = "question", ["asks for confirmation"]
    elif CHANGE.search(text):
        kind, reasons = "change", ["requests a drawing or BOM change"]
    else:
        kind, reasons = "review", ["unclear request"]
    return {
        "kind": kind,
        "engineer": bool(engineer),
        "reasons": reasons + engineer,
        "tags": tags,
    }


def price_book(s, order):
    """Trusted ERP price rows and accepted-scope records for this order only."""
    from backend.domain.service import scoped
    from backend.lifecycle.service import _trusted_commercial_source
    from backend.storage.models import Document, Span

    prices, scope = [], []
    docs = [
        d
        for d in s.scalars(
            scoped(Document).where(Document.project_id == order.project_id)
        )
        if d.meta.get("lifecycle_trust")
        or d.meta.get("connected_source", {}).get("kind") == "erp"
    ]
    for doc in docs:
        for span in s.scalars(scoped(Span).where(Span.document_id == doc.id)):
            try:
                value = json.loads(span.text)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            try:
                if value.get("unit_cost") is not None and _trusted_commercial_source(
                    s, order, span.id, "pricing"
                ):
                    prices.append({**value, "source_id": span.id, "document": doc.name})
                elif value.get("billable_scope_tag") and _trusted_commercial_source(
                    s, order, span.id, "commercial_scope"
                ):
                    scope.append({**value, "source_id": span.id, "document": doc.name})
            except HTTPException:  # an unverifiable source never prices anything
                continue
    return {"prices": prices, "scope": scope}


STOP = {
    "the", "a", "an", "and", "or", "for", "of", "to", "in", "on", "is", "are", "be", "this", "that",
    "with", "from", "per", "our", "your", "please", "also", "one", "two", "new", "add", "extra",
    "additional", "spare", "scope", "price", "quote", "option", "states", "contract", "fictional",
    "excluded", "exclude", "customer", "requires", "approval", "accepted", "base", "item", "items",
}  # fmt: skip


def _words(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in STOP}


def _scope_sentences(text):
    """Only the sentences that actually ask for added scope name the priced item."""
    parts = [p for p in re.split(r"(?<=[.;?!])\s+|\n+", text or "") if p.strip()]
    return [p for p in parts if any(pattern.search(p) for pattern, _ in SCOPE)] or parts


def _estimate(text, book):
    sentences = _scope_sentences(text)
    scoped_text = " ".join(sentences)
    tags = set(TAG.findall(scoped_text))
    by_tag = {str(p.get("tag")): p for p in book["prices"] if p.get("tag")}
    row = next((by_tag[t] for t in sorted(tags) if t in by_tag), None)
    if not row:
        row = next(
            (
                p
                for p in book["prices"]
                if p.get("part_number") and str(p["part_number"]) in scoped_text
            ),
            None,
        )
    basis = "ERP price for the item named in the comment"
    if not row:
        # The accepted-scope record names an excluded, billable item; use it only when the
        # comment's scope sentence names the same kind of equipment (e.g. "breaker").
        words = _words(scoped_text)
        billable = [
            x
            for x in book["scope"]
            if x.get("billable") is True
            and x.get("billable_scope_tag") in by_tag
            and words & _words(" ".join(str(v) for v in x.values()))
        ]
        if len(billable) == 1:
            row = by_tag[billable[0]["billable_scope_tag"]]
            basis = "ERP price for the excluded item on the accepted-scope record"
    if not row:
        return {"status": "needs_pricing"}
    try:
        quantity = _quantity(scoped_text)
        unit = gross_margin(row["unit_cost"], row.get("gross_margin_percent", 0))
    except (ValueError, ArithmeticError):
        return {"status": "needs_pricing"}
    billable = next(
        (
            x.get("billable")
            for x in book["scope"]
            if x.get("billable_scope_tag") == row.get("tag")
        ),
        None,
    )
    return {
        "status": "estimated",
        "tag": str(row.get("tag")),
        "quantity": str(quantity),
        "unit_price": str(unit),
        "estimate": str(money(unit * quantity)),
        "currency": str(row.get("currency") or "USD"),
        "basis": basis,
        "source_id": row["source_id"],
        "source": row["document"],
        "billable": "per accepted scope" if billable is True else "unconfirmed",
    }


def annotate(s, order, comments):
    """Attach `impact` to each comment and return the order summary."""
    book = price_book(s, order)
    counts = {"change": 0, "question": 0, "scope": 0, "no_action": 0, "review": 0}
    scope_items, engineer = [], []
    for c in comments:
        impact = classify(c.get("text") or c.get("original_text") or "")
        impact["method"] = "automatic"
        if impact["kind"] == "scope":
            impact["cost"] = _estimate(c.get("text") or "", book)
            scope_items.append(
                {
                    "id": c["id"],
                    "number": c.get("number", ""),
                    "text": c.get("text", ""),
                    "cost": impact["cost"],
                }
            )
        c["impact"] = impact
        counts[impact["kind"]] += 1
        if impact["engineer"]:
            engineer.append(c.get("number", ""))
    # The same comment can be recorded on more than one revision: count it once.
    unique = {(i["number"], i["text"].strip()): i for i in scope_items}.values()
    priced = [i["cost"] for i in unique if i["cost"]["status"] == "estimated"]
    currencies = {p["currency"] for p in priced}
    return {
        "counts": counts,
        "engineer": list(dict.fromkeys(engineer)),
        "scope": scope_items,
        "estimated_total": str(
            money(sum((Decimal(p["estimate"]) for p in priced), Decimal(0)))
        )
        if priced and len(currencies) == 1
        else None,
        "currency": currencies.pop() if len(currencies) == 1 else None,
        "needs_pricing": sum(i["cost"]["status"] == "needs_pricing" for i in unique),
        "method": "automatic",
        "note": "Automatic labels from the comment text. Costs come only from trusted ERP prices; a person confirms scope and billability.",
    }
