# -*- coding: utf-8 -*-
from d2c13_2.canonical_arbitration import apply_canonical_arbitration, arbitration_gate
from d2c13_2.queries import answer, generate_query_cases
from d2c13_2.document_alias import score_mention_pair
from d2c13_2.semantics import materialize_views


def _edge(edge_id, source, target, kind="candidate", docs=None, assertions=None):
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "kind": kind,
        "support_doc_ids": list(docs or []),
        "candidate_doc_ids": list(docs or []),
        "support_assertion_ids": list(assertions or []),
        "candidate_assertion_ids": list(assertions or []),
        "span_ids": [],
        "candidate_span_ids": [],
        "derivation_edge_ids": [],
        "candidate_derivation_ids": [],
        "verification_status": "unverified",
        "evidence_grade": "assertion_grounded",
        "candidate_tier": "high",
        "candidate_support_mode": "assertion_only",
        "support_mode": "assertion_only",
        "claim_scope": "hypothesis",
        "semantic_support": "candidate",
        "direction_status": "unverified",
        "identity_status": "ok",
    }


def _artifact():
    return {
        "entities": [
            {"id": "a", "label": "A", "aliases": ["alpha"]},
            {"id": "b", "label": "B", "aliases": []},
            {"id": "c", "label": "C", "aliases": []},
        ],
        "assertions": [
            {"id": "as1", "source": "a", "target": "b", "doc_id": "doc1"},
            {"id": "as2", "source": "a", "target": "b", "doc_id": "doc2"},
            {"id": "as3", "source": "b", "target": "a", "doc_id": "doc2"},
        ],
        "edges": [
            _edge("ab", "a", "b", "direct_core", ["doc1", "doc2"], ["as1", "as2"]),
            _edge("ba", "b", "a", "candidate", ["doc2"], ["as3"]),
            _edge("bc", "b", "c", "candidate", ["doc2"], []),
            _edge("ca", "c", "a", "candidate", ["doc2"], []),
        ],
        "diagnostics": {},
        "views": {},
        "promotion_policy": {"name": "test"},
    }


def test_canonical_replay_and_candidate_risk_do_not_delete_edges():
    artifact = _artifact()
    before = {e["id"] for e in artifact["edges"]}
    result = apply_canonical_arbitration(artifact, {"enabled": True})
    assert {e["id"] for e in artifact["edges"]} == before
    ab = next(e for e in artifact["edges"] if e["id"] == "ab")
    assert ab["canonical_multi_document_support"] is True
    assert ab["canonical_support_doc_count"] == 2
    ba = next(e for e in artifact["edges"] if e["id"] == "ba")
    assert ba["candidate_conflict"] is True
    assert "candidate_conflict" in ba["candidate_risk_flags"]
    assert result["candidate_risk"]["candidate_conflicts"] >= 1
    assert arbitration_gate(ba, {"exclude_candidate_conflict": True})["eligible"] is False
    assert ab["candidate_risk_flags"] == []
    assert ab["arbitration_eligible"] is True


def test_candidate_risk_is_projected_into_q4_and_q7_queries():
    artifact = _artifact()
    result = apply_canonical_arbitration(artifact, {"enabled": True})
    assert result["query_decision_projection"]["conflict_decisions"] >= 1
    assert result["query_decision_projection"]["cycle_decisions"] >= 1

    q4 = answer(artifact, "Q4", "ba")["decisions"]
    assert any(row["kind"] == "candidate_conflict" for row in q4)
    assert q4[0]["edge_ids"]
    q7 = answer(artifact, "Q7", "ba")["decisions"]
    assert q7 and all(row["removed_edge"] is None for row in q7)
    assert q7[0]["edge_ids"]

    cases = generate_query_cases(artifact, limit=1)
    assert {"Q4", "Q7"}.issubset({case["query"] for case in cases})
    for query in ("Q4", "Q7"):
        case = next(case for case in cases if case["query"] == query)
        payload = answer(artifact, query, case["edge_id"])
        assert payload["decisions"]

    conflict_count = len(artifact["conflict_decisions"])
    cycle_count = len(artifact["cycle_decisions"])
    apply_canonical_arbitration(artifact, {"enabled": True})
    assert len(artifact["conflict_decisions"]) == conflict_count
    assert len(artifact["cycle_decisions"]) == cycle_count
    assert sum(
        row.get("decision_source") == "canonical_arbitration.candidate_risk"
        for row in artifact["conflict_decisions"]
    ) >= 1
    assert sum(
        row.get("decision_source") == "canonical_arbitration.candidate_risk"
        for row in artifact["cycle_decisions"]
    ) >= 1


def test_materialize_keeps_legacy_view_and_adds_arbitrated_view():
    artifact = _artifact()
    apply_canonical_arbitration(artifact, {"enabled": True})
    materialize_views(artifact)
    assert artifact["views"]["soft_core"]
    assert artifact["views"]["canonical_soft_core"] != artifact["views"]["soft_core"]
    risky = next(e for e in artifact["edges"] if e["id"] == "ba")
    assert risky["arbitration_eligible"] is False
    assert risky["id"] not in {row["edge_id"] for row in artifact["views"]["canonical_soft_core"]}


def test_relation_role_guard_downgrades_auto_merge_to_review():
    left = {
        "entity_id": "left", "doc_id": "d", "surfaces": {"same variable"}, "contexts": [], "types": set(),
        "neighbors": set(), "roles": {"source"}, "role_counts": {"source": 3, "target": 0},
    }
    right = {
        "entity_id": "right", "doc_id": "d", "surfaces": {"same variable"}, "contexts": [], "types": set(),
        "neighbors": set(), "roles": {"target"}, "role_counts": {"source": 0, "target": 3},
    }
    result = score_mention_pair(left, right)
    assert result["decision"] == "needs_review"
    assert result["relation_role_guard"] is True
    assert "source_target_role_asymmetry" in result["reasons"]
def test_canonical_document_vote_coalesces_alias_evidence():
    import networkx as nx
    from d2c13_2.lang7_flow.run_extract import resolve_conflicts

    graph = nx.DiGraph()
    graph.add_edge("canonical", "beta", condition="")
    graph.add_edge("beta", "canonical", condition="")
    evidence = {
        ("alpha", "beta"): {
            "assertions": [{"doc_id": "doc1"}, {"doc_id": "doc2"}],
            "sign": [], "rung": [], "mechanism": [], "sources": [], "first_order": 1,
        },
        ("beta", "canonical"): {
            "assertions": [{"doc_id": "doc1"}],
            "sign": [], "rung": [], "mechanism": [], "sources": [], "first_order": 2,
        },
    }
    resolved, records = resolve_conflicts(
        graph, evidence, vote_mode="canonical_document",
        member_of={"alpha": "canonical", "canonical": "canonical", "beta": "beta"},
    )
    assert resolved.has_edge("canonical", "beta")
    assert not resolved.has_edge("beta", "canonical")
    assert records and records[0]["vote_mode"] == "canonical_document"
def test_embedded_lang7_uses_configured_canonical_document_vote(tmp_path, monkeypatch):
    from pathlib import Path
    from d2c13_2 import lang7_pipeline

    captured = {}
    def fake_main(argv):
        captured["argv"] = list(argv)
        return 0
    monkeypatch.setattr(lang7_pipeline.lang7_flow, "main", fake_main)
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "input"
    source.mkdir()
    lang7_pipeline.run_lang7(
        source,
        tmp_path / "lang7",
        root / "D2C_13_2" / "config.json",
        use_content_cache=False,
    )
    argv = captured["argv"]
    assert argv[argv.index("--vote-mode") + 1] == "canonical_document"

def test_canonical_replay_handles_self_loop_without_support_tier():
    artifact = _artifact()
    artifact["edges"].append(_edge("self", "a", "a", "candidate", ["doc1"], []))
    result = apply_canonical_arbitration(artifact, {"enabled": True})
    edge = next(e for e in artifact["edges"] if e["id"] == "self")
    assert edge["canonical_support_tier"] == "none"
    assert result["replay"]["edge_support_tiers"]["none"] >= 1
