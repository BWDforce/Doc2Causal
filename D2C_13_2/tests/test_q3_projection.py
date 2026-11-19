# -*- coding: utf-8 -*-
from d2c13_2.canonical_votes import project_canonical_votes
from d2c13_2.queries import answer, generate_query_cases


def _artifact():
    return {
        "entities": [
            {"id": "a", "label": "A", "aliases": []},
            {"id": "b", "label": "B", "aliases": []},
        ],
        "documents": [], "spans": [], "assertions": [],
        "edges": [{
            "id": "e1", "source": "a", "target": "b", "source_name": "A", "target_name": "B",
            "kind": "candidate", "support_assertion_ids": [], "candidate_assertion_ids": [],
            "support_doc_ids": [], "candidate_doc_ids": ["doc1"], "span_ids": [], "candidate_span_ids": [],
            "condition_id": None, "derivation_edge_ids": [], "views": {}, "diagnostics": {},
        }],
        "votes": [], "conflict_decisions": [], "cycle_decisions": [], "views": {}, "diagnostics": {},
    }


def test_canonical_votes_are_projected_and_queryable_as_q3():
    artifact = _artifact()
    summary = project_canonical_votes(artifact, {"votes": [{
        "pair": ["a", "b"],
        "edge_ids": ["e1"],
        "support_doc_ids": ["doc1", "doc2"],
        "opposition_doc_ids": ["doc3"],
        "uncertain_doc_ids": [],
        "forward_doc_ids": ["doc1", "doc2"],
        "reverse_doc_ids": ["doc3"],
        "support_votes": 2,
        "opposition_votes": 1,
        "direction_rule": "majority_forward",
        "reference_orientation": "forward",
        "source_label": "A",
        "target_label": "B",
        "assertion_count": 3,
    }]})
    assert summary["projected_votes"] == 1
    assert len(artifact["votes"]) == 1
    payload = answer(artifact, "Q3", "e1")
    assert payload["votes"][0]["support_doc_ids"] == ["doc1", "doc2"]
    assert payload["votes"][0]["opposition_doc_ids"] == ["doc3"]
    assert payload["votes"][0]["relative_direction"] == "mixed"
    cases = generate_query_cases(artifact, limit=2)
    assert any(case["query"] == "Q3" for case in cases)
