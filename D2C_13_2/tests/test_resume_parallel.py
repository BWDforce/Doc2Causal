# -*- coding: utf-8 -*-
import importlib.util
import threading
from pathlib import Path
from types import SimpleNamespace

from d2c13_2.util import read_json, write_json


def _load_resume_module():
    path = Path(__file__).resolve().parents[1] / "tools" / "resume_missing_verdicts.py"
    spec = importlib.util.spec_from_file_location("resume_missing_verdicts_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_workers_execute_all_plans_concurrently(tmp_path, monkeypatch):
    module = _load_resume_module()
    barrier = threading.Barrier(3)

    def fake_enhance(lang7_dir, out_dir, **kwargs):
        barrier.wait(timeout=5)
        out_dir = Path(out_dir)
        write_json(out_dir / "verification_results.json", [])
        write_json(out_dir / "validation_report.json", {"valid": True})
        write_json(out_dir / "pipeline_summary.json", {"ok": True})
        return {
            "verification": {"requests": 1, "verdicts": 1, "missing_verdict_ids": []},
            "validation_valid": True,
            "roundtrip_rdf": True,
            "roundtrip_queries": True,
        }

    monkeypatch.setattr(module, "enhance_lang7_run", fake_enhance)
    plans = []
    for index in range(1, 4):
        run_dir = tmp_path / "water" / f"run_{index:02d}"
        (run_dir / "lang7").mkdir(parents=True)
        (run_dir / "artifact.pre_verification.json").write_text("{}", encoding="utf-8")
        plans.append({"corpus": "water", "run_dir": run_dir, "missing": ["edge"]})

    args = SimpleNamespace(
        config="unused.json",
        verification_policy="balanced",
        run_workers=3,
        status_json=str(tmp_path / "parallel_run_status.json"),
    )
    status_lock = threading.Lock()
    status = {
        "started_at": "test",
        "updated_at": "test",
        "run_workers": 3,
        "verification_policy": "balanced",
        "runs": {
            module._run_key(plan): {
                "corpus": plan["corpus"],
                "run": plan["run_dir"].name,
                "state": "queued",
                "missing_at_start": 1,
            }
            for plan in plans
        },
    }

    results = module._execute_plans(plans, args, tmp_path, status, status_lock)

    assert [row["state"] for row in results] == ["complete", "complete", "complete"]
    saved = read_json(tmp_path / "parallel_run_status.json", {})
    assert {row["state"] for row in saved["runs"].values()} == {"complete"}


def test_one_failed_run_does_not_block_others(tmp_path, monkeypatch):
    module = _load_resume_module()

    def fake_enhance(lang7_dir, out_dir, **kwargs):
        out_dir = Path(out_dir)
        if out_dir.name == "run_02":
            raise RuntimeError("simulated verifier failure")
        write_json(out_dir / "verification_results.json", [])
        write_json(out_dir / "validation_report.json", {"valid": True})
        write_json(out_dir / "pipeline_summary.json", {"ok": True})
        return {
            "verification": {"requests": 1, "verdicts": 1, "missing_verdict_ids": []},
            "validation_valid": True,
        }

    monkeypatch.setattr(module, "enhance_lang7_run", fake_enhance)
    plans = []
    for index in range(1, 4):
        run_dir = tmp_path / "water" / f"run_{index:02d}"
        (run_dir / "lang7").mkdir(parents=True)
        (run_dir / "artifact.pre_verification.json").write_text("{}", encoding="utf-8")
        plans.append({"corpus": "water", "run_dir": run_dir, "missing": ["edge"]})
    args = SimpleNamespace(
        config="unused.json",
        verification_policy="balanced",
        run_workers=3,
        status_json=str(tmp_path / "parallel_run_status.json"),
    )
    status_lock = threading.Lock()
    status = {
        "started_at": "test",
        "updated_at": "test",
        "run_workers": 3,
        "verification_policy": "balanced",
        "runs": {
            module._run_key(plan): {
                "corpus": plan["corpus"],
                "run": plan["run_dir"].name,
                "state": "queued",
                "missing_at_start": 1,
            }
            for plan in plans
        },
    }

    results = module._execute_plans(plans, args, tmp_path, status, status_lock)

    assert [row["state"] for row in results] == ["complete", "failed", "complete"]