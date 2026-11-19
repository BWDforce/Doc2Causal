# -*- coding: utf-8 -*-
"""Resume only missing verifier verdicts from cached lang7 runs; no extraction API calls."""

from __future__ import annotations

import argparse
import json
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from d2c13_2.config import role_config
from d2c13_2.lang7_pipeline import enhance_lang7_run
from d2c13_2.semantics import build_verification_requests
from d2c13_2.util import now_iso, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]


def _request_ids(pre_artifact: dict) -> set[str]:
    return {
        str(row.get("edge_id"))
        for row in build_verification_requests(pre_artifact)
        if row.get("edge_id")
    }


def _saved_ids(run_dir: Path) -> set[str]:
    ids: set[str] = set()
    for name in ("verification_checkpoint.json", "verification_results.json"):
        path = run_dir / name
        if not path.exists():
            continue
        payload = read_json(path, {}) or {}
        rows = payload.get("verdicts", []) if isinstance(payload, dict) else payload
        ids.update(
            str(row.get("edge_id"))
            for row in rows
            if isinstance(row, dict) and row.get("edge_id")
        )
    return ids


def _run_key(plan: dict[str, Any]) -> str:
    return f"{plan['corpus']}/{plan['run_dir'].name}"


def _write_status(path: Path | None, payload: dict[str, Any], lock: threading.Lock) -> None:
    if path is None:
        return
    with lock:
        payload["updated_at"] = now_iso()
        temp_path = path.with_suffix(path.suffix + ".tmp")
        write_json(temp_path, payload)
        os.replace(temp_path, path)


def _execute_plan(
    plan: dict[str, Any],
    args: argparse.Namespace,
    status_path: Path | None,
    status_payload: dict[str, Any],
    status_lock: threading.Lock,
) -> dict[str, Any]:
    run_dir: Path = plan["run_dir"]
    corpus = plan["corpus"]
    run_name = run_dir.name
    key = _run_key(plan)
    required_names = (
        "artifact.pre_verification.json",
        "verification_results.json",
        "validation_report.json",
        "pipeline_summary.json",
    )

    status_payload["runs"][key].update(
        {"state": "running", "started_at": now_iso(), "missing_at_start": len(plan["missing"])}
    )
    _write_status(status_path, status_payload, status_lock)
    print(f"[D2C_13_2] start corpus={corpus} run={run_name} parallel=true", flush=True)

    try:
        docs_dir = ROOT / "data" / "Test-text" / "input" / "goldgen_gen5" / corpus
        summary = enhance_lang7_run(
            run_dir / "lang7",
            run_dir,
            docs_dir=docs_dir,
            corpus=corpus,
            config_path=args.config,
            verify=True,
            verification_policy=args.verification_policy,
        )
        verification = summary.get("verification") or {}
        missing_verdict_ids = verification.get("missing_verdict_ids", [])
        output_complete = all((run_dir / name).exists() for name in required_names)
        successful = bool(
            not missing_verdict_ids
            and summary.get("validation_valid")
            and output_complete
        )
        result = {
            "corpus": corpus,
            "run": run_name,
            "state": "complete" if successful else "incomplete",
            "previous_missing": plan["missing"],
            "requests": verification.get("requests"),
            "verdicts": verification.get("verdicts"),
            "verdict_coverage": verification.get("verdict_coverage"),
            "missing_verdict_ids": missing_verdict_ids,
            "validation_valid": summary.get("validation_valid"),
            "roundtrip_rdf": summary.get("roundtrip_rdf"),
            "roundtrip_queries": summary.get("roundtrip_queries"),
            "all_required_outputs_present": output_complete,
            "finished_at": now_iso(),
        }
        status_payload["runs"][key].update(
            {
                "state": result["state"],
                "finished_at": result["finished_at"],
                "requests": result["requests"],
                "verdicts": result["verdicts"],
                "verdict_coverage": result["verdict_coverage"],
                "missing_at_end": len(missing_verdict_ids),
            }
        )
        print(
            f"[D2C_13_2] completed corpus={corpus} run={run_name} "
            f"state={result['state']} verdicts={result['verdicts']}/{result['requests']}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 - preserve other parallel runs
        traceback.print_exc()
        result = {
            "corpus": corpus,
            "run": run_name,
            "state": "failed",
            "previous_missing": plan["missing"],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "finished_at": now_iso(),
        }
        status_payload["runs"][key].update(
            {
                "state": "failed",
                "finished_at": result["finished_at"],
                "error_type": result["error_type"],
                "error": result["error"],
            }
        )
        print(
            f"[D2C_13_2] failed corpus={corpus} run={run_name} "
            f"error={type(exc).__name__}: {exc}",
            flush=True,
        )

    _write_status(status_path, status_payload, status_lock)
    return result


def _execute_plans(
    plans: list[dict[str, Any]],
    args: argparse.Namespace,
    root: Path,
    status_payload: dict[str, Any],
    status_lock: threading.Lock,
) -> list[dict[str, Any]]:
    workers = max(1, int(args.run_workers))
    status_path = Path(args.status_json).resolve() if args.status_json else root / "parallel_run_status.json"
    _write_status(status_path, status_payload, status_lock)

    if workers == 1:
        return [
            _execute_plan(plan, args, status_path, status_payload, status_lock)
            for plan in plans
        ]

    results_by_key: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(plans)), thread_name_prefix="d2c-run") as pool:
        futures = {
            pool.submit(
                _execute_plan,
                plan,
                args,
                status_path,
                status_payload,
                status_lock,
            ): plan
            for plan in plans
        }
        for future in as_completed(futures):
            plan = futures[future]
            result = future.result()
            results_by_key[_run_key(plan)] = result

    return [results_by_key[_run_key(plan)] for plan in plans]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resume missing D2C_13_2 verifier verdicts without re-extraction"
    )
    parser.add_argument("--exp-root", required=True)
    parser.add_argument("--config", default=str(ROOT / "D2C_13_2" / "config.json"))
    parser.add_argument(
        "--verification-policy",
        choices=["advisory", "balanced", "strict"],
        default="balanced",
    )
    parser.add_argument(
        "--run-workers",
        type=int,
        default=1,
        help="Number of corpus runs executed concurrently; use 18 for exp18.",
    )
    parser.add_argument(
        "--status-json",
        default=None,
        help="Live parallel-run status file (defaults to <exp-root>/parallel_run_status.json).",
    )
    parser.add_argument(
        "--summary-json",
        default=None,
        help="Child summary path (defaults to <exp-root>/resume_verification_summary.json).",
    )
    parser.add_argument(
        "--run-path",
        default=None,
        help="Optional single run directory, absolute or relative to <exp-root>.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.exp_root).resolve()
    summary_path = Path(args.summary_json).resolve() if args.summary_json else root / "resume_verification_summary.json"
    run_filter = None
    if args.run_path:
        candidate = Path(args.run_path)
        run_filter = (candidate if candidate.is_absolute() else root / candidate).resolve()
    verifier_config = role_config("verifier", args.config)
    key_env = verifier_config["api_key_env"]
    if not args.dry_run and not os.environ.get(key_env):
        raise SystemExit(f"{key_env} is not set; refusing to run verifier-only resume")

    plans = []
    for run_dir in sorted(root.glob("*/run_*")):
        if run_filter is not None and run_dir.resolve() != run_filter:
            continue
        pre_path = run_dir / "artifact.pre_verification.json"
        lang7_dir = run_dir / "lang7"
        if not pre_path.exists() or not lang7_dir.exists():
            continue
        corpus = run_dir.parent.name
        pre = read_json(pre_path, {}) or {}
        expected = _request_ids(pre)
        saved = _saved_ids(run_dir)
        missing = sorted(expected - saved)
        if missing:
            plans.append(
                {
                    "corpus": corpus,
                    "run_dir": run_dir,
                    "missing": missing,
                }
            )

    if not plans:
        print("All cached runs already have complete verifier verdict coverage.")
        write_json(
            summary_path,
            {"runs_needing_resume": 0, "runs": []},
        )
        return 0

    workers = max(1, int(args.run_workers))
    print(f"Runs needing verifier resume: {len(plans)}", flush=True)
    print(f"Parallel run workers: {workers}", flush=True)
    for plan in plans:
        print(
            f"  {plan['corpus']}/{plan['run_dir'].name}: missing={len(plan['missing'])}",
            flush=True,
        )

    if args.dry_run:
        write_json(
            summary_path,
            {
                "dry_run": True,
                "run_workers": workers,
                "runs_needing_resume": len(plans),
                "runs": [
                    {
                        "corpus": plan["corpus"],
                        "run": plan["run_dir"].name,
                        "missing": plan["missing"],
                    }
                    for plan in plans
                ],
            },
        )
        return 0

    status_lock = threading.Lock()
    status_payload: dict[str, Any] = {
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "run_workers": workers,
        "verification_policy": args.verification_policy,
        "runs": {
            _run_key(plan): {
                "corpus": plan["corpus"],
                "run": plan["run_dir"].name,
                "state": "queued",
                "missing_at_start": len(plan["missing"]),
            }
            for plan in plans
        },
    }
    results = _execute_plans(plans, args, root, status_payload, status_lock)
    failed = sum(row.get("state") != "complete" for row in results)
    write_json(
        summary_path,
        {
            "run_workers": workers,
            "runs_needing_resume": len(plans),
            "runs_completed": len(results) - failed,
            "runs_failed": failed,
            "all_success": failed == 0,
            "runs": results,
        },
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())