# -*- coding: utf-8 -*-
"""Refresh query reports from cached D2C_13_2 artifacts without API calls."""

from __future__ import annotations

import argparse
from pathlib import Path

from d2c13_2.queries import execute_query_cases, generate_query_cases
from d2c13_2.util import read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Q1-Q7 query reports from cached artifacts")
    parser.add_argument("--exp-root", required=True)
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    root = Path(args.exp_root).resolve()
    artifacts = sorted(root.glob("*/run_*/artifact.json"))
    if not artifacts:
        raise SystemExit(f"no artifact.json found under {root}")
    rows = []
    for artifact_path in artifacts:
        artifact = read_json(artifact_path, {}) or {}
        cases = generate_query_cases(artifact, limit=args.limit)
        report = execute_query_cases(artifact, cases)
        run_dir = artifact_path.parent
        write_json(run_dir / "query_report.json", report)
        summary_path = run_dir / "pipeline_summary.json"
        if summary_path.exists():
            summary = read_json(summary_path, {}) or {}
            summary["query_total"] = report["total"]
            summary["query_failed"] = report["failed"]
            write_json(summary_path, summary)
        rows.append({
            "run_dir": str(run_dir),
            "total": report["total"],
            "failed": report["failed"],
        })
        print(f"{run_dir}: total={report['total']} failed={report['failed']}")
    write_json(root / "query_refresh_summary.json", {"runs": len(rows), "total": sum(r["total"] for r in rows), "failed": sum(r["failed"] for r in rows), "results": rows})
    return 0 if all(row["failed"] == 0 for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())