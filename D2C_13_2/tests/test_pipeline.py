# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from d2c13_2.compiler import compile_run
from d2c13_2.queries import answer, execute_query_cases, generate_query_cases
from d2c13_2.rdfio import roundtrip
from d2c13_2.semantics import materialize_views
from d2c13_2.validator import validate_artifact

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "graph_exp" / "exp29" / "results" / "win95pts" / "lang7" / "run_01"
DOCS = ROOT / "data" / "Test-text" / "input" / "goldgen_gen5" / "win95pts"


@pytest.mark.skipif(not RUN.exists() or not DOCS.exists(), reason="external experiment data not included in the repository")
def test_compile_actual_run():
    artifact = compile_run(RUN, docs_dir=DOCS, chain_mode="safe")
    assert artifact["version"] == "D2C_13_2"
    assert artifact["source"]["corpus"] == "win95pts"
    assert len(artifact["entities"]) > 0
    assert len(artifact["edges"]) > 0
    assert len(artifact["spans"]) > 0
    assert "direct_core" in artifact["views"]
    assert artifact["diagnostics"]["edge_counts"]


@pytest.mark.skipif(not RUN.exists() or not DOCS.exists(), reason="external experiment data not included in the repository")
def test_validate_actual_run():
    artifact = compile_run(RUN, docs_dir=DOCS, chain_mode="safe")
    report = validate_artifact(artifact, run_shacl=True)
    assert report["valid"], report["errors"]
    assert report["shacl"]["conforms"] is True


@pytest.mark.skipif(not RUN.exists() or not DOCS.exists(), reason="external experiment data not included in the repository")
def test_roundtrip_actual_run(tmp_path):
    artifact = compile_run(RUN, docs_dir=DOCS, chain_mode="safe")
    report = roundtrip(artifact, tmp_path)
    assert report["json_roundtrip"] is True
    assert report["rdf_roundtrip"] is True
    assert report["query_roundtrip"] is True


@pytest.mark.skipif(not RUN.exists() or not DOCS.exists(), reason="external experiment data not included in the repository")
def test_queries_actual_run():
    artifact = compile_run(RUN, docs_dir=DOCS, chain_mode="safe")
    edge = next(e for e in artifact["edges"] if e["kind"] == "direct_core")
    assert answer(artifact, "Q1", edge["id"])["evidence"]
    assert answer(artifact, "Q6", edge["id"])["kind"] in {"direct_core", "conditional", "regime"}
    report = execute_query_cases(artifact, generate_query_cases(artifact, limit=10))
    assert report["total"] > 0
    assert report["failed"] == 0, report



def test_q1_assertion_only_with_empty_span_is_nonempty():
    artifact = {
        "entities": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "spans": [{"id": "s1", "doc_id": "doc1", "start": 0, "end": 0, "text": "", "context": ""}],
        "assertions": [{
            "id": "a1", "source": "a", "target": "b", "doc_id": "doc1",
            "span_id": "s1", "channel": "pre", "mechanism": "A causes B",
        }],
        "edges": [{
            "id": "e1", "source": "a", "target": "b", "source_name": "A", "target_name": "B",
            "kind": "conditional", "claim_scope": "conditional",
            "support_assertion_ids": ["a1"], "span_ids": ["s1"],
            "condition_id": None, "derivation_edge_ids": [],
            "support_scope": "assertion_only", "evidence_completeness": "assertion_only",
        }],
        "views": {}, "diagnostics": {},
    }
    payload = answer(artifact, "Q1", "e1")
    assert payload["assertion_count"] == 1
    assert payload["evidence"][0]["span_text_missing"] is True
    assert payload["evidence"][0]["assertion_text"] == "A causes B"
    report = execute_query_cases(artifact, [{"query": "Q1", "edge_id": "e1", "expected_nonempty": True}])
    assert report["failed"] == 0

def test_q1_context_scope_filters_empty_span_support():
    artifact = {
        "entities": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "spans": [
            {"id": "s1", "doc_id": "doc1", "start": 0, "end": 0, "text": "", "context": ""},
            {"id": "s2", "doc_id": "doc1", "start": 0, "end": 11, "text": "A causes B", "context": ""},
        ],
        "assertions": [
            {"id": "a1", "source": "a", "target": "b", "doc_id": "doc1", "span_id": "s1", "channel": "llm", "mechanism": "A causes B"},
            {"id": "a2", "source": "a", "target": "b", "doc_id": "doc1", "span_id": "s2", "channel": "pre", "mechanism": "A causes B"},
        ],
        "edges": [{
            "id": "e1", "source": "a", "target": "b", "source_name": "A", "target_name": "B",
            "kind": "direct_core", "support_assertion_ids": ["a1", "a2"], "span_ids": ["s2"],
            "support_scope": "context_assisted", "evidence_completeness": "context_assisted",
        }],
        "views": {},
    }
    payload = answer(artifact, "Q1", "e1")
    assert payload["assertion_count"] == 1
    assert payload["evidence"][0]["assertion_id"] == "a2"
    assert payload["diagnostics"]["empty_span_assertion_ids"] == ["a1"]

def test_q1_candidate_only_with_empty_span_has_no_evidence_expectation():
    artifact = {
        "entities": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "spans": [{"id": "s1", "doc_id": "doc1", "start": 0, "end": 0, "text": "", "context": ""}],
        "assertions": [{"id": "a1", "source": "a", "target": "b", "doc_id": "doc1", "span_id": "s1", "channel": "llm", "mechanism": ""}],
        "edges": [{
            "id": "e1", "source": "a", "target": "b", "source_name": "A", "target_name": "B",
            "kind": "candidate", "support_assertion_ids": ["a1"], "span_ids": [],
            "support_scope": "candidate", "evidence_completeness": "candidate_only",
        }],
        "views": {}, "diagnostics": {},
    }
    cases = generate_query_cases(artifact, limit=10)
    q1 = next(case for case in cases if case["query"] == "Q1")
    assert q1["expected_nonempty"] is False
    report = execute_query_cases(artifact, [q1])
    assert report["failed"] == 0

