import os
import time
from pathlib import Path

from d2c13_2.experiment import Task, _make_tasks, _progress_detail, _progress_percent, _progress_stage, build_parser


def _task(tmp_path: Path, marker: str) -> Task:
    out = tmp_path / "run"
    logs = out / "runner_logs"
    (out / "lang7" / "_checkpoints").mkdir(parents=True)
    logs.mkdir(parents=True)
    (logs / "stdout.log").write_text("prompt text\n" * 600 + marker + "\n", encoding="utf-8")
    return Task("water", "run_01", out, logs, [])


def test_extract_pass_marker_drives_stage_and_percent(tmp_path):
    task = _task(tmp_path, "[D2C11-progress] stage=extract_pass phase=start doc=2/5 pass_index=2/2")
    stage, message = _progress_stage(task)
    assert stage == "extract_pass"
    assert _progress_detail(stage, message) == "D2/5 P2/2"
    assert _progress_percent(stage, message) > _progress_percent("extract_pass", "[D2C11-progress] stage=extract_pass phase=start doc=1/5 pass_index=1/2")


def test_extract_complete_and_merge_markers_are_monotonic(tmp_path):
    task = _task(tmp_path, "[D2C11-progress] stage=extract_complete phase=done doc=5/5")
    stage, message = _progress_stage(task)
    assert stage == "extract_complete"
    assert _progress_percent(stage, message) == 43.0
    (task.out_dir / "lang7" / "evidence.json").write_text("{}", encoding="utf-8")
    (task.log_dir / "stdout.log").write_text("[D2C11-progress] stage=merge_llm phase=start\n", encoding="utf-8")
    stage, message = _progress_stage(task)
    assert stage == "merge_llm"
    assert _progress_percent(stage, message) >= 45.0


def test_verifier_batch_detail(tmp_path):
    task = _task(tmp_path, "[verifier] batch 7/10 running")
    stage, message = _progress_stage(task)
    assert stage == "verifying"
    assert _progress_detail("verifying", message) == "B7/10"

def test_stale_rerun_artifacts_do_not_force_write_or_compile(tmp_path):
    out = tmp_path / "run"
    logs = out / "runner_logs"
    (out / "lang7").mkdir(parents=True)
    logs.mkdir(parents=True)
    marker = "[D2C11-progress] stage=merge_llm phase=start"
    (logs / "stdout.log").write_text(marker + "\n", encoding="utf-8")
    task = Task("water", "run_01", out, logs, [])
    task.started_epoch = time.time()
    old = task.started_epoch - 60
    for name, body in (("pipeline_summary.json", "{}"), ("visualization.html", "old")):
        path = out / name
        path.write_text(body, encoding="utf-8")
        os.utime(path, (old, old))
    old_gml = out / "lang7" / "old.gml"
    old_gml.write_text("graph", encoding="utf-8")
    os.utime(old_gml, (old, old))
    os.utime(out / "lang7", (old, old))
    stage, _ = _progress_stage(task)
    assert stage == "merge_llm"


def test_fresh_gml_marks_compiling(tmp_path):
    out = tmp_path / "run"
    logs = out / "runner_logs"
    (out / "lang7").mkdir(parents=True)
    logs.mkdir(parents=True)
    task = Task("water", "run_01", out, logs, [])
    task.started_epoch = time.time()
    (out / "lang7" / "new.gml").write_text("graph", encoding="utf-8")
    stage, _ = _progress_stage(task)
    assert stage == "compiling"


def test_single_run_selection_keeps_child_logs_inside_run(tmp_path):
    args = build_parser().parse_args([
        "--out-root", str(tmp_path / "exp"), "--corpus", "water", "--run-ids", "run_05",
        "--max-parallel", "1", "--no-content-cache", "--progress",
        "--summary-name", "experiment_summary_water_run05.json",
    ])
    tasks = _make_tasks(args)
    assert len(tasks) == 1
    assert tasks[0].run_id == "run_05"
    assert tasks[0].out_dir == tmp_path / "exp" / "water" / "run_05"
    assert tasks[0].log_dir == tasks[0].out_dir / "runner_logs"
    assert "--no-content-cache" in tasks[0].command
    assert args.summary_name == "experiment_summary_water_run05.json"