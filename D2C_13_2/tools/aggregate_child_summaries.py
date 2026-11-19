# -*- coding: utf-8 -*-
"""Aggregate per-run child summaries after a process-per-run experiment."""

from __future__ import annotations

import argparse
from pathlib import Path

from d2c13_2.util import now_iso, read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate process-per-run child summaries")
    parser.add_argument("--exp-root", required=True)
    args = parser.parse_args()
    root = Path(args.exp_root).resolve()
    runs = sorted(root.glob("*/run_*"))
    rows = []
    for run_dir in runs:
        child = read_json(run_dir / "verifier_resume_summary.json", {}) or {}
        child_rows = child.get("runs") or []
        row = dict(child_rows[0]) if child_rows else {}
        required = all((run_dir / name).exists() for name in (
            "artifact.json", "verification_results.json", "validation_report.json", "pipeline_summary.json"
        ))
        row.update({
            "corpus": run_dir.parent.name,
            "run": run_dir.name,
            "state": "complete" if row.get("state") == "complete" and required else "failed",
            "all_required_outputs_present": required,
            "finished_at": row.get("finished_at") or now_iso(),
            "stdout_log": str(run_dir / "runner_logs" / "stdout.log"),
            "stderr_log": str(run_dir / "runner_logs" / "stderr.log"),
        })
        rows.append(row)

    failed = sum(1 for row in rows if row.get("state") != "complete")
    summary = {
        "execution_mode": "process_per_run",
        "run_workers": 18 if len(rows) == 18 else len(rows),
        "runs_needing_resume": len(rows),
        "runs_completed": len(rows) - failed,
        "runs_failed": failed,
        "all_success": failed == 0,
        "runs": rows,
    }
    write_json(root / "resume_verification_summary.json", summary)

    status_path = root / "parallel_run_status.json"
    status = read_json(status_path, {}) or {}
    status.setdefault("execution_mode", "process_per_run")
    status.setdefault("started_at", now_iso())
    status["max_parallel"] = summary["run_workers"]
    status["updated_at"] = now_iso()
    status["runs_completed"] = summary["runs_completed"]
    status["runs_failed"] = summary["runs_failed"]
    status["all_success"] = summary["all_success"]
    if summary["all_success"]:
        status["finished_at"] = now_iso()
    status_runs = status.setdefault("runs", {})
    for row in rows:
        key = f"{row['corpus']}/{row['run']}"
        current = status_runs.setdefault(key, {"corpus": row["corpus"], "run": row["run"]})
        current.update(row)
    write_json(status_path, status)
    print({key: summary[key] for key in ("runs_needing_resume", "runs_completed", "runs_failed", "all_success")})
    return 0 if summary["all_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())