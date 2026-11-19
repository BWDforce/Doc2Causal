# -*- coding: utf-8 -*-
"""Compare independent SPARQL Q1-Q7 answers with the frozen producer reports."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sparql_consumer import QUERY_FILES, QUERY_DIR, query_artifact  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTROLLED = (
    PROJECT_ROOT
    / "nova_exp"
    / "exp20_d2c13_2_aliasguard_clean"
    / "acceptance"
    / "e4"
    / "e4_report.json"
)
DEFAULT_CSB_ROOT = PROJECT_ROOT / "CSB_exp" / "results" / "csb_d2c13_2_aliasguard_latest"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "nova_exp" / "exp_sparql_parity_d2c13_2"


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def _as_int(value: object) -> int:
    if value is None or str(value) == "":
        return 0
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strings(values) -> list[str]:
    return sorted({_clean_text(value) for value in values or [] if _clean_text(value)})


def _documents(values) -> list[dict[str, Any]]:
    rows = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        rows.append({
            "doc_id": _clean_text(value.get("doc_id")),
            "id": _clean_text(value.get("id")),
        })
    return sorted(rows, key=lambda row: (row["doc_id"], row["id"]))


def _evidence(values) -> list[dict[str, Any]]:
    rows = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        rows.append({
            "assertion_id": _clean_text(value.get("assertion_id")),
            "assertion_text": _clean_text(value.get("assertion_text")),
            "channel": _clean_text(value.get("channel")),
            "doc_id": _clean_text(value.get("doc_id")),
            "mechanism": _clean_text(value.get("mechanism")),
            "rung": _clean_text(value.get("rung")),
            "sign": _clean_text(value.get("sign")),
            "span_id": _clean_text(value.get("span_id")),
            "span_text": _clean_text(value.get("span_text")),
            "span_text_missing": _as_bool(value.get("span_text_missing")),
        })
    return sorted(rows, key=lambda row: (row["doc_id"], row["span_id"], row["assertion_id"]))


def _canonical_q1(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "assertion_count": _as_int(payload.get("assertion_count")),
        "assertion_ids": _strings(payload.get("assertion_ids")),
        "span_ids": _strings(payload.get("span_ids")),
        "doc_ids": _strings(payload.get("doc_ids")),
        "evidence": _evidence(payload.get("evidence")),
        "evidence_completeness": _clean_text(payload.get("evidence_completeness")),
        "support_scope": _clean_text(payload.get("support_scope")),
        "verifier_support_scope": _clean_text(payload.get("verifier_support_scope")),
        "condition_grounding": _clean_text(payload.get("condition_grounding")),
        "evidence_contract_complete": _as_bool(payload.get("evidence_contract_complete")),
        "evidence_contract": _as_dict(payload.get("evidence_contract")),
        "evidence_coverage": _as_dict(payload.get("evidence_coverage")),
        "best_evidence_window_id": _clean_text(payload.get("best_evidence_window_id")),
        "best_window_kind": _clean_text(payload.get("best_window_kind")),
        "best_window_source": _clean_text(payload.get("best_window_source")),
        "best_window_width": _as_int(payload.get("best_window_width")),
        "evidence_exact_match": _as_bool(payload.get("evidence_exact_match")),
        "context_window_complete": _as_bool(payload.get("context_window_complete")),
    }


def _canonical_q2(payload: dict[str, Any]) -> dict[str, Any]:
    return {"documents": _documents(payload.get("documents"))}


def _canonical_votes(values) -> list[dict[str, Any]]:
    rows = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        rows.append({
            "vote_id": _clean_text(value.get("vote_id")),
            "direction": _clean_text(value.get("direction")),
            "relative_direction": _clean_text(value.get("relative_direction")),
            "documents": _as_int(value.get("documents")),
            "assertions": _as_int(value.get("assertions")),
            "rule": _clean_text(value.get("rule")),
            "doc_vote_detail": _as_dict(value.get("doc_vote_detail")),
            "support_doc_ids": _strings(value.get("support_doc_ids")),
            "opposition_doc_ids": _strings(value.get("opposition_doc_ids")),
            "uncertain_doc_ids": _strings(value.get("uncertain_doc_ids")),
            "relative_support_doc_ids": _strings(value.get("relative_support_doc_ids")),
            "relative_opposition_doc_ids": _strings(value.get("relative_opposition_doc_ids")),
            "forward_doc_ids": _strings(value.get("forward_doc_ids")),
            "reverse_doc_ids": _strings(value.get("reverse_doc_ids")),
            "support_votes": _as_int(value.get("support_votes")),
            "opposition_votes": _as_int(value.get("opposition_votes")),
            "direction_rule": _clean_text(value.get("direction_rule")),
            "reference_orientation": _clean_text(value.get("reference_orientation")),
            "edge_ids": _strings(value.get("edge_ids")),
            "source_label": _clean_text(value.get("source_label")),
            "target_label": _clean_text(value.get("target_label")),
            "provenance": _clean_text(value.get("provenance")),
        })
    return sorted(rows, key=lambda row: row["vote_id"])


def _canonical_q3(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_direction": _clean_text(payload.get("edge_direction")),
        "votes": _canonical_votes(payload.get("votes")),
    }


def _canonical_decisions(values) -> list[dict[str, Any]]:
    rows = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        rows.append({
            "decision_id": _clean_text(value.get("decision_id")),
            "kind": _clean_text(value.get("kind")),
            "rule": _clean_text(value.get("rule")),
            "decision_value": _clean_text(value.get("decision_value")),
            "selected": _clean_text(value.get("selected")),
            "dropped": _clean_text(value.get("dropped")),
            "edge_ids": _strings(value.get("edge_ids")),
            "edge_direction": _clean_text(value.get("edge_direction")),
        })
    return sorted(rows, key=lambda row: row["decision_id"])


def _canonical_q4(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_direction": _clean_text(payload.get("edge_direction")),
        "decisions": _canonical_decisions(payload.get("decisions")),
    }


def _canonical_q5(payload: dict[str, Any]) -> dict[str, Any]:
    condition = payload.get("condition")
    if isinstance(condition, dict):
        condition = {
            "condition_kind": _clean_text(condition.get("condition_kind")),
            "equivalence_key": _clean_text(condition.get("equivalence_key")),
            "id": _clean_text(condition.get("id")),
            "normalized": _clean_text(condition.get("normalized")),
            "raw": _clean_text(condition.get("raw")),
        }
    else:
        condition = None
    grounding = []
    for row in payload.get("grounding") or []:
        if not isinstance(row, dict):
            continue
        grounding.append({
            "assertion_id": _clean_text(row.get("assertion_id")),
            "doc_id": _clean_text(row.get("doc_id")),
            "match_types": _strings(row.get("match_types")),
            "span_id": _clean_text(row.get("span_id")),
            "span_text": _clean_text(row.get("span_text")),
        })
    grounding.sort(key=lambda row: (row["doc_id"], row["span_id"], row["assertion_id"]))
    return {
        "condition": condition,
        "condition_id": _clean_text(payload.get("condition_id")) or None,
        "condition_text": _clean_text(payload.get("condition_text")) or None,
        "grounding_doc_ids": _strings(payload.get("grounding_doc_ids")),
        "grounding_span_ids": _strings(payload.get("grounding_span_ids")),
        "grounding_status": _clean_text(payload.get("grounding_status")),
        "grounding": grounding,
        "has_condition": _as_bool(payload.get("has_condition")),
    }


def _canonical_q6(payload: dict[str, Any]) -> dict[str, Any]:
    verifier_support_scope = _clean_text(payload.get("verifier_support_scope"))
    if verifier_support_scope == "none":
        verifier_support_scope = ""
    return {
        "best_window_kind": _clean_text(payload.get("best_window_kind")),
        "claim_scope": _clean_text(payload.get("claim_scope")),
        "condition_grounding": _clean_text(payload.get("condition_grounding")),
        "direct_support": _clean_text(payload.get("direct_support")),
        "evidence_completeness": _clean_text(payload.get("evidence_completeness")),
        "evidence_contract": _as_dict(payload.get("evidence_contract")),
        "evidence_contract_complete": _as_bool(payload.get("evidence_contract_complete")),
        "evidence_grade": _clean_text(payload.get("evidence_grade")),
        "fraction_consistency": _clean_text(payload.get("fraction_consistency")),
        "is_hypothesis": _as_bool(payload.get("is_hypothesis")),
        "kind": _clean_text(payload.get("kind")),
        "path_support": _clean_text(payload.get("path_support")),
        "promotion_status": _clean_text(payload.get("promotion_status")),
        "provenance": _clean_text(payload.get("provenance")),
        "semantic_support": _clean_text(payload.get("semantic_support")),
        "support_scope": _clean_text(payload.get("support_scope")),
        "temporal_consistency": _clean_text(payload.get("temporal_consistency")),
        "verification_status": _clean_text(payload.get("verification_status")),
        "verifier_direction": _clean_text(payload.get("verifier_direction")),
        "verifier_recommended_kind": _clean_text(payload.get("verifier_recommended_kind")),
        "verifier_support": _clean_text(payload.get("verifier_support")),
        "verifier_support_scope": verifier_support_scope,
    }


def _canonical_cycle_decisions(values) -> list[dict[str, Any]]:
    rows = []
    for value in values or []:
        if not isinstance(value, dict):
            continue
        rows.append({
            "classification": _clean_text(value.get("classification")),
            "decision": _clean_text(value.get("decision")),
            "decision_id": _clean_text(value.get("decision_id")),
            "edge_direction": _clean_text(value.get("edge_direction")),
            "edge_ids": _strings(value.get("edge_ids")),
            "reason": _clean_text(value.get("reason")),
            "regime_dependent": _as_bool(value.get("regime_dependent")),
            "removed_edge": _clean_text(value.get("removed_edge")) or None,
        })
    return sorted(rows, key=lambda row: row["decision_id"])


def _canonical_q7(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "edge_direction": _clean_text(payload.get("edge_direction")),
        "decisions": _canonical_cycle_decisions(payload.get("decisions")),
    }


CANONICALIZERS = {
    "Q1": _canonical_q1,
    "Q2": _canonical_q2,
    "Q3": _canonical_q3,
    "Q4": _canonical_q4,
    "Q5": _canonical_q5,
    "Q6": _canonical_q6,
    "Q7": _canonical_q7,
}


def _nonempty(query: str, payload: dict[str, Any]) -> bool:
    key = {
        "Q1": "evidence",
        "Q2": "documents",
        "Q3": "votes",
        "Q4": "decisions",
        "Q5": "condition",
        "Q6": "kind",
        "Q7": "decisions",
    }[query]
    return bool(payload.get(key))


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_controlled_cases(report_path: Path) -> list[dict[str, Any]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    cases = []
    for row in report.get("rows", []):
        artifact = _resolve_path(row["artifact"])
        ttl = artifact.with_suffix(".ttl")
        cases.append({
            "source": "controlled",
            "artifact_json": artifact,
            "artifact_ttl": ttl,
            "query": row["query"],
            "edge_id": row["edge_id"],
            "expected_nonempty": bool(row.get("expected_nonempty")),
            "producer": row.get("payload") or row.get("answer") or {},
        })
    return cases


def load_csb_cases(root: Path) -> list[dict[str, Any]]:
    cases = []
    for report_path in sorted(root.glob("*/run_01/query_report.json")):
        ttl = report_path.parent / "artifact.ttl"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for row in report.get("results", []):
            case = row.get("case") or {}
            cases.append({
                "source": "csb",
                "artifact_json": report_path.parent / "artifact.json",
                "artifact_ttl": ttl,
                "query": case.get("query"),
                "edge_id": case.get("edge_id"),
                "expected_nonempty": bool(case.get("expected_nonempty")),
                "producer": row.get("answer") or {},
            })
    return cases


def _first_diff(left: Any, right: Any, path: str = "$") -> str | None:
    if type(left) is not type(right) and not isinstance(left, (int, float)) and not isinstance(right, (int, float)):
        return f"{path}: type {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        if set(left) != set(right):
            return f"{path}: keys {sorted(left)} != {sorted(right)}"
        for key in sorted(left):
            diff = _first_diff(left[key], right[key], f"{path}.{key}")
            if diff:
                return diff
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path}: lengths {len(left)} != {len(right)}"
        for index, (a, b) in enumerate(zip(left, right)):
            diff = _first_diff(a, b, f"{path}[{index}]")
            if diff:
                return diff
        return None
    if left != right:
        return f"{path}: {left!r} != {right!r}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run independent SPARQL Q1-Q7 parity over existing D2C artifacts")
    parser.add_argument("--controlled-report", default=str(DEFAULT_CONTROLLED))
    parser.add_argument("--csb-root", default=str(DEFAULT_CSB_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--max-artifacts", type=int, default=None, help="Debug: limit unique artifacts")
    parser.add_argument("--allow-mismatch", action="store_true", help="Return 0 even when answers mismatch")
    args = parser.parse_args()

    controlled_report = _resolve_path(args.controlled_report)
    csb_root = _resolve_path(args.csb_root)
    output_dir = _resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = load_controlled_cases(controlled_report) + load_csb_cases(csb_root)
    artifacts = sorted({case["artifact_ttl"] for case in cases})
    if args.max_artifacts:
        keep = set(artifacts[: args.max_artifacts])
        cases = [case for case in cases if case["artifact_ttl"] in keep]
        artifacts = sorted(keep)

    started = time.time()
    consumer_by_artifact: dict[Path, dict[str, Any]] = {}
    artifact_rows: list[dict[str, Any]] = []
    consumer_errors = []
    for index, artifact in enumerate(artifacts, start=1):
        artifact_started = time.time()
        print(f"[{index}/{len(artifacts)}] SPARQL {artifact}", flush=True)
        try:
            payload = query_artifact(artifact)
            consumer_by_artifact[artifact] = payload
            artifact_rows.append({
                "artifact": str(artifact),
                "triples": payload.get("triples"),
                "edges": payload.get("edge_count"),
                "elapsed_seconds": round(time.time() - artifact_started, 3),
                "error": None,
            })
        except Exception as exc:  # noqa: BLE001 - parity report must preserve failures
            consumer_errors.append({"artifact": str(artifact), "error": f"{type(exc).__name__}: {exc}"})
            artifact_rows.append({
                "artifact": str(artifact),
                "triples": None,
                "edges": None,
                "elapsed_seconds": round(time.time() - artifact_started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            })
            print(f"  ERROR: {type(exc).__name__}: {exc}", flush=True)

    by_query: dict[str, Counter] = {query: Counter() for query in QUERY_FILES}
    by_source: dict[str, Counter] = defaultdict(Counter)
    failures = []
    for case in cases:
        query = case["query"]
        source = case["source"]
        artifact = case["artifact_ttl"]
        edge_id = case["edge_id"]
        expected_nonempty = case["expected_nonempty"]
        producer = case["producer"]
        consumer_payload = consumer_by_artifact.get(artifact, {}).get("answers", {}).get(edge_id, {}).get(query, {})
        try:
            producer_canonical = CANONICALIZERS[query](producer)
            consumer_canonical = CANONICALIZERS[query](consumer_payload)
            exact_match = producer_canonical == consumer_canonical
            mismatch = _first_diff(producer_canonical, consumer_canonical)
        except Exception as exc:  # noqa: BLE001
            exact_match = False
            producer_canonical = {}
            consumer_canonical = {}
            mismatch = f"canonicalization error: {type(exc).__name__}: {exc}"
        consumer_nonempty = _nonempty(query, consumer_payload)
        nonempty_match = (not expected_nonempty) or consumer_nonempty
        ok = exact_match and nonempty_match
        by_query[query]["cases"] += 1
        by_query[query]["exact_matches"] += int(exact_match)
        by_query[query]["nonempty_matches"] += int(nonempty_match)
        by_query[query]["failures"] += int(not ok)
        by_source[source]["cases"] += 1
        by_source[source]["failures"] += int(not ok)
        if not ok:
            failures.append({
                "source": source,
                "artifact": str(artifact),
                "edge_id": edge_id,
                "query": query,
                "expected_nonempty": expected_nonempty,
                "consumer_nonempty": consumer_nonempty,
                "exact_match": exact_match,
                "mismatch": mismatch,
                "producer": producer_canonical,
                "consumer": consumer_canonical,
            })

    query_contract = {}
    for query, filename in QUERY_FILES.items():
        path = QUERY_DIR / filename
        query_contract[query] = {
            "file": str(path),
            "sha256": _sha256(path),
        }

    elapsed = round(time.time() - started, 3)
    report = {
        "experiment": "independent-sparql-q1-q7-parity",
        "version": "D2C_13_2",
        "contract_version": "D2C_13_2-sparql-q1-q7-v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "inputs": {
            "controlled_report": str(controlled_report),
            "csb_root": str(csb_root),
        },
        "consumer": "rdflib-sparql-independent",
        "consumer_imports_producer": False,
        "artifact_count": len(artifacts),
        "case_count": len(cases),
        "consumer_errors": consumer_errors,
        "summary": {
            "elapsed_seconds": elapsed,
            "exact_matches": sum(counter["exact_matches"] for counter in by_query.values()),
            "nonempty_matches": sum(counter["nonempty_matches"] for counter in by_query.values()),
            "failures": len(failures),
            "pass": not consumer_errors and not failures,
        },
        "by_query": {
            query: {
                "cases": counter["cases"],
                "exact_matches": counter["exact_matches"],
                "nonempty_matches": counter["nonempty_matches"],
                "failures": counter["failures"],
            }
            for query, counter in by_query.items()
        },
        "by_source": {
            source: {"cases": counter["cases"], "failures": counter["failures"]}
            for source, counter in sorted(by_source.items())
        },
        "query_contract": query_contract,
        "artifacts": artifact_rows,
        "failures": failures,
    }

    report_path = output_dir / "sparql_parity_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mismatch_path = output_dir / "sparql_parity_mismatches.csv"
    with mismatch_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "source", "artifact", "edge_id", "query", "expected_nonempty",
            "consumer_nonempty", "exact_match", "mismatch", "producer", "consumer",
        ])
        writer.writeheader()
        for failure in failures:
            writer.writerow({
                **failure,
                "producer": json.dumps(failure["producer"], ensure_ascii=False, sort_keys=True),
                "consumer": json.dumps(failure["consumer"], ensure_ascii=False, sort_keys=True),
            })

    print(json.dumps({
        "report": str(report_path),
        "mismatches": str(mismatch_path),
        "artifacts": len(artifacts),
        "cases": len(cases),
        "failures": len(failures),
        "consumer_errors": len(consumer_errors),
        "pass": not consumer_errors and not failures,
        "by_query": report["by_query"],
        "elapsed_seconds": elapsed,
    }, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["pass"] or args.allow_mismatch else 1


if __name__ == "__main__":
    raise SystemExit(main())
