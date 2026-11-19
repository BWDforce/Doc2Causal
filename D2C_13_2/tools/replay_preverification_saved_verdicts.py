# -*- coding: utf-8 -*-
"""Replay a pre-verification artifact with saved verdicts under D2C_13_2."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from d2c13_2.audit import artifact_audit
from d2c13_2.canonical_arbitration import apply_canonical_arbitration
from d2c13_2.canonical_audit import build_canonical_audit
from d2c13_2.canonical_votes import project_canonical_votes
from d2c13_2.config import load_config
from d2c13_2.constants import VERSION
from d2c13_2.evidence_completeness import package_evidence, restore_pre_verification_evidence
from d2c13_2.lang7_pipeline import direction_guard
from d2c13_2.queries import execute_query_cases, generate_query_cases
from d2c13_2.rdfio import roundtrip, write_jsonld, write_turtle
from d2c13_2.semantics import (
    apply_verdicts,
    classify_edge_directness,
    materialize_views,
    normalize_transitive_derivation,
    repair_core_graph,
    repair_identity_edges,
    shortcut_guard,
)
from d2c13_2.util import read_json, write_json
from d2c13_2.validator import validate_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay saved verdicts into a D2C_13_2 artifact")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config.deepseek-official.json"))
    parser.add_argument("--evidence", default=None)
    parser.add_argument("--roundtrip", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact = read_json(run_dir / "artifact.pre_verification.json", {}) or {}
    verdicts = read_json(run_dir / "verification_results.json", []) or []
    evidence_path = Path(args.evidence).resolve() if args.evidence else run_dir / "lang7" / "evidence.json"
    artifact["version"] = VERSION
    artifact.setdefault("diagnostics", {})["verification_source"] = "saved_verdicts"
    applied = apply_verdicts(artifact, verdicts, direction_policy="balanced")
    normalize_transitive_derivation(artifact)
    repair_core_graph(artifact)
    direction_guard(artifact)
    repair_identity_edges(artifact)
    classify_edge_directness(artifact)
    shortcut_guard(artifact)
    package_evidence(artifact)
    restore_pre_verification_evidence(artifact)
    config = load_config(args.config)
    apply_canonical_arbitration(artifact, config.get("canonical_arbitration") or {})
    materialize_views(artifact)
    write_json(out_dir / "artifact.json", artifact)
    audit_report = artifact_audit(artifact)
    write_json(out_dir / "evidence_audit.json", audit_report)
    audit_summary = build_canonical_audit(
        out_dir / "artifact.json",
        evidence_path if evidence_path.exists() else None,
        out_dir / "audit",
    )
    canonical_votes_path = out_dir / "audit" / "canonical_votes.json"
    if canonical_votes_path.exists():
        payload = read_json(canonical_votes_path, {}) or {}
        before_hash = str(audit_summary.get("artifact_sha256_after") or "")
        projection = project_canonical_votes(artifact, payload)
        write_json(out_dir / "artifact.json", artifact)
        after_hash = hashlib.sha256((out_dir / "artifact.json").read_bytes()).hexdigest()
        payload["projection"] = {
            **projection,
            "artifact_sha256_before_projection": before_hash,
            "artifact_sha256_after_projection": after_hash,
        }
        write_json(canonical_votes_path, payload)
        audit_summary["artifact_sha256_before"] = before_hash
        audit_summary["artifact_sha256_after"] = after_hash
        audit_summary["canonical_vote_projection"] = payload["projection"]
        write_json(out_dir / "audit" / "audit_report.json", audit_summary)
    write_jsonld(artifact, out_dir / "artifact.jsonld")
    write_turtle(artifact, out_dir / "artifact.ttl")
    validation = validate_artifact(artifact, run_shacl=True)
    write_json(out_dir / "validation_report.json", validation)
    cases = generate_query_cases(artifact, limit=30)
    query_report = execute_query_cases(artifact, cases)
    write_json(out_dir / "query_report.json", query_report)
    rt = roundtrip(artifact, out_dir / "roundtrip") if args.roundtrip else None
    summary = {
        "version": VERSION,
        "source_run": str(run_dir),
        "applied": applied,
        "canonical_vote_projection": (audit_summary or {}).get("canonical_vote_projection"),
        "validation_valid": validation.get("valid"),
        "shacl_conforms": (validation.get("shacl") or {}).get("conforms"),
        "query_cases": query_report.get("total"),
        "query_failed": query_report.get("failed"),
        "roundtrip": (None if rt is None else {
            key: rt.get(key)
            for key in ("version", "json_roundtrip", "rdf_roundtrip", "edge_semantics_roundtrip", "entity_identity_roundtrip", "query_roundtrip", "query_cases")
        }),
        "roundtrip_failure_count": (0 if rt is None else len(rt.get("query_failures") or [])),
        "edge_counts": artifact.get("diagnostics", {}).get("edge_counts") or {},
        "view_counts": artifact.get("view_counts") or (artifact.get("diagnostics") or {}).get("view_counts") or {},
    }
    write_json(out_dir / "replay_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if validation.get("valid") and query_report.get("failed") == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
