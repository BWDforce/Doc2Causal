# -*- coding: utf-8 -*-
from d2c13_2.evidence_completeness import package_evidence, restore_pre_verification_evidence, snapshot_pre_verification_evidence
from d2c13_2.semantics import apply_verdicts, materialize_views


def _artifact():
    return {
        "version": "D2C_13_2", "artifact_id": "urn:d2c:artifact:t",
        "entities": [
            {"id": "e:a", "label": "A", "aliases": []},
            {"id": "e:b", "label": "B", "aliases": []},
        ],
        "documents": [], "spans": [
            {"id": "s", "doc_id": "d", "start": 0, "end": 10, "text": "A causes B"}
        ],
        "assertions": [
            {"id": "a1", "source": "e:a", "target": "e:b", "doc_id": "d", "span_id": "s"}
        ],
        "conditions": [],
        "edges": [{
            "id": "x", "source": "e:a", "target": "e:b",
            "source_name": "A", "target_name": "B", "kind": "direct_core",
            "support_assertion_ids": ["a1"], "support_doc_ids": ["d"], "span_ids": ["s"],
            "candidate_assertion_ids": ["a1"], "candidate_doc_ids": ["d"], "candidate_span_ids": ["s"],
            "candidate_derivation_ids": [], "condition_id": None, "derivation_edge_ids": [],
            "semantic_support": "entailed", "direction_status": "supported",
            "support_mode": "direct_span", "support_strength": "strong",
            "provenance": "document_grounded", "attributes": {},
        }],
        "votes": [], "conflict_decisions": [], "cycle_decisions": [], "hypotheses": [],
        "views": {}, "diagnostics": {},
    }


def test_support_and_direction_are_decoupled():
    artifact = _artifact(); edge = artifact["edges"][0]
    apply_verdicts(artifact, [{"edge_id": "x", "support": "entailed", "direction": "ambiguous"}], direction_policy="balanced")
    assert edge["kind"] == "direct_core"
    assert edge["direction_status"] == "ambiguous"
    assert edge["verifier_support"] == "entailed"
    assert edge["verifier_direction"] == "ambiguous"


def test_strict_ambiguous_entailed_becomes_candidate():
    artifact = _artifact(); edge = artifact["edges"][0]
    apply_verdicts(artifact, [{"edge_id": "x", "support": "entailed", "direction": "ambiguous"}], direction_policy="strict")
    assert edge["kind"] == "candidate"
    assert edge["span_ids"] == []
    assert edge["candidate_span_ids"] == ["s"]


def test_advisory_preserves_kind_and_records_verdict():
    artifact = _artifact(); edge = artifact["edges"][0]
    apply_verdicts(artifact, [{"edge_id": "x", "support": "unsupported", "direction": "forward"}], direction_policy="advisory")
    assert edge["kind"] == "direct_core"
    assert edge["verification_status"] == "advisory"
    assert edge["verifier_support"] == "unsupported"
    assert edge["verifier_recommended_kind"] == "candidate"



def _span_only_alias_backfill_artifact():
    artifact = _artifact()
    edge = artifact["edges"][0]
    edge.update({
        "kind": "candidate",
        "edge_kind": "candidate",
        "support_assertion_ids": [],
        "support_doc_ids": [],
        "span_ids": [],
        "candidate_assertion_ids": [],
        "candidate_doc_ids": ["d"],
        "candidate_span_ids": ["s"],
        "canonical_support_assertion_ids": ["a1"],
        "canonical_direction_assertion_ids": ["a1"],
        "support_mode": "weak_candidate",
        "candidate_support_mode": "span_only",
        "evidence_grade": "span_only",
        "claim_scope": "direct",
        "direct_support": "untested",
        "assertion_support": "untested",
        "path_support": "untested",
    })
    return artifact


def test_span_only_entailed_edge_backfills_exact_assertion_before_promotion():
    artifact = _span_only_alias_backfill_artifact()
    edge = artifact["edges"][0]
    result = apply_verdicts(artifact, [{
        "edge_id": "x",
        "support": "entailed",
        "support_scope": "exact_span",
        "direct_support": "entailed",
        "assertion_support": "not_applicable",
        "path_support": "not_applicable",
        "direction": "forward",
        "coverage": "complete",
    }], direction_policy="balanced")
    assert result["assertion_backfilled"] == 1
    assert edge["kind"] == "direct_core"
    assert edge["support_assertion_ids"] == ["a1"]
    assert edge["span_ids"] == ["s"]
    assert edge["verification_status"] == "accepted"


def test_entailed_span_only_edge_without_assertion_stays_candidate():
    artifact = _span_only_alias_backfill_artifact()
    artifact["assertions"] = []
    edge = artifact["edges"][0]
    result = apply_verdicts(artifact, [{
        "edge_id": "x",
        "support": "entailed",
        "support_scope": "exact_span",
        "direct_support": "entailed",
        "assertion_support": "not_applicable",
        "path_support": "not_applicable",
        "direction": "forward",
        "coverage": "complete",
    }], direction_policy="balanced")
    assert result["assertion_backfilled"] == 0
    assert edge["kind"] == "candidate"
    assert edge["support_assertion_ids"] == []
    assert edge["verification_status"] == "needs_review"


def _context_complete(artifact):
    edge = artifact["edges"][0]
    edge.update({
        "evidence_completeness": "context_assisted",
        "support_scope": "context_assisted",
        "condition_grounding": "not_applicable",
        "evidence_coverage": {"source": True, "target": True, "predicate": True, "condition": True, "score": 1.0},
        "best_window_kind": "context",
        "best_window_source": "full_context",
        "best_window_width": 2,
        "evidence_exact_match": False,
        "context_window_complete": True,
    })
    return artifact


def test_complete_context_entailment_is_accepted():
    artifact = _context_complete(_artifact())
    edge = artifact["edges"][0]
    apply_verdicts(artifact, [{
        "edge_id": "x", "support": "entailed", "support_scope": "context_assisted",
        "direct_support": "partial", "assertion_support": "entailed",
        "path_support": "not_applicable", "direction": "forward", "coverage": "complete",
    }], direction_policy="balanced")
    assert edge["kind"] == "direct_core"
    assert edge["verification_status"] == "accepted"
    assert edge["verifier_support_scope"] == "context_assisted"
    assert edge["support_scope"] == "context_assisted"


def test_demoted_exact_evidence_remains_in_audit_view():
    artifact = _artifact()
    package_evidence(artifact)
    snapshot_pre_verification_evidence(artifact)
    edge = artifact["edges"][0]
    apply_verdicts(artifact, [{
        "edge_id": "x", "support": "uncertain", "support_scope": "exact_span",
        "direct_support": "uncertain", "direction": "forward", "coverage": "complete",
    }], direction_policy="balanced")
    assert edge["kind"] == "candidate"
    package_evidence(artifact)
    restore_pre_verification_evidence(artifact)
    materialize_views(artifact)
    assert edge["kind"] == "candidate"
    assert edge["evidence_completeness"] == "exact"
    assert any(row["edge_id"] == "x" for row in artifact["views"]["exact_support_core"])