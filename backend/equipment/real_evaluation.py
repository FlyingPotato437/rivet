"""Score real source packages against separate, explicitly reviewed references.

No extractor output becomes an answer key. Partial annotations establish recall,
not precision outside the labelled subset. Missing labels yield null metrics.
Native PDF annotation transport, actionable-comment extraction, equipment
extraction and downstream revision checks are distinct measurements.

This module has no database, network, provider or source-writing dependency.
Originals stay outside git. Real/private and synthetic-control results must never
be pooled as though controls were independent customer packages.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from backend.ingestion.markup_classification import annotation_actionability

MANIFEST_SCHEMA = "rivet.real-packages.v1"
KEY_SCHEMA = "rivet.real-answer-key.v1"
PREDICTION_SCHEMA = "rivet.real-predictions.v1"
REPORT_SCHEMA = "rivet.real-evaluation.v1"
CATEGORIES = ("comments", "devices", "bom_rows", "issues", "revision_checks")


class EvaluationError(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def normalized_text(value):
    """Formatting only; retain technical numbers, units and internal punctuation."""
    value = unicodedata.normalize("NFKC", str(value)).translate(
        str.maketrans(
            {
                "‐": "-",
                "‑": "-",
                "‒": "-",
                "–": "-",
                "—": "-",
                "−": "-",
                "’": "'",
                "‘": "'",
                "“": '"',
                "”": '"',
            }
        )
    )
    value = re.sub(r"^\s*(?:[-•]\s+|\d{1,3}[.)]+\s+)", "", value)
    return " ".join(value.split()).casefold().rstrip(" .")


def _valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value)


def _valid_page(value):
    return type(value) is int and value >= 1


def _valid_bbox(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(
            type(v) in {int, float} and math.isfinite(v) and 0 <= v <= 1 for v in value
        )
        and value[0] < value[2]
        and value[1] < value[3]
    )


def _overlap(a, b):
    if not _valid_bbox(a) or not _valid_bbox(b):
        return False
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    minimum = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return intersection / minimum >= 0.5


def _review(key):
    review = key.get("review", {})
    if review.get("method") not in {"human_review", "assistant_visual_review"}:
        raise EvaluationError(
            "References need an explicit human or assistant visual review method."
        )
    if not str(review.get("reviewer", "")).strip():
        raise EvaluationError("References need the actual reference reviewer identity.")
    try:
        stamp = datetime.fromisoformat(review["reviewed_at"])
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise EvaluationError(
            "Reference review time must include its timezone."
        ) from exc
    if (
        review.get("method") == "assistant_visual_review"
        and review.get("engineering_reviewed", False) is not False
    ):
        raise EvaluationError(
            "Assistant visual labels cannot assert engineering review."
        )
    return review


def validate_answer_key(key, predictions):
    if key.get("schema_version") != KEY_SCHEMA:
        raise EvaluationError("Unsupported real-document answer-key schema.")
    if key.get("package_id") != predictions.get("package_id"):
        raise EvaluationError("The answer key belongs to another package.")
    _review(key)
    labelled_hashes = key.get("source_hashes", {})
    actual_hashes = predictions.get("source_hashes", {})
    if not labelled_hashes or any(
        not _valid_hash(value) for value in labelled_hashes.values()
    ):
        raise EvaluationError("References must be pinned to actual original SHA256s.")
    if any(
        actual_hashes.get(source_id) != checksum
        for source_id, checksum in labelled_hashes.items()
    ):
        raise EvaluationError(
            "Reference and prediction source hashes differ; the run is unscored."
        )
    labels = key.get("labels", {})
    if not isinstance(labels, dict):
        raise EvaluationError(
            "Labels must be an object; use null for unlabelled categories."
        )
    for category in CATEGORIES:
        label = labels.get(category)
        if label is None:
            continue
        if (
            not isinstance(label, dict)
            or label.get("scope") not in {"complete", "partial"}
            or not isinstance(label.get("items"), list)
        ):
            raise EvaluationError(
                f"{category} needs explicit complete/partial scope and an items list."
            )
        if len(label["items"]) > 20_000:
            raise EvaluationError(
                "Reference item count exceeds the bounded scoring limit."
            )
        if any(not isinstance(item, dict) for item in label["items"]):
            raise EvaluationError(
                "Reference items must be explicitly labelled objects."
            )
        ids = [item.get("id") for item in label["items"]]
        if any(not isinstance(item_id, str) or not item_id for item_id in ids) or len(
            set(ids)
        ) != len(ids):
            raise EvaluationError(
                f"{category} reference IDs must be nonempty and unique."
            )
        if category in {"comments", "devices"}:
            pages = label.get("pages")
            if (
                not isinstance(pages, list)
                or not pages
                or any(
                    page.get("source_id") not in labelled_hashes
                    or not _valid_page(page.get("page"))
                    for page in pages
                )
            ):
                raise EvaluationError(
                    f"{category} needs an explicit, source-bound list of reviewed pages."
                )
            domain = {(page["source_id"], page["page"]) for page in pages}
            if any(
                (item.get("source_id"), item.get("page")) not in domain
                for item in label["items"]
            ):
                raise EvaluationError(
                    f"A {category} reference lies outside its reviewed pages."
                )
        if category == "comments":
            if any(
                type(item.get("actionable")) is not bool
                or not str(item.get("text", "")).strip()
                for item in label["items"]
            ):
                raise EvaluationError(
                    "Comment references need explicit actionable true/false and visible text."
                )
        elif category == "devices" and any(
            not str(item.get("tag", "")).strip() for item in label["items"]
        ):
            raise EvaluationError("Device references need exact source tags.")
        elif category == "bom_rows":
            sources = label.get("source_ids", [])
            if not sources or any(
                source_id not in labelled_hashes for source_id in sources
            ):
                raise EvaluationError("BOM labels need explicitly reviewed source IDs.")
            if any(
                item.get("source_id") not in sources
                or not (
                    _valid_page(item.get("page")) or str(item.get("sheet", "")).strip()
                )
                or not _valid_page(item.get("row"))
                for item in label["items"]
            ):
                raise EvaluationError(
                    "BOM references need an exact physical page or sheet name and positive source row numbers."
                )
        elif category in {"issues", "revision_checks"} and any(
            not str(item.get("kind", "")).strip() for item in label["items"]
        ):
            raise EvaluationError("Check references need an explicit check kind.")


def merge_comment_predictions(native, external):
    """Augment actual native comments, avoiding only demonstrable duplicates.

    Equal text alone does not collapse repeated comments. Explicit annotation ID
    or overlapping source bounds is needed; ambiguous duplicates stay visible.
    """
    merged = deepcopy(native)
    duplicates, ambiguous = [], []
    for candidate in external:
        matches, similar = [], []
        for index, observed in enumerate(merged):
            if (candidate.get("source_id"), candidate.get("page")) != (
                observed.get("source_id"),
                observed.get("page"),
            ):
                continue
            same_text = normalized_text(candidate.get("text", "")) == normalized_text(
                observed.get("text", "")
            )
            explicit = candidate.get("native_annotation_id") and candidate[
                "native_annotation_id"
            ] == observed.get("native_annotation_id")
            geometric = same_text and _overlap(
                candidate.get("bbox"), observed.get("bbox")
            )
            if explicit or geometric:
                matches.append(index)
            elif same_text:
                similar.append(index)
        if len(matches) == 1:
            previous = merged[matches[0]]
            # A visual transcript may correct native text, but no candidate
            # invents a new PDF annotation identity or original source anchor.
            merged[matches[0]] = {
                **previous,
                **candidate,
                "id": previous.get("id", candidate.get("id")),
                "native_annotation_id": previous.get("native_annotation_id")
                or candidate.get("native_annotation_id"),
                "origins": sorted(
                    set(
                        previous.get(
                            "origins", [previous.get("origin", "native_annotation")]
                        )
                    )
                    | set(candidate.get("origins", [candidate.get("origin", "vision")]))
                ),
            }
            duplicates.append(
                {
                    "retained_id": previous.get("id"),
                    "merged_id": candidate.get("id"),
                    "method": "annotation_id"
                    if candidate.get("native_annotation_id")
                    else "same_text_overlapping_bounds",
                }
            )
        else:
            if matches or similar:
                ambiguous.append(
                    {
                        "candidate_id": candidate.get("id"),
                        "possible_matches": [
                            merged[index].get("id")
                            for index in sorted(set(matches + similar))
                        ],
                        "reason": "No unique source-grounded duplicate match; retained separately.",
                    }
                )
            merged.append(deepcopy(candidate))
    return merged, {"merged_duplicates": duplicates, "ambiguous_duplicates": ambiguous}


def _in_scope(item, label, category):
    if category in {"comments", "devices"}:
        return (item.get("source_id"), item.get("page")) in {
            (page["source_id"], page["page"]) for page in label["pages"]
        }
    if category == "bom_rows":
        return item.get("source_id") in label["source_ids"]
    if label.get("source_ids"):
        return item.get("source_id") in label["source_ids"]
    return True


def _matches(expected, observed, category, mode="transcription"):
    if category == "comments":
        if (expected.get("source_id"), expected.get("page")) != (
            observed.get("source_id"),
            observed.get("page"),
        ):
            return False
        if mode == "detection" and expected.get("bbox"):
            return _overlap(expected["bbox"], observed.get("bbox"))
        accepted = [expected["text"], *expected.get("accepted_texts", [])]
        return normalized_text(observed.get("text", "")) in {
            normalized_text(text) for text in accepted
        }
    if category == "devices":
        return all(
            expected.get(field) == observed.get(field)
            for field in ("source_id", "page", "tag")
        )
    if category == "bom_rows":
        # A PDF has a physical page, not a worksheet name. Reviewer-friendly
        # table captions must not become required extractor-specific labels.
        location_fields = (
            ("source_id", "page", "row")
            if _valid_page(expected.get("page"))
            else ("source_id", "sheet", "row")
        )
        return all(
            expected.get(field) == observed.get(field) for field in location_fields
        ) and (
            not expected.get("device_tag")
            or expected["device_tag"] == observed.get("device_tag")
        )
    if category in {"issues", "revision_checks"}:
        # Two identical printed additions in different sections are separate
        # changes. A shared phrase must not match the wrong source location.
        for field, alias in (
            ("target_bbox", "after_bbox"),
            ("baseline_bbox", "before_bbox"),
        ):
            region = expected.get(field, expected.get(alias))
            if region is not None and not _overlap(region, observed.get(field)):
                return False
    for field in (
        "kind",
        "device_tag",
        "field",
        "source_id",
        "page",
        "status",
        "before",
        "after",
    ):
        if field in expected and expected[field] != observed.get(field):
            if field == "kind" and observed.get(field) in expected.get(
                "accepted_kinds", []
            ):
                continue
            return False
    return True


def _maximum_matches(expected, observed, category, mode="transcription"):
    def bucket(item):
        if category == "bom_rows" and _valid_page(item.get("page")):
            return (
                item.get("source_id"),
                "physical_pdf_page",
                item["page"],
                item.get("row"),
            )
        fields = {
            "comments": ("source_id", "page"),
            "devices": ("source_id", "page", "tag"),
            "bom_rows": ("source_id", "sheet", "row"),
        }.get(category, ("kind",))
        return tuple(item.get(field) for field in fields)

    index = defaultdict(list)
    for number, item in enumerate(observed):
        index[bucket(item)].append(number)
    candidates, compared = [], 0
    for item in expected:
        possible = index[bucket(item)]
        if category in {"issues", "revision_checks"}:
            possible = list(
                dict.fromkeys(
                    [
                        *possible,
                        *(
                            number
                            for kind in item.get("accepted_kinds", [])
                            for number in index[(kind,)]
                        ),
                    ]
                )
            )
        compared += len(possible)
        if compared > 2_000_000:
            raise EvaluationError(
                "Scoring exceeds the bounded matching workload; divide reviewed data into page/sheet scopes."
            )
        candidates.append(
            [
                number
                for number in possible
                if _matches(item, observed[number], category, mode)
            ]
        )
    # Explicit one-to-one augmenting paths handle accepted transcription variants
    # without allowing a single prediction to satisfy multiple reference notes.
    assigned, by_reference = {}, {}
    for reference in sorted(
        range(len(expected)), key=lambda index: len(candidates[index])
    ):
        queue, visited, predecessor, free = [reference], {reference}, {}, None
        for current in queue:
            for prediction in candidates[current]:
                if prediction in predecessor:
                    continue
                predecessor[prediction] = current
                if prediction not in assigned:
                    free = prediction
                    break
                owner = assigned[prediction]
                if owner not in visited:
                    visited.add(owner)
                    queue.append(owner)
            if free is not None:
                break
        while free is not None:
            owner = predecessor[free]
            previous = by_reference.get(owner)
            assigned[free] = owner
            by_reference[owner] = free
            free = previous
    return {reference: prediction for prediction, reference in assigned.items()}


def _score(label, predictions, category, mode="transcription"):
    if label is None:
        return None
    expected = label["items"]
    observed = [item for item in predictions if _in_scope(item, label, category)]
    out_of_scope = [
        item for item in predictions if not _in_scope(item, label, category)
    ]
    matches = _maximum_matches(expected, observed, category, mode)
    matched_predictions = set(matches.values())
    complete = label["scope"] == "complete"
    missed = [item for index, item in enumerate(expected) if index not in matches]
    unmatched = [
        item for index, item in enumerate(observed) if index not in matched_predictions
    ]
    tp, fn = len(matches), len(missed)
    fp = len(unmatched) if complete else None
    precision = tp / (tp + fp) if complete and tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    result = {
        "scope": label["scope"],
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "precision": precision,
        "recall": recall,
        "f1": (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
        if precision is not None and recall is not None
        else None,
        "expected_count": len(expected),
        "prediction_count_in_scope": len(observed),
        "matched": [
            {
                "reference_id": expected[reference]["id"],
                "prediction_id": observed[prediction].get("id"),
            }
            for reference, prediction in sorted(matches.items())
        ],
        "missed": missed,
        "false_positives": unmatched if complete else None,
        "unscored_predictions": out_of_scope + ([] if complete else unmatched),
        "empty_reference_meaning": "Explicit reviewed empty scope; recall remains undefined, never 100%."
        if not expected
        else None,
    }
    if category == "comments":
        result["measurement"] = (
            "Location-based note detection where reference boxes exist; otherwise accepted normalized phrases. One block matches at most one reference."
            if mode == "detection"
            else "Normalized transcription; leading numbering/bullets, dash typography, whitespace and final periods are ignored, technical values are retained."
        )
    if category == "bom_rows":
        correct, wrong, missing = [], [], []
        for reference, reference_row in enumerate(expected):
            prediction = matches.get(reference)
            actual_fields = (
                observed[prediction].get("fields", {}) if prediction is not None else {}
            )
            for field, expected_value in reference_row.get("fields", {}).items():
                actual = actual_fields.get(field)
                item = {
                    "reference_id": reference_row["id"],
                    "field": field,
                    "expected": expected_value,
                    "observed": actual,
                    "row_missing": prediction is None,
                }
                (
                    missing
                    if actual is None
                    else correct
                    if actual == expected_value
                    or isinstance(actual, str)
                    and isinstance(expected_value, str)
                    and normalized_text(actual) == normalized_text(expected_value)
                    else wrong
                ).append(item)
        total = len(correct) + len(wrong) + len(missing)
        result["field_values"] = {
            "correct": len(correct),
            "wrong": len(wrong),
            "missing": len(missing),
            "accuracy_on_labelled_fields": len(correct) / total if total else None,
            "errors": wrong + missing,
        }
        result["measurement"] = (
            "Source-row detection; labelled cell values are scored separately."
        )
    return result


def _comment_content_coverage(label, predictions):
    if label is None:
        return None
    covered, missed = [], []
    for expected in label["items"]:
        phrases = [
            normalized_text(text)
            for text in [expected["text"], *expected.get("accepted_texts", [])]
        ]
        candidates = [
            observed
            for observed in predictions
            if (observed.get("source_id"), observed.get("page"))
            == (expected["source_id"], expected["page"])
        ]
        found = [
            observed.get("id")
            for observed in candidates
            if any(
                phrase and phrase in normalized_text(observed.get("text", ""))
                for phrase in phrases
            )
        ]
        (covered if found else missed).append(
            {"reference_id": expected["id"], "containing_prediction_ids": found}
        )
    total = len(covered) + len(missed)
    return {
        "covered_reference_texts": len(covered),
        "reference_texts": total,
        "recall": len(covered) / total if total else None,
        "covered": covered,
        "missed": missed,
        "measurement": "Text presence inside returned blocks only; not distinct comment segmentation, location or correctness.",
    }


def score_predictions(key, predictions):
    """Pure scoring of a frozen prediction file against independent references."""
    if predictions.get("schema_version") != PREDICTION_SCHEMA:
        raise EvaluationError("Unsupported prediction schema.")
    validate_answer_key(key, predictions)
    labels = deepcopy(key.get("labels", {}))
    comments = [
        item
        for item in predictions.get("comments", [])
        if item.get("actionable") is True
    ]
    comment_label = labels.get("comments")
    non_actionable = None
    if comment_label is not None:
        non_actionable = {
            **comment_label,
            "scope": "complete"
            if comment_label.get("non_actionable_complete") is True
            else "partial",
            "items": [
                item for item in comment_label["items"] if not item["actionable"]
            ],
        }
        comment_label["items"] = [
            item for item in comment_label["items"] if item["actionable"]
        ]
    extraction = {
        "actionable_comments": _score(comment_label, comments, "comments", "detection"),
        "comment_transcription": _score(comment_label, comments, "comments"),
        "comment_content_coverage": _comment_content_coverage(comment_label, comments),
        "non_actionable_marks": _score(
            non_actionable,
            [
                item
                for item in predictions.get("comments", [])
                if item.get("actionable") is False
            ],
            "comments",
        ),
        "device_tags": _score(
            labels.get("devices"),
            _unique_tags(predictions.get("devices", [])),
            "devices",
        ),
        "bom_rows": _score(
            labels.get("bom_rows"), predictions.get("bom_rows", []), "bom_rows"
        ),
        "unclassified_comment_predictions": [
            item
            for item in predictions.get("comments", [])
            if item.get("actionable") is None
        ],
    }
    checks = {
        category: _score(labels.get(category), predictions.get(category, []), category)
        for category in ("issues", "revision_checks")
    }
    return {
        "package_id": predictions["package_id"],
        "reference_review": _review(key),
        "reference_sha256": _hash(key),
        "prediction_sha256": _hash(predictions),
        "reference_status": "preliminary_assistant_visual_reference"
        if key["review"]["method"] == "assistant_visual_review"
        else "human_reviewed_reference",
        "extraction": extraction,
        "checks": checks,
        "limits": [
            "Reference matches are not engineering approval or manufacturer-validated production accuracy.",
            "Extraction recall is separate from issue detection and revision-check correctness.",
            "Null metrics mean unlabelled or undefined; partial reference scopes do not establish false positives or precision.",
            "Device tags measure exact source/page tag presence, not an independently complete physical device inventory.",
        ],
        "engineering_accuracy_validated": False,
    }


def _unique_tags(items):
    return list(
        {
            (item.get("source_id"), item.get("page"), item.get("tag")): item
            for item in items
            if isinstance(item.get("tag"), str) and item["tag"].strip()
        }.values()
    )


def _stage(errors, timings, name, call, *, source_id=None):
    started = time.perf_counter()
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - report one bounded engine failure; never substitute success.
        errors.append(
            {
                "stage": name,
                "source_id": source_id,
                "error_type": type(exc).__name__,
                "message": str(exc)[:1500],
            }
        )
        return None
    finally:
        timings[name] = timings.get(name, 0.0) + time.perf_counter() - started


def _source_inputs(package, base):
    from .schemas import SourceInput

    inputs, hashes, metadata = [], {}, {}
    if not isinstance(package.get("sources"), list) or not package["sources"]:
        raise EvaluationError("A package needs explicit original source files.")
    for item in package["sources"]:
        if item.get("id") in hashes:
            raise EvaluationError("Source IDs must be unique in a package.")
        if item.get("side") not in {"baseline", "target", "requirements"}:
            raise EvaluationError(
                "Each source needs baseline, target or requirements side."
            )
        path = (base / item["path"]).resolve()
        if not path.is_file():
            raise EvaluationError(f"Original source {item.get('id')} is unavailable.")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if not _valid_hash(item.get("sha256")) or checksum != item["sha256"].lower():
            raise EvaluationError(
                f"Original source {item.get('id')} changed from its pinned hash."
            )
        source = SourceInput(
            id=item["id"],
            name=item.get("name") or path.name,
            path=str(path),
            role=item["role"],
            revision=item["revision"],
            sha256=checksum,
            lifecycle=item.get("lifecycle", "draft"),
        )
        inputs.append(source)
        hashes[source.id] = checksum
        metadata[source.id] = {**item, "path": str(path)}
    return inputs, hashes, metadata


def _probe_pdf(source):
    import pymupdf

    pages, annotations, errors = [], [], []
    with pymupdf.open(source.path) as pdf:
        if pdf.needs_pass:
            raise EvaluationError(
                "Encrypted PDF original cannot be evaluated without a readable authorized copy."
            )
        total = len(pdf)
        for index in range(total):
            try:
                page = pdf[index]
                text = page.get_text().strip()
                pages.append(
                    {
                        "source_id": source.id,
                        "page": index + 1,
                        "status": "text_present"
                        if text
                        else "image_only"
                        if page.get_images()
                        else "no_selectable_text",
                    }
                )
                for number, annotation in enumerate(page.annots() or [], 1):
                    if annotation.type[1] in {"Link", "Widget", "Popup"}:
                        continue
                    content = str(annotation.info.get("content", "")).strip()
                    if content:
                        annotations.append(
                            {
                                "id": annotation.info.get("id")
                                or f"p{index + 1}-a{number}",
                                "source_id": source.id,
                                "page": index + 1,
                                "text": content,
                            }
                        )
            except Exception as exc:  # noqa: BLE001 - retain every unreadable original page.
                errors.append(
                    {
                        "source_id": source.id,
                        "page": index + 1,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                )
                if not any(item["page"] == index + 1 for item in pages):
                    pages.append(
                        {
                            "source_id": source.id,
                            "page": index + 1,
                            "status": "unreadable",
                        }
                    )
    return {
        "physical_pages": total,
        "pages": pages,
        "annotation_texts": annotations,
        "page_errors": errors,
    }


def _annotation_transport(expected, actual, *, oracle_failed=False):
    if oracle_failed:
        return {
            "status": "oracle_incomplete",
            "tp": None,
            "fn": None,
            "fp": None,
            "precision": None,
            "recall": None,
        }
    truth = Counter(
        (item["source_id"], item["page"], normalized_text(item["text"]))
        for item in expected
    )
    observations = Counter(
        (item["source_id"], item["page"], normalized_text(item["text"]))
        for item in actual
        if str(item.get("text", "")).strip()
    )
    tp, fn, fp = (
        sum((truth & observations).values()),
        sum((truth - observations).values()),
        sum((observations - truth).values()),
    )
    return {
        "status": "measured_native_text_transport"
        if truth
        else "no_native_text_expected",
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "missed": [
            {
                "source_id": key[0],
                "page": key[1],
                "normalized_text": key[2],
                "count": count,
            }
            for key, count in (truth - observations).items()
        ],
        "measurement": "Native annotation text transport only; zero annotations does not establish zero visual/actionable comments.",
        "oracle": "Independent PyMuPDF annotation-object traversal; production fallback may share this library. Not visual ground truth.",
    }


def _collect_rows(facts):
    grouped = {}
    for fact in facts:
        if fact.get("role") != "bom":
            continue
        evidence = fact.get("evidence", {})
        if not evidence.get("sheet") or not _valid_page(evidence.get("row")):
            continue
        key = fact["source_id"], evidence["sheet"], evidence["row"]
        row = grouped.setdefault(
            key,
            {
                "id": "bom-row:" + _hash(key)[:20],
                "source_id": key[0],
                "sheet": key[1],
                "row": key[2],
                "device_tag": fact["device_tag"],
                "fields": {},
                "field_conflicts": {},
            },
        )
        field, value = fact["field"], fact["value"]
        if field in row["fields"] and row["fields"][field] != value:
            row["field_conflicts"].setdefault(field, [row["fields"][field]]).append(
                value
            )
            row["fields"][field] = None
        elif field not in row["field_conflicts"]:
            row["fields"][field] = value
    return list(grouped.values())


def _bom_observations(reports, facts):
    """Raw source rows do not imply device identity or engineering verification."""
    rows = _collect_rows(facts)
    for report in reports:
        if report.get("role") != "bom":
            continue
        for original in report.get("observed_bom_rows", []):
            if original.get("status") != "observed_row":
                continue
            row = deepcopy(original)
            row["source_id"] = report["id"]
            row["origin"] = "source_bom_row_reader"
            row["engineering_verified"] = False
            location = (
                row.get("source_id"),
                row.get("page"),
                row.get("sheet"),
                row.get("row"),
            )
            rows = [
                old
                for old in rows
                if (
                    old.get("source_id"),
                    old.get("page"),
                    old.get("sheet"),
                    old.get("row"),
                )
                != location
            ]
            rows.append(row)
    return rows


def merge_bom_observations(native, visual):
    """Corroborate uniquely overlapping source rows, never suppress a page.

    Both raw readers remain in source-observations. Disagreement is unknown in
    the merged scoring view; reader confidence cannot decide which is correct.
    Row ordinals without geometric evidence are insufficient to merge PDFs.
    """
    rows, merges = deepcopy(native), []
    index = defaultdict(list)

    def anchor(row):
        return (row.get("source_id"), row.get("page"), row.get("row"), row.get("table"))

    for i, row in enumerate(rows):
        index[anchor(row)].append(i)
    for original in visual:
        candidates = [
            i
            for i in index[anchor(original)]
            if _valid_page(original.get("page"))
            and _overlap(rows[i].get("bbox"), original.get("bbox"))
        ]
        if len(candidates) != 1:
            index[anchor(original)].append(len(rows))
            rows.append(deepcopy(original))
            continue
        row = rows[candidates[0]]
        conflicts = row.setdefault("field_conflicts", {})
        fields = row.setdefault("fields", {})
        for field, value in original.get("fields", {}).items():
            previous = fields.get(field)
            if field in conflicts:
                if value is not None and value not in conflicts[field]:
                    conflicts[field].append(value)
            elif previous is None:
                fields[field] = value
            elif value is not None and normalized_text(
                str(previous)
            ) != normalized_text(str(value)):
                fields[field] = None
                conflicts[field] = [previous, value]
        row["engineering_verified"] = False
        row["requires_human_verification"] = True
        row.setdefault("reader_observation_ids", [row.get("id")]).append(
            original.get("id")
        )
        merges.append(
            {
                "source_id": row.get("source_id"),
                "page": row.get("page"),
                "row": row.get("row"),
                "observation_ids": row["reader_observation_ids"],
                "conflicting_fields": sorted(conflicts),
            }
        )
    return rows, merges


def _field_value(check, prefix):
    values = []
    for role in ("drawing", "bom"):
        cell = check.get(prefix + "_" + role, {})
        for item in cell.get("values", []):
            if item.get("value") not in values:
                values.append(item.get("value"))
    return values[0] if len(values) == 1 else values or None


def _check_predictions(comparison):
    revision, issues = [], []
    for check in comparison.get("checks", []):
        status = check["status"]
        normalized = (
            "remaining"
            if status in {"mismatch", "missing"}
            else "cannot_verify"
            if status == "unknown"
            else status
        )
        item = {
            "id": check["id"],
            "kind": "equipment_field",
            "device_tag": check["device_tag"],
            "field": check["field"],
            "status": normalized,
            "before": _field_value(check, "baseline"),
            "after": _field_value(check, "target"),
            "reason": check.get("reason"),
            "evidence": check.get("evidence", []),
        }
        revision.append(item)
        if status in {"mismatch", "missing", "unknown"}:
            issues.append(
                {
                    **item,
                    "kind": {
                        "mismatch": "field_mismatch",
                        "missing": "device_missing",
                        "unknown": "unverified_field",
                    }[status],
                }
            )
    return revision, issues


def vector_page_failure(source_id, page):
    """An explicitly failed page cannot disappear into a successful headline."""
    failures = [
        issue
        for issue in page.get("issues", [])
        if issue.get("code")
        in {"page_complexity_or_coverage", "page_parse_failed", "unreadable_page"}
    ]
    if not failures:
        return None
    return {
        "stage": "vector_pdf_page",
        "source_id": source_id,
        "page": page["page"],
        "error_type": "PageInspectionFailed",
        "message": "; ".join(
            issue.get("reason", issue.get("detail", issue["code"]))
            for issue in failures
        )[:1500],
    }


def _external_predictions(raw, package_id, hashes):
    if raw is None:
        return None
    if (
        raw.get("schema_version") != PREDICTION_SCHEMA
        or raw.get("package_id") != package_id
    ):
        raise EvaluationError(
            "External predictions need the exact schema and package ID."
        )
    cited = raw.get("source_hashes", {})
    if not cited or any(
        hashes.get(source_id) != value for source_id, value in cited.items()
    ):
        raise EvaluationError(
            "External predictions do not match pinned original hashes."
        )
    from backend.ingestion.markup_classification import actionability, atomic_findings

    raw["comments"] = atomic_findings(raw.get("comments", []))
    for category in (*CATEGORIES, "pages"):
        items = raw.get(category, [])
        if not isinstance(items, list) or len(items) > 20_000:
            raise EvaluationError(
                "External predictions exceed the bounded item format."
            )
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise EvaluationError("External prediction items must be objects.")
            if item.get("source_id") is not None and item["source_id"] not in cited:
                raise EvaluationError("An external prediction cites an unbound source.")
            if category in {"comments", "devices", "pages"} and (
                item.get("source_id") not in cited or not _valid_page(item.get("page"))
            ):
                raise EvaluationError(
                    "External page observations need a bound source and positive page."
                )
            item.setdefault("id", f"external:{category}:{index + 1}")
            if category == "comments" and item.get("subcomment_index"):
                item["id"] += ":note-" + str(item["subcomment_index"])
            item.setdefault("origin", "vision")
            if category == "comments":
                if "actionable" not in item:
                    item["actionable"] = actionability(item)
                    item["classification"] = {
                        "method": "production_markup_classification",
                        "answer_key_used": False,
                    }
                if (
                    item["actionable"] not in {True, False, None}
                    or not str(item.get("text", "")).strip()
                ):
                    raise EvaluationError(
                        "External comments require visible text and explicit actionable true/false/null."
                    )
    return raw


def extract_package(package, base, *, external=None, include_vector=True):
    """Run the actual pure readers/checkers; no model calls or DB writes."""
    from .comparison import compare_packages
    from .model import compile_model
    from .readers import read_source

    started = time.perf_counter()
    errors, timings = [], {}
    inputs, hashes, metadata = _source_inputs(package, Path(base))
    external = _external_predictions(deepcopy(external), package["id"], hashes)
    reports, facts, native, expected_native = [], [], [], []
    physical_pages, page_probes, pdf_pages = {}, [], []
    vectors, devices, source_diagnostics = [], [], []
    oracle_failed = False
    for source in inputs:
        item = {
            "source_id": source.id,
            "reader_status": None,
            "physical_pages": None,
            "equipment_pages_processed": None,
            "vector_pages_processed": None,
        }
        extracted = _stage(
            errors,
            timings,
            "equipment_readers",
            lambda s=source: read_source(s, requested_changes=s.role == "markup"),
            source_id=source.id,
        )
        if extracted:
            report, observed = extracted
            reports.append(report)
            facts.extend(observed)
            item["reader_status"] = report["status"]
            if report["status"] in {"unreadable", "unsupported"}:
                errors.append(
                    {
                        "stage": "equipment_readers",
                        "source_id": source.id,
                        "error_type": "SourceUnavailable",
                        "message": "The actual reader marked this source "
                        + report["status"]
                        + "; no clean zero result was inferred.",
                    }
                )
            item["warnings"] = report.get("warnings", [])
            page_coverage = report.get("page_coverage", [])
            unreadable_pages = {
                warning.get("page")
                for warning in report.get("warnings", [])
                if warning.get("code") in {"unreadable_page", "page_parse_failed"}
                and _valid_page(warning.get("page"))
            } | {
                page["page"]
                for page in page_coverage
                if page.get("status") in {"unreadable", "failed"}
            }
            item["equipment_page_coverage"] = page_coverage
            item["equipment_pages_processed"] = (
                sum(
                    page.get("status") in {"processed", "partial"}
                    and page.get("page") not in unreadable_pages
                    for page in page_coverage
                )
                if page_coverage
                else report.get("processed_page_count")
            )
            item["equipment_pages_attempted"] = (
                len(page_coverage) if page_coverage else None
            )
            item["equipment_pages_unreadable"] = (
                len(unreadable_pages) if page_coverage or unreadable_pages else None
            )
            item["equipment_unreadable_page_numbers"] = sorted(unreadable_pages)
            item["equipment_processing_batches"] = report.get("processing_batches", [])
            for field in observed:
                page = field.get("evidence", {}).get("page")
                if _valid_page(page) and field.get("role") in {"drawing", "bom"}:
                    devices.append(
                        {
                            "id": "field-tag:" + field["id"],
                            "source_id": source.id,
                            "page": page,
                            "tag": field["device_tag"],
                            "origin": "equipment_reader",
                        }
                    )
        if Path(source.name).suffix.lower() == ".pdf":
            probe = _stage(
                errors,
                timings,
                "original_pdf_probe",
                lambda s=source: _probe_pdf(s),
                source_id=source.id,
            )
            if probe:
                physical_pages[source.id] = probe["physical_pages"]
                item["physical_pages"] = probe["physical_pages"]
                page_probes.extend(probe["pages"])
                expected_native.extend(probe["annotation_texts"])
                if probe["page_errors"]:
                    oracle_failed = True
                    errors.extend(
                        {"stage": "original_page_probe", **error}
                        for error in probe["page_errors"]
                    )
            else:
                oracle_failed = True
            from backend.orders.extraction import parse_pdf_annotations

            annotations = _stage(
                errors,
                timings,
                "native_annotation_extraction",
                lambda s=source: parse_pdf_annotations(s.path),
                source_id=source.id,
            )
            if annotations is not None:
                item["native_annotation_pages_inspected"] = physical_pages.get(
                    source.id
                )
                for annotation in annotations:
                    actionable, reason = annotation_actionability(annotation)
                    native.append(
                        {
                            **annotation,
                            "id": "native:" + source.id + ":" + annotation["id"],
                            "native_annotation_id": annotation["id"],
                            "source_id": source.id,
                            "origin": "native_annotation",
                            "actionable": actionable,
                            "classification": {
                                "method": "fixed_annotation_content_rules_v1",
                                "reason": reason,
                                "answer_key_used": False,
                            },
                        }
                    )
            if include_vector and source.role in {"drawing", "markup"}:
                from .pdf_model import inspect_pdf

                graph = _stage(
                    errors,
                    timings,
                    "vector_pdf_extraction",
                    lambda s=source: inspect_pdf(s),
                    source_id=source.id,
                )
                if graph:
                    vectors.append(
                        (graph, source.model_dump(), metadata[source.id]["side"])
                    )
                    item["vector_pages_processed"] = len(graph.get("pages", []))
                    item["vector_coverage"] = graph.get("coverage", {})
                    for page in graph.get("pages", []):
                        failure = vector_page_failure(source.id, page)
                        if failure:
                            errors.append(failure)
                        pdf_pages.append(
                            {
                                "source_id": source.id,
                                "page": page["page"],
                                "engine": "vector_pdf",
                                "status": "failed" if failure else "processed",
                                "coverage": page.get("coverage", {}),
                                "issues": page.get("issues", []),
                            }
                        )
                    for drawn in graph.get("devices", []):
                        devices.append(
                            {
                                "id": "vector-device:"
                                + source.id
                                + ":"
                                + str(drawn.get("id", _hash(drawn)[:24])),
                                "source_id": source.id,
                                "page": drawn["page"],
                                "tag": drawn["tag"],
                                "bbox_points": drawn.get("bbox"),
                                "bbox": drawn.get("normalized_bbox"),
                                "origin": "vector_pdf",
                            }
                        )
        source_diagnostics.append(item)
    sides = {
        side: [source for source in inputs if metadata[source.id]["side"] == side]
        for side in ("baseline", "target", "requirements")
    }
    comparison = _stage(
        errors,
        timings,
        "comparison_including_reextraction",
        lambda: compare_packages(
            sides["baseline"], sides["target"], sides["requirements"]
        ),
    )
    revision, issues = _check_predictions(comparison) if comparison else ([], [])
    pdf_revisions = []
    for pair in package.get("revision_pairs", []):
        from .pdf_model import compare_pdf

        lookup = {source.id: source for source in inputs}
        before, after = (
            lookup.get(pair.get("baseline_source_id")),
            lookup.get(pair.get("target_source_id")),
        )
        if before is None or after is None:
            errors.append(
                {
                    "stage": "pdf_revision_check",
                    "message": "An explicit drawing pair cites unavailable originals.",
                }
            )
            continue
        checked = _stage(
            errors,
            timings,
            "pdf_revision_check",
            lambda a=before, b=after: compare_pdf(a, b),
        )
        if checked:
            pdf_revisions.append(checked)
            for index, change in enumerate(checked.get("changes", [])):
                revision.append(
                    {
                        **change,
                        "id": f"pdf-change:{before.id}:{after.id}:{index + 1}",
                        "source_id": after.id,
                        "status": "cannot_verify"
                        if change.get("status") == "unknown"
                        else "observed_change",
                    }
                )
    model = None
    if comparison:
        fingerprint = _hash([[source.id, source.sha256] for source in inputs])
        model = _stage(
            errors,
            timings,
            "equipment_model_projection",
            lambda: compile_model(
                comparison,
                fingerprint,
                sides["baseline"][0].revision if sides["baseline"] else "Unspecified",
                sides["target"][0].revision if sides["target"] else "Unspecified",
                pdf_snapshots=[
                    entry for entry in vectors if entry[1]["role"] == "drawing"
                ],
            ),
        )
    external_comments = external.get("comments", []) if external else []
    comments, duplicates = merge_comment_predictions(native, external_comments)
    if external:
        devices.extend(external.get("devices", []))
    source_observations = {
        "native_schedule_rows": _bom_observations(reports, facts),
        "visual_devices": external.get("devices", []) if external else [],
        "visual_schedule_rows": external.get("bom_rows", []) if external else [],
        "visual_connections": external.get("connections", []) if external else [],
        "sheet_identities": external.get("sheet_identities", []) if external else [],
        "cad_reports": [
            report["cad_report"] for report in reports if report.get("cad_report")
        ],
        "requires_human_verification": True,
        "engineering_approved": False,
    }
    if model is not None:
        model["source_observations"] = source_observations
        model["coverage"]["visual_device_observation_count"] = len(
            source_observations["visual_devices"]
        )
        model["coverage"]["visual_observations_verified"] = False
    predictions = {
        "schema_version": PREDICTION_SCHEMA,
        "package_id": package["id"],
        "source_hashes": hashes,
        "producer": {
            "name": "Rivet actual equipment readers + vector PDF + native annotations",
            "external": external.get("producer") if external else None,
        },
        "comments": comments,
        "devices": _unique_tags(devices),
        "bom_rows": _bom_observations(reports, facts),
        "issues": issues,
        "revision_checks": revision,
    }
    if external:
        # Extra check predictions stay identifiable as external. They are not
        # evidence that the current deterministic checker found the same issue.
        for category in ("issues", "revision_checks"):
            predictions[category].extend(external.get(category, []))
        predictions["bom_rows"], source_observations["bom_row_merges"] = (
            merge_bom_observations(
                predictions["bom_rows"], external.get("bom_rows", [])
            )
        )
    timings["total_seconds"] = time.perf_counter() - started
    coverage = {
        "physical_pages": sum(physical_pages.values()) if physical_pages else None,
        "physical_pages_by_source": physical_pages,
        "original_page_probe": page_probes,
        "pages_unreadable_in_probe": [
            item for item in page_probes if item["status"] == "unreadable"
        ],
        "pages_without_selectable_text": [
            item
            for item in page_probes
            if item["status"] in {"image_only", "no_selectable_text"}
        ],
        "vector_pdf_pages": pdf_pages,
        "vector_pages_processed": sum(
            item["status"] == "processed" for item in pdf_pages
        ),
        "vector_pages_failed": sum(item["status"] == "failed" for item in pdf_pages),
        "equipment_pages_processed": sum(
            item.get("equipment_pages_processed") or 0 for item in source_diagnostics
        )
        if any(
            item.get("equipment_pages_processed") is not None
            for item in source_diagnostics
        )
        else None,
        "equipment_pages_attempted": sum(
            item.get("equipment_pages_attempted") or 0 for item in source_diagnostics
        )
        if any(
            item.get("equipment_pages_attempted") is not None
            for item in source_diagnostics
        )
        else None,
        "equipment_pages_unreadable": sum(
            item.get("equipment_pages_unreadable") or 0 for item in source_diagnostics
        )
        if any(
            item.get("equipment_pages_unreadable") is not None
            for item in source_diagnostics
        )
        else None,
        "external_pages": external.get("pages", []) if external else [],
        "complete_package_coverage_established": False,
    }
    return {
        "predictions": predictions,
        "coverage": coverage,
        "source_diagnostics": source_diagnostics,
        "native_annotation_transport": _annotation_transport(
            expected_native, native, oracle_failed=oracle_failed
        ),
        "comment_merge": duplicates,
        "errors": errors,
        "timings": timings,
        "comparison_summary": comparison.get("summary") if comparison else None,
        "model_summary": {
            "version": model["version"],
            "observed_device_count": model["observed_device_count"],
            "coverage": model["coverage"],
        }
        if model
        else None,
        "equipment_model": model,
        "raw_revision_checks": {"equipment": comparison, "pdf_pairs": pdf_revisions},
        "source_observations": source_observations,
        "external_prediction_timings": external.get("timings") if external else None,
        "timing_note": "The comparison stage includes its own re-extraction. Total seconds measures this local run, not earlier external vision generation; external provider timings are null unless supplied.",
    }


def evaluate_package(
    package,
    manifest_path,
    predictions=None,
    output_dir=None,
    *,
    answer_key=None,
    include_vector=True,
):
    """Run one manifest package and optionally write actual, reviewable artifacts.

    Paths are relative to the manifest, not the shell's working directory.
    ``predictions`` is a source-hash-bound dict or a JSON file path. No API keys,
    provider calls, exports, downloads or database mutations occur here.
    """
    manifest_path = Path(manifest_path)
    base = (
        manifest_path.parent
        if manifest_path.suffix.lower() == ".json"
        else manifest_path
    )
    if not isinstance(package.get("id"), str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", package["id"]
    ):
        raise EvaluationError("Use a bounded, filename-safe package ID.")
    private = package.get("kind") == "real_private"
    if private and output_dir is not None:
        private_root = (Path(__file__).resolve().parents[2] / ".data").resolve()
        if (
            not (Path(output_dir) / package["id"])
            .resolve()
            .is_relative_to(private_root)
        ):
            raise EvaluationError(
                "Private package reports must stay under the ignored .data directory."
            )
    if predictions is not None and not isinstance(predictions, dict):
        prediction_path = Path(predictions)
        if not prediction_path.is_absolute():
            prediction_path = base / prediction_path
        predictions = json.loads(prediction_path.read_text())
    if answer_key is None and package.get("answer_key"):
        answer_key = json.loads((base / package["answer_key"]).read_text())
    run = extract_package(
        package, base, external=predictions, include_vector=include_vector
    )
    scores = (
        score_predictions(answer_key, run["predictions"])
        if answer_key is not None
        else None
    )
    report = {
        "schema_version": REPORT_SCHEMA,
        "package_id": package["id"],
        "kind": package.get("kind"),
        "package_type": package.get("package_type"),
        "provenance": package.get("provenance"),
        "scores": scores,
        **run,
        "status": "completed_with_engine_errors"
        if run["errors"]
        else "scored_preliminary_references"
        if scores and scores["reference_status"].startswith("preliminary")
        else "scored_reviewed_references"
        if scores
        else "unlabelled_extraction_only",
    }
    if output_dir is not None:
        destination = Path(output_dir) / package["id"]
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        if private:
            destination.chmod(0o700)

        def write_artifact(path, value):
            data = json.dumps(value, indent=2, allow_nan=False) + "\n"
            if not private:
                path.write_text(data)
                return
            # Do not follow an old artifact symlink out of private storage.
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)

        artifacts = {
            "equipment_model": (
                "equipment-model.json",
                run["equipment_model"]
                if run["equipment_model"] is not None
                else {"status": "failed", "errors": run["errors"]},
            ),
            "comment_log": (
                "comment-log.json",
                {
                    "package_id": package["id"],
                    "source_hashes": run["predictions"]["source_hashes"],
                    "comments": run["predictions"]["comments"],
                    "merge_diagnostics": run["comment_merge"],
                },
            ),
            "revision_checks": ("revision-checks.json", run["raw_revision_checks"]),
            "source_diagnostics": (
                "source-diagnostics.json",
                {
                    "coverage": run["coverage"],
                    "source_diagnostics": run["source_diagnostics"],
                    "errors": run["errors"],
                    "timings": run["timings"],
                },
            ),
            "source_observations": (
                "source-observations.json",
                run["source_observations"],
            ),
            "predictions": ("predictions.json", run["predictions"]),
        }
        report["artifact_paths"] = {}
        for label, (filename, data) in artifacts.items():
            path = destination / filename
            write_artifact(path, data)
            report["artifact_paths"][label] = str(path.resolve())
        report_path = destination / "report.json"
        report["artifact_paths"]["report"] = str(report_path.resolve())
        write_artifact(report_path, report)
    return report


def summarize_reports(reports):
    """Pool count metrics only within explicit real/synthetic provenance groups."""
    grouped = defaultdict(list)
    for report in reports:
        grouped[report.get("kind", "unknown")].append(report)
    result = {}
    for kind, cases in grouped.items():
        aggregate = {}
        for section, names in (
            ("extraction", ("actionable_comments", "device_tags", "bom_rows")),
            ("checks", ("issues", "revision_checks")),
        ):
            aggregate[section] = {}
            for name in names:
                measured = [
                    case["scores"][section][name]
                    for case in cases
                    if case.get("scores") and case["scores"][section][name] is not None
                ]
                complete = [
                    metric for metric in measured if metric["scope"] == "complete"
                ]
                tp, fn = (
                    sum(metric["tp"] for metric in measured),
                    sum(metric["fn"] for metric in measured),
                )
                complete_tp, fp = (
                    sum(metric["tp"] for metric in complete),
                    sum(metric["fp"] for metric in complete),
                )
                aggregate[section][name] = {
                    "labelled_packages": len(measured),
                    "complete_scope_packages": len(complete),
                    "tp": tp if measured else None,
                    "fn": fn if measured else None,
                    "fp_complete_scopes_only": fp if complete else None,
                    "recall_labelled_scopes": tp / (tp + fn) if tp + fn else None,
                    "precision_complete_scopes_only": complete_tp / (complete_tp + fp)
                    if complete_tp + fp
                    else None,
                }
        result[kind] = {
            "packages": len(cases),
            "packages_with_engine_errors": sum(
                bool(case.get("errors")) for case in cases
            ),
            "labelled_packages": sum(case.get("scores") is not None for case in cases),
            "physical_pages": sum(
                (case.get("coverage") or {}).get("physical_pages") or 0
                for case in cases
            ),
            **aggregate,
        }
    return result
