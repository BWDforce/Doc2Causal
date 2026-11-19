#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""exp11: discovery vs lang5 on gold-DAG generated corpora (water/win95pts/alarm).

For each corpus:
  - input documents: data/Test-text/input/goldgen_<dag>_seed1/
  - gold: gold_gen_text/output/<dag>/seed1/manifest.json
    (gold_edges = planted true edges, spurious_edges = planted fake edges,
     aliases = surface->canonical mapping)
  - theme: gold_gen_text/output/<dag>/seed1/extract_theme.txt (passed to lang5 --theme)

Runs:
  - discovery: multi_extract/run_multi_discovery.py (original RAG discovery)
  - lang5:     multi_extract/lang5/run_extract.py (lang4 + conflict resolution)

Evaluation (gold-aware, no human annotation):
  - node recall (canonical nodes recovered)
  - edge precision / recall / F1 vs gold_edges (alias + fuzzy node alignment)
  - spurious edges wrongly extracted (false positives from planted fakes)
  - DAG status
  - lang5 conflict-resolution summary (from conflicts.json)

Usage::
    set "DEEPSEEK_API_KEY=..."
    python graph_exp/exp11/run_comparison.py
    python graph_exp/exp11/run_comparison.py --evaluate-only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_ROOT = PROJECT_ROOT / "data" / "Test-text" / "input"
META_ROOT = PROJECT_ROOT / "gold_gen_text" / "output"
DISCOVERY_RUNNER = PROJECT_ROOT / "multi_extract" / "run_multi_discovery.py"
LANG5_RUNNER = PROJECT_ROOT / "multi_extract" / "lang5" / "run_extract.py"
DEFAULT_BASE_DIR = Path(__file__).resolve().parent / "results"
CORPORA = ["water", "win95pts", "alarm"]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="exp11: discovery vs lang5 on gold-DAG corpora.")
    p.add_argument("--n", type=int, default=1)
    p.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    p.add_argument("--python", type=Path, default=None)
    p.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY"))
    p.add_argument("--base-url", default="https://llmapi.zihangwu.com.cn:8443/v1")
    p.add_argument("--model", default="deepseek-v4-flash-0731")
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--merge-threshold", type=float, default=0.7)
    p.add_argument("--max-steps", type=int, default=2)
    p.add_argument("--extraction-passes", type=int, default=1)
    p.add_argument("--propagate-threshold", type=float, default=0.2)
    p.add_argument("--timeout", type=float, default=3600.0)
    p.add_argument("--skip-discovery", action="store_true")
    p.add_argument("--skip-lang5", action="store_true")
    p.add_argument("--evaluate-only", action="store_true")
    return p.parse_args(argv)


def resolve_python(args):
    if args.python:
        return str(args.python)
    return sys.executable


def latest_gml(run_dir: Path) -> Optional[Path]:
    files = sorted(run_dir.glob("*.gml"), key=lambda x: x.stat().st_mtime)
    return files[-1] if files else None


def load_manifest(name):
    m = json.loads((META_ROOT / name / "seed1" / "manifest.json").read_text(encoding="utf-8"))
    alias_map = {}
    for d in m.get("docs", []):
        for a in d.get("aliases", []):
            if "surface" in a and "canonical" in a:
                alias_map[a["surface"].lower().strip()] = a["canonical"]
    return m, alias_map


def _tokens(x):
    return set(re.findall(r"[a-z0-9]+", x.lower()))


def _dice(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


def align_node(surface, canonical_nodes, alias_map, threshold=0.6):
    s = surface.lower().strip()
    if not s:
        return None
    if s in alias_map:
        return alias_map[s]
    canon_lower = {c.lower(): c for c in canonical_nodes}
    if s in canon_lower:
        return canon_lower[s]
    for c in canonical_nodes:
        cl = c.lower()
        if cl and (cl in s or s in cl):
            return c
    best, best_score = None, 0.0
    for surf, c in alias_map.items():
        sc = _dice(s, surf)
        if sc > best_score:
            best, best_score = c, sc
    for c in canonical_nodes:
        sc = _dice(s, c)
        if sc > best_score:
            best, best_score = c, sc
    return best if best_score >= threshold else None


def evaluate_recovery(gml_path: Path, manifest, alias_map):
    G = nx.read_gml(str(gml_path))
    gold_nodes = manifest["gold_nodes"]
    gold_edges = set(tuple(e) for e in manifest["gold_edges"])
    canon_of = {}
    for n in G.nodes():
        c = align_node(str(n), gold_nodes, alias_map)
        if c:
            canon_of[n] = c
    found = set(canon_of.values())
    ext_edges = set()
    for u, v in G.edges():
        cu, cv = canon_of.get(u), canon_of.get(v)
        if cu and cv:
            ext_edges.add((cu, cv))
    tp = len(ext_edges & gold_edges)
    fp = len(ext_edges - gold_edges)
    fn = len(gold_edges - ext_edges)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    spurious_canon = set()
    for d in manifest.get("docs", []):
        for e in d.get("spurious_edges", []):
            if len(e) == 2:
                cu = align_node(e[0], gold_nodes, alias_map)
                cv = align_node(e[1], gold_nodes, alias_map)
                if cu and cv:
                    spurious_canon.add((cu, cv))
    hit = sorted(ext_edges & spurious_canon)
    return {"nodes_extracted": G.number_of_nodes(), "edges_extracted": G.number_of_edges(),
            "is_dag": nx.is_directed_acyclic_graph(G),
            "nodes_aligned": len(found), "node_recall": len(found) / len(gold_nodes) if gold_nodes else 0.0,
            "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "spurious_planted": len(spurious_canon), "spurious_hit": len(hit),
            "spurious_hit_edges": [list(e) for e in hit]}


def run_pipeline(args, python, cmd, run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.log"
    print(f"  $ {' '.join([t if t != args.api_key else '<masked>' for t in cmd])}")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "DEEPSEEK_API_KEY": args.api_key}
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            log.write(line)
        try:
            proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.write(f"\n[exit code: {proc.returncode}]\n")
    gml = latest_gml(run_dir)
    return {"ok": proc.returncode == 0 and gml is not None, "gml": str(gml) if gml else None,
            "log": str(log_path)}


def pct(x):
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "n/a"


def main(argv=None):
    args = parse_args(argv)
    python = resolve_python(args)
    if not args.evaluate_only and not args.api_key:
        raise SystemExit("Set DEEPSEEK_API_KEY or pass --api-key (or use --evaluate-only).")
    args.base_dir.mkdir(parents=True, exist_ok=True)

    summary = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "n": args.n,
               "corpora": {}, "lang5_conflicts": {}}
    for name in CORPORA:
        input_dir = INPUT_ROOT / f"goldgen_{name}_seed1"
        meta_dir = META_ROOT / name / "seed1"
        if not input_dir.exists() or not (meta_dir / "manifest.json").exists():
            print(f"[skip] {name}: corpus/manifest missing")
            continue
        manifest, alias_map = load_manifest(name)
        theme = (meta_dir / "extract_theme.txt").read_text(encoding="utf-8").strip()
        print(f"\n===== corpus {name} (theme={theme!r}, gold {len(manifest['gold_edges'])} edges) =====")

        rows = {}
        for pipeline, runner in (("discovery", DISCOVERY_RUNNER), ("lang5", LANG5_RUNNER)):
            run_dir = args.base_dir / name / pipeline / f"run_{1:02d}"
            if not args.evaluate_only:
                if pipeline == "discovery" and args.skip_discovery:
                    continue
                if pipeline == "lang5" and args.skip_lang5:
                    continue
                print(f"\n[{pipeline} {name}]")
                if pipeline == "discovery":
                    cmd = [python, str(runner), "--input-dir", str(input_dir),
                           "--base-url", args.base_url, "--model-base", args.model,
                           "--temperature", str(args.temperature),
                           "--output-dir", str(run_dir)]
                else:
                    cmd = [python, str(runner), "--input-dir", str(input_dir),
                           "--output-dir", str(run_dir),
                           "--base-url", args.base_url, "--model", args.model,
                           "--temperature", str(args.temperature),
                           "--merge-threshold", str(args.merge_threshold),
                           "--max-steps", str(args.max_steps),
                           "--extraction-passes", str(args.extraction_passes),
                           "--propagate-threshold", str(args.propagate_threshold),
                           "--theme", theme]
                run_pipeline(args, python, cmd, run_dir)

            gml = latest_gml(run_dir)
            if not gml:
                rows[pipeline] = {"ok": False, "gml": None}
                continue
            m = evaluate_recovery(gml, manifest, alias_map)
            m["gml"] = str(gml)
            if pipeline == "lang5":
                cfile = run_dir / "conflicts.json"
                if cfile.exists():
                    cj = json.loads(cfile.read_text(encoding="utf-8"))
                    m["n_conflicts"] = cj.get("total_conflicts", 0)
                    summary["lang5_conflicts"][name] = cj
            rows[pipeline] = m
            print(f"  {pipeline}: nodes={m['nodes_extracted']} edges={m['edges_extracted']} "
                  f"node_recall={pct(m['node_recall'])} P={pct(m['precision'])} "
                  f"R={pct(m['recall'])} F1={pct(m['f1'])} DAG={m['is_dag']} "
                  f"spurious_hit={m['spurious_hit']}/{m['spurious_planted']}")
        summary["corpora"][name] = {"theme": theme, "gold_edges": len(manifest["gold_edges"]),
                                    "gold_nodes": len(manifest["gold_nodes"]), "rows": rows}

    with (args.base_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    md = ["# exp11: discovery vs lang5 on gold-DAG generated corpora", "",
          f"- generated: {summary['generated_at']}", "",
          "| corpus | pipeline | node_recall | edge P | edge R | edge F1 | DAG | spurious_hit | conflicts |",
          "|---|---|---|---|---|---|---|---|---|"]
    for name, c in summary["corpora"].items():
        for pipe, m in c["rows"].items():
            if m.get("ok") is False:
                md.append(f"| {name} | {pipe} | FAILED | - | - | - | - | - | - |")
                continue
            md.append(f"| {name} | {pipe} | {pct(m['node_recall'])} | {pct(m['precision'])} | "
                      f"{pct(m['recall'])} | {pct(m['f1'])} | {m['is_dag']} | "
                      f"{m['spurious_hit']}/{m['spurious_planted']} | {m.get('n_conflicts', 0)} |")
    (args.base_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nsummary -> {args.base_dir / 'summary.json'} + summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
