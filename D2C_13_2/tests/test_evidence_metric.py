# -*- coding: utf-8 -*-
from d2c13_2.audit import evidence_attribution_report


def test_implicit_cross_sentence_evidence_counts_as_positive_support():
    artifact = {
        "entities": [
            {"id": "a", "label": "Pump vibration", "aliases": []},
            {"id": "b", "label": "Seal failure", "aliases": []},
        ],
        "spans": [
            {"id": "s1", "doc_id": "d", "text": "Pump vibration was elevated during the shift."},
            {"id": "s2", "doc_id": "d", "text": "The seal later failed during restart."},
        ],
        "assertions": [
            {"id": "a1", "source": "a", "target": "b", "doc_id": "d", "span_id": "s1"},
            {"id": "a2", "source": "a", "target": "b", "doc_id": "d", "span_id": "s2"},
        ],
        "conditions": [],
        "edges": [{
            "id": "e1", "source": "a", "target": "b", "source_name": "Pump vibration", "target_name": "Seal failure",
            "kind": "direct_core", "support_assertion_ids": ["a1", "a2"], "candidate_assertion_ids": [],
            "support_doc_ids": ["d"], "candidate_doc_ids": [], "span_ids": ["s1", "s2"], "candidate_span_ids": [],
            "condition_id": None, "derivation_edge_ids": [], "temporal_consistency": "not_applicable",
            "fraction_consistency": "not_applicable", "condition_grounding": "not_applicable",
            "verification_status": "accepted", "semantic_support": "entailed", "verifier_support": "entailed",
            "best_window_width": 2, "context_window_complete": False,
            "attributes": {"description": "Pump vibration led to seal failure"},
        }],
        "views": {}, "diagnostics": {},
    }
    report = evidence_attribution_report(artifact)
    assert report["grounded_evidence_coverage"] == 1.0
    assert report["verified_evidence_coverage"] == 1.0
    assert report["cross_sentence_evidence_coverage"] == 1.0
    assert report["implicit_verified_coverage"] == 0.0
    assert report["lexical_cue_coverage"] == 1.0
    assert "evidence_contract_coverage" not in report


def test_no_lexical_cue_is_not_penalized():
    artifact = {
        "entities": [
            {"id": "a", "label": "Pump vibration", "aliases": []},
            {"id": "b", "label": "Seal failure", "aliases": []},
        ],
        "spans": [
            {"id": "s1", "doc_id": "d", "text": "Pump vibration was elevated during the shift."},
            {"id": "s2", "doc_id": "d", "text": "The seal later failed during restart."},
        ],
        "assertions": [
            {"id": "a1", "source": "a", "target": "b", "doc_id": "d", "span_id": "s1"},
            {"id": "a2", "source": "a", "target": "b", "doc_id": "d", "span_id": "s2"},
        ],
        "conditions": [],
        "edges": [{
            "id": "e1", "source": "a", "target": "b", "source_name": "Pump vibration", "target_name": "Seal failure",
            "kind": "direct_core", "support_assertion_ids": ["a1", "a2"], "candidate_assertion_ids": [],
            "support_doc_ids": ["d"], "candidate_doc_ids": [], "span_ids": ["s1", "s2"], "candidate_span_ids": [],
            "condition_id": None, "derivation_edge_ids": [], "temporal_consistency": "not_applicable",
            "fraction_consistency": "not_applicable", "condition_grounding": "not_applicable",
            "verification_status": "accepted", "semantic_support": "entailed", "verifier_support": "entailed",
            "best_window_width": 2, "context_window_complete": False, "attributes": {},
        }],
        "views": {}, "diagnostics": {},
    }
    report = evidence_attribution_report(artifact)
    assert report["verified_evidence_coverage"] == 1.0
    assert report["implicit_verified_coverage"] == 1.0
    assert report["lexical_cue_coverage"] == 0.0
