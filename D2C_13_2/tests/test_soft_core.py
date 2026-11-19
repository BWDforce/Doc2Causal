# -*- coding: utf-8 -*-
from d2c13_2.semantics import apply_verdicts, materialize_views


def _artifact():
    return {
        "version": "D2C_13_2",
        "artifact_id": "urn:d2c:artifact:soft-core-test",
        "entities": [
            {"id": "e:a", "label": "A", "aliases": []},
            {"id": "e:b", "label": "B", "aliases": []},
            {"id": "e:c", "label": "C", "aliases": []},
        ],
        "documents": [],
        "spans": [],
        "assertions": [],
        "conditions": [],
        "edges": [
            {
                "id": "x:ab", "source": "e:a", "target": "e:b", "source_name": "A", "target_name": "B",
                "kind": "direct_core", "evidence_grade": "span_grounded",
                "support_assertion_ids": ["a:ab"], "span_ids": ["s:ab"], "support_doc_ids": ["d"],
                "candidate_tier": "high", "support_mode": "direct_span",
                "candidate_support_mode": "direct_span", "verification_status": "unverified",
                "direction_status": "unverified", "attributes": {},
            },
            {
                "id": "x:bc", "source": "e:b", "target": "e:c", "source_name": "B", "target_name": "C",
                "kind": "candidate", "evidence_grade": "assertion_grounded",
                "candidate_assertion_ids": ["a:bc"], "candidate_doc_ids": ["d"],
                "candidate_tier": "medium", "support_mode": "weak_candidate",
                "candidate_support_mode": "weak_candidate", "verification_status": "needs_review",
                "verifier_support": "uncertain", "direction_status": "ambiguous", "attributes": {},
            },
            {
                "id": "x:ac", "source": "e:a", "target": "e:c", "source_name": "A", "target_name": "C",
                "kind": "candidate", "evidence_grade": "none",
                "candidate_tier": "low", "support_mode": "candidate",
                "candidate_support_mode": "candidate", "verification_status": "unsupported",
                "direction_status": "unverified", "attributes": {},
            },
        ],
        "views": {}, "diagnostics": {},
    }


def test_soft_core_keeps_high_medium_and_excludes_low():
    artifact = _artifact()
    materialize_views(artifact)
    soft = {row["edge_id"] for row in artifact["views"]["soft_core"]}
    lenient = {row["edge_id"] for row in artifact["views"]["soft_core_lenient"]}
    review = {row["edge_id"] for row in artifact["views"]["needs_review"]}
    assert {"x:ab", "x:bc"}.issubset(lenient)
    assert "x:ab" in soft
    assert "x:bc" not in soft
    assert "x:ac" not in soft
    assert {"x:bc", "x:ac"}.issubset(review)


def test_assertion_only_verdict_is_review_not_direct_core():
    artifact = _artifact()
    edge = artifact["edges"][0]
    edge.update({
        "support_mode": "weak_candidate",
        "candidate_support_mode": "weak_candidate",
        "candidate_assertion_ids": ["a:edge"],
        "evidence_grade": "assertion_grounded",
        "candidate_tier": "medium",
    })
    apply_verdicts(artifact, [{
        "edge_id": "x:ab", "support": "unsupported", "direction": "forward",
        "direct_support": "not_applicable", "assertion_support": "entailed",
    }])
    assert edge["kind"] == "candidate"
    assert edge["verification_status"] == "needs_review"
    assert edge["promotion_status"] == "soft_core"
    assert edge["id"] in {row["edge_id"] for row in artifact["views"]["soft_core"]}


def test_partial_forward_candidate_is_promoted_to_soft_core():
    artifact = _artifact()
    edge = artifact["edges"][1]
    edge.update({
        "verifier_support": "partial",
        "verifier_direction": "forward",
        "evidence_grade": "span_grounded",
        "candidate_tier": "medium",
    })
    materialize_views(artifact)
    assert edge["partial_forward_promotion"] is True
    assert edge["promotion_status"] == "soft_core"
    assert edge["id"] in {row["edge_id"] for row in artifact["views"]["partial_forward_core"]}
