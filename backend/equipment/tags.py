"""Exact electrical tag spelling; separators are never inferred aliases."""

import re
from collections import defaultdict

SPACED_PREFIX = r"(?:FDR|BKR|CB|SW|RLY|CT|PT|TB|MTR|BUS|DS|FUSE|KA|KM)"
TAG_BODY = (
    r"(?:[A-Za-z0-9]{1,10}[-_]\d{1,8}(?:[-_][A-Za-z0-9]{1,12})?"
    rf"|{SPACED_PREFIX}[ ]+\d{{1,8}}[A-Za-z]?"
    r"|\d{1,6}(?!(?:A|V|VAC|VDC|KA|HZ)\b)[A-Za-z]{1,6}(?:\d{1,5})?)"
)
TAG_FULL = re.compile(TAG_BODY, re.IGNORECASE)
TAG_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + TAG_BODY + r")(?![A-Za-z0-9])", re.IGNORECASE
)


def ambiguous_tag_styles(tags):
    grouped = defaultdict(set)
    for tag in tags:
        grouped[re.sub(r"[^A-Z0-9]", "", tag.upper())].add(tag)
    return [sorted(values) for values in grouped.values() if len(values) > 1]
