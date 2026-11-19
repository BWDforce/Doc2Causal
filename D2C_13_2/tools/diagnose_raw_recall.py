# -*- coding: utf-8 -*-
"""Break raw-lang7 recall loss into entity, relation and direct-edge components."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import networkx as nx

from d2c13_2.evaluation import align_node, edge_metrics, gml_views
from d2c13_2.util import read_json, write_json

ROOT = Path(__file__).resolve().parents[2]


def _manifest(corpus: str, manifest_root: Path):
    data = read_json(manifest_root / corpus / "seed1" / "manifest.json", {}) or {}
    aliases = {}
    for document in data.get("docs", []):
        for item in document.get("aliases", []) or []:
            if isinstance(item, dict) and item.get("surface") and item.get("canonical"):
                aliases[str(item["surface"]).strip().lower()] = str(item["canonical"])
    return data.get("gold_nodes", []), aliases, {tuple(edge) for edge in data.get("gold_edges", []) if len(edge) == 2}


def analyze_gml(path: Path, gold_nodes, aliases, gold_edges):
    """Analyze the evidence-core GML view, excluding hypothesis completions."""
    views = gml_views(path, gold_nodes, aliases)
    pairs = views["core"]
    aligned_nodes = views["nodes"]
    graph_pairs = nx.DiGraph()
    graph_pairs.add_nodes_from(aligned_nodes)
    graph_pairs.add_edges_from(pairs)
    counts = Counter()
    for source, target in gold_edges - pairs:
        counts["fn_total"] += 1
        if source not in aligned_nodes and target not in aligned_nodes:
            counts["both_nodes_missing"] += 1
        elif source not in aligned_nodes or target not in aligned_nodes:
            counts["one_node_missing"] += 1
        elif nx.has_path(graph_pairs, source, target):
            counts["both_nodes_path_no_direct"] += 1
        else:
            counts["both_nodes_no_path"] += 1
    metrics = edge_metrics(
        pairs,
        gold_edges,
        aligned_nodes,
        set(gold_nodes),
        views.get("self_loops", 0),
        views.get("unaligned_edges", 0),
    )
    metrics["raw_node_recall"] = round(len(aligned_nodes & set(gold_nodes)) / len(gold_nodes), 4) if gold_nodes else 0.0
    metrics["candidate_edges"] = len(pairs)
    metrics["hypothesis_edges"] = int(views.get("hypothesis_edges", 0))
    metrics.update(counts)
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose raw edge recall by missing-node/relation component")
    parser.add_argument("--exp-root", required=True)
    parser.add_argument("--manifest-root", default=str(ROOT / "gold_gen_text" / "output_gen5"))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    exp_root = Path(args.exp_root).resolve()
    manifest_root = Path(args.manifest_root).resolve()
    records = []
    aggregates = {}
    for corpus_dir in sorted(path for path in exp_root.iterdir() if path.is_dir()):
        corpus = corpus_dir.name
        manifest_path = manifest_root / corpus / "seed1" / "manifest.json"
        if not manifest_path.exists():
            continue
        gold_nodes, aliases, gold_edges = _manifest(corpus, manifest_root)
        for run_dir in sorted(corpus_dir.glob("run_*")):
            gmls = sorted((run_dir / "lang7").glob("*.gml"))
            if not gmls:
                continue
            metrics = analyze_gml(gmls[-1], gold_nodes, aliases, gold_edges)
            records.append({"corpus": corpus, "run": run_dir.name, "metrics": metrics})
    for corpus in sorted({row["corpus"] for row in records}):
        rows = [row["metrics"] for row in records if row["corpus"] == corpus]
        keys = sorted({key for row in rows for key in row})
        aggregates[corpus] = {
            key: round(sum(float(row.get(key, 0)) for row in rows) / len(rows), 4)
            for key in keys
        }
        aggregates[corpus]["n"] = len(rows)
    payload = {"protocol": "raw lang7 GML; no D2C verifier or promotion", "aggregates": aggregates, "records": records}
    output = Path(args.output) if args.output else exp_root / "raw_recall_diagnosis.json"
    write_json(output, payload)
    print(json.dumps(aggregates, ensure_ascii=False, indent=2))
    print(f"output: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())