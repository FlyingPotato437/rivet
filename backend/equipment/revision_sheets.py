"""Source-cited sheet identity and revision matching, independent of page order.

Only explicit visible drawing/sheet/title fields establish identity. Similar
pages or a sheet's position never establish a match. Renumbering needs a unique
explicit title and identical independent body text; other candidates stay open.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict

FIELDS = {
    "drawing_id": re.compile(
        r"^(?:DRAWING|DWG|DRG)[ ._-]*(?:NO\.?|NUMBER|#|ID)\s*[:#]?\s*([A-Z0-9][A-Z0-9_.\-/]{0,79})\b",
        re.IGNORECASE,
    ),
    "sheet_id": re.compile(
        r"^SHEET(?:[ ._-]*(?:NO\.?|NUMBER|#|ID))?\s*[:#]?\s*([A-Z0-9][A-Z0-9_.\-/]{0,79})(?:\s+OF\s+\d+)?\b",
        re.IGNORECASE,
    ),
    "revision": re.compile(
        r"^(?:REVISION|REV\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9_.-]{0,19})\b", re.IGNORECASE
    ),
    "title": re.compile(
        r"^(?:DRAWING\s+TITLE|TITLE)\s*[:#]\s*(.{1,160})$", re.IGNORECASE
    ),
}


def sheet_identity(page, words, checksum):
    # Words have already passed the vector adapter's visible-text guard. Their
    # evidence is in visible-page coordinates, including rotation/crop handling.
    groups = defaultdict(list)
    for word in words:
        groups[round(word["center"][1] / 2)].append(word)
    lines = []
    for group in groups.values():
        group.sort(key=lambda w: w["bbox"][0])
        cluster = []
        for word in group:
            if cluster and word["bbox"][0] - cluster[-1]["bbox"][2] > max(
                20, word["size"] * 2.5
            ):
                lines.append((" ".join(w["text"] for w in cluster), cluster))
                cluster = []
            cluster.append(word)
        if cluster:
            lines.append((" ".join(w["text"] for w in cluster), cluster))
    values, evidence = defaultdict(set), defaultdict(list)
    for text, group in lines:
        # A literal field label inside body prose or a referenced instruction is
        # not promoted into the title block. Bottom/right fields are accepted;
        # a wholly dedicated Drawing No./Sheet field is also explicit evidence.
        for field, pattern in FIELDS.items():
            match = pattern.match(text.strip())
            if not match:
                continue
            value = match[1].strip().upper()
            if value in {"NO", "NUMBER", "REV", "REVISION", "OF", "TITLE"}:
                continue
            values[field].add(value)
            evidence[field].extend(w["evidence"] for w in group)
    fields = {
        key: next(iter(items)) if len(items) == 1 else None
        for key, items in values.items()
    }
    conflicts = {key: sorted(items) for key, items in values.items() if len(items) > 1}
    title_words = {
        id(w)
        for _, group in lines
        for w in group
        if any(w["evidence"] in anchors for anchors in evidence.values())
    }
    body = [
        w["text"]
        for w in sorted(words, key=lambda w: (round(w["center"][1], 1), w["center"][0]))
        if id(w) not in title_words and w["center"][1] < page.cropbox.height * 0.85
    ]
    return {
        "status": "ambiguous"
        if conflicts
        else "identified"
        if fields.get("drawing_id") or fields.get("sheet_id")
        else "unknown",
        **{field: fields.get(field) for field in FIELDS},
        "conflicting_fields": conflicts,
        "evidence": dict(evidence),
        "original_sha256": checksum,
        "body_text_sha256": hashlib.sha256("\x1f".join(body).encode()).hexdigest()
        if body
        else None,
        "body_word_count": len(body),
    }


def match_sheet_indexes(before_pages, after_pages):
    def eligible(p):
        return p.get("sheet_identity", {}).get("status") == "identified"

    old, new = {p["page"]: p for p in before_pages}, {p["page"]: p for p in after_pages}
    left, right = set(old), set(new)
    matches, ambiguous = [], []

    def pair_by(key, method):
        a, b = defaultdict(list), defaultdict(list)
        for n in left:
            if eligible(old[n]) and (value := key(old[n]["sheet_identity"])):
                a[value].append(n)
        for n in right:
            if eligible(new[n]) and (value := key(new[n]["sheet_identity"])):
                b[value].append(n)
        for value in sorted(a.keys() & b.keys()):
            if len(a[value]) != 1 or len(b[value]) != 1:
                ambiguous.append(
                    {
                        "identity": value,
                        "baseline_pages": a[value],
                        "target_pages": b[value],
                        "reason": "Repeated title-block identity requires a reviewed sheet mapping.",
                    }
                )
                continue
            x, y = a[value][0], b[value][0]
            matches.append(
                {
                    "baseline_page": x,
                    "target_page": y,
                    "identity": value,
                    "method": method,
                    "status": "renumbered"
                    if any(
                        old[x]["sheet_identity"].get(k)
                        != new[y]["sheet_identity"].get(k)
                        for k in ("drawing_id", "sheet_id")
                    )
                    else "matched",
                    "baseline_identity": old[x]["sheet_identity"],
                    "target_identity": new[y]["sheet_identity"],
                }
            )
            left.remove(x)
            right.remove(y)

    # A master drawing number may repeat across its individual sheet IDs.
    old_counts = Counter(
        p.get("sheet_identity", {}).get("drawing_id")
        for p in before_pages
        if eligible(p)
    )
    new_counts = Counter(
        p.get("sheet_identity", {}).get("drawing_id")
        for p in after_pages
        if eligible(p)
    )
    pair_by(
        lambda s: (
            s["drawing_id"]
            if s.get("drawing_id")
            and old_counts[s["drawing_id"]] == new_counts[s["drawing_id"]] == 1
            else None
        ),
        "unique_drawing_id",
    )
    pair_by(
        lambda s: (
            s["drawing_id"] + "|" + s["sheet_id"]
            if s.get("drawing_id") and s.get("sheet_id")
            else None
        ),
        "drawing_and_sheet_id",
    )
    pair_by(
        lambda s: (
            s["sheet_id"] if not s.get("drawing_id") and s.get("sheet_id") else None
        ),
        "explicit_sheet_id",
    )
    pair_by(
        lambda s: (
            s["title"] + "|" + s["body_text_sha256"]
            if s.get("title")
            and s.get("body_word_count", 0) >= 5
            and s.get("body_text_sha256")
            else None
        ),
        "unique_title_and_unchanged_body",
    )
    unresolved = {
        "baseline": sorted(n for n in left if not eligible(old[n])),
        "target": sorted(n for n in right if not eligible(new[n])),
    }
    complete = not unresolved["baseline"] and not unresolved["target"] and not ambiguous
    return {
        "matches": sorted(matches, key=lambda m: m["baseline_page"]),
        "removed": [
            {
                "baseline_page": n,
                "identity": old[n].get("sheet_identity"),
                "status": "removed" if complete else "not_observed_in_target",
                "absence_verified": complete,
            }
            for n in sorted(left)
        ],
        "added": [
            {
                "target_page": n,
                "identity": new[n].get("sheet_identity"),
                "status": "added" if complete else "not_observed_in_baseline",
                "absence_verified": complete,
            }
            for n in sorted(right)
        ],
        "ambiguous": ambiguous,
        "unidentified_pages": unresolved,
        "complete_sheet_identity_coverage": complete,
        "page_order_used_for_matching": False,
        "engineering_approved": False,
    }
