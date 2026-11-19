# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
"""CMD-friendly parallel experiment launcher for D2C_13_2 (lang7-derived)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_config
from .preflight import check_api_access
from .util import write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Task:
    corpus: str
    run_id: str
    out_dir: Path
    log_dir: Path
    command: list[str]
    process: subprocess.Popen | None = None
    stdout_handle: object | None = None
    stderr_handle: object | None = None
    started_at: float = 0.0
    started_epoch: float = 0.0
    last_stage: str = ""
    last_message: str = ""
    last_emit: float = 0.0
    progress_bar: object | None = None
    progress_value: float = 0.0
    quiet: bool = False


def _apply_runtime_defaults(args: argparse.Namespace) -> None:
    """Apply progress settings stored in the selected JSON config."""
    config = load_config(args.config)
    runtime = config.get("runtime", {}) if isinstance(config, dict) else {}
    if not isinstance(runtime, dict):
        runtime = {}
    if getattr(args, "progress", None) is None:
        args.progress = bool(runtime.get("progress_enabled", False))
    if getattr(args, "progress_interval", None) is None:
        args.progress_interval = runtime.get(
            "progress_interval_seconds", runtime.get("progress_interval", 10.0)
        )
    if getattr(args, "progress_heartbeat", None) is None:
        args.progress_heartbeat = runtime.get(
            "progress_heartbeat_seconds", runtime.get("progress_heartbeat", 30.0)
        )
    args.progress_interval = max(0.5, float(args.progress_interval))
    args.progress_heartbeat = max(0.5, float(args.progress_heartbeat))
    # Make runtime concurrency settings authoritative for child processes too.
    os.environ["D2C11_API_MAX_CONCURRENCY"] = str(max(0, int(runtime.get("api_max_concurrency", 0))))
    os.environ["D2C11_VERIFIER_WORKERS"] = str(max(1, int(runtime.get("verifier_parallel_workers", 4))))
    entity = config.get("entity_first", {}) if isinstance(config, dict) else {}
    if isinstance(entity, dict) and "relation_parallel_workers" in entity:
        os.environ["D2C11_RELATION_WORKERS"] = str(max(1, int(entity["relation_parallel_workers"])))


def _out_root(args: argparse.Namespace) -> Path:
    path = Path(args.out_root)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _requested_run_ids(args: argparse.Namespace) -> list[str]:
    raw = str(getattr(args, "run_ids", "") or "").strip()
    if not raw:
        return [f"run_{index:02d}" for index in range(1, args.runs + 1)]
    result = []
    for token in raw.split(","):
        value = token.strip()
        if not value:
            continue
        suffix = value[4:] if value.startswith("run_") else value
        if not suffix.isdigit():
            raise ValueError(f"invalid run id: {value}")
        result.append(f"run_{int(suffix):02d}")
    return list(dict.fromkeys(result)) or [f"run_{index:02d}" for index in range(1, args.runs + 1)]

def _make_tasks(args: argparse.Namespace) -> list[Task]:
    corpora = ["water", "win95pts"] if args.corpus == "both" else [args.corpus]
    out_root = _out_root(args)
    if out_root.exists() and not out_root.is_dir():
        print(f"[D2C_13_2] output root exists but is not a directory: {out_root}", flush=True)
        return 2
    out_root.mkdir(parents=True, exist_ok=True)
    tasks = []
    run_ids = _requested_run_ids(args)
    for corpus in corpora:
        input_dir = PROJECT_ROOT / "data" / "Test-text" / "input" / "goldgen_gen5" / corpus
        for run_id in run_ids:
            out_dir = out_root / corpus / run_id
            log_dir = out_dir / "runner_logs"
            if args.pipeline == "direct":
                command = [
                    sys.executable, "-u", "-m", "d2c13_2", "run",
                    "--input-dir", str(input_dir),
                    "--out-dir", str(out_dir),
                    "--config", args.config,
                    "--temperature", str(args.temperature),
                    "--max-chars", str(args.max_chars),
                    "--chain-mode", args.chain_mode,
                ]
            else:
                command = [
                    sys.executable, "-u", "-m", "d2c13_2", "lang7-run",
                    "--input-dir", str(input_dir),
                    "--out-dir", str(out_dir),
                    "--config", args.config,
                    "--temperature", str(args.temperature),
                    "--chain-mode", args.chain_mode,
                    "--cache-namespace", run_id,
                ]
                if args.theme:
                    command.extend(["--theme", args.theme])
                if args.verdicts:
                    command.extend(["--verdicts", args.verdicts])
                if args.no_verify:
                    command.append("--no-verify")
                else:
                    command.extend(["--verification-policy", args.verification_policy])
            if args.pipeline != "direct" and getattr(args, "no_content_cache", False):
                command.append("--no-content-cache")
            if args.dry_run:
                command.append("--dry-run")
            tasks.append(Task(corpus, run_id, out_dir, log_dir, command, quiet=bool(getattr(args, "progress", False))))
    return tasks


def _start(task: Task) -> None:
    task.log_dir.mkdir(parents=True, exist_ok=True)
    task.stdout_handle = (task.log_dir / "stdout.log").open("w", encoding="utf-8")
    task.stderr_handle = (task.log_dir / "stderr.log").open("w", encoding="utf-8")
    task.started_at = time.monotonic()
    task.started_epoch = time.time()
    if not task.quiet:
        print(f"[D2C_13_2] start corpus={task.corpus} run={task.run_id}", flush=True)
    task.process = subprocess.Popen(
        task.command,
        cwd=str(PROJECT_ROOT),
        stdout=task.stdout_handle,
        stderr=task.stderr_handle,
        text=True,
    )


def _tail_lines(path: Path, max_bytes: int = 400000, limit: int = 4000) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read().decode("utf-8", errors="replace")
        lines = [line.strip() for line in data.splitlines() if line.strip()]
        return lines[-limit:]
    except OSError:
        return []


def _tail_line(path: Path, max_bytes: int = 400000) -> str:
    lines = _tail_lines(path, max_bytes=max_bytes, limit=1)
    return lines[-1] if lines else ""


def _elapsed(task: Task) -> float:
    return max(0.0, time.monotonic() - task.started_at) if task.started_at else 0.0


def _fmt_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _marker_fields(message: str) -> dict[str, str]:
    if not message.startswith("[D2C11-progress]"):
        return {}
    return {key: value for key, value in re.findall(r"([A-Za-z_]+)=([^\s]+)", message)}


def _parse_fraction(value: str, default: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    try:
        left, right = value.split("/", 1)
        return int(left), max(1, int(right))
    except (AttributeError, ValueError):
        return default


def _progress_detail(stage: str, message: str) -> str:
    fields = _marker_fields(message)
    doc_current, doc_total = _parse_fraction(fields.get("doc", ""))
    pass_current, pass_total = _parse_fraction(fields.get("pass_index", ""))
    if stage in {"extract_pass", "extract_relation_matrix"} and doc_total:
        return f"D{doc_current}/{doc_total} P{pass_current}/{pass_total}"
    if doc_total:
        return f"D{doc_current}/{doc_total}"
    if stage == "verifying":
        match = re.search(r"batch\s+(\d+)/(\d+)", message, re.IGNORECASE)
        if match:
            return f"B{match.group(1)}/{match.group(2)}"
    return ""


def _fresh_stage_file(task: Task, path: Path, tolerance: float = 2.0) -> bool:
    if not path.exists():
        return False
    if not task.started_epoch:
        return True
    try:
        return path.stat().st_mtime >= task.started_epoch - tolerance
    except OSError:
        return False


def _progress_stage(task: Task) -> tuple[str, str]:
    out = task.out_dir
    if  _fresh_stage_file(task, out / "pipeline_summary.json") or _fresh_stage_file(task, out / "visualization.html") :
        stage = "finalizing"
    elif _fresh_stage_file(task, out / "validation_report.json"):
        stage = "validation"
    elif _fresh_stage_file(task, out / "verification_results.json"):
        stage = "post_verification"
    elif _fresh_stage_file(task, out / "verification_checkpoint.json"):
        stage = "verifying"
    elif _fresh_stage_file(task, out / "verification_requests.input.json"):
        stage = "verifier_prepare"
    elif _fresh_stage_file(task, out / "artifact.pre_verification.json"):
        stage = "evidence_packaging"
    elif _fresh_stage_file(task, out / "artifact.json"):
        stage = "artifact_written"
    elif _fresh_stage_file(task, out / "lang7" / "evidence_union_report.json") or _fresh_stage_file(task, out / "lang7" / "evidence.json"):
        stage = "lang7_merge"
    elif any(_fresh_stage_file(task, path) for path in (out / "lang7").glob("*.gml")):
        stage = "compiling"
    elif _fresh_stage_file(task, out / "lang7" / "_checkpoints") or _fresh_stage_file(task, out / "lang7"):
        stage = "lang7_extract"
    else:
        stage = "starting"

    stdout_lines = _tail_lines(task.log_dir / "stdout.log")
    stderr_lines = _tail_lines(task.log_dir / "stderr.log")
    stdout_line = stdout_lines[-1] if stdout_lines else ""
    stderr_line = stderr_lines[-1] if stderr_lines else ""
    marker_line = next((line for line in reversed(stdout_lines) if line.startswith("[D2C11-progress]")), "")
    message = marker_line or stderr_line or stdout_line or "(no log yet)"
    marker = _marker_fields(marker_line)

    if stderr_line and "Traceback" in stderr_line:
        stage = "error"
    elif stage in {"starting", "lang7_extract", "lang7_merge"} and marker.get("stage"):
        stage = marker["stage"]
    elif message.startswith("[verifier]"):
        stage = "verifying"
    elif message.startswith("Document ") and "/" in message:
        stage = "extract_document"
    elif message.startswith("[LangExtract-union]"):
        stage = "extract_pass"
    elif message.startswith("[LangExtract]"):
        stage = "extract_pass"
    elif message.startswith("[compact agent]"):
        stage = "extract_agent"
    elif message.startswith("[merge"):
        stage = "merge_llm"
    elif message.startswith("[evidence-union]"):
        stage = "lang7_evidence_union"
    return stage, message[:300]


STAGE_LABELS = {
    "queued": "WAIT",
    "starting": "START",
    "lang7_extract": "EXTRACT",
    "extract_document": "EXTRACT_DOC",
    "extract_pass": "EXTRACT_PASS",
    "extract_inventory": "EXTRACT_INV",
    "extract_relation_matrix": "REL_MATRIX",
    "extract_agent": "EXTRACT_AGENT",
    "extract_complete": "EXTRACT_DONE",
    "lang7_merge": "MERGE",
    "merge_llm": "MERGE_LLM",
    "lang7_evidence_union": "EVIDENCE",
    "compiling": "COMPILE",
    "evidence_packaging": "PACK",
    "verifier_prepare": "PREP_VERIFY",
    "verifying": "VERIFY",
    "post_verification": "POST",
    "validation": "VALIDATE",
    "finalizing": "WRITE",
    "done": "DONE",
    "skipped": "SKIP",
    "error": "FAIL",
}
STAGE_PERCENT = {
    "queued": 0.0,
    "starting": 2.0,
    "lang7_extract": 6.0,
    "extract_document": 6.0,
    "extract_pass": 6.0,
    "extract_inventory": 6.0,
    "extract_relation_matrix": 6.0,
    "extract_agent": 6.0,
    "extract_complete": 43.0,
    "lang7_merge": 45.0,
    "merge_llm": 45.0,
    "lang7_evidence_union": 54.0,
    "compiling": 60.0,
    "evidence_packaging": 68.0,
    "verifier_prepare": 74.0,
    "verifying": 80.0,
    "post_verification": 94.0,
    "validation": 97.0,
    "finalizing": 99.0,
    "done": 100.0,
    "skipped": 100.0,
    "error": 100.0,
}


def _progress_percent(stage: str, message: str) -> float:
    value = STAGE_PERCENT.get(stage, 5.0)
    fields = _marker_fields(message)
    doc_current, doc_total = _parse_fraction(fields.get("doc", ""))
    pass_current, pass_total = _parse_fraction(fields.get("pass_index", ""))
    if stage in {"extract_document", "extract_pass", "extract_agent", "extract_inventory", "extract_relation_matrix"} and doc_total:
        doc_index = min(max(doc_current, 1), doc_total)
        phase = fields.get("phase", "")
        if stage in {"extract_pass", "extract_relation_matrix"} and pass_total:
            pass_fraction = (min(max(pass_current, 1), pass_total) - 1) / pass_total
            if phase in {"done", "checkpoint", "items", "degraded"}:
                pass_fraction = min(1.0, pass_fraction + 1.0 / pass_total)
            completed = (doc_index - 1) + pass_fraction
        elif stage == "extract_agent":
            completed = (doc_index - 1) + (0.9 if phase == "done" else 0.35)
        else:
            completed = doc_index if phase == "done" else doc_index - 1
        value = 6.0 + 36.0 * min(1.0, completed / doc_total)
    elif stage == "verifying":
        match = re.search(r"batch\s+(\d+)/(\d+)", message, re.IGNORECASE)
        if match:
            done, total = int(match.group(1)), max(1, int(match.group(2)))
            value = 80.0 + 13.0 * min(1.0, done / total)
    return max(0.0, min(100.0, value))

def _create_progress_bars(tasks: list[Task]) -> None:
    if tqdm is None:
        return
    for index, task in enumerate(tasks):
        task.progress_bar = tqdm(
            total=100,
            desc=f"{task.corpus}/{task.run_id}",
            position=index,
            leave=True,
            dynamic_ncols=True,
            bar_format="{l_bar}{bar:20}| {n:3.0f}% [{elapsed}] {postfix}",
        )
        task.progress_bar.set_postfix_str(STAGE_LABELS["queued"], refresh=True)


def _emit_progress(task: Task, heartbeat: float = 30.0, force: bool = False) -> None:
    stage, message = _progress_stage(task)
    now = time.monotonic()
    value = _progress_percent(stage, message)
    label = STAGE_LABELS.get(stage, stage)
    detail = _progress_detail(stage, message)
    postfix = f"{label} {detail} {_fmt_elapsed(_elapsed(task))}".replace("  ", " ").strip()
    if task.progress_bar is not None:
        if not force and stage == task.last_stage and message == task.last_message and now - task.last_emit < heartbeat:
            return
        task.progress_value = max(task.progress_value, value)
        task.progress_bar.n = task.progress_value
        task.progress_bar.set_postfix_str(postfix, refresh=False)
        task.progress_bar.refresh()
    elif not force and stage == task.last_stage and message == task.last_message and now - task.last_emit < heartbeat:
        return
    elif task.progress_bar is None:
        print(f"[progress] {task.corpus}/{task.run_id:<7} {postfix} {value:3.0f}%", flush=True)
    task.last_stage = stage
    task.last_message = message
    task.last_emit = now
def _mark_task_bar(task: Task, stage: str, code: int | None = None) -> None:
    if task.progress_bar is None:
        return
    task.progress_value = 100.0
    label = STAGE_LABELS.get(stage, stage)
    if code is not None and code != 0:
        label = f"FAIL(exit={code})"
    task.progress_bar.n = 100
    task.progress_bar.set_postfix_str(f"{label} {_fmt_elapsed(_elapsed(task))}", refresh=True)


def _finish(task: Task, code: int) -> dict:
    if task.stdout_handle:
        task.stdout_handle.close()
    if task.stderr_handle:
        task.stderr_handle.close()
    _mark_task_bar(task, "done" if code == 0 else "error", code)
    if not task.quiet:
        print(f"[D2C_13_2] completed corpus={task.corpus} run={task.run_id} exit={code} elapsed={_fmt_elapsed(_elapsed(task))}", flush=True)
    return {
        "corpus": task.corpus,
        "run": task.run_id,
        "exit_code": code,
        "out_dir": str(task.out_dir),
        "stdout": str(task.log_dir / "stdout.log"),
        "stderr": str(task.log_dir / "stderr.log"),
        "elapsed_seconds": round(_elapsed(task), 2),
    }


def _wait(task: Task) -> dict:
    assert task.process is not None
    code = task.process.wait()
    if task.stdout_handle:
        task.stdout_handle.close()
    if task.stderr_handle:
        task.stderr_handle.close()
    print(f"[D2C_13_2] completed corpus={task.corpus} run={task.run_id} exit={code}", flush=True)
    return {
        "corpus": task.corpus,
        "run": task.run_id,
        "exit_code": code,
        "out_dir": str(task.out_dir),
        "stdout": str(task.log_dir / "stdout.log"),
        "stderr": str(task.log_dir / "stderr.log"),
    }


def _summary(args: argparse.Namespace, tasks: list[Task], results: list[dict], preflight: dict | None, aborted: bool = False) -> dict:
    return {
        "version": "D2C_13_2",
        "config": args.config,
        "pipeline": args.pipeline,
        "corpus": args.corpus,
        "runs": len(_requested_run_ids(args)),
        "run_ids": _requested_run_ids(args),
        "max_parallel": args.max_parallel,
        "verification_enabled": not bool(getattr(args, "no_verify", False)),
        "verification_policy": getattr(args, "verification_policy", "balanced"),
        "dry_run": bool(args.dry_run),
        "aborted": aborted,
        "planned": len(tasks),
        "total": len(results),
        "failed": sum(1 for r in results if r["exit_code"] != 0),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "preflight": preflight,
        "results": results,
    }


def run(args: argparse.Namespace) -> int:
    _apply_runtime_defaults(args)
    tasks = _make_tasks(args)
    out_root = _out_root(args)
    summary_path = out_root / Path(getattr(args, "summary_name", "experiment_summary.json") or "experiment_summary.json").name
    preflight = None
    if not args.dry_run and not getattr(args, "skip_preflight", False):
        preflight = check_api_access(
            args.config,
            out_root,
            timeout=int(getattr(args, "preflight_timeout", 90)),
        )
        if not preflight.get("ok"):
            summary = _summary(args, tasks, [], preflight, aborted=True)
            write_json(summary_path, summary)
            print("[D2C_13_2] API preflight failed; no runs started.", flush=True)
            print(json.dumps(preflight, ensure_ascii=False, indent=2), flush=True)
            return 1

    results: list[dict] = []
    if not args.progress:
        pending: list[Task] = []
        for task in tasks:
            if args.resume and not args.force_rerun and (task.out_dir / "pipeline_summary.json").exists():
                try:
                    prior = json.loads((task.out_dir / "pipeline_summary.json").read_text(encoding="utf-8"))
                except Exception:
                    prior = {}
                if prior.get("validation_valid") and (task.out_dir / "artifact.json").exists():
                    print(f"[D2C_13_2] resume skip corpus={task.corpus} run={task.run_id}", flush=True)
                    results.append({
                        "corpus": task.corpus, "run": task.run_id, "exit_code": 0,
                        "skipped": True, "out_dir": str(task.out_dir),
                        "stdout": str(task.log_dir / "stdout.log"),
                        "stderr": str(task.log_dir / "stderr.log"),
                    })
                    continue
            _start(task)
            pending.append(task)
            if len(pending) >= args.max_parallel:
                results.append(_wait(pending.pop(0)))
        while pending:
            results.append(_wait(pending.pop(0)))
    else:
        _create_progress_bars(tasks)
        queue = list(tasks)
        running: list[Task] = []

        def launch_available() -> None:
            nonlocal queue
            while queue and len(running) < args.max_parallel:
                task = queue.pop(0)
                if args.resume and not args.force_rerun and (task.out_dir / "pipeline_summary.json").exists():
                    try:
                        prior = json.loads((task.out_dir / "pipeline_summary.json").read_text(encoding="utf-8"))
                    except Exception:
                        prior = {}
                    if prior.get("validation_valid") and (task.out_dir / "artifact.json").exists():
                        _mark_task_bar(task, "skipped", 0)
                        results.append({
                            "corpus": task.corpus, "run": task.run_id, "exit_code": 0,
                            "skipped": True, "out_dir": str(task.out_dir),
                            "stdout": str(task.log_dir / "stdout.log"),
                            "stderr": str(task.log_dir / "stderr.log"),
                        })
                        continue
                _start(task)
                running.append(task)

        launch_available()
        while running or queue:
            for task in list(running):
                if task.process is not None and task.process.poll() is not None:
                    running.remove(task)
                    results.append(_finish(task, task.process.returncode))
            launch_available()
            for task in running:
                _emit_progress(task, heartbeat=args.progress_heartbeat)
            if running or queue:
                time.sleep(max(0.5, args.progress_interval))
        for task in tasks:
            if task.progress_bar is not None:
                task.progress_bar.close()
    summary = _summary(args, tasks, results, preflight, aborted=False)
    write_json(summary_path, summary)
    if not getattr(args, "quiet_summary", False):
        print(json.dumps({k: summary[k] for k in ("planned", "total", "failed", "max_parallel", "dry_run", "aborted")}, indent=2), flush=True)
    return 0 if summary["failed"] == 0 and not summary["aborted"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run D2C_13_2 main-comparison experiments")
    p.add_argument("--pipeline", choices=["lang7", "direct"], default="lang7", help="lang7 is the D2C_13_2 default; direct is a legacy ablation")
    p.add_argument("--corpus", choices=["water", "win95pts", "both"], default="both")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--run-ids", default="", help="Comma-separated run ids to execute, for example run_05")
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    p.add_argument("--out-root", default="D2C_13_2/results/main_comparison")
    p.add_argument("--max-parallel", type=int, default=18)
    p.add_argument("--max-chars", type=int, default=6000)
    p.add_argument("--theme", default=None)
    p.add_argument("--verdicts", default=None)
    p.add_argument("--resume", action="store_true", help="Skip completed valid runs and reuse evidence checkpoints; verifier retries are configured in config.json")
    p.add_argument("--force-rerun", action="store_true", help="With --resume, reprocess completed runs instead of skipping them")
    p.add_argument("--no-verify", action="store_true", help="Disable LLM semantic/direction verification")
    p.add_argument("--verification-policy", choices=["advisory", "balanced", "strict"], default="balanced", help="How verifier advice is applied")
    p.add_argument("--chain-mode", choices=["safe", "aggressive", "off"], default="safe")
    p.add_argument("--preflight-timeout", type=int, default=90, help="Seconds for the initial API/model access check")
    p.add_argument("--skip-preflight", action="store_true", help="Skip the API/model access check (offline tests only)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-content-cache", action="store_true", help="Force fresh Lang7 extraction for the selected run(s)")
    p.add_argument("--summary-name", default="experiment_summary.json", help="Filename for the experiment summary JSON")
    p.add_argument("--quiet-summary", action="store_true", help="Do not print the final summary JSON to stdout")
    p.add_argument("--progress", action="store_true", default=None, help="Show one live stage progress bar per run")
    p.add_argument("--progress-interval", type=float, default=None, help="Seconds between progress polling cycles; defaults to config.runtime.progress_interval_seconds")
    p.add_argument("--progress-heartbeat", type=float, default=None, help="Seconds before an unchanged run is printed again; defaults to config.runtime.progress_heartbeat_seconds")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))

