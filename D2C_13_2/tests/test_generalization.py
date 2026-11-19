# -*- coding: utf-8 -*-
import hashlib
import json

from d2c13_2.compiler import _identity_conflict
from d2c13_2.evaluation import path_metrics
from d2c13_2.semantics import build_verification_requests
from d2c13_2.verifier import verify_artifact


def _artifact():
    return {
        "version": "D2C_13_2",
        "artifact_id": "urn:d2c:artifact:checkpoint-test",
        "entities": [
            {"id": "e:a", "label": "A", "aliases": []},
            {"id": "e:b", "label": "B", "aliases": []},
        ],
        "documents": [{"id": "d:1", "doc_id": "doc1"}],
        "spans": [{"id": "s:1", "doc_id": "doc1", "start": 0, "end": 10, "text": "A causes B"}],
        "assertions": [{
            "id": "a:1", "source": "e:a", "target": "e:b", "doc_id": "doc1", "span_id": "s:1",
        }],
        "conditions": [],
        "edges": [{
            "id": "x:1", "source": "e:a", "target": "e:b",
            "source_name": "A", "target_name": "B", "kind": "direct_core", "edge_kind": "direct_core",
            "support_assertion_ids": ["a:1"], "support_doc_ids": ["doc1"], "span_ids": ["s:1"],
            "candidate_assertion_ids": ["a:1"], "candidate_doc_ids": ["doc1"], "candidate_span_ids": ["s:1"],
            "candidate_derivation_ids": [], "condition_id": None, "derivation_edge_ids": [],
            "claim_scope": "direct", "evidence_grade": "span_grounded",
            "candidate_support_mode": "direct_span", "candidate_support_strength": "strong",
            "candidate_origin": "compiler", "candidate_tier": "high",
            "support_mode": "direct_span", "support_strength": "strong",
            "verification_status": "unverified", "direction_status": "unverified",
            "semantic_support": "untested", "provenance": "document_grounded", "attributes": {},
        }],
        "views": {}, "diagnostics": {},
    }


def test_identity_guard_rejects_state_and_time_conflicts():
    assert _identity_conflict("Printer Active", "Printer Inactive")
    assert _identity_conflict("TrueType Font", "Non-TrueType Font")
    assert _identity_conflict("Nitrogen (12:00)", "Nitrogen (12:15)")
    assert not _identity_conflict("Printer Active", "Printer Active")


def test_path_metrics_compares_reachability():
    result = path_metrics(
        {("A", "B")},
        {("A", "B"), ("B", "C"), ("A", "C")},
        {"A", "B", "C"},
        {"A", "B", "C"},
    )
    assert result["candidate_paths"] == 1
    assert result["gold_paths"] == 3
    assert result["f1"] < 1.0


def test_verifier_checkpoint_resume_skips_completed_batch(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    artifact = _artifact()
    requests_payload = build_verification_requests(artifact)
    fingerprint = hashlib.sha1(json.dumps(requests_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    checkpoint = tmp_path / "verification_checkpoint.json"
    checkpoint.write_text(json.dumps({
        "requests_fingerprint": fingerprint,
        "verdicts": [{
            "edge_id": "x:1", "support": "entailed", "direct_support": "entailed",
            "path_support": "not_applicable", "direction": "forward",
        }],
    }), encoding="utf-8")
    result = verify_artifact(artifact, checkpoint_path=checkpoint)
    assert result["checkpoint_hits"] == 1
    assert result["requests"] == 1
    assert result["pending_requests"] == 0
    assert artifact["edges"][0]["verification_status"] == "accepted"
