# -*- coding: utf-8 -*-
"""Run verifier-only experiment runs in independent child processes."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from d2c13_2.util import now_iso, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_HELPER = ROOT / "D2C_13_2" / "tools" / "resume_missing_verdicts.py"
DEFAULT_REPLAY = ROOT / "D2C_13_2" / "tools" / "replay_report.py"


def _run_key(run_dir: Path, root: Path) -> str:
    return f"{run_dir.parent.name}/{run_dir.name}"


def _child_summary(run_dir: Path) -> dict:
    return read_json(run_dir / "verifier_resume_summary.json", {}) or {}


def _checkpoint_progress(run_dir: Path) -> dict:
    checkpoint = read_json(run_dir / "verification_checkpoint.json", {}) or {}
    telemetry = checkpoint.get("telemetry") or {}
    return {
        "checkpoint_verdicts": len(checkpoint.get("verdicts") or []),
        "checkpoint_api_calls": telemetry.get("api_calls"),
        "checkpoint_latency_seconds": telemetry.get("latency_seconds"),
        "checkpoint_max_latency_seconds": telemetry.get("max_latency_seconds"),
    }


def _write_status(path: Path, payload: dict) -> None:
    payload["updated_at"] = now_iso()
    temp = path.with_suffix(path.suffix + ".tmp")
    write_json(temp, payload)
    os.replace(temp, path)


def _discover_runs(root: Path) -> list[Path]:
    return [
        run_dir
        for run_dir in sorted(root.glob("*/run_*"))
        if (run_dir / "artifact.pre_verification.json").exists() and (run_dir / "lang7").exists()
    ]


def _command(args: argparse.Namespace, root: Path, run_dir: Path) -> list[str]:
    relative_run = run_dir.relative_to(root).as_posix()
    return [
        sys.executable,
        str(Path(args.helper).resolve()),
        "--exp-root",
        str(root),
        "--config",
        str(Path(args.config).resolve()),
        "--verification-policy",
        args.verification_policy,
        "--run-workers",
        "1",
        "--run-path",
        relative_run,
        "--summary-json",
        str(run_dir / "verifier_resume_summary.json"),
        "--status-json",
        str(run_dir / "verifier_status.json"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run each cached verifier run in its own child process")
    parser.add_argument("--exp-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--verification-policy", choices=["advisory", "balanced", "strict"], default="balanced")
    parser.add_argument("--max-parallel", type=int, default=18)
    parser.add_argument("--source-exp-root", default=None)
    parser.add_argument("--comparison-out", default=None)
    parser.add_argument("--helper", default=str(DEFAULT_HELPER))
    parser.add_argument("--replay-report", default=str(DEFAULT_REPLAY))
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.exp_root).resolve()
    runs = _discover_runs(root)
    if not runs:
        raise SystemExit(f"No run inputs found under {root}")
    max_parallel = max(1, int(args.max_parallel))
    commands = {run_dir: _command(args, root, run_dir) for run_dir in runs}

    if args.dry_run:
        for run_dir, command in commands.items():
            print(f"[dry-run] {_run_key(run_dir, root)}: {' '.join(command)}")
        print({"runs": len(runs), "max_parallel": max_parallel, "mode": "process_per_run"})
        return 0

    status_path = root / "parallel_run_status.json"
    status = {
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "execution_mode": "process_per_run",
        "max_parallel": max_parallel,
        "runs": {
            _run_key(run_dir, root): {
                "corpus": run_dir.parent.name,
                "run": run_dir.name,
                "state": "queued",
                "stdout_log": str(run_dir / "runner_logs" / "stdout.log"),
                "stderr_log": str(run_dir / "runner_logs" / "stderr.log"),
            }
            for run_dir in runs
        },
    }
    _write_status(status_path, status)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    pending = list(runs)
    running: dict[Path, dict] = {}
    rows_by_run: dict[Path, dict] = {}

    def launch(run_dir: Path) -> None:
        key = _run_key(run_dir, root)
        log_dir = run_dir / "runner_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_handle = (log_dir / "stdout.log").open("w", encoding="utf-8", buffering=1)
        stderr_handle = (log_dir / "stderr.log").open("w", encoding="utf-8", buffering=1)
        process = subprocess.Popen(
            commands[run_dir],
            cwd=str(ROOT),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
        )
        running[run_dir] = {
            "process": process,
            "stdout": stdout_handle,
            "stderr": stderr_handle,
        }
        status["runs"][key].update({
            "state": "running",
            "pid": process.pid,
            "started_at": now_iso(),
            **_checkpoint_progress(run_dir),
        })
        print(f"[exp-runner] start {key} pid={process.pid}", flush=True)

    try:
        while pending or running:
            while pending and len(running) < max_parallel:
                launch(pending.pop(0))

            for run_dir in list(running):
                item = running[run_dir]
                process = item["process"]
                returncode = process.poll()
                key = _run_key(run_dir, root)
                if returncode is None:
                    status["runs"][key].update(_checkpoint_progress(run_dir))
                    continue

                item["stdout"].close()
                item["stderr"].close()
                child_summary = _child_summary(run_dir)
                child_rows = child_summary.get("runs") or []
                child_row = child_rows[0] if child_rows else {}
                complete = bool(returncode == 0 and child_row.get("state") == "complete")
                row = {
                    "corpus": run_dir.parent.name,
                    "run": run_dir.name,
                    "state": "complete" if complete else "failed",
                    "returncode": returncode,
                    "pid": process.pid,
                    "previous_missing": child_row.get("previous_missing", []),
                    "requests": child_row.get("requests"),
                    "verdicts": child_row.get("verdicts"),
                    "verdict_coverage": child_row.get("verdict_coverage"),
                    "missing_verdict_ids": child_row.get("missing_verdict_ids", []),
                    "validation_valid": child_row.get("validation_valid"),
                    "roundtrip_rdf": child_row.get("roundtrip_rdf"),
                    "roundtrip_queries": child_row.get("roundtrip_queries"),
                    "all_required_outputs_present": child_row.get("all_required_outputs_present"),
                    "finished_at": now_iso(),
                    "stdout_log": str(run_dir / "runner_logs" / "stdout.log"),
                    "stderr_log": str(run_dir / "runner_logs" / "stderr.log"),
                }
                rows_by_run[run_dir] = row
                status["runs"][key].update({
                    **row,
                    "state": row["state"],
                    **_checkpoint_progress(run_dir),
                })
                print(
                    f"[exp-runner] completed {key} state={row['state']} returncode={returncode} "
                    f"verdicts={row.get('verdicts')}/{row.get('requests')}",
                    flush=True,
                )
                del running[run_dir]

            _write_status(status_path, status)
            if running or pending:
                time.sleep(max(0.2, float(args.poll_seconds)))
    except BaseException:
        for item in running.values():
            try:
                item["process"].terminate()
            except Exception:
                pass
            item["stdout"].close()
            item["stderr"].close()
        raise

    ordered_rows = [rows_by_run[run_dir] for run_dir in runs]
    failed = sum(1 for row in ordered_rows if row.get("state") != "complete")
    summary = {
        "execution_mode": "process_per_run",
        "run_workers": max_parallel,
        "runs_needing_resume": len(runs),
        "runs_completed": len(runs) - failed,
        "runs_failed": failed,
        "all_success": failed == 0,
        "runs": ordered_rows,
    }
    write_json(root / "resume_verification_summary.json", summary)
    status["finished_at"] = now_iso()
    status["runs_completed"] = len(runs) - failed
    status["runs_failed"] = failed
    status["all_success"] = failed == 0
    _write_status(status_path, status)

    if failed:
        return 1

    if args.source_exp_root and args.comparison_out:
        command = [
            sys.executable,
            str(Path(args.replay_report).resolve()),
            "--replay-root",
            str(root),
            "--source-exp-root",
            str(Path(args.source_exp_root).resolve()),
            "--out",
            str(Path(args.comparison_out).resolve()),
        ]
        proc = subprocess.run(command, cwd=str(ROOT), env=env, check=False)
        if proc.returncode != 0:
            print(f"[exp-runner] replay report failed with returncode {proc.returncode}", file=sys.stderr)
            return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())