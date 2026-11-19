# -*- coding: utf-8 -*-
import json
from d2c13_2.evaluation import edge_metrics
from d2c13_2.evaluation import artifact_views


def test_edge_metrics_exact_direction_and_f1():
    gold = {("A", "B"), ("B", "C")}
    candidate = {("A", "B"), ("C", "B")}
    result = edge_metrics(candidate, gold, {"A", "B", "C"}, {"A", "B", "C"})
    assert result["tp"] == 1
    assert result["fp"] == 1
    assert result["fn"] == 1
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["f1"] == 0.5


def test_artifact_views_selects_retained_and_strict(tmp_path):
    artifact = {
        "version": "D2C_13_2",
        "entities": [
            {"id": "e:a", "label": "A"},
            {"id": "e:b", "label": "B"},
            {"id": "e:c", "label": "C"},
        ],
        "edges": [
            {"id": "x:ab", "source": "e:a", "target": "e:b", "source_name": "A", "target_name": "B", "kind": "direct_core"},
            {"id": "x:bc", "source": "e:b", "target": "e:c", "source_name": "B", "target_name": "C", "kind": "conditional"},
            {"id": "x:ac", "source": "e:a", "target": "e:c", "source_name": "A", "target_name": "C", "kind": "transitive_derived"},
            {"id": "x:ca", "source": "e:c", "target": "e:a", "source_name": "C", "target_name": "A", "kind": "hypothesis"},
        ],
    }
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    views = artifact_views(path, ["A", "B", "C"], {"a": "A", "b": "B", "c": "C"})
    assert views["strict"] == {("A", "B")}
    assert views["grounded"] == {("A", "B"), ("B", "C")}
    assert views["retained"] == {("A", "B"), ("B", "C"), ("A", "C")}
    assert views["hypothesis"] == {("C", "A")}


def test_artifact_views_separates_span_and_evidence_core(tmp_path):
    artifact = {
        "version": "D2C_13_2",
        "entities": [
            {"id": "e:a", "label": "A"},
            {"id": "e:b", "label": "B"},
            {"id": "e:c", "label": "C"},
        ],
        "edges": [
            {"id": "x:ab", "source": "e:a", "target": "e:b", "source_name": "A", "target_name": "B",
             "kind": "direct_core", "evidence_grade": "span_grounded",
             "evidence_contract_complete": True,
             "evidence_contract": {"text_complete": True}},
            {"id": "x:bc", "source": "e:b", "target": "e:c", "source_name": "B", "target_name": "C",
             "kind": "candidate", "support_mode": "weak_candidate", "evidence_grade": "assertion_grounded",
             "candidate_tier": "medium", "verification_status": "unverified"},
            {"id": "x:ac", "source": "e:a", "target": "e:c", "source_name": "A", "target_name": "C",
             "kind": "candidate", "support_mode": "candidate", "evidence_grade": "span_grounded",
             "candidate_tier": "high", "verification_status": "needs_review"},
        ],
    }
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    views = artifact_views(path, ["A", "B", "C"], {"a": "A", "b": "B", "c": "C"})
    assert views["span_core"] == {("A", "B")}
    assert views["evidence_core"] == {("A", "B"), ("B", "C")}
    assert views["candidate_high"] == {("A", "C")}
