# -*- coding: utf-8 -*-
"""Candidate-level conflict and cycle risk annotation.

The checks in this module never delete or rewrite an edge. They attach audit
flags that downstream arbitration views can consume, while legacy evidence and
verification views remain unchanged.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import networkx as nx

from .constants import HYPOTHESIS_KINDS
from .util import as_text


RISK_POLICIES = {"needs_review", "exclude", "keep"}


def _policy(value: Any, default: str = "needs_review") -> str:
    text = as_text(value or default).strip().lower()
    return text if text in RISK_POLICIES else default


def _pair(edge: dict) -> tuple[str, str]:
    return tuple(sorted((as_text(edge.get("source")), as_text(edge.get("target")))))


def _cycle_class(edges: list[dict]) -> str:
    kinds = {as_text(edge.get("kind")) for edge in edges}
    has_evidence = bool(kinds & {"direct_core", "conditional", "regime", "transitive_derived"})
    has_hypothesis = bool(kinds & set(HYPOTHESIS_KINDS))
    regime = any(
        edge.get("regime_dependent") or as_text(edge.get("kind")) in {"regime", "conditional"}
        for edge in edges
    )
    if regime and (has_hypothesis or not has_evidence):
        return "regime_candidate_cycle"
    if has_hypothesis and not has_evidence:
        return "candidate_cycle"
    if has_evidence and not has_hypothesis:
        return "evidence_cycle"
    return "mixed_cycle"


def _simple_cycles(graph: nx.DiGraph, max_cycles: int, max_length: int) -> list[list[str]]:
    try:
        cycles = list(nx.simple_cycles(graph, length_bound=max_length))
    except TypeError:  # networkx < 3.3
        cycles = list(nx.simple_cycles(graph))
    return cycles[:max_cycles]


def annotate_candidate_risks(
    artifact: dict,
    *,
    candidate_conflict_policy: str = "needs_review",
    candidate_cycle_policy: str = "needs_review",
    max_cycles: int = 200,
    max_cycle_length: int = 12,
) -> dict:
    """Attach candidate conflict/cycle flags to edges.

    Reciprocal hypothesis pairs and any cycle containing a hypothesis edge are
    retained in the artifact. The default policy is ``needs_review`` so an
    audit flag cannot silently become a deletion.
    """
    conflict_policy = _policy(candidate_conflict_policy)
    cycle_policy = _policy(candidate_cycle_policy)
    edges = [edge for edge in artifact.get("edges", []) if as_text(edge.get("kind")) != "rejected"]
    for edge in edges:
        edge["candidate_conflict"] = False
        edge["candidate_cycle"] = False
        edge["candidate_cycle_classes"] = []
        edge["candidate_cycle_ids"] = []
        edge["candidate_risk_flags"] = []

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for edge in edges:
        source, target = as_text(edge.get("source")), as_text(edge.get("target"))
        if source and target and source != target:
            grouped[_pair(edge)].append(edge)

    conflict_pairs: list[dict] = []
    for pair, rows in sorted(grouped.items()):
        directions = {(as_text(row.get("source")), as_text(row.get("target"))) for row in rows}
        if len(directions) < 2 or not any(as_text(row.get("kind")) in HYPOTHESIS_KINDS for row in rows):
            continue
        conflict_pairs.append({
            "pair": list(pair),
            "edge_ids": sorted(as_text(row.get("id")) for row in rows),
            "candidate_edge_ids": sorted(
                as_text(row.get("id")) for row in rows
                if as_text(row.get("kind")) in HYPOTHESIS_KINDS
            ),
            "kinds": sorted({as_text(row.get("kind")) for row in rows}),
        })
        for row in rows:
            if as_text(row.get("kind")) not in HYPOTHESIS_KINDS:
                continue
            row["candidate_conflict"] = True
            row["candidate_risk_flags"].append("candidate_conflict")

    graph = nx.DiGraph()
    edge_by_id = {as_text(edge.get("id")): edge for edge in edges}
    for edge in edges:
        source, target = as_text(edge.get("source")), as_text(edge.get("target"))
        if not source or not target or source == target:
            continue
        graph.add_edge(
            source,
            target,
            edge_id=as_text(edge.get("id")),
            kind=as_text(edge.get("kind")),
        )
    cycles: list[dict] = []
    seen_cycles = set()
    for nodes in _simple_cycles(graph, max_cycles=max_cycles, max_length=max_cycle_length):
        cycle_edges: list[dict] = []
        for index, source in enumerate(nodes):
            target = nodes[(index + 1) % len(nodes)]
            data = graph.get_edge_data(source, target) or {}
            edge = edge_by_id.get(as_text(data.get("edge_id")))
            if edge is not None:
                cycle_edges.append(edge)
        if len(cycle_edges) != len(nodes):
            continue
        classification = _cycle_class(cycle_edges)
        if classification == "evidence_cycle":
            continue
        cycle_id = "cycle:" + ",".join(sorted(as_text(edge.get("id")) for edge in cycle_edges))
        if cycle_id in seen_cycles:
            continue
        seen_cycles.add(cycle_id)
        cycles.append({
            "cycle_id": cycle_id,
            "classification": classification,
            "edge_ids": sorted(as_text(edge.get("id")) for edge in cycle_edges),
        })
        for edge in cycle_edges:
            # A mixed cycle is evidence for reviewing its hypothesis edge(s);
            # it is not evidence against an independently grounded evidence
            # edge in the same cycle. Keep evidence edges out of the risk set.
            if as_text(edge.get("kind")) not in HYPOTHESIS_KINDS:
                continue
            edge["candidate_cycle"] = True
            edge["candidate_cycle_classes"].append(classification)
            edge["candidate_cycle_ids"].append(cycle_id)
            if "candidate_cycle" not in edge["candidate_risk_flags"]:
                edge["candidate_risk_flags"].append("candidate_cycle")

    for edge in edges:
        flags = sorted(set(edge.get("candidate_risk_flags") or []))
        edge["candidate_risk_flags"] = flags
        edge["candidate_cycle_classes"] = sorted(set(edge.get("candidate_cycle_classes") or []))
        edge["candidate_cycle_ids"] = sorted(set(edge.get("candidate_cycle_ids") or []))
        edge["candidate_conflict_status"] = conflict_policy if edge.get("candidate_conflict") else "none"
        edge["candidate_cycle_status"] = cycle_policy if edge.get("candidate_cycle") else "none"
        if not flags:
            edge["candidate_risk_status"] = "clear"
            edge["candidate_risk_reason"] = ""
        elif "exclude" in {conflict_policy, cycle_policy}:
            edge["candidate_risk_status"] = "exclude"
            edge["candidate_risk_reason"] = ";".join(flags)
        elif "needs_review" in {conflict_policy, cycle_policy}:
            edge["candidate_risk_status"] = "needs_review"
            edge["candidate_risk_reason"] = ";".join(flags)
        else:
            edge["candidate_risk_status"] = "keep"
            edge["candidate_risk_reason"] = ";".join(flags)

    counts = Counter(edge.get("candidate_risk_status") for edge in edges)
    return {
        "enabled": True,
        "candidate_conflict_policy": conflict_policy,
        "candidate_cycle_policy": cycle_policy,
        "candidate_conflicts": len(conflict_pairs),
        "candidate_cycles": len(cycles),
        "risk_status_counts": dict(sorted(counts.items())),
        "conflicts": conflict_pairs,
        "cycles": cycles,
    }