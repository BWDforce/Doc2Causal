# -*- coding: utf-8 -*-
import json
from pathlib import Path

import networkx as nx

from d2c13_2.compiler import compile_run
from d2c13_2.semantics import (
    apply_verdicts,
    build_verification_requests,
    classify_edge_directness,
    materialize_views,
    relation_role_direction_guard,
    shortcut_guard,
    temporal_direction_guard,
    textual_direction_guard,
)


def _artifact():
    return {
        "version": "D2C_13_2",
        "artifact_id": "urn:d2c:artifact:p0-d2c13_2",
        "entities": [
            {"id": "e:out", "label": "Rendering Normal", "description": "rendering performance is normal", "entity_type": "outcome", "aliases": []},
            {"id": "e:cap", "label": "Printer Accepts TrueType Fonts", "description": "printer capability", "entity_type": "capability", "aliases": []},
            {"id": "e:a", "label": "A", "description": "", "entity_type": "event", "aliases": []},
            {"id": "e:b", "label": "B", "description": "", "entity_type": "event", "aliases": []},
            {"id": "e:c", "label": "C", "description": "", "entity_type": "event", "aliases": []},
        ],
        "documents": [{"id": "d:1", "doc_id": "doc1"}],
        "spans": [],
        "assertions": [],
        "conditions": [],
        "edges": [
            {
                "id": "x:role", "source": "e:out", "target": "e:cap",
                "source_name": "Rendering Normal", "target_name": "Printer Accepts TrueType Fonts",
                "kind": "candidate", "edge_kind": "candidate",
                "support_assertion_ids": [], "support_doc_ids": [], "span_ids": [],
                "candidate_assertion_ids": ["a:role"], "candidate_doc_ids": ["doc1"], "candidate_span_ids": [],
                "candidate_derivation_ids": [], "condition_id": None, "derivation_edge_ids": [],
                "claim_scope": "direct", "evidence_grade": "assertion_grounded",
                "candidate_support_mode": "assertion_only", "candidate_support_strength": "medium",
                "candidate_origin": "test", "candidate_tier": "medium",
                "support_mode": "candidate", "support_strength": "weak",
                "verification_status": "unverified", "direction_status": "unverified",
                "semantic_support": "candidate", "attributes": {"description": "rendering normal causes printer acceptance"},
            },
            {
                "id": "x:ab", "source": "e:a", "target": "e:b",
                "source_name": "A", "target_name": "B", "kind": "direct_core", "edge_kind": "direct_core",
                "support_assertion_ids": ["a:ab"], "support_doc_ids": ["doc1"], "span_ids": ["s:ab"],
                "candidate_assertion_ids": ["a:ab"], "candidate_doc_ids": ["doc1"], "candidate_span_ids": ["s:ab"],
                "candidate_derivation_ids": [], "condition_id": None, "derivation_edge_ids": [],
                "claim_scope": "direct", "evidence_grade": "span_grounded",
                "candidate_support_mode": "direct_span", "candidate_support_strength": "strong",
                "candidate_origin": "test", "candidate_tier": "high",
                "support_mode": "direct_span", "support_strength": "strong",
                "verification_status": "unverified", "direction_status": "unverified",
                "semantic_support": "untested", "attributes": {},
            },
            {
                "id": "x:bc", "source": "e:b", "target": "e:c",
                "source_name": "B", "target_name": "C", "kind": "direct_core", "edge_kind": "direct_core",
                "support_assertion_ids": ["a:bc"], "support_doc_ids": ["doc1"], "span_ids": ["s:bc"],
                "candidate_assertion_ids": ["a:bc"], "candidate_doc_ids": ["doc1"], "candidate_span_ids": ["s:bc"],
                "candidate_derivation_ids": [], "condition_id": None, "derivation_edge_ids": [],
                "claim_scope": "direct", "evidence_grade": "span_grounded",
                "candidate_support_mode": "direct_span", "candidate_support_strength": "strong",
                "candidate_origin": "test", "candidate_tier": "high",
                "support_mode": "direct_span", "support_strength": "strong",
                "verification_status": "unverified", "direction_status": "unverified",
                "semantic_support": "untested", "attributes": {},
            },
            {
                "id": "x:ac", "source": "e:a", "target": "e:c",
                "source_name": "A", "target_name": "C", "kind": "direct_core", "edge_kind": "direct_core",
                "support_assertion_ids": [], "support_doc_ids": [], "span_ids": [],
                "candidate_assertion_ids": [], "candidate_doc_ids": [], "candidate_span_ids": [],
                "candidate_derivation_ids": ["x:ab", "x:bc"], "condition_id": None,
                "derivation_edge_ids": ["x:ab", "x:bc"],
                "claim_scope": "composed", "evidence_grade": "derived_path",
                "candidate_support_mode": "weak_candidate", "candidate_support_strength": "weak",
                "candidate_origin": "test", "candidate_tier": "medium",
                "support_mode": "weak_candidate", "support_strength": "weak",
                "verification_status": "unverified", "direction_status": "unverified",
                "semantic_support": "derived", "attributes": {},
            },
        ],
        "views": {}, "diagnostics": {},
    }


def test_relation_role_reverses_outcome_to_capability_when_span_is_absent():
    artifact = _artifact()
    edge = artifact["edges"][0]
    textual_direction_guard(artifact)
    relation_role_direction_guard(artifact)
    assert (edge["source"], edge["target"]) == ("e:cap", "e:out")
    assert edge["direction_audit"] == "relation_role"
    assert edge["role_override"] is True


def test_temporal_guard_abstains_for_reverse_order_context():
    artifact = {
        "version": "D2C_13_2", "artifact_id": "urn:d2c:artifact:temporal", "entities": [
            {"id": "e:late", "label": "Value at 12:30", "description": "back-corrected record", "aliases": []},
            {"id": "e:early", "label": "Value at 12:15", "description": "back-corrected record", "aliases": []},
        ], "documents": [], "spans": [], "assertions": [], "conditions": [], "edges": [{
            "id": "x:t", "source": "e:late", "target": "e:early", "source_name": "Value at 12:30", "target_name": "Value at 12:15",
            "kind": "direct_core", "evidence_grade": "assertion_grounded", "support_assertion_ids": ["a:t"],
            "span_ids": [], "support_doc_ids": [], "candidate_assertion_ids": ["a:t"],
            "attributes": {"description": "reverse-order control loop with back-corrected record"},
            "verification_status": "unverified", "direction_status": "unverified",
        }], "views": {}, "diagnostics": {},
    }
    result = temporal_direction_guard(artifact)
    edge = artifact["edges"][0]
    assert result["abstained"] == 1
    assert (edge["source"], edge["target"]) == ("e:late", "e:early")
    assert edge["temporal_abstained"] is True


def test_directness_shortcut_guard_excludes_path_only_edge_from_new_soft_core():
    artifact = _artifact()
    classify_edge_directness(artifact)
    shortcut_guard(artifact)
    materialize_views(artifact)
    edge = next(e for e in artifact["edges"] if e["id"] == "x:ac")
    soft = {row["edge_id"] for row in artifact["views"]["soft_core"]}
    lenient = {row["edge_id"] for row in artifact["views"]["soft_core_lenient"]}
    assert edge["directness_class"] == "path_only"
    assert edge["shortcut_risk"] == "high"
    assert "x:ac" not in soft
    assert "x:ac" in lenient


def test_verification_request_contains_bidirectional_candidates():
    artifact = _artifact()
    textual_direction_guard(artifact)
    relation_role_direction_guard(artifact)
    classify_edge_directness(artifact)
    shortcut_guard(artifact)
    requests = build_verification_requests(artifact)
    request = next(r for r in requests if r["edge_id"] == "x:role")
    assert request["direction_candidates"][0]["candidate_id"] == "forward"
    assert request["direction_candidates"][1]["candidate_id"] == "reverse"
    assert request["relation_role_source"] == "capability"
    assert request["relation_role_target"] == "outcome"


def test_direction_scores_can_select_reverse_without_explicit_direction():
    artifact = _artifact()
    edge = artifact["edges"][0]
    apply_verdicts(artifact, [{
        "edge_id": "x:role", "support": "entailed", "direction": "unspecified",
        "direction_support_forward": 0.1, "direction_support_reverse": 0.9,
    }])
    assert (edge["source"], edge["target"]) == ("e:cap", "e:out")
    assert edge["direction_status"] == "corrected"


def test_compile_recovers_evidence_endpoints_missing_from_gml(tmp_path: Path):
    run = tmp_path / "run"
    run.mkdir()
    graph = nx.DiGraph()
    graph.add_node("A")
    graph.add_node("B")
    graph.add_edge("A", "B", edge_type="direct", confidence="high", span_ids_json="[]", merged_aliases="[]", values="[]")
    nx.write_gml(graph, run / "causal_graph.gml")
    evidence = {"entries": {
        "C || D": {"assertions": [{"doc_id": "doc1", "source": "pre", "order": 1, "span": ""}]},
    }}
    (run / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    artifact = compile_run(run, repair_chain=False)
    labels = {entity["label"] for entity in artifact["entities"]}
    assert {"A", "B", "C", "D"}.issubset(labels)
    assert artifact["diagnostics"]["entity_recovery"]["added"] >= 2

