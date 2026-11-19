# -*- coding: utf-8 -*-
"""Score exp14 D2C_13_2 against exp12 D2C_9 with the same evaluator and gold."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

from d2c13_2.evaluation import evaluate_unified_views
from d2c13_2.util import write_json

ROOT = Path(__file__).resolve().parents[2]
METHODS = {"D2C_9": "exp12", "D2C_13_2": "exp14_d2c13_2_clean"}
CORPORA = ("water", "win95pts")
REPORT_VIEWS = (
    "raw", "pre_verification", "pre_soft_core", "soft_core", "soft_core_lenient",
    "soft_core_balanced", "soft_core_precision", "support_aware_core",
    "exact_support_core", "context_assisted_core", "partial_evidence_review",
    "path_support_core", "no_evidence_queue", "direct_core", "verified_core",
    "needs_review", "candidate", "candidate_high", "candidate_medium", "candidate_low",
    "grounded", "soft_core_path", "raw_path",
)


def load_gold(corpus: str):
    p = ROOT / "gold_gen_text" / "output_gen5" / corpus / "seed1" / "manifest.json"
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    aliases = {}
    for doc in data.get("docs", []):
        for item in doc.get("aliases", []) or []:
            if isinstance(item, dict) and item.get("surface") and item.get("canonical"):
                aliases[str(item["surface"]).strip().lower()] = str(item["canonical"])
    edges = {tuple(e) for e in data.get("gold_edges", []) if len(e) == 2}
    return data.get("gold_nodes", []), aliases, edges


def gml_for(run_dir: Path) -> Path | None:
    candidates = sorted((run_dir / "lang7").glob("*.gml"))
    return candidates[-1] if candidates else None


def mean_sd(values):
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    return round(mean, 4), round(statistics.stdev(values), 4) if len(values) > 1 else 0.0


def aggregate(records, method, corpus, view):
    rows = [r for r in records if r["method"] == method and r["corpus"] == corpus and r["view"] == view]
    if not rows:
        return None
    p, psd = mean_sd([float(r["metrics"]["precision"]) for r in rows])
    rec, rsd = mean_sd([float(r["metrics"]["recall"]) for r in rows])
    f1, f1sd = mean_sd([float(r["metrics"]["f1"]) for r in rows])
    tp = sum(int(r["metrics"]["tp"]) for r in rows)
    fp = sum(int(r["metrics"]["fp"]) for r in rows)
    fn = sum(int(r["metrics"]["fn"]) for r in rows)
    mp = tp / (tp + fp) if tp + fp else 0.0
    mr = tp / (tp + fn) if tp + fn else 0.0
    mf1 = 2 * mp * mr / (mp + mr) if mp + mr else 0.0
    return {
        "n": len(rows), "precision_mean": p, "precision_sd": psd,
        "recall_mean": rec, "recall_sd": rsd, "f1_mean": f1, "f1_sd": f1sd,
        "micro_precision": round(mp, 4), "micro_recall": round(mr, 4), "micro_f1": round(mf1, 4),
        "tp": tp, "fp": fp, "fn": fn,
        "candidate_edges_mean": round(statistics.mean([r["metrics"].get("candidate_edges", 0) for r in rows]), 2),
        "reversed_edges": sum(int(r["metrics"].get("reversed_edges", 0)) for r in rows),
        "self_loops": sum(int(r["metrics"].get("self_loops", 0)) for r in rows),
        "unaligned_edges": sum(int(r["metrics"].get("unaligned_edges", 0)) for r in rows),
    }


def evidence_counts(exp_name: str):
    all_counts = Counter()
    core_counts = Counter()
    for corpus in CORPORA:
        for run_dir in sorted((ROOT / "nova_exp" / exp_name / corpus).glob("run_*")):
            artifact = run_dir / "artifact.json"
            if not artifact.exists():
                continue
            data = json.loads(artifact.read_text(encoding="utf-8-sig"))
            for edge in data.get("edges", []):
                value = str(edge.get("evidence_completeness") or "unclassified")
                all_counts[value] += 1
                if edge.get("kind") in {"direct_core", "conditional", "regime", "transitive_derived"}:
                    core_counts[value] += 1
    return dict(sorted(all_counts.items())), dict(sorted(core_counts.items()))


def evidence_metadata(exp_name: str) -> dict:
    versions = Counter()
    scopes = Counter()
    stages = Counter()
    for corpus in CORPORA:
        for run_dir in sorted((ROOT / "nova_exp" / exp_name / corpus).glob("run_*")):
            artifact = run_dir / "artifact.json"
            if not artifact.exists():
                continue
            data = json.loads(artifact.read_text(encoding="utf-8-sig"))
            for edge in data.get("edges", []):
                versions[str(edge.get("evidence_packaging_version") or "unclassified")] += 1
                scopes[str(edge.get("support_scope") or "unclassified")] += 1
                stages[str(edge.get("evidence_packaging_stage") or "unclassified")] += 1
    return {
        "packaging_versions": dict(sorted(versions.items())),
        "support_scopes": dict(sorted(scopes.items())),
        "stages": dict(sorted(stages.items())),
    }

def main() -> int:
    gold_data = {corpus: load_gold(corpus) for corpus in CORPORA}
    records = []
    missing = []
    for method, exp_name in METHODS.items():
        for corpus in CORPORA:
            nodes, aliases, gold_edges = gold_data[corpus]
            for run_dir in sorted((ROOT / "nova_exp" / exp_name / corpus).glob("run_*")):
                artifact = run_dir / "artifact.json"
                pre = run_dir / "artifact.pre_verification.json"
                if not artifact.exists() or not pre.exists():
                    missing.append(str(run_dir))
                    continue
                views = evaluate_unified_views(artifact, pre, nodes, aliases, gold_edges, gml_for(run_dir))
                for view in REPORT_VIEWS:
                    if view in views:
                        records.append({"method": method, "experiment": exp_name, "corpus": corpus, "run": run_dir.name, "view": view, "metrics": views[view]})
    aggregates = {corpus: {} for corpus in CORPORA}
    for corpus in CORPORA:
        for method in METHODS:
            for view in REPORT_VIEWS:
                value = aggregate(records, method, corpus, view)
                if value is not None:
                    aggregates[corpus][f"{method}/{view}"] = value
    deltas = []
    for corpus in CORPORA:
        for view in ("raw", "soft_core_balanced", "support_aware_core", "verified_core"):
            base = aggregates[corpus].get("D2C_9/" + view)
            new = aggregates[corpus].get("D2C_13_2/" + view)
            if base and new:
                deltas.append({"corpus": corpus, "view": view, "d2c9_f1": base["f1_mean"], "d2c13_2_f1": new["f1_mean"], "delta_f1": round(new["f1_mean"] - base["f1_mean"], 4), "d2c9_p": base["precision_mean"], "d2c13_2_p": new["precision_mean"], "d2c9_r": base["recall_mean"], "d2c13_2_r": new["recall_mean"]})
    all_counts, core_counts = evidence_counts("exp14_d2c13_2_clean")
    evidence_meta = evidence_metadata("exp14_d2c13_2_clean")
    experiment_summary = json.loads((ROOT / "nova_exp" / "exp14_d2c13_2_clean" / "experiment_summary.json").read_text(encoding="utf-8-sig"))
    elapsed = [float(r.get("elapsed_seconds") or 0) for r in experiment_summary.get("results", [])]
    payload = {
        "protocol": "same canonical evaluator and gold manifests; D2C_9=exp12, D2C_13_2=exp14; independent stochastic runs",
        "methods": METHODS,
        "records": records,
        "aggregates": aggregates,
        "deltas": deltas,
        "d2c13_2_evidence_all_edges": all_counts,
        "d2c13_2_evidence_core_edges": core_counts,
        "d2c13_2_evidence_metadata": evidence_meta,
        "run_summary": {"planned": experiment_summary.get("planned"), "total": experiment_summary.get("total"), "failed": experiment_summary.get("failed"), "mean_elapsed_seconds": round(statistics.mean(elapsed), 2) if elapsed else None, "max_elapsed_seconds": max(elapsed) if elapsed else None},
        "missing_runs": missing,
    }
    out = ROOT / "nova_exp" / "exp14_d2c13_2_clean" / "analysis_summary.json"
    write_json(out, payload)
    print(json.dumps({"records": len(records), "missing": len(missing), "deltas": deltas, "evidence_core": core_counts, "output": str(out)}, ensure_ascii=False, indent=2))
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
