"""Measure PDF observations against independently annotated circuit subsets."""

from collections import Counter, defaultdict
from itertools import combinations


def _score(expected, observed):
    matched = expected & observed
    return {
        "expected": len(expected),
        "observed": len(observed),
        "matched": len(matched),
        "missed": sorted(expected - observed),
        "unexpected": sorted(observed - expected),
        "recall": len(matched) / len(expected) if expected else None,
        "precision": len(matched) / len(observed) if observed else None,
    }


def _pairs(nets):
    return {tuple(sorted(pair)) for net in nets for pair in combinations(set(net), 2)}


def _observed_nets(connections):
    parent = {}

    def find(item):
        parent.setdefault(item, item)
        if parent[item] != item:
            parent[item] = find(parent[item])
        return parent[item]

    def join(values):
        values = [value for value in values if isinstance(value, str) and ":" in value]
        if not values:
            return
        root = find(values[0])
        for item in values[1:]:
            parent[find(item)] = root

    components = defaultdict(list)
    for connection in connections:
        terminals = connection.get("terminal_ids", [])
        if not terminals:
            terminals = [connection.get("from"), connection.get("to")]
        join(terminals)
        component = connection.get("net_component")
        if component is not None:
            components[(connection.get("page"), str(component))].extend(terminals)
    for values in components.values():
        join(values)
    groups = defaultdict(list)
    for item in parent:
        groups[find(item)].append(item)
    return list(groups.values())


def _inside_region(connection, bbox):
    points = connection.get("path_points") or [
        point
        for segment in connection.get("segments", [])
        for point in segment.get("points", [])
    ]
    x0, y0, x1, y1 = bbox
    return bool(points) and all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in points)


def _labelled_subset(report, truth):
    """Name only unique visible terminal labels within a manually chosen figure.

    The namespace is an evaluation locator, never an equipment/device identity.
    A duplicate label remains unscored instead of choosing whichever is first.
    """
    scope = truth.get("unassigned_terminal_scope")
    if not scope:
        return {}, set()
    allowed = set(scope["labels"])
    terminals = [
        {**terminal, "page": page["page"]}
        for page in report.get("pages", [])
        for terminal in page.get("unassigned_terminals", [])
    ] or report.get("unassigned_terminals", [])
    if truth.get("annotation_page"):
        terminals = [
            terminal
            for terminal in terminals
            if terminal.get("page") == truth["annotation_page"]
        ]
    if truth.get("evaluation_bbox"):
        x0, y0, x1, y1 = truth["evaluation_bbox"]
        terminals = [
            terminal
            for terminal in terminals
            if terminal.get("position")
            and x0 <= terminal["position"][0] <= x1
            and y0 <= terminal["position"][1] <= y1
        ]
    labelled = defaultdict(list)
    for terminal in terminals:
        if terminal.get("label") in allowed and terminal.get("id"):
            labelled[terminal["label"]].append(terminal["id"])
    mapping = {
        ids[0]: scope["namespace"] + ":" + label
        for label, ids in labelled.items()
        if len(ids) == 1
    }
    return mapping, set(mapping.values())


def evaluate_pdf_graph(report, truth):
    """Do not infer whole-sheet accuracy from a small public-document subset.

    Connectivity is evaluated as terminal net membership, independent of how an
    extractor divides a physical branch into connection segments. Unknown
    terminal contacts are never assigned fictional names for this comparison.
    """
    devices = report.get("devices", [])
    connections = report.get("connections", [])
    if truth.get("annotation_page"):
        page_number = truth["annotation_page"]
        devices = [device for device in devices if device.get("page") == page_number]
        connections = [item for item in connections if item.get("page") == page_number]
    if truth.get("evaluation_bbox"):
        x0, y0, x1, y1 = truth["evaluation_bbox"]
        devices = [
            device
            for device in devices
            if device.get("bbox")
            and x0 <= (device["bbox"][0] + device["bbox"][2]) / 2 <= x1
            and y0 <= (device["bbox"][1] + device["bbox"][3]) / 2 <= y1
        ]
        connections = [
            item
            for item in connections
            if _inside_region(item, truth["evaluation_bbox"])
        ]
    tags = [device.get("tag") for device in devices if device.get("tag")]
    observed_tags = set(tags)
    expected_tags = set(truth.get("expected_tags", []))
    label_mapping, subset_ports = _labelled_subset(report, truth)
    observed_ports = subset_ports | {
        port["id"]
        for device in devices
        for port in device.get("ports", [])
        if port.get("id") and port.get("label") is not None
    }
    expected_ports = set(truth.get("expected_ports", []))
    expected_pairs = _pairs(truth.get("expected_nets", []))
    mapped_connections = [
        {
            **connection,
            "terminal_ids": [
                label_mapping.get(item, item)
                for item in connection.get("terminal_ids", [])
            ],
        }
        for connection in connections
    ]
    all_observed_pairs = _pairs(_observed_nets(mapped_connections))
    # A geometric contact has a stable machine identifier but no readable pin
    # name. It must not be counted as a falsely predicted named terminal pair.
    # Keep it visible as unscored geometric evidence instead of inventing a pin.
    unscored_geometric_pairs = {
        pair for pair in all_observed_pairs if not set(pair) <= observed_ports
    }
    observed_pairs = all_observed_pairs - unscored_geometric_pairs
    partial = truth.get("scope") == "partial_subset"
    unscored_tags, unscored_pairs = set(), set()
    if partial:
        # A partial annotation establishes recall on named observations. It
        # cannot establish the precision of predictions elsewhere on the sheet.
        unscored_tags = observed_tags - expected_tags
        observed_tags &= expected_tags
        universe = set(truth.get("evaluated_terminal_ids", expected_ports))
        unscored_pairs = {pair for pair in observed_pairs if not set(pair) <= universe}
        observed_pairs -= unscored_pairs
    ambiguous = {tuple(sorted(pair)) for pair in truth.get("ambiguous_pairs", [])}
    observed_pairs -= ambiguous
    tag_metrics = _score(expected_tags, observed_tags)
    port_metrics = _score(
        expected_ports,
        observed_ports if not partial else observed_ports & expected_ports,
    )
    if partial:
        tag_metrics["precision"] = None
        tag_metrics["precision_scope"] = (
            "Not established: the rest of this public sheet is unannotated."
        )
        port_metrics["precision"] = None
        port_metrics["precision_scope"] = tag_metrics["precision_scope"]
    issues = [
        *report.get("issues", []),
        *[item for page in report.get("pages", []) for item in page.get("issues", [])],
    ]
    coverage = report.get("coverage", {})
    unknown_visible = (
        bool(issues)
        or coverage.get("status") == "unknown"
        or any(
            page.get("coverage", {}).get("status") == "unknown"
            for page in report.get("pages", [])
        )
    )
    false_complete = bool(truth.get("unknown_required")) and (
        coverage.get("status") in {"verified", "complete"}
        or report.get("engineering_verified") is True
        or report.get("physical_dimensions_verified") is True
    )
    return {
        "scope": truth.get("scope"),
        "tags": tag_metrics,
        "ports": port_metrics,
        "connections": _score(expected_pairs, observed_pairs),
        "missed_tag_count": len(expected_tags - observed_tags),
        "duplicate_tag_observations": {
            tag: count for tag, count in Counter(tags).items() if count > 1
        },
        "unscored_prediction_tags": sorted(unscored_tags),
        "unscored_connection_pairs": sorted(unscored_pairs),
        "unscored_geometric_connection_pairs": sorted(unscored_geometric_pairs),
        "unknown_required": bool(truth.get("unknown_required")),
        "unknown_preserved": not truth.get("unknown_required")
        or (unknown_visible and not false_complete),
        "false_complete_claim": false_complete,
        "engineering_approval_established": False,
    }
