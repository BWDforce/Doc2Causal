# -*- coding: utf-8 -*-
"""Independent D2C_13_2 artifact consumer.

This script intentionally imports only rdflib and the Python standard library.
It never imports d2c13_2, so it can serve as a cross-tool round-trip check against
the JSON-LD/Turtle files produced by the method.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import RDF

ACC = Namespace("https://w3id.org/d2c/acc#")
PROV = Namespace("http://www.w3.org/ns/prov#")


def _objects(graph: Graph, subject, predicate) -> list[str]:
    return [str(value) for value in graph.objects(subject, predicate)]


def _edge_rows(graph: Graph, kind: str) -> list[dict]:
    labels = {
        str(subject): str(graph.value(subject, ACC.label) or subject)
        for subject in graph.subjects(RDF.type, ACC.Entity)
    }
    rows = []
    for subject in graph.subjects(ACC.edgeKind, None):
        value = graph.value(subject, ACC.edgeKind)
        if str(value or "") != kind:
            continue
        rows.append({
            "edge_id": str(subject),
            "kind": kind,
            "source": labels.get(str(graph.value(subject, ACC.source) or ""), str(graph.value(subject, ACC.source) or "")),
            "target": labels.get(str(graph.value(subject, ACC.target) or ""), str(graph.value(subject, ACC.target) or "")),
            "verification_status": str(graph.value(subject, ACC.verificationState) or graph.value(subject, ACC.verificationStatus) or ""),
            "semantic_support": str(graph.value(subject, ACC.verificationStatus) or ""),
            "claim_scope": str(graph.value(subject, ACC.claimScope) or ""),
            "evidence_grade": str(graph.value(subject, ACC.evidenceGrade) or ""),
            "candidate_tier": str(graph.value(subject, ACC.candidateTier) or ""),
            "promotion_status": str(graph.value(subject, ACC.promotionStatus) or ""),
            "assertion_support": str(graph.value(subject, ACC.assertionSupport) or ""),
            "support_mode": str(graph.value(subject, ACC.supportMode) or ""),
            "direction_status": str(graph.value(subject, ACC.directionStatus) or ""),
            "directness_class": str(graph.value(subject, ACC.directnessClass) or ""),
            "shortcut_risk": str(graph.value(subject, ACC.shortcutRisk) or "low"),
            "path_summary_explicit_direct": bool(graph.value(subject, ACC.pathSummaryExplicitDirect) or False),
            "verifier_support": str(graph.value(subject, ACC.verifierSupport) or ""),
            "verifier_direction": str(graph.value(subject, ACC.verifierDirection) or ""),
            "provisional_recovery": bool(graph.value(subject, ACC.provisionalRecovery) or False),
            "assertion_recovery": bool(graph.value(subject, ACC.assertionRecovery) or False),
            "support_aware_eligible": bool(graph.value(subject, ACC.supportAwareEligible) or False),
            "identity_status": str(graph.value(subject, ACC.identityStatus) or "ok"),
            "polarity_status": str(graph.value(subject, ACC.polarityStatus) or ""),
            "derived_from": _objects(graph, subject, PROV.wasDerivedFrom),
            "candidate_assertions": _objects(graph, subject, ACC.candidateAssertion),
            "candidate_spans": _objects(graph, subject, ACC.candidateSpan),
        })
    return sorted(rows, key=lambda row: (row["source"], row["target"], row["edge_id"]))


def _is_evidence_core(edge: dict) -> bool:
    kind = str(edge.get("kind") or "")
    grade = str(edge.get("evidence_grade") or "none")
    mode = str(edge.get("support_mode") or "")
    if kind in {"direct_core", "conditional", "regime", "transitive_derived"}:
        return grade != "none"
    return kind == "candidate" and mode in {"weak_candidate", "assertion_only", "span_only"} and grade != "none"


def _is_soft_core_lenient(edge: dict) -> bool:
    kind = str(edge.get("kind") or "")
    grade = str(edge.get("evidence_grade") or "none")
    tier = str(edge.get("candidate_tier") or "none")
    if edge.get("provisional_recovery"):
        accepted = (
            str(edge.get("verification_state") or "") == "accepted"
            or str(edge.get("verifier_support") or "") in {"entailed", "conditional", "transitive"}
            or (
                str(edge.get("verifier_support") or "") == "partial"
                and str(edge.get("verifier_direction") or "") == "forward"
            )
        )
        if not accepted:
            return False
    if kind in {"direct_core", "conditional", "regime", "transitive_derived"}:
        return grade != "none"
    return kind == "candidate" and tier in {"high", "medium"}


def _is_soft_core_balanced(edge: dict) -> bool:
    return _is_soft_core_lenient(edge)
def _is_soft_core(edge: dict) -> bool:
    if not _is_soft_core_lenient(edge):
        return False
    if str(edge.get("direction_status") or "") in {"ambiguous", "role_conflict"}:
        return False
    if str(edge.get("shortcut_risk") or "low") == "high":
        return False
    return True


def _is_soft_core_precision(edge: dict) -> bool:
    if not _is_soft_core(edge):
        return False
    directness = str(edge.get("directness_class") or "")
    if not directness:
        return True
    if str(edge.get("kind") or "") == "transitive_derived":
        return bool(edge.get("path_summary_explicit_direct"))
    return directness in {"direct_span", "direct_assertion", "span_only"}


def _json_edges(path: Path | None) -> dict[str, list[tuple[str, str]]]:
    if path is None:
        return {}
    artifact = json.loads(path.read_text(encoding="utf-8-sig"))
    labels = {str(e.get("id")): e.get("label", e.get("id")) for e in artifact.get("entities", [])}
    views = {name: set() for name in ("direct_core", "candidate", "unsupported_hypothesis", "evidence_core", "soft_core", "soft_core_lenient", "soft_core_balanced", "soft_core_precision", "support_aware_core")}
    for edge in artifact.get("edges", []):
        kind = str(edge.get("kind") or "")
        source = str(edge.get("source_name") or labels.get(str(edge.get("source")), edge.get("source")))
        target = str(edge.get("target_name") or labels.get(str(edge.get("target")), edge.get("target")))
        if kind in views:
            views[kind].add((source, target))
        if _is_evidence_core(edge):
            views["evidence_core"].add((source, target))
        if _is_soft_core_lenient(edge):
            views["soft_core_lenient"].add((source, target))
        if _is_soft_core_balanced(edge):
            views["soft_core_balanced"].add((source, target))
        if _is_soft_core(edge):
            views["soft_core"].add((source, target))
        if _is_soft_core_precision(edge):
            views["soft_core_precision"].add((source, target))
        if edge.get("support_aware_eligible"):
            views["support_aware_core"].add((source, target))
    return {name: sorted(values) for name, values in views.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Consume a D2C_13_2 artifact with rdflib only")
    parser.add_argument("--input", required=True, help="JSON-LD or Turtle artifact")
    parser.add_argument("--artifact-json", default=None, help="Optional original JSON artifact for cross-check")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    graph = Graph()
    graph.parse(args.input, format=None)
    edge_kinds = {}
    for kind in ("direct_core", "conditional", "regime", "transitive_derived", "candidate", "unsupported_hypothesis", "rejected"):
        rows = _edge_rows(graph, kind)
        edge_kinds[kind] = rows
    report = {
        "consumer": "rdflib-independent",
        "input": str(Path(args.input).resolve()),
        "triples": len(graph),
        "edge_counts": {kind: len(rows) for kind, rows in edge_kinds.items()},
        "direct_core": edge_kinds["direct_core"],
        "candidate": edge_kinds["candidate"],
        "unsupported_hypothesis": edge_kinds["unsupported_hypothesis"],
        "provenance_triples": sum(1 for _ in graph.triples((None, PROV.wasDerivedFrom, None))),
        "documents": sum(1 for _ in graph.subjects(RDF.type, ACC.Document)),
        "entities": sum(1 for _ in graph.subjects(RDF.type, ACC.Entity)),
        "assertions": sum(1 for _ in graph.subjects(RDF.type, ACC.Assertion)),
        "spans": sum(1 for _ in graph.subjects(RDF.type, ACC.Span)),
    }
    json_views = _json_edges(Path(args.artifact_json) if args.artifact_json else None)
    if json_views:
        report["cross_tool_view_equality"] = {
            kind: {tuple(row) for row in json_views.get(kind, [])}
            == {(row["source"], row["target"]) for row in edge_kinds[kind]}
            for kind in ("direct_core", "candidate", "unsupported_hypothesis")
        }
        report["cross_tool_view_equality"]["evidence_core"] = {
            tuple(row) for row in json_views.get("evidence_core", [])
        } == {
            (row["source"], row["target"]) for kind in edge_kinds for row in edge_kinds[kind]
            if _is_evidence_core({
                "kind": row.get("kind", ""),
                "evidence_grade": row.get("evidence_grade", "none"),
                "support_mode": row.get("support_mode", ""),
            })
        }
        report["cross_tool_view_equality"]["soft_core"] = {
            tuple(row) for row in json_views.get("soft_core", [])
        } == {
            (row["source"], row["target"]) for kind in edge_kinds for row in edge_kinds[kind]
            if _is_soft_core({
                "kind": row.get("kind", ""),
                "evidence_grade": row.get("evidence_grade", "none"),
                "candidate_tier": row.get("candidate_tier", "none"),
                "directness_class": row.get("directness_class", ""),
                "shortcut_risk": row.get("shortcut_risk", "low"),
                "path_summary_explicit_direct": row.get("path_summary_explicit_direct", False),
                "direction_status": row.get("direction_status", ""),
                "provisional_recovery": row.get("provisional_recovery", False),
                "verification_state": row.get("verification_status", ""),
                "verifier_support": row.get("verifier_support", ""),
                "verifier_direction": row.get("verifier_direction", ""),
            })
        }
        report["cross_tool_view_equality"]["soft_core_precision"] = {
            tuple(row) for row in json_views.get("soft_core_precision", [])
        } == {
            (row["source"], row["target"]) for kind in edge_kinds for row in edge_kinds[kind]
            if _is_soft_core_precision({
                "kind": row.get("kind", ""),
                "evidence_grade": row.get("evidence_grade", "none"),
                "candidate_tier": row.get("candidate_tier", "none"),
                "directness_class": row.get("directness_class", ""),
                "shortcut_risk": row.get("shortcut_risk", "low"),
                "path_summary_explicit_direct": row.get("path_summary_explicit_direct", False),
                "direction_status": row.get("direction_status", ""),
                "provisional_recovery": row.get("provisional_recovery", False),
                "verification_state": row.get("verification_status", ""),
                "verifier_support": row.get("verifier_support", ""),
                "verifier_direction": row.get("verifier_direction", ""),
            })
        }
        report["cross_tool_view_equality"]["soft_core_lenient"] = {
            tuple(row) for row in json_views.get("soft_core_lenient", [])
        } == {
            (row["source"], row["target"]) for kind in edge_kinds for row in edge_kinds[kind]
            if _is_soft_core_lenient({
                "kind": row.get("kind", ""),
                "evidence_grade": row.get("evidence_grade", "none"),
                "candidate_tier": row.get("candidate_tier", "none"),
                "provisional_recovery": row.get("provisional_recovery", False),
                "verification_state": row.get("verification_status", ""),
                "verifier_support": row.get("verifier_support", ""),
                "verifier_direction": row.get("verifier_direction", ""),
            })
        }
        report["cross_tool_view_equality"]["soft_core_balanced"] = report["cross_tool_view_equality"]["soft_core_lenient"]
        report["cross_tool_view_equality"]["support_aware_core"] = {
            tuple(row) for row in json_views.get("support_aware_core", [])
        } == {
            (row["source"], row["target"]) for kind in edge_kinds for row in edge_kinds[kind]
            if row.get("support_aware_eligible")
        }
        report["cross_tool_equal"] = all(report["cross_tool_view_equality"].values())
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("cross_tool_equal", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())


