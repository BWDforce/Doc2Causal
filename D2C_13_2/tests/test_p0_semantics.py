# -*- coding: utf-8 -*-
from d2c13_2.semantics import apply_verdicts, build_verification_requests, materialize_views


def _artifact_with_chain():
    return {
        "version": "D2C_13_2",
        "artifact_id": "urn:d2c:artifact:test",
        "entities": [
            {"id": "e:a", "label": "A", "aliases": []},
            {"id": "e:b", "label": "B", "aliases": []},
            {"id": "e:c", "label": "C", "aliases": []},
        ],
        "documents": [{"id": "d:1", "doc_id": "doc1"}],
        "spans": [
            {"id": "s:ab", "doc_id": "doc1", "start": 0, "end": 8, "text": "A causes B"},
            {"id": "s:bc", "doc_id": "doc1", "start": 9, "end": 17, "text": "B causes C"},
        ],
        "assertions": [
            {"id": "a:ab", "source": "e:a", "target": "e:b", "doc_id": "doc1", "span_id": "s:ab"},
            {"id": "a:bc", "source": "e:b", "target": "e:c", "doc_id": "doc1", "span_id": "s:bc"},
        ],
        "conditions": [],
        "edges": [
            {
                "id": "x:ab", "source": "e:a", "target": "e:b", "source_name": "A", "target_name": "B",
                "kind": "direct_core", "edge_kind": "direct_core",
                "support_assertion_ids": ["a:ab"], "support_doc_ids": ["doc1"], "span_ids": ["s:ab"],
                "candidate_assertion_ids": ["a:ab"], "candidate_doc_ids": ["doc1"], "candidate_span_ids": ["s:ab"],
                "candidate_derivation_ids": [], "condition_id": None, "derivation_edge_ids": [],
                "claim_scope": "direct", "evidence_grade": "span_grounded",
                "candidate_support_mode": "direct_span", "candidate_support_strength": "strong",
                "candidate_origin": "compiler", "candidate_tier": "high",
                "support_mode": "direct_span", "support_strength": "strong",
                "verification_status": "unverified", "direction_status": "unverified",
                "semantic_support": "untested", "provenance": "document_grounded", "attributes": {},
            },
            {
                "id": "x:bc", "source": "e:b", "target": "e:c", "source_name": "B", "target_name": "C",
                "kind": "direct_core", "edge_kind": "direct_core",
                "support_assertion_ids": ["a:bc"], "support_doc_ids": ["doc1"], "span_ids": ["s:bc"],
                "candidate_assertion_ids": ["a:bc"], "candidate_doc_ids": ["doc1"], "candidate_span_ids": ["s:bc"],
                "candidate_derivation_ids": [], "condition_id": None, "derivation_edge_ids": [],
                "claim_scope": "direct", "evidence_grade": "span_grounded",
                "candidate_support_mode": "direct_span", "candidate_support_strength": "strong",
                "candidate_origin": "compiler", "candidate_tier": "high",
                "support_mode": "direct_span", "support_strength": "strong",
                "verification_status": "unverified", "direction_status": "unverified",
                "semantic_support": "untested", "provenance": "document_grounded", "attributes": {},
            },
            {
                "id": "x:ac", "source": "e:a", "target": "e:c", "source_name": "A", "target_name": "C",
                "kind": "transitive_derived", "edge_kind": "transitive_derived",
                "support_assertion_ids": [], "support_doc_ids": [], "span_ids": [],
                "candidate_assertion_ids": [], "candidate_doc_ids": [], "candidate_span_ids": [],
                "candidate_derivation_ids": ["x:ab", "x:bc"], "condition_id": None,
                "derivation_edge_ids": ["x:ab", "x:bc"],
                "claim_scope": "composed", "evidence_grade": "derived_path",
                "candidate_support_mode": "weak_candidate", "candidate_support_strength": "weak",
                "candidate_origin": "compiler", "candidate_tier": "high",
                "support_mode": "weak_candidate", "support_strength": "weak",
                "verification_status": "unverified", "direction_status": "unverified",
                "semantic_support": "derived", "provenance": "derived_chain", "attributes": {},
            },
        ],
        "views": {}, "diagnostics": {},
    }


def test_demotion_preserves_candidate_provenance_and_tier():
    artifact = _artifact_with_chain()
    edge = artifact["edges"][0]
    apply_verdicts(artifact, [{
        "edge_id": "x:ab", "support": "unsupported", "direction": "forward",
    }])
    assert edge["kind"] == "candidate"
    assert edge["support_mode"] == "candidate"
    assert edge["candidate_support_mode"] == "direct_span"
    assert edge["candidate_support_strength"] == "strong"
    assert edge["candidate_tier"] == "high"
    assert edge["evidence_grade"] == "span_grounded"
    assert edge["verification_status"] == "unsupported"
    assert edge["id"] in {row["edge_id"] for row in artifact["views"]["candidate_high"]}


def test_weak_candidate_is_evidence_core_not_span_core():
    artifact = _artifact_with_chain()
    edge = artifact["edges"][0]
    edge.update({
        "kind": "candidate", "edge_kind": "candidate",
        "support_assertion_ids": [], "support_doc_ids": [], "span_ids": [],
        "support_mode": "weak_candidate", "support_strength": "weak",
        "candidate_support_mode": "weak_candidate", "candidate_tier": "medium",
        "verification_status": "unverified",
    })
    materialize_views(artifact)
    evidence_ids = {row["edge_id"] for row in artifact["views"]["evidence_core"]}
    span_ids = {row["edge_id"] for row in artifact["views"]["span_core"]}
    assert "x:ab" in evidence_ids
    assert "x:ab" not in span_ids


def test_verification_request_contains_union_and_path():
    artifact = _artifact_with_chain()
    materialize_views(artifact)
    requests = {row["edge_id"]: row for row in build_verification_requests(artifact)}
    request = requests["x:ac"]
    assert request["path_required"] is True
    assert request["path_edge_ids"] == ["x:ab", "x:bc"]
    assert set(request["path_edge_ids"]).issubset(set(request["evidence_union_ids"]) | {"x:ab", "x:bc"})
    assert {row["span_id"] for row in request["spans"]} == {"s:ab", "s:bc"}


def test_composed_claim_requires_path_support():
    artifact = _artifact_with_chain()
    edge = artifact["edges"][2]
    apply_verdicts(artifact, [{
        "edge_id": "x:ac", "support": "entailed", "direct_support": "entailed",
        "path_support": "not_applicable", "direction": "forward",
    }])
    assert edge["kind"] == "candidate"
    assert edge["verification_status"] == "needs_review"

    artifact = _artifact_with_chain()
    edge = artifact["edges"][2]
    apply_verdicts(artifact, [{
        "edge_id": "x:ac", "support": "entailed", "direct_support": "not_applicable",
        "path_support": "entailed", "direction": "forward",
    }])
    assert edge["kind"] == "transitive_derived"
    assert edge["verification_status"] == "accepted"
    assert edge["claim_scope"] == "composed"
    assert edge["evidence_grade"] == "derived_path"
