# -*- coding: utf-8 -*-
"""Score exp11 and compare its D2C_13_2 views with prior D2C versions."""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import networkx as nx

from d2c13_2.evaluation import artifact_views, evaluate_unified_views
from d2c13_2.util import write_json

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = {
    "D2C_8": "exp11",
    "D2C_13_2": "exp12",
    "D2C_7": "exp10",
    "D2C_5": "exp8",
    "D2C_4": "exp7_d2c4",
}
CORPORA = ("water", "win95pts")
REPORT_VIEWS = (
    "raw",
    "pre_verification",
    "pre_soft_core",
    "soft_core",
    "soft_core_lenient",
    "soft_core_balanced",
    "soft_core_precision",
    "support_aware_core",
    "direct_core",
    "verified_core",
    "needs_review",
    "candidate",
    "candidate_high",
    "candidate_medium",
    "candidate_low",
    "grounded",
    "soft_core_path",
    "raw_path",
)


def load_gold(corpus: str) -> tuple[list[str], dict[str, str], set[tuple[str, str]]]:
    path = ROOT / "gold_gen_text" / "output_gen5" / corpus / "seed1" / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    aliases: dict[str, str] = {}
    for document in payload.get("docs", []):
        for item in document.get("aliases", []) or []:
            surface = item.get("surface") if isinstance(item, dict) else None
            canonical = item.get("canonical") if isinstance(item, dict) else None
            if surface and canonical:
                aliases[str(surface).lower().strip()] = str(canonical)
    gold_edges = {tuple(edge) for edge in payload.get("gold_edges", []) if len(edge) == 2}
    return list(payload.get("gold_nodes", [])), aliases, gold_edges


def gml_for(run_dir: Path) -> Path | None:
    candidates = sorted((run_dir / "lang7").glob("*.gml"))
    if not candidates:
        candidates = sorted(run_dir.glob("*.gml"))
    return candidates[-1] if candidates else None


def mean_sd(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    if len(values) < 2:
        return round(mean, 4), 0.0
    return round(mean, 4), round(statistics.stdev(values), 4)


def aggregate(records: list[dict], method: str, corpus: str, view: str) -> dict | None:
    selected = [r for r in records if r["method"] == method and r["corpus"] == corpus and r["view"] == view]
    if not selected:
        return None
    precision, precision_sd = mean_sd([float(r["metrics"]["precision"]) for r in selected])
    recall, recall_sd = mean_sd([float(r["metrics"]["recall"]) for r in selected])
    f1, f1_sd = mean_sd([float(r["metrics"]["f1"]) for r in selected])
    tp = sum(int(r["metrics"]["tp"]) for r in selected)
    fp = sum(int(r["metrics"]["fp"]) for r in selected)
    fn = sum(int(r["metrics"]["fn"]) for r in selected)
    micro_p = tp / (tp + fp) if tp + fp else 0.0
    micro_r = tp / (tp + fn) if tp + fn else 0.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if micro_p + micro_r else 0.0
    return {
        "n": len(selected),
        "precision_mean": precision,
        "precision_sd": precision_sd,
        "recall_mean": recall,
        "recall_sd": recall_sd,
        "f1_mean": f1,
        "f1_sd": f1_sd,
        "micro_precision": round(micro_p, 4),
        "micro_recall": round(micro_r, 4),
        "micro_f1": round(micro_f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "candidate_edges_mean": round(statistics.mean([r["metrics"].get("candidate_edges", 0) for r in selected]), 2),
        "reversed_edges": sum(int(r["metrics"].get("reversed_edges", 0)) for r in selected),
        "self_loops": sum(int(r["metrics"].get("self_loops", 0)) for r in selected),
        "unaligned_edges": sum(int(r["metrics"].get("unaligned_edges", 0)) for r in selected),
    }


def path_length_at_least_two(graph: nx.DiGraph, source: str, target: str) -> bool:
    if source not in graph or target not in graph:
        return False
    try:
        return nx.shortest_path_length(graph, source, target) > 1
    except nx.NetworkXNoPath:
        return False


def categories_for_run(run_dir: Path, gold_nodes: list[str], aliases: dict[str, str], gold_edges: set[tuple[str, str]], view: str) -> dict:
    artifact = run_dir / "artifact.json"
    data = artifact_views(artifact, gold_nodes, aliases)
    predicted = data[view]
    gold_graph = nx.DiGraph()
    gold_graph.add_edges_from(gold_edges)
    predicted_graph = nx.DiGraph()
    predicted_graph.add_edges_from(predicted)
    counts = Counter()
    for source, target in predicted - gold_edges:
        if (target, source) in gold_edges:
            counts["reversed_direct_fp"] += 1
        elif path_length_at_least_two(gold_graph, source, target):
            counts["shortcut_forward_fp"] += 1
        elif path_length_at_least_two(gold_graph, target, source):
            counts["shortcut_reverse_fp"] += 1
        else:
            counts["unrelated_fp"] += 1
    for source, target in gold_edges - predicted:
        if path_length_at_least_two(predicted_graph, source, target):
            counts["path_only_fn"] += 1
        else:
            counts["missing_direct_fn"] += 1
    counts["self_loops"] += int(data["self_loops"])
    counts["unaligned_edges"] += int(data["unaligned_edges"])
    counts["tp"] = len(predicted & gold_edges)
    counts["fp"] = len(predicted - gold_edges)
    counts["fn"] = len(gold_edges - predicted)
    return dict(counts)


def main() -> int:
    gold_data = {corpus: load_gold(corpus) for corpus in CORPORA}
    records: list[dict] = []
    category_records: list[dict] = []
    missing: list[str] = []
    for method, exp_name in EXPERIMENTS.items():
        for corpus in CORPORA:
            nodes, aliases, gold_edges = gold_data[corpus]
            runs = sorted((ROOT / "nova_exp" / exp_name / corpus).glob("run_*"))
            for run_dir in runs:
                artifact = run_dir / "artifact.json"
                pre = run_dir / "artifact.pre_verification.json"
                gml = gml_for(run_dir)
                if not artifact.exists() or not pre.exists():
                    missing.append(str(run_dir))
                    continue
                views = evaluate_unified_views(artifact, pre, nodes, aliases, gold_edges, gml)
                for view in REPORT_VIEWS:
                    if view in views:
                        records.append({
                            "method": method,
                            "experiment": exp_name,
                            "corpus": corpus,
                            "run": run_dir.name,
                            "view": view,
                            "metrics": views[view],
                        })
                for view in ("soft_core", "support_aware_core", "verified_core"):
                    category_records.append({
                        "method": method,
                        "experiment": exp_name,
                        "corpus": corpus,
                        "run": run_dir.name,
                        "view": view,
                        "categories": categories_for_run(run_dir, nodes, aliases, gold_edges, view),
                    })
    aggregates: dict[str, Any] = {}
    for corpus in CORPORA:
        aggregates[corpus] = {}
        for method in EXPERIMENTS:
            for view in REPORT_VIEWS:
                result = aggregate(records, method, corpus, view)
                if result is not None:
                    aggregates[corpus][f"{method}/{view}"] = result
    categories: dict[str, Any] = {}
    for method in EXPERIMENTS:
        for corpus in CORPORA:
            for view in ("soft_core", "soft_core_balanced", "support_aware_core", "verified_core"):
                selected = [r for r in category_records if r["method"] == method and r["corpus"] == corpus and r["view"] == view]
                if not selected:
                    continue
                total = Counter()
                for row in selected:
                    total.update(row["categories"])
                categories[f"{method}/{corpus}/{view}"] = {"n": len(selected), **dict(total)}
    payload = {
        "protocol": "canonical exact directed edge matching; same D2C_13_2 evaluator and gold manifests for all methods",
        "experiments": EXPERIMENTS,
        "records": records,
        "aggregates": aggregates,
        "categories": categories,
        "missing_runs": missing,
    }
    write_json(ROOT / "nova_exp" / "exp12" / "analysis_summary.json", payload)
    print(json.dumps({"records": len(records), "categories": len(category_records), "missing_runs": len(missing), "output": str(ROOT / 'nova_exp' / 'exp11' / 'analysis_summary.json')}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

