# -*- coding: utf-8 -*-
"""Oracle sensitivity analysis for D2C_13_2 raw-recall improvements.

This does not claim measured LLM performance. It estimates how raw F1 changes if
an extraction mechanism recovers a stated fraction of each diagnosed FN class.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from d2c13_2.util import write_json
from diagnose_raw_recall import _manifest, analyze_gml

ROOT = Path(__file__).resolve().parents[2]


def _f1(tp: int, fp: int, fn: int) -> float:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Oracle raw-recall sensitivity by FN component")
    parser.add_argument("--exp-root", required=True)
    parser.add_argument("--manifest-root", default=str(ROOT / "gold_gen_text" / "output_gen5"))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    exp_root = Path(args.exp_root).resolve()
    manifest_root = Path(args.manifest_root).resolve()
    scenarios = {
        "entity_first_25": {"one_node_missing": 0.25},
        "entity_first_50": {"one_node_missing": 0.50},
        "relation_matrix_25": {"both_nodes_no_path": 0.25},
        "relation_matrix_50": {"both_nodes_no_path": 0.50},
        "direct_bridge_50": {"both_nodes_path_no_direct": 0.50},
        "combined_25": {"one_node_missing": 0.25, "both_nodes_no_path": 0.25, "both_nodes_path_no_direct": 0.25},
        "combined_50": {"one_node_missing": 0.50, "both_nodes_no_path": 0.50, "both_nodes_path_no_direct": 0.50},
        "combined_75": {"one_node_missing": 0.75, "both_nodes_no_path": 0.75, "both_nodes_path_no_direct": 0.75},
    }
    rows = []
    for corpus_dir in sorted(path for path in exp_root.iterdir() if path.is_dir()):
        corpus = corpus_dir.name
        manifest = manifest_root / corpus / "seed1" / "manifest.json"
        if not manifest.exists():
            continue
        gold_nodes, aliases, gold_edges = _manifest(corpus, manifest_root)
        for run_dir in sorted(corpus_dir.glob("run_*")):
            gmls = sorted((run_dir / "lang7").glob("*.gml"))
            if not gmls:
                continue
            metrics = analyze_gml(gmls[-1], gold_nodes, aliases, gold_edges)
            rows.append({"corpus": corpus, "run": run_dir.name, "metrics": metrics})
    summary = {}
    for corpus in sorted({row["corpus"] for row in rows}):
        selected = [row["metrics"] for row in rows if row["corpus"] == corpus]
        base_tp = sum(int(m["tp"]) for m in selected)
        base_fp = sum(int(m["fp"]) for m in selected)
        base_fn = sum(int(m["fn"]) for m in selected)
        corpus_summary = {"base": {"tp": base_tp, "fp": base_fp, "fn": base_fn, "f1": _f1(base_tp, base_fp, base_fn)}}
        for name, fractions in scenarios.items():
            recovered = 0.0
            for row in selected:
                for category, fraction in fractions.items():
                    recovered += float(row.get(category, 0)) * fraction
            recovered_int = int(round(recovered))
            corpus_summary[name] = {
                "recovered_tp": recovered_int,
                "tp": base_tp + recovered_int,
                "fp": base_fp,
                "fn": max(0, base_fn - recovered_int),
                "f1": _f1(base_tp + recovered_int, base_fp, max(0, base_fn - recovered_int)),
            }
        summary[corpus] = corpus_summary
    payload = {
        "protocol": "oracle component-recovery sensitivity; no LLM calls; not a measured method result",
        "scenarios": scenarios,
        "summary": summary,
    }
    output = Path(args.output) if args.output else exp_root / "d2c13_2_recall_simulation.json"
    write_json(output, payload)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())