# -*- coding: utf-8 -*-
"""Revalidate an experiment sequentially after thread-unsafe RDF/SHACL stages."""

from __future__ import annotations

import argparse
from pathlib import Path

from d2c13_2.util import now_iso, read_json, write_json
from d2c13_2.validator import validate_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Sequentially revalidate D2C artifacts and refresh summaries")
    parser.add_argument("--exp-root", required=True)
    args = parser.parse_args()
    root = Path(args.exp_root).resolve()
    run_dirs = sorted(root.glob("*/run_*"))
    if not run_dirs:
        raise SystemExit(f"No run directories found under {root}")

    summary_path = root / "resume_verification_summary.json"
    status_path = root / "parallel_run_status.json"
    verification_summary = read_json(summary_path, {}) or {}
    run_status = read_json(status_path, {}) or {}
    verification_by_key = {
        f"{row.get('corpus')}/{row.get('run')}": row
        for row in verification_summary.get("runs", [])
        if isinstance(row, dict)
    }
    status_by_key = run_status.get("runs", {}) if isinstance(run_status.get("runs"), dict) else {}
    rows = []

    for run_dir in run_dirs:
        corpus = run_dir.parent.name
        run = run_dir.name
        key = f"{corpus}/{run}"
        artifact_path = run_dir / "artifact.json"
        artifact = read_json(artifact_path, {}) or {}
        if not artifact:
            rows.append({"corpus": corpus, "run": run, "valid": False, "error": "artifact.json missing or unreadable"})
            continue
        report = validate_artifact(artifact, run_shacl=True)
        write_json(run_dir / "validation_report.json", report)

        pipeline_path = run_dir / "pipeline_summary.json"
        pipeline = read_json(pipeline_path, {}) or {}
        if pipeline:
            pipeline["validation_valid"] = bool(report.get("valid"))
            pipeline["shacl_conforms"] = (report.get("shacl") or {}).get("conforms")
            pipeline["validation_error_count"] = len(report.get("errors", []))
            pipeline["validation_warning_count"] = len(report.get("warnings", []))
            pipeline["validation_rechecked_at"] = now_iso()
            write_json(pipeline_path, pipeline)

        state = "complete" if report.get("valid") else "failed"
        if key in verification_by_key:
            verification_by_key[key]["state"] = state
            verification_by_key[key]["validation_valid"] = bool(report.get("valid"))
            verification_by_key[key]["rechecked_at"] = now_iso()
        if key in status_by_key:
            status_by_key[key]["state"] = state
            status_by_key[key]["validation_valid"] = bool(report.get("valid"))
            status_by_key[key]["rechecked_at"] = now_iso()
            status_by_key[key]["missing_at_end"] = len((verification_by_key.get(key) or {}).get("missing_verdict_ids", []))

        rows.append({
            "corpus": corpus,
            "run": run,
            "valid": bool(report.get("valid")),
            "shacl_conforms": (report.get("shacl") or {}).get("conforms"),
            "errors": [row.get("code") for row in report.get("errors", [])],
            "warnings": len(report.get("warnings", [])),
        })
        print(
            f"[revalidate] {key} valid={rows[-1]['valid']} shacl={rows[-1]['shacl_conforms']} "
            f"errors={rows[-1]['errors']}",
            flush=True,
        )

    if verification_summary:
        verification_summary["runs"] = list(verification_by_key.values())
        verification_summary["runs_completed"] = sum(
            1 for row in verification_summary["runs"] if row.get("state") == "complete"
        )
        verification_summary["runs_failed"] = sum(
            1 for row in verification_summary["runs"] if row.get("state") != "complete"
        )
        verification_summary["all_success"] = verification_summary["runs_failed"] == 0
        verification_summary["sequential_validation_recheck_at"] = now_iso()
        write_json(summary_path, verification_summary)

    if run_status:
        run_status["runs"] = status_by_key
        run_status["updated_at"] = now_iso()
        run_status["sequential_validation_recheck_at"] = now_iso()
        write_json(status_path, run_status)

    write_json(root / "sequential_validation_recheck.json", {"generated_at": now_iso(), "rows": rows})
    failed = [row for row in rows if not row.get("valid")]
    print({"runs": len(rows), "valid": len(rows) - len(failed), "failed": len(failed)})
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())