"""Independent terminal-graph checks over established CAD evidence.

Inspired by Beyond Pixels / V2G-Audit (arXiv:2602.11678), implemented
independently. No author code or benchmark data is incorporated. Geometry and
terminal identity must be verified by the caller; visual crossings are NOT edges.
An incomplete graph cannot establish absence or a passing circuit audit.
"""

from __future__ import annotations

import re
from collections import deque


def audit(terminals, connections, rules, *, coverage_complete, source_sha256):
    """Run only typed, bounded operators; model-generated Python is never run.

    Nodes are (device, terminal) pairs. Each connection has an evidence locator.
    Rules operate on the explicitly supplied circuit, never an inferred region.
    Grounding rules count explicit electrode/bond IDs, not every PE symbol.
    """
    nodes = {tuple(n) for n in terminals}
    if len(nodes) > 20000 or len(connections) > 40000 or len(rules) > 500:
        raise ValueError("Terminal graph budget exceeded")
    if any(len(n) != 2 or not all(isinstance(v, str) and v for v in n) for n in nodes):
        raise ValueError("Named device terminals are required")
    if not isinstance(source_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", source_sha256
    ):
        raise ValueError("Graph must be bound to a source hash")
    adjacency = {n: [] for n in nodes}
    edges = []
    omissions = []
    for connection in connections:
        a, b = tuple(connection["a"]), tuple(connection["b"])
        locator = connection.get("source")
        if (
            a not in nodes
            or b not in nodes
            or not locator
            or not connection.get("geometry_verified")
        ):
            omissions.append(
                {"source": locator, "reason": "unverified_terminal_incidence"}
            )
            continue
        index = len(edges)
        edges.append({"a": a, "b": b, "source": locator})
        adjacency[a].append((b, index))
        adjacency[b].append((a, index))

    def path(start, target):
        queue, seen = deque([start]), {start: None}
        while queue:
            node = queue.popleft()
            if node == target:
                result = []
                while seen[node] is not None:
                    previous, edge = seen[node]
                    result.append(edges[edge]["source"])
                    node = previous
                return list(reversed(result))
            for neighbor, edge in adjacency[node]:
                if neighbor not in seen:
                    seen[neighbor] = (node, edge)
                    queue.append(neighbor)
        return None

    complete = bool(coverage_complete) and not omissions
    findings = []
    allowed = {"connected", "isolated", "one_grounding_point"}
    for rule in rules:
        kind = rule.get("operator")
        if kind not in allowed:
            raise ValueError("Unsupported terminal graph operator")
        finding = {
            "id": rule["id"],
            "operator": kind,
            "status": "unknown",
            "evidence": [],
        }
        if kind == "one_grounding_point":
            members = {tuple(n) for n in rule["circuit_terminals"]}
            grounds = rule["grounding_points"]
            # The caller must establish a complete region and explicit physical
            # bonding-point identities. Three repeated PE symbols aren't three grounds.
            if not members or not members <= nodes or not rule.get("boundary_verified"):
                finding["reason"] = "unverified_circuit_boundary"
            elif any(
                tuple(g["terminal"]) not in members
                or not g.get("source")
                or not g.get("id")
                for g in grounds
            ):
                finding["reason"] = "unverified_grounding_point"
            elif complete:
                count = len({g["id"] for g in grounds})
                root = next(iter(members))
                connected = all(path(root, member) is not None for member in members)
                finding.update(
                    status="pass" if count == 1 and connected else "fail",
                    grounding_points=count,
                    circuit_connected=connected,
                    evidence=[g["source"] for g in grounds],
                )
            else:
                finding["reason"] = "incomplete_extraction"
        else:
            a, b = tuple(rule["a"]), tuple(rule["b"])
            if a not in nodes or b not in nodes:
                finding["reason"] = "terminal_not_extracted"
            else:
                evidence = path(a, b)
                if complete:
                    good = (evidence is not None) == (kind == "connected")
                    finding.update(
                        status="pass" if good else "fail", evidence=evidence or []
                    )
                elif evidence is not None and kind == "isolated":
                    # A physically established short is still positive evidence
                    # in a partial graph. Absence of a path proves nothing there.
                    finding.update(status="fail", evidence=evidence)
                else:
                    finding["reason"] = "incomplete_extraction"
        findings.append(finding)
    visited, components = set(), 0
    for node in nodes:
        if node in visited:
            continue
        components += 1
        queue = [node]
        visited.add(node)
        while queue:
            for neighbor, _ in adjacency[queue.pop()]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
    return {
        "schema": "rivet.terminal-graph-audit.v1",
        "source_sha256": source_sha256,
        "coverage_complete": complete,
        "nodes": len(nodes),
        "edges": len(edges),
        "components": components,
        # Equivalent to nullity(L)=components and rank(L)=|V|-components;
        # no dense eigensolver is needed. Cycles are descriptive, not faults.
        "laplacian_rank": len(nodes) - components,
        "cycle_rank": len(edges) - len(nodes) + components,
        "findings": findings,
        "omissions": omissions,
        "all_rules_passed": bool(findings)
        and all(f["status"] == "pass" for f in findings),
        "engineering_approved": False,
    }
