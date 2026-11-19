# -*- coding: utf-8 -*-
from pathlib import Path

from d2c13_2.audit import artifact_audit
from d2c13_2.compiler import _identity_conflict
from d2c13_2.policy import promotion_gate
from d2c13_2.rdfio import artifact_from_rdf, semantic_snapshot, write_jsonld, write_turtle
from d2c13_2.semantics import (
    apply_verdicts,
    materialize_views,
    recover_assertion_edges,
    repair_condition_edges,
    textual_direction_guard,
)


def _edge(source: str, target: str, text: str, kind: str = "direct_core"):
    return {
        "version": "D2C_13_2",
        "artifact_id": "urn:d2c:artifact:p0p1",
        "entities": [
            {"id": "e:a", "label": "Smoking", "aliases": [], "description": "", "entity_type": "event", "values": [], "context_signature": "", "time_indices": [], "state_signature": {"negation": False, "state_terms": []}, "identity_key": "a"},
            {"id": "e:b", "label": "Cancer", "aliases": [], "description": "", "entity_type": "event", "values": [], "context_signature": "", "time_indices": [], "state_signature": {"negation": False, "state_terms": []}, "identity_key": "b"},
        ],
        "documents": [{"id": "d:1", "doc_id": "doc1"}],
        "spans": [{"id": "s:1", "doc_id": "doc1", "start": 0, "end": len(text), "text": text}],
        "assertions": [{"id": "a:1", "source": "e:a", "target": "e:b", "doc_id": "doc1", "span_id": "s:1"}],
        "conditions": [],
        "edges": [{
            "id": "x:1", "source": source, "target": target,
            "source_name": "Smoking" if source == "e:a" else "Cancer",
            "target_name": "Cancer" if target == "e:b" else "Smoking",
            "kind": kind, "edge_kind": kind,
            "support_assertion_ids": ["a:1"], "support_doc_ids": ["doc1"], "span_ids": ["s:1"],
            "candidate_assertion_ids": ["a:1"], "candidate_doc_ids": ["doc1"], "candidate_span_ids": ["s:1"],
            "candidate_derivation_ids": [], "condition_id": None, "derivation_edge_ids": [],
            "claim_scope": "conditional" if kind == "conditional" else "direct",
            "evidence_grade": "span_grounded", "candidate_support_mode": "direct_span",
            "candidate_support_strength": "strong", "candidate_origin": "compiler",
            "candidate_tier": "high", "support_mode": "direct_span", "support_strength": "strong",
            "verification_status": "unverified", "direction_status": "unverified",
            "semantic_support": "untested", "direction_audit": "", "path_summary": False,
            "path_summary_explicit_direct": False, "attributes": {},
        }],
        "views": {}, "diagnostics": {},
    }


def test_textual_direction_guard_flips_active_and_passive_reverse_edges():
    for text in ("Smoking causes cancer.", "Cancer is caused by smoking."):
        artifact = _edge("e:b", "e:a", text)
        textual_direction_guard(artifact)
        edge = artifact["edges"][0]
        assert (edge["source"], edge["target"]) == ("e:a", "e:b"), text
        assert edge["direction_status"] == "corrected"
        assert edge["direction_audit"] == "textual_cue"


def test_textual_direction_guard_quarantines_negated_claim():
    artifact = _edge("e:a", "e:b", "Smoking does not cause cancer.")
    textual_direction_guard(artifact)
    edge = artifact["edges"][0]
    assert edge["kind"] == "candidate"
    assert edge["polarity_status"] == "negated"
    assert edge["verification_status"] == "needs_review"


def test_support_aware_gate_rejects_provisional_and_path_only_edges():
    candidate = _edge("e:a", "e:b", "Smoking causes cancer.")["edges"][0]
    assert promotion_gate(candidate)["eligible"] is True
    candidate["provisional_recovery"] = True
    assert promotion_gate(candidate)["eligible"] is False
    candidate["verification_status"] = "accepted"
    candidate["verifier_support"] = "entailed"
    assert promotion_gate(candidate)["eligible"] is True
    candidate["provisional_recovery"] = False
    candidate["path_summary"] = True
    candidate["path_summary_explicit_direct"] = False
    assert promotion_gate(candidate)["eligible"] is False


def test_assertion_recovery_is_visible_but_not_soft_core_until_verified():
    artifact = _edge("e:a", "e:b", "Smoking causes cancer.")
    artifact["edges"] = []
    result = recover_assertion_edges(artifact)
    assert result["added"] == 1
    assert artifact["edges"][0]["assertion_recovery"] is True
    assert artifact["edges"][0]["id"] in {row["edge_id"] for row in artifact["views"]["assertion_recovery"]}
    assert artifact["edges"][0]["id"] not in {row["edge_id"] for row in artifact["views"]["soft_core"]}
    apply_verdicts(artifact, [{"edge_id": artifact["edges"][0]["id"], "support": "entailed", "direction": "forward"}])
    assert artifact["edges"][0]["kind"] == "direct_core"


def test_conditionless_modal_edge_is_quarantined():
    artifact = _edge("e:a", "e:b", "Smoking causes cancer.", kind="conditional")
    result = repair_condition_edges(artifact)
    assert result["demoted"] == 1
    assert artifact["edges"][0]["kind"] == "candidate"
    assert artifact["edges"][0]["condition_status"] == "missing"


def test_entity_identity_roundtrip_and_audit(tmp_path: Path):
    artifact = _edge("e:a", "e:b", "Smoking causes cancer.")
    materialize_views(artifact)
    jsonld = tmp_path / "artifact.jsonld"
    turtle = tmp_path / "artifact.ttl"
    write_jsonld(artifact, jsonld)
    write_turtle(artifact, turtle)
    recovered = artifact_from_rdf(turtle)
    assert semantic_snapshot(artifact)["entity_identity"] == semantic_snapshot(recovered)["entity_identity"]
    audit = artifact_audit(artifact)
    assert audit["entity_identity"]["identity_key_coverage"] == 1.0
    assert audit["evidence_attribution"]["assertion_coverage"] == 1.0


def test_verifier_telemetry_records_usage_and_latency(monkeypatch):
    from d2c13_2 import verifier as verifier_module

    class FakeResponse:
        status_code = 200
        content = b"telemetry"

        def json(self):
            return {
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
                "choices": [{"message": {"content": '{"verdicts": []}'}}],
            }

    monkeypatch.setattr(verifier_module.requests, "post", lambda *args, **kwargs: FakeResponse())
    verifier = verifier_module.LLMEdgeVerifier(api_key="test", base_url="https://example.invalid/v1", model="fake")
    assert verifier._call([{"edge_id": "x"}]) == []
    assert verifier.telemetry["api_calls"] == 1
    assert verifier.telemetry["prompt_tokens"] == 11
    assert verifier.telemetry["completion_tokens"] == 7
    assert verifier.telemetry["total_tokens"] == 18
    assert verifier.telemetry["response_bytes"] > 0

