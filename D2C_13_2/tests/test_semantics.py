# -*- coding: utf-8 -*-
from d2c13_2.semantics import apply_verdicts, classify_edge_kinds, discover_span_chain_candidates, materialize_views, repair_chain_edges, repair_core_graph
from d2c13_2.validator import validate_artifact


def _base_artifact():
    return {
        "version": "D2C_13_2",
        "artifact_id": "urn:d2c:artifact:test",
        "entities": [
            {"id": "urn:d2c:entity:a", "label": "A", "description": "", "entity_type": "", "values": [], "aliases": []},
            {"id": "urn:d2c:entity:b", "label": "B", "description": "", "entity_type": "", "values": [], "aliases": []},
            {"id": "urn:d2c:entity:c", "label": "C", "description": "", "entity_type": "", "values": [], "aliases": []},
        ],
        "spans": [{"id": "urn:d2c:span:s", "doc_id": "doc1", "document_id": "urn:d2c:document:doc1", "start": 0, "end": 20, "text_hash": "", "text": "A causes B and C"}],
        "conditions": [],
        "assertions": [
            {"id": "urn:d2c:assertion:ab", "source": "urn:d2c:entity:a", "target": "urn:d2c:entity:b", "doc_id": "doc1", "span_id": "urn:d2c:span:s", "channel": "pre", "order": 1, "sign": "+", "rung": "2", "mechanism": "A -> B"},
            {"id": "urn:d2c:assertion:bc", "source": "urn:d2c:entity:b", "target": "urn:d2c:entity:c", "doc_id": "doc1", "span_id": "urn:d2c:span:s", "channel": "pre", "order": 2, "sign": "+", "rung": "2", "mechanism": "B -> C"},
            {"id": "urn:d2c:assertion:ac", "source": "urn:d2c:entity:a", "target": "urn:d2c:entity:c", "doc_id": "doc1", "span_id": "urn:d2c:span:s", "channel": "pre", "order": 3, "sign": "+", "rung": "2", "mechanism": "A -> B -> C"},
        ],
        "edges": [
            {"id": "urn:d2c:edge:ab", "source": "urn:d2c:entity:a", "target": "urn:d2c:entity:b", "kind": "direct_core", "support_assertion_ids": ["urn:d2c:assertion:ab"], "span_ids": ["urn:d2c:span:s"], "support_doc_ids": ["doc1"], "condition_id": None, "derivation_edge_ids": [], "semantic_support": "untested", "attributes": {"mechanism": "A -> B", "description": "A causes B"}},
            {"id": "urn:d2c:edge:bc", "source": "urn:d2c:entity:b", "target": "urn:d2c:entity:c", "kind": "direct_core", "support_assertion_ids": ["urn:d2c:assertion:bc"], "span_ids": ["urn:d2c:span:s"], "support_doc_ids": ["doc1"], "condition_id": None, "derivation_edge_ids": [], "semantic_support": "untested", "attributes": {"mechanism": "B -> C", "description": "B causes C"}},
            {"id": "urn:d2c:edge:ac", "source": "urn:d2c:entity:a", "target": "urn:d2c:entity:c", "kind": "direct_core", "support_assertion_ids": ["urn:d2c:assertion:ac"], "span_ids": ["urn:d2c:span:s"], "support_doc_ids": ["doc1"], "condition_id": None, "derivation_edge_ids": [], "semantic_support": "untested", "attributes": {"mechanism": "A -> B -> C", "description": "A leads to C"}},
        ],
        "votes": [], "conflict_decisions": [], "cycle_decisions": [], "hypotheses": [], "views": {}, "diagnostics": {},
    }


def test_chain_compression_classified_as_transitive():
    artifact = _base_artifact()
    stats = classify_edge_kinds(artifact, mode="safe")
    assert stats["candidates"] >= 1
    ac = next(e for e in artifact["edges"] if e["id"] == "urn:d2c:edge:ac")
    assert ac["kind"] == "transitive_derived"
    assert ac["derivation_edge_ids"]


def test_verdict_demotes_unsupported_and_conditionalizes():
    artifact = _base_artifact()
    edge = artifact["edges"][0]
    apply_verdicts(artifact, [{"edge_id": edge["id"], "verdict": "conditional", "condition": "if B active"}])
    assert edge["kind"] == "conditional"
    assert edge["condition_id"]
    apply_verdicts(artifact, [{"edge_id": edge["id"], "verdict": "contradicted"}])
    assert edge["kind"] == "rejected"


def test_span_chain_repair_adds_hypothesis_link():
    artifact = _base_artifact()
    artifact["entities"][0]["aliases"] = ["alpha"]
    artifact["entities"][1]["aliases"] = ["beta"]
    artifact["entities"][2]["aliases"] = ["gamma"]
    artifact["spans"][0]["text"] = "alpha causes beta and gamma"
    # Remove B->C so the span-based repair has a missing adjacent link.
    artifact["edges"] = [e for e in artifact["edges"] if e["id"] != "urn:d2c:edge:bc"]
    artifact["diagnostics"] = {}
    materialize_views(artifact)
    candidates = discover_span_chain_candidates(artifact)
    assert candidates
    stats = repair_chain_edges(artifact)
    assert stats["repaired_direct"] >= 1
    assert any(
        e["source"] == "urn:d2c:entity:b" and e["target"] == "urn:d2c:entity:c" and e["kind"] == "direct_core"
        for e in artifact["edges"]
    )
    assert not any(
        e["source"] == "urn:d2c:entity:c" and e["target"] == "urn:d2c:entity:b"
        for e in artifact["edges"]
    )


def test_core_repair_removes_self_loop_and_cycle():
    artifact = _base_artifact()
    artifact["edges"].append({
        "id": "urn:d2c:edge:self", "source": "urn:d2c:entity:a", "target": "urn:d2c:entity:a",
        "kind": "direct_core", "support_assertion_ids": [], "span_ids": [], "support_doc_ids": [],
        "condition_id": None, "derivation_edge_ids": [], "semantic_support": "untested",
    })
    artifact["edges"].append({
        "id": "urn:d2c:edge:ba", "source": "urn:d2c:entity:b", "target": "urn:d2c:entity:a",
        "kind": "direct_core", "support_assertion_ids": [], "span_ids": [], "support_doc_ids": [],
        "condition_id": None, "derivation_edge_ids": [], "semantic_support": "untested",
    })
    stats = repair_core_graph(artifact)
    assert stats["self_loops_removed"] == 1
    assert stats["cycle_edges_removed"] >= 1
    assert all(not (e["source"] == e["target"] and e["kind"] == "direct_core") for e in artifact["edges"])

