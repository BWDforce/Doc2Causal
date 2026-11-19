# -*- coding: utf-8 -*-
"""View-aware canonical evaluation for causal graph methods.

The primary score is exact directed edge F1 after canonicalizing method node
names against a gold manifest.  Candidate edge sets are defined explicitly:

* ``raw`` / ``discovery``: all non-hypothesis GML edges;
* D2C_13_2 ``retained``: direct_core + conditional + regime + transitive_derived;
* D2C_13_2 ``grounded``: direct_core + conditional + regime;
* D2C_13_2 ``strict``: direct_core only;
* D2C_13_2 ``evidence_core``: span-grounded, assertion-grounded and derived
  evidence, including high/medium weak candidates from the pre-verifier view;
* D2C_13_2 ``soft_core``: retained evidence plus candidate_high/medium, with
  candidate_low explicitly excluded;
* D2C_13_2 ``support_aware_core``: evidence-gated promotion view that also
  excludes provisional, ambiguous, negated and unresolved path-summary claims.

Hypothesis/rejected edges are never counted as core.  Self-loops and edges with
an unalignable endpoint are excluded from F1 and reported separately.  This
module deliberately does not invent a weighted composite score.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

import networkx as nx

from .policy import promotion_gate


def normalize_name(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    return set(normalize_name(value).split())


def _dice(a: Any, b: Any) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return 2.0 * len(ta & tb) / (len(ta) + len(tb))


def align_node(
    surface: Any,
    gold_nodes: list[str],
    alias_map: dict[str, str],
    threshold: float = 0.6,
) -> str | None:
    """Align a method node label to a gold canonical node."""
    raw = str(surface or "").strip()
    if not raw:
        return None
    key = raw.lower().strip()
    if key in alias_map:
        return alias_map[key]
    canonical_lower = {str(node).lower(): str(node) for node in gold_nodes}
    if key in canonical_lower:
        return canonical_lower[key]
    for node in gold_nodes:
        nl = str(node).lower()
        if nl and (nl in key or key in nl):
            return str(node)
    best_node = None
    best_score = 0.0
    for surface_name, canonical in alias_map.items():
        score = _dice(raw, surface_name)
        if score > best_score:
            best_node, best_score = canonical, score
    for node in gold_nodes:
        score = _dice(raw, node)
        if score > best_score:
            best_node, best_score = str(node), score
    return best_node if best_node is not None and best_score >= threshold else None


def node_map_for_gml(graph: nx.Graph, gold_nodes: list[str], alias_map: dict[str, str]) -> dict[str, str | None]:
    return {
        str(node): align_node(node, gold_nodes, alias_map)
        for node in graph.nodes()
    }


def node_map_for_artifact(artifact: dict, gold_nodes: list[str], alias_map: dict[str, str]) -> dict[str, str | None]:
    values = {}
    for entity in artifact.get("entities", []):
        label = entity.get("label", entity.get("id", ""))
        values[str(entity.get("id", label))] = align_node(label, gold_nodes, alias_map)
    return values


def _add_edge(
    target: set[tuple[str, str]],
    source_id: Any,
    target_id: Any,
    labels: dict[str, str | None],
    counters: Counter,
) -> None:
    left = labels.get(str(source_id))
    right = labels.get(str(target_id))
    if not left or not right:
        counters["unaligned_edges"] += 1
        return
    if left == right:
        counters["self_loops"] += 1
        return
    target.add((left, right))


def gml_views(
    gml_path: str | Path,
    gold_nodes: list[str],
    alias_map: dict[str, str],
) -> dict[str, Any]:
    """Return core/hypothesis edge sets from a method GML."""
    graph = nx.read_gml(str(gml_path))
    labels = node_map_for_gml(graph, gold_nodes, alias_map)
    core: set[tuple[str, str]] = set()
    hypothesis: set[tuple[str, str]] = set()
    counters = Counter()
    for source, target, attrs in graph.edges(data=True):
        edge_type = str(attrs.get("edge_type") or "").lower()
        destination = hypothesis if edge_type == "hypothesis" else core
        _add_edge(destination, source, target, labels, counters)
    aligned_nodes = {node for node in labels.values() if node}
    return {
        "graph": graph,
        "nodes": aligned_nodes,
        "core": core,
        "hypothesis": hypothesis,
        "self_loops": counters["self_loops"],
        "unaligned_edges": counters["unaligned_edges"],
        "raw_edges": graph.number_of_edges(),
        "raw_nodes": graph.number_of_nodes(),
        "hypothesis_edges": len(hypothesis),
    }


def artifact_views(
    artifact_path: str | Path,
    gold_nodes: list[str],
    alias_map: dict[str, str],
) -> dict[str, Any]:
    """Return strict/grounded/retained views from a D2C_13_2 artifact."""
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8-sig"))
    entity_labels = {
        str(entity.get("id")): entity.get("label", entity.get("id", ""))
        for entity in artifact.get("entities", [])
    }
    strict: set[tuple[str, str]] = set()
    grounded: set[tuple[str, str]] = set()
    retained: set[tuple[str, str]] = set()
    verified_core: set[tuple[str, str]] = set()
    contract_verified_core: set[tuple[str, str]] = set()
    candidate: set[tuple[str, str]] = set()
    unsupported_hypothesis: set[tuple[str, str]] = set()
    hypothesis: set[tuple[str, str]] = set()
    rejected: set[tuple[str, str]] = set()
    span_core: set[tuple[str, str]] = set()
    assertion_core: set[tuple[str, str]] = set()
    derived_core: set[tuple[str, str]] = set()
    evidence_core: set[tuple[str, str]] = set()
    evidence_contract_core: set[tuple[str, str]] = set()
    soft_core: set[tuple[str, str]] = set()
    soft_core_lenient: set[tuple[str, str]] = set()
    soft_core_precision: set[tuple[str, str]] = set()
    support_aware_core: set[tuple[str, str]] = set()
    contract_support_aware_core: set[tuple[str, str]] = set()
    partial_forward_core: set[tuple[str, str]] = set()
    path_core: set[tuple[str, str]] = set()
    path_review: set[tuple[str, str]] = set()
    candidate_high: set[tuple[str, str]] = set()
    candidate_medium: set[tuple[str, str]] = set()
    candidate_low: set[tuple[str, str]] = set()
    needs_review: set[tuple[str, str]] = set()
    exact_support_core: set[tuple[str, str]] = set()
    context_assisted_core: set[tuple[str, str]] = set()
    partial_evidence_review: set[tuple[str, str]] = set()
    path_support_core: set[tuple[str, str]] = set()
    no_evidence_queue: set[tuple[str, str]] = set()
    canonical_soft_core: set[tuple[str, str]] = set()
    canonical_verified_core: set[tuple[str, str]] = set()
    canonical_multi_document_core: set[tuple[str, str]] = set()
    candidate_safe_core: set[tuple[str, str]] = set()
    candidate_risk_review: set[tuple[str, str]] = set()
    arbitration_review: set[tuple[str, str]] = set()
    counters = Counter()
    aligned_nodes = set()
    for entity in artifact.get("entities", []):
        mapped = align_node(entity.get("label", entity.get("id", "")), gold_nodes, alias_map)
        if mapped:
            aligned_nodes.add(mapped)
    for edge in artifact.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        source_label = edge.get("source_name") or entity_labels.get(str(source), source)
        target_label = edge.get("target_name") or entity_labels.get(str(target), target)
        left = align_node(source_label, gold_nodes, alias_map)
        right = align_node(target_label, gold_nodes, alias_map)
        if not left or not right:
            counters["unaligned_edges"] += 1
            continue
        if left == right:
            counters["self_loops"] += 1
            continue
        pair = (left, right)
        kind = str(edge.get("kind") or "")
        grade = str(edge.get("evidence_grade") or "none")
        support_mode = str(edge.get("support_mode") or "")
        tier = str(edge.get("candidate_tier") or "none")
        if kind == "candidate":
            candidate.add(pair)
        elif kind == "unsupported_hypothesis":
            unsupported_hypothesis.add(pair)
        elif kind == "hypothesis":
            hypothesis.add(pair)
        elif kind == "rejected":
            rejected.add(pair)
        elif kind in {"direct_core", "conditional", "regime", "transitive_derived"}:
            retained.add(pair)
            if kind in {"direct_core", "conditional", "regime"}:
                grounded.add(pair)
            if kind == "direct_core":
                strict.add(pair)
                if edge.get("semantic_support") == "entailed" and edge.get("direction_status") in {"supported", "corrected"}:
                    verified_core.add(pair)
                    if edge.get("evidence_contract_complete"):
                        contract_verified_core.add(pair)
        is_evidence = (
            (kind in {"direct_core", "conditional", "regime", "transitive_derived"} and grade != "none")
            or (kind == "candidate" and support_mode in {"weak_candidate", "assertion_only", "span_only"} and grade != "none")
        )
        if is_evidence:
            evidence_core.add(pair)
        if (kind in {"direct_core", "conditional", "regime"} and grade == "span_grounded"
                and edge.get("evidence_contract_complete")):
            span_core.add(pair)
        if kind in {"direct_core", "conditional", "regime", "transitive_derived"} and edge.get("evidence_contract_complete"):
            evidence_contract_core.add(pair)
        if is_evidence and grade in {"assertion_grounded", "span_only"}:
            assertion_core.add(pair)
        if is_evidence and (kind == "transitive_derived" or grade == "derived_path"):
            derived_core.add(pair)
        is_soft_lenient = (
            (kind in {"direct_core", "conditional", "regime", "transitive_derived"} and grade != "none")
            or (kind == "candidate" and tier in {"high", "medium"})
        )
        directness = edge.get("directness_class")
        is_soft = is_soft_lenient
        if is_soft_lenient:
            soft_core_lenient.add(pair)
        if is_soft and edge.get("direction_status") in {"ambiguous", "role_conflict"}:
            is_soft = False
        if is_soft and edge.get("shortcut_risk") == "high":
            is_soft = False
        if is_soft:
            soft_core.add(pair)
        canonical_soft = edge.get("canonical_soft_core_eligible")
        if canonical_soft is None:
            canonical_soft = is_soft_lenient and edge.get("arbitration_eligible", True)
        if canonical_soft:
            canonical_soft_core.add(pair)
            if edge.get("canonical_multi_document_support"):
                canonical_multi_document_core.add(pair)
        if kind == "direct_core" and edge.get("semantic_support") == "entailed" and edge.get("direction_status") in {"supported", "corrected"} and edge.get("arbitration_eligible", True):
            canonical_verified_core.add(pair)
        if edge.get("candidate_risk_flags"):
            candidate_risk_review.add(pair)
        if not edge.get("arbitration_eligible", True) or edge.get("candidate_risk_flags"):
            arbitration_review.add(pair)
        is_precision = is_soft
        if is_precision and directness:
            if kind == "transitive_derived":
                is_precision = bool(edge.get("path_summary_explicit_direct"))
            else:
                is_precision = directness in {"direct_span", "direct_assertion", "span_only"}
        if is_precision:
            soft_core_precision.add(pair)
        eligible = edge.get("support_aware_eligible")
        if eligible is None:
            eligible = promotion_gate(edge)["eligible"]
        if eligible:
            support_aware_core.add(pair)
            if edge.get("evidence_contract_complete"):
                contract_support_aware_core.add(pair)
            if kind == "candidate" and tier in {"high", "medium"} and edge.get("arbitration_eligible", True):
                candidate_safe_core.add(pair)
        completeness = edge.get("evidence_completeness")
        condition_grounding = edge.get("condition_grounding")
        if completeness == "exact" and condition_grounding != "ungrounded" and edge.get("evidence_contract_complete") and edge.get("direction_status") not in {"ambiguous", "role_conflict"} and edge.get("identity_status", "ok") != "conflict":
            exact_support_core.add(pair)
        if completeness in {"exact", "context_assisted"} and condition_grounding != "ungrounded" and edge.get("evidence_contract_complete") and edge.get("direction_status") not in {"ambiguous", "role_conflict"} and edge.get("identity_status", "ok") != "conflict":
            context_assisted_core.add(pair)
        if completeness in {"partial", "assertion_only"}:
            partial_evidence_review.add(pair)
        if completeness == "path_only" or kind == "transitive_derived":
            path_support_core.add(pair)
        if completeness == "absent":
            no_evidence_queue.add(pair)
        if kind == "transitive_derived" and edge.get("derivation_edge_ids"):
            path_core.add(pair)
        if edge.get("path_summary") and not edge.get("path_summary_explicit_direct"):
            path_review.add(pair)
        if (
            kind in {"direct_core", "conditional", "regime", "transitive_derived"}
            and grade != "none"
        ):
            partial_forward_core.add(pair)
        if (
            kind == "candidate" and tier in {"high", "medium"}
            and str(edge.get("verifier_support") or "") == "partial"
            and str(edge.get("verifier_direction") or "") == "forward"
            and grade != "none"
        ):
            partial_forward_core.add(pair)
        if kind == "candidate" and tier == "high":
            candidate_high.add(pair)
        if kind == "candidate" and tier == "medium":
            candidate_medium.add(pair)
        if kind == "candidate" and tier == "low":
            candidate_low.add(pair)
        if edge.get("verification_status") in {"needs_review", "unsupported"} or (kind == "candidate" and tier in {"high", "medium"}):
            needs_review.add(pair)
    return {
        "artifact": artifact,
        "nodes": aligned_nodes,
        "strict": strict,
        "grounded": grounded,
        "retained": retained,
        "verified_core": verified_core,
        "contract_verified_core": contract_verified_core,
        "candidate": candidate,
        "unsupported_hypothesis": unsupported_hypothesis,
        "hypothesis": hypothesis,
        "rejected": rejected,
        "span_core": span_core,
        "assertion_core": assertion_core,
        "derived_core": derived_core,
        "evidence_core": evidence_core,
        "evidence_contract_core": evidence_contract_core,
        "soft_core": soft_core,
        "soft_core_lenient": soft_core_lenient,
        "soft_core_precision": soft_core_precision,
        "support_aware_core": support_aware_core,
        "contract_support_aware_core": contract_support_aware_core,
        "partial_forward_core": partial_forward_core,
        "path_core": path_core,
        "path_review": path_review,
        "candidate_high": candidate_high,
        "candidate_medium": candidate_medium,
        "candidate_low": candidate_low,
        "needs_review": needs_review,
        "exact_support_core": exact_support_core,
        "context_assisted_core": context_assisted_core,
        "partial_evidence_review": partial_evidence_review,
        "path_support_core": path_support_core,
        "no_evidence_queue": no_evidence_queue,
        "canonical_soft_core": canonical_soft_core,
        "canonical_verified_core": canonical_verified_core,
        "canonical_multi_document_core": canonical_multi_document_core,
        "candidate_safe_core": candidate_safe_core,
        "candidate_risk_review": candidate_risk_review,
        "arbitration_review": arbitration_review,
        "self_loops": counters["self_loops"],
        "unaligned_edges": counters["unaligned_edges"],
        "raw_edges": len(artifact.get("edges", [])),
        "raw_nodes": len(artifact.get("entities", [])),
        "hypothesis_edges": len(hypothesis),
        "rejected_edges": len(rejected),
    }

def reachable_pairs(edges: set[tuple[str, str]], nodes: set[str]) -> set[tuple[str, str]]:
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes)
    graph.add_edges_from(edges)
    pairs = set()
    for source in graph.nodes():
        for target in nx.descendants(graph, source):
            if source != target:
                pairs.add((source, target))
    return pairs


def path_metrics(
    candidate: set[tuple[str, str]],
    gold: set[tuple[str, str]],
    candidate_nodes: set[str],
    gold_nodes: set[str],
) -> dict[str, Any]:
    candidate_paths = reachable_pairs(candidate, candidate_nodes)
    gold_paths = reachable_pairs(gold, gold_nodes)
    tp = candidate_paths & gold_paths
    fp = candidate_paths - gold_paths
    fn = gold_paths - candidate_paths
    precision = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 0.0
    recall = len(tp) / len(gold_paths) if gold_paths else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "tp": len(tp),
        "fp": len(fp),
        "fn": len(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "candidate_paths": len(candidate_paths),
        "gold_paths": len(gold_paths),
    }


def edge_metrics(
    candidate: set[tuple[str, str]],
    gold: set[tuple[str, str]],
    candidate_nodes: set[str],
    gold_nodes: set[str],
    self_loops: int = 0,
    unaligned_edges: int = 0,
) -> dict[str, Any]:
    tp = candidate & gold
    fp = candidate - gold
    fn = gold - candidate
    precision = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 0.0
    recall = len(tp) / len(gold) if gold else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    reversed_count = sum(
        1 for source, target in fp
        if (target, source) in gold and (source, target) not in gold
    )
    node_recall = len(candidate_nodes & gold_nodes) / len(gold_nodes) if gold_nodes else 0.0
    return {
        "tp": len(tp),
        "fp": len(fp),
        "fn": len(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "reversed_edges": reversed_count,
        "self_loops": self_loops,
        "unaligned_edges": unaligned_edges,
        "node_recall": round(node_recall, 4),
        "candidate_edges": len(candidate),
        "gold_edges": len(gold),
    }

def evaluate_unified_views(
    artifact_path: str | Path,
    pre_verification_path: str | Path,
    gold_nodes: list[str],
    alias_map: dict[str, str],
    gold_edges: set[tuple[str, str]],
    raw_gml_path: str | Path | None = None,
) -> dict[str, dict]:
    """Evaluate all D2C_13_2 views with one shared canonical edge metric."""
    final = artifact_views(artifact_path, gold_nodes, alias_map)
    pre = artifact_views(pre_verification_path, gold_nodes, alias_map)
    result = {}
    if raw_gml_path is not None:
        raw = gml_views(raw_gml_path, gold_nodes, alias_map)
        result["raw"] = edge_metrics(
            raw["core"], gold_edges, raw["nodes"], set(gold_nodes),
            raw["self_loops"], raw["unaligned_edges"],
        )
    for name, data, key in (
        ("pre_verification", pre, "retained"),
        ("pre_evidence_core", pre, "evidence_core"),
        ("span_core", final, "span_core"),
        ("assertion_core", final, "assertion_core"),
        ("derived_core", final, "derived_core"),
        ("evidence_core", final, "evidence_core"),
        ("evidence_contract_core", final, "evidence_contract_core"),
        ("pre_soft_core", pre, "soft_core"),
        ("pre_soft_core_lenient", pre, "soft_core_lenient"),
        ("pre_support_aware_core", pre, "support_aware_core"),
        ("soft_core", final, "soft_core"),
        ("soft_core_lenient", final, "soft_core_lenient"),
        ("soft_core_precision", final, "soft_core_precision"),
        ("support_aware_core", final, "support_aware_core"),
        ("contract_support_aware_core", final, "contract_support_aware_core"),
        ("exact_support_core", final, "exact_support_core"),
        ("context_assisted_core", final, "context_assisted_core"),
        ("partial_evidence_review", final, "partial_evidence_review"),
        ("path_support_core", final, "path_support_core"),
        ("no_evidence_queue", final, "no_evidence_queue"),
        ("canonical_soft_core", final, "canonical_soft_core"),
        ("canonical_verified_core", final, "canonical_verified_core"),
        ("canonical_multi_document_core", final, "canonical_multi_document_core"),
        ("candidate_safe_core", final, "candidate_safe_core"),
        ("candidate_risk_review", final, "candidate_risk_review"),
        ("arbitration_review", final, "arbitration_review"),
        ("partial_forward_core", final, "partial_forward_core"),
        ("path_core", final, "path_core"),
        ("path_review", final, "path_review"),
        ("grounded", final, "grounded"),
        ("direct_core", final, "strict"),
        ("verified_core", final, "verified_core"),
        ("contract_verified_core", final, "contract_verified_core"),
        ("retained", final, "retained"),
        ("candidate", final, "candidate"),
        ("candidate_high", final, "candidate_high"),
        ("candidate_medium", final, "candidate_medium"),
        ("candidate_low", final, "candidate_low"),
        ("needs_review", final, "needs_review"),
        ("unsupported_hypothesis", final, "unsupported_hypothesis"),
    ):
        result[name] = edge_metrics(
            data[key], gold_edges, data["nodes"], set(gold_nodes),
            data["self_loops"], data["unaligned_edges"],
        )
    result["soft_core_path"] = path_metrics(
        final["soft_core"], gold_edges, final["nodes"], set(gold_nodes),
    )
    if raw_gml_path is not None:
        raw_data = gml_views(raw_gml_path, gold_nodes, alias_map)
        result["raw_path"] = path_metrics(
            raw_data["core"], gold_edges, raw_data["nodes"], set(gold_nodes),
        )
    return result

def aggregate_records(records: list[dict], method: str, view: str) -> dict[str, Any]:
    selected = [r for r in records if r["method"] == method and r["view"] == view]
    if not selected:
        return {"n": 0}
    def mean_sd(key: str) -> tuple[float, float]:
        values = [float(r["metrics"][key]) for r in selected]
        mean = sum(values) / len(values)
        if len(values) < 2:
            return round(mean, 4), 0.0
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return round(mean, 4), round(math.sqrt(variance), 4)
    precision = mean_sd("precision")
    recall = mean_sd("recall")
    f1 = mean_sd("f1")
    tp = sum(r["metrics"]["tp"] for r in selected)
    fp = sum(r["metrics"]["fp"] for r in selected)
    fn = sum(r["metrics"]["fn"] for r in selected)
    micro_p = tp / (tp + fp) if (tp + fp) else 0.0
    micro_r = tp / (tp + fn) if (tp + fn) else 0.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) else 0.0
    return {
        "n": len(selected),
        "precision_mean": precision[0],
        "precision_sd": precision[1],
        "recall_mean": recall[0],
        "recall_sd": recall[1],
        "f1_mean": f1[0],
        "f1_sd": f1[1],
        "micro_precision": round(micro_p, 4),
        "micro_recall": round(micro_r, 4),
        "micro_f1": round(micro_f1, 4),
        "tp": tp, "fp": fp, "fn": fn,
        "node_recall_mean": round(sum(r["metrics"]["node_recall"] for r in selected) / len(selected), 4),
        "reversed_edges": sum(r["metrics"]["reversed_edges"] for r in selected),
        "self_loops": sum(r["metrics"]["self_loops"] for r in selected),
        "unaligned_edges": sum(r["metrics"]["unaligned_edges"] for r in selected),
    }


_original_evaluate_unified_views = evaluate_unified_views
def _balanced_view_metrics(artifact_path, gold_nodes, alias_map, gold_edges):
    artifact = json.loads(Path(artifact_path).read_text(encoding='utf-8-sig'))
    labels = {str(entity.get('id')): entity.get('label', entity.get('id', '')) for entity in artifact.get('entities', [])}
    by_id = {str(edge.get('id')): edge for edge in artifact.get('edges', [])}
    pairs = set()
    unaligned = 0
    self_loops = 0
    for row in artifact.get('views', {}).get('soft_core_balanced', []):
        edge = by_id.get(str(row.get('edge_id')))
        if not edge:
            continue
        left = align_node(edge.get('source_name') or labels.get(str(edge.get('source')), edge.get('source')), gold_nodes, alias_map)
        right = align_node(edge.get('target_name') or labels.get(str(edge.get('target')), edge.get('target')), gold_nodes, alias_map)
        if not left or not right:
            unaligned += 1
        elif left == right:
            self_loops += 1
        else:
            pairs.add((left, right))
    nodes = {align_node(entity.get('label', entity.get('id', '')), gold_nodes, alias_map) for entity in artifact.get('entities', [])}
    nodes.discard(None)
    return edge_metrics(pairs, gold_edges, nodes, set(gold_nodes), self_loops, unaligned)
def evaluate_unified_views(artifact_path, pre_verification_path, gold_nodes, alias_map, gold_edges, raw_gml_path=None):
    result = _original_evaluate_unified_views(artifact_path, pre_verification_path, gold_nodes, alias_map, gold_edges, raw_gml_path)
    result['soft_core_balanced'] = _balanced_view_metrics(artifact_path, gold_nodes, alias_map, gold_edges)
    return result

