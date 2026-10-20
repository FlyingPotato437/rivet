"""Fit agreement reliability on development packages, never on evaluation labels.

Measurements within one physical package are correlated. Report cell accuracy
and a conservative package-level all-correct confidence bound separately.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from .reconcile import reliability_bucket

VERSION = "rivet.clustered-reliability.v1"


def wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if not total:
        return 0.0
    p, z2 = successes / total, z * z
    return max(0.0, (p + z2 / (2 * total) - z * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total))) / (1 + z2 / total))


def fit_development(examples: list[dict], *, minimum_packages: int = 5,
                    acceptance_lower_bound: float = .99) -> dict:
    """Examples contain resolution, correct, package_ancestry and split.

    package_ancestry must identify the original public/manufacturer package,
    not one-page copies, individual extractors, cells or model invocations.
    Heldout labels are rejected rather than silently filtered into training.
    """
    buckets = defaultdict(list)
    for example in examples:
        if example.get("split") != "development":
            raise ValueError("Calibration accepts development labels only")
        if not example.get("package_ancestry") or not isinstance(example.get("correct"), bool):
            raise ValueError("A labelled correctness outcome and package ancestry are required")
        buckets[reliability_bucket(example["resolution"])].append(example)
    result = {"version": VERSION, "split": "development", "minimum_packages": max(5, minimum_packages),
              "acceptance_lower_bound": max(.99, acceptance_lower_bound),
              "method": "Wilson 95% lower bound on independent-package all-correct outcomes",
              "raw_model_confidence_used": False, "buckets": {}}
    for bucket, rows in sorted(buckets.items()):
        projects = defaultdict(list)
        for row in rows:
            projects[row["package_ancestry"]].append(row["correct"])
        correct = sum(r["correct"] for r in rows)
        package_successes = sum(all(outcomes) for outcomes in projects.values())
        result["buckets"][bucket] = {
            "labelled_value_count": len(rows), "correct_value_count": correct,
            "empirical_probability": correct / len(rows),
            "independent_package_count": len(projects),
            "all_correct_package_count": package_successes,
            "package_success_lower_bound": wilson_lower(package_successes, len(projects)),
            "package_ancestries": sorted(projects),
        }
    result["training_examples_sha256"] = hashlib.sha256(json.dumps(examples, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
