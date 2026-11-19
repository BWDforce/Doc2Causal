# -*- coding: utf-8 -*-
from d2c13_2.semantics import apply_verdicts, materialize_views, normalize_transitive_derivation
from d2c13_2.validator import validate_artifact


def _artifact(kind="direct_core"):
    return {
        "version": "D2C_13_2",
        "artifact_id": "urn:d2c:artifact:test",
        "entities": [
            {"id": "urn:d2c:entity:a", "label": "A", "aliases": []},
            {"id": "urn:d2c:entity:b", "label": "B", "aliases": []},
        ],
        "documents": [],
        "spans": [{"id": "urn:d2c:span:s", "doc_id": "doc1", "start": 0, "end": 20, "text": "A causes B"}],
        "assertions": [{
            "id": "urn:d2c:assertion:ab",
            "source": "urn:d2c:entity:a",
            "target": "urn:d2c:entity:b",
            "doc_id": "doc1",
            "span_id": "urn:d2c:span:s",
        }],
        "conditions": [],
        "edges": [{
            "id": "urn:d2c:edge:ab",
            "source": "urn:d2c:entity:a",
            "target": "urn:d2c:entity:b",
            "kind": kind,
            "support_assertion_ids": ["urn:d2c:assertion:ab"] if kind == "direct_core" else [],
            "support_doc_ids": ["doc1"] if kind == "direct_core" else [],
            "span_ids": ["urn:d2c:span:s"] if kind == "direct_core" else [],
            "candidate_assertion_ids": ["urn:d2c:assertion:ab"],
            "candidate_doc_ids": ["doc1"],
            "candidate_span_ids": ["urn:d2c:span:s"],
            "condition_id": None,
            "derivation_edge_ids": [],
            "semantic_support": "untested",
            "direction_status": "unverified",
            "provenance": "test",
            "attributes": {},
        }],
        "votes": [], "conflict_decisions": [], "cycle_decisions": [], "hypotheses": [],
        "views": {}, "diagnostics": {},
    }


def test_unsupported_demotion_moves_authority_to_candidates():
    artifact = _artifact()
    edge = artifact["edges"][0]
    result = apply_verdicts(artifact, [{
        "edge_id": edge["id"], "verdict": "unsupported", "direction": "ambiguous",
    }])
    assert result["demoted"] == 1
    assert edge["kind"] == "candidate"
    assert edge["support_assertion_ids"] == []
    assert edge["support_doc_ids"] == []
    assert edge["span_ids"] == []
    assert edge["candidate_assertion_ids"] == ["urn:d2c:assertion:ab"]
    assert edge["candidate_doc_ids"] == ["doc1"]
    assert edge["candidate_span_ids"] == ["urn:d2c:span:s"]
    assert validate_artifact(artifact, run_shacl=False)["valid"], validate_artifact(artifact, run_shacl=False)


def test_balanced_keeps_entailed_ambiguous_edge_in_review_view():
    artifact = _artifact()
    edge = artifact["edges"][0]
    apply_verdicts(artifact, [{
        "edge_id": edge["id"], "verdict": "entailed", "direction": "ambiguous",
    }], direction_policy="balanced")
    assert edge["kind"] == "direct_core"
    assert edge["direction_status"] == "ambiguous"
    assert edge["support_assertion_ids"] == ["urn:d2c:assertion:ab"]
    assert artifact["views"]["direction_review"]


def test_strict_demotes_entailed_ambiguous_edge():
    artifact = _artifact()
    edge = artifact["edges"][0]
    apply_verdicts(artifact, [{
        "edge_id": edge["id"], "verdict": "entailed", "direction": "ambiguous",
    }], direction_policy="strict")
    assert edge["kind"] == "candidate"
    assert edge["span_ids"] == []
    assert edge["candidate_span_ids"] == ["urn:d2c:span:s"]


def test_transitive_without_path_is_demoted():
    artifact = _artifact(kind="transitive_derived")
    edge = artifact["edges"][0]
    result = normalize_transitive_derivation(artifact)
    assert result["demoted"] == 1
    assert edge["kind"] == "candidate"
    assert edge["provenance"] == "transitive_without_path"
    assert edge["candidate_span_ids"] == ["urn:d2c:span:s"]
    assert validate_artifact(artifact, run_shacl=False)["valid"]


def test_composite_views_are_materialized():
    artifact = _artifact()
    materialize_views(artifact)
    assert "grounded" in artifact["views"]
    assert "verified_core" in artifact["views"]
    assert "direction_review" in artifact["views"]

