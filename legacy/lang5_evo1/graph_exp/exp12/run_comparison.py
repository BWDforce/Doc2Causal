#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""exp12: discovery vs lang5_evo on gen3 gold-DAG corpora (moderate synonym variants), 4+1 incremental, n=1 per corpus.

Corpora: data/Test-text/input/goldgen_gen3/{water,win95pts,alarm} (5 docs each, moderate synonym variants).
Gold/metadata: gold_gen_text/output_gen3/<corpus>/seed1/{manifest.json,extract_theme.txt}.

Pipelines:
  - discovery  : multi_extract/run_multi_discovery.py (original RAG discovery)
  - lang5_evo  : multi_extract/lang5_evo/run_extract.py
                 (lang5 + semantic merge rules + evidence-based decycling)

Phases (4+1 incremental):
  - phase0: extract docs 01-04 only;
  - phase1: lang5_evo continues from the phase0 graph with --init-graph on doc 05
            only (true staged continuation); discovery has no staged mode, so its
            phase1 is an honest full 5-doc rerun -- the SAME canonical-edge delta
            metrics are computed for both pipelines.

Hyperparameters (selected values):
  temperature=0.3 (stable sampling), max_steps=2, extraction_passes=1,
  propagate_threshold=0.2, merge_threshold=0.7 (with semantic merge rules).

Evaluation (gold-aware, no human annotation):
  - node recall, edge precision/recall/F1 vs full gold edge set;
  - phase0 additionally reports recall against the doc01-04 planted horizon;
  - spurious-hit X/Y against the deterministic gen2 bait (phase0: 4, phase1: 5);
  - DAG status (lang5_evo de-cycles by construction);
  - conflict count; decycle count; merge statistics;
  - phase0 -> phase1 canonical-edge deltas (added / removed / direction-flipped).

Usage::
    set "DEEPSEEK_API_KEY=..."
    python graph_exp/exp12/run_comparison.py --n 3
    python graph_exp/exp12/run_comparison.py --n 3 --corpus water   # single corpus
    python graph_exp/exp12/run_comparison.py --evaluate-only
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MULTI_EXTRACT_DIR = PROJECT_ROOT / "multi_extract"
EXP11_DIR = PROJECT_ROOT / "graph_exp" / "exp11"
META_ROOT = PROJECT_ROOT / "gold_gen_text" / "output_gen3"
INPUT_GEN2 = PROJECT_ROOT / "data" / "Test-text" / "input" / "goldgen_gen3"
DISCOVERY_RUNNER = MULTI_EXTRACT_DIR / "run_multi_discovery.py"
LANG5_EVO_RUNNER = MULTI_EXTRACT_DIR / "lang5_evo" / "run_extract.py"
DEFAULT_BASE_DIR = Path(__file__).resolve().parent / "results"
CORPORA = ["water", "win95pts", "alarm"]

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "exp11_run_comparison", EXP11_DIR / "run_comparison.py")
rc11 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc11)  # reuses align_node / token helpers


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="exp12: discovery vs lang5_evo, 4+1 incremental, n=3.")
    p.add_argument("--n", type=int, default=1)
    p.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    p.add_argument("--corpus", default=None, help="Only run this corpus (water|win95pts|alarm).")
    p.add_argument("--python", type=Path, default=None)
    p.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY"))
    p.add_argument("--base-url", default="https://llmapi.zihangwu.com.cn:8443/v1")
    p.add_argument("--model", default="deepseek-v4.1-flash")
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--merge-threshold", type=float, default=0.7)
    p.add_argument("--max-steps", type=int, default=2)
    p.add_argument("--extraction-passes", type=int, default=1)
    p.add_argument("--propagate-threshold", type=float, default=0.2)
    p.add_argument("--timeout", type=float, default=3600.0)
    p.add_argument("--corpus-root", type=Path, default=None,
                   help="Override input corpus parent (default: data/Test-text/input/goldgen_gen3).")
    p.add_argument("--meta-root", type=Path, default=None,
                   help="Override manifest parent (default: gold_gen_text/output_gen3).")
    p.add_argument("--skip-discovery", action="store_true")
    p.add_argument("--skip-lang5", action="store_true")
    p.add_argument("--evaluate-only", action="store_true")
    return p.parse_args(argv)


def resolve_python(args):
    return str(args.python) if args.python else sys.executable


def latest_gml(run_dir: Path) -> Optional[Path]:
    files = sorted(run_dir.glob("*.gml"), key=lambda x: x.stat().st_mtime)
    return files[-1] if files else None


def load_manifest(corpus: str, meta_root: Path = None):
    root = meta_root or META_ROOT
    mf = json.loads((root / corpus / "seed1" / "manifest.json").read_text(encoding="utf-8"))
    return mf


def build_alias_map(mf):
    alias_map = {}
    for d in mf.get("docs", []):
        for a in d.get("aliases", []):
            if "surface" in a and "canonical" in a:
                alias_map[a["surface"].lower().strip()] = a["canonical"]
    return alias_map


def prep_inputs(corpus: str, base_dir: Path, corpus_root: Path = None):
    src = (corpus_root or INPUT_GEN2) / corpus
    top4 = base_dir / "_inputs" / corpus / "top4"
    doc05 = base_dir / "_inputs" / corpus / "doc05"
    shutil.rmtree(top4, ignore_errors=True)
    shutil.rmtree(doc05, ignore_errors=True)
    top4.mkdir(parents=True, exist_ok=True)
    doc05.mkdir(parents=True, exist_ok=True)
    n4 = n5 = 0
    for f in sorted(src.glob("*.json")):
        if f.name[:2] in ("01", "02", "03", "04"):
            shutil.copy2(f, top4 / f.name)
            n4 += 1
        elif f.name[:2] == "05":
            shutil.copy2(f, doc05 / f.name)
            n5 += 1
    assert n4 == 4 and n5 == 1, (corpus, n4, n5)
    return top4, doc05


def run_pipeline(args, python, cmd, run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.log"
    print(f"  $ {' '.join([str(t) for t in cmd])}  (cwd={PROJECT_ROOT})")
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "DEEPSEEK_API_KEY": args.api_key}
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        try:
            for line in proc.stdout:
                log.write(line)
                log.flush()
        except Exception:
            pass
        try:
            ret = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            ret = -1
            log.write(f"\n[exp12] TIMEOUT after {args.timeout}s\n")
    ok = ret == 0 and (latest_gml(run_dir) is not None)
    print(f"  -> exit={ret} ok={ok}")
    return ok


def canonical_graph(gml_path: Path, mf, alias_map):
    """Returns (canonical_nodes_set, canonical_edges_set) of an extracted graph."""
    g = nx.read_gml(str(gml_path))
    gold_nodes = mf["gold_nodes"]
    canon_of = {}
    for n in g.nodes():
        c = rc11.align_node(str(n), gold_nodes, alias_map)
        if c:
            canon_of[n] = c
    nodes = set(canon_of.values())
    edges = set()
    for u, v in g.edges():
        cu, cv = canon_of.get(u), canon_of.get(v)
        if cu and cv:
            edges.add((cu, cv))
    return nodes, edges, g.number_of_nodes(), g.number_of_edges()


def evaluate_run(mf, gml_path, phase0: bool):
    """Gold-aware metrics for one produced GML. phase0 limits aliases/spurious
    to docs 01-04 (bait denominator 4); phase1 uses all 5."""
    alias_map = build_alias_map(mf)
    gold_nodes = mf["gold_nodes"]
    gold_edges = set(tuple(e) for e in mf["gold_edges"])
    nodes, ext, raw_nodes, raw_edges = canonical_graph(gml_path, mf, alias_map)

    doc_limit = 4 if phase0 else 5
    spurious = set()
    for d in mf.get("docs", [])[:doc_limit]:
        for e in d.get("spurious_edges", []):
            if len(e) == 2:
                cu = rc11.align_node(e[0], gold_nodes, alias_map)
                cv = rc11.align_node(e[1], gold_nodes, alias_map)
                if cu and cv:
                    spurious.add((cu, cv))
    horizon = set()
    for d in mf.get("docs", [])[:4]:
        for e in d.get("planted_edges", []):
            if len(e) == 2:
                horizon.add(tuple(e))
    if not phase0:
        horizon = gold_edges

    tp = ext & gold_edges
    fp = ext - gold_edges
    fn = gold_edges - ext
    precision = len(tp) / (len(tp) + len(fp)) if (len(tp) + len(fp)) else 0.0
    recall = len(tp) / len(gold_edges) if gold_edges else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    horizon_recall = len(ext & horizon) / len(horizon) if horizon else 0.0
    return {
        "canonical_nodes": len(nodes),
        "canonical_edges": len(ext),
        "raw_nodes": raw_nodes,
        "raw_edges": raw_edges,
        "tp": len(tp), "fp": len(fp), "fn": len(fn),
        "precision": precision, "recall": recall, "f1": f1,
        "horizon_edges": len(horizon), "horizon_recall": horizon_recall,
        "spurious_planted": len(spurious), "spurious_hit": len(ext & spurious),
        "spurious_hit_edges": [list(e) for e in sorted(ext & spurious)],
    }


def read_sidecars(run_dir: Path):
    out = {"conflicts": 0, "decycle": 0, "merge_merged": 0,
           "merge_protected": 0, "merge_pattern": 0, "merge_loops": 0, "merge_mode": "?"}
    for path, key in ((run_dir / "conflicts.json", "conflicts"),
                      (run_dir / "decycle.json", "decycle")):
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                out[key] = d.get("total_conflicts" if key == "conflicts" else "total_removed", 0)
            except Exception:
                pass
    mp = run_dir / "merge_stats.json"
    if mp.exists():
        try:
            d = json.loads(mp.read_text(encoding="utf-8"))
            s = d.get("stats", {})
            out["merge_merged"] = s.get("merged", 0)
            out["merge_protected"] = s.get("protected_numeric", 0)
            out["merge_pattern"] = s.get("protected_pattern", 0)
            out["merge_loops"] = s.get("self_loops_removed", 0)
            out["merge_mode"] = s.get("mode", "?")
        except Exception:
            pass
    return out


def delta_metrics(nodes0, edges0, nodes1, edges1):
    added = sorted(edges1 - edges0)
    removed = sorted(edges0 - edges1)
    flipped = sorted([(a, b) for (a, b) in edges0 if (b, a) in edges1 and (a, b) not in edges1])
    return {
        "nodes_0": len(nodes0), "nodes_1": len(nodes1),
        "edges_0": len(edges0), "edges_1": len(edges1),
        "added_edges": len(added), "removed_edges": len(removed),
        "flipped_edges": len(flipped),
        "added": added, "removed": removed, "flipped": flipped,
    }


def mean_std(vals):
    if not vals:
        return 0.0, 0.0
    if len(vals) == 1:
        return vals[0], 0.0
    return statistics.mean(vals), statistics.pstdev(vals)


def build_summary(args, corpora):
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n": args.n,
        "corpus_root": str(getattr(args, "corpus_root", None) or INPUT_GEN2),
        "meta_root": str(getattr(args, "meta_root", None) or META_ROOT),
        "temperature": args.temperature,
        "merge_threshold": args.merge_threshold,
        "propagate_threshold": args.propagate_threshold,
        "max_steps": args.max_steps,
        "extraction_passes": args.extraction_passes,
        "note": ("phase1 discovery = full 5-doc rerun (baseline has no staged mode); "
                 "phase1 lang5_evo = staged continuation from phase0 graph on doc05"),
        "corpora": {},
    }
    for corpus in corpora:
        mf = load_manifest(corpus, meta_root=getattr(args, "meta_root", None))
        alias_map = build_alias_map(mf)
        gold_edges = set(tuple(e) for e in mf["gold_edges"])
        horizon = set()
        for d in mf.get("docs", [])[:4]:
            for e in d.get("planted_edges", []):
                if len(e) == 2:
                    horizon.add(tuple(e))
        c = {"gold_nodes": len(mf["gold_nodes"]), "gold_edges": len(gold_edges),
             "horizon_edges": len(horizon), "rows": {}}
        for pipeline in ("discovery", "lang5_evo"):
            rows = {"phase0": [], "phase1": [], "deltas": [],
                    "ok0": 0, "ok1": 0, "sidecars0": [], "sidecars1": []}
            for ri in range(1, args.n + 1):
                d0 = args.base_dir / corpus / pipeline / "phase0" / f"run_{ri:02d}"
                d1 = args.base_dir / corpus / pipeline / "phase1" / f"run_{ri:02d}"
                g0, g1 = latest_gml(d0), latest_gml(d1)
                if g0 and g1:
                    m0 = evaluate_run(mf, g0, phase0=True)
                    m1 = evaluate_run(mf, g1, phase0=False)
                    nodes0, edges0, _, _ = canonical_graph(g0, mf, alias_map)
                    nodes1, edges1, _, _ = canonical_graph(g1, mf, alias_map)
                    rows["phase0"].append(m0)
                    rows["phase1"].append(m1)
                    rows["ok0"] += 1
                    rows["ok1"] += 1
                    delta = delta_metrics(nodes0, edges0, nodes1, edges1)
                    delta["run"] = ri
                    rows["deltas"].append(delta)
                    rows["sidecars0"].append(read_sidecars(d0))
                    rows["sidecars1"].append(read_sidecars(d1))
            agg = {"phase0": {}, "phase1": {}, "deltas": {}}
            for ph in ("phase0", "phase1"):
                rr = rows[ph]
                if not rr:
                    continue
                agg[ph]["runs"] = len(rr)
                for k in ("f1", "precision", "recall", "horizon_recall", "spurious_hit", "spurious_planted"):
                    mean, sd = mean_std([r[k] for r in rr])
                    agg[ph][k + "_mean"] = round(mean, 4)
                    agg[ph][k + "_std"] = round(sd, 4)
                agg[ph]["tp"] = [r["tp"] for r in rr]
                agg[ph]["fp"] = [r["fp"] for r in rr]
                agg[ph]["fn"] = [r["fn"] for r in rr]
                agg[ph]["torun"] = sum(r["spurious_hit"] for r in rr)
                agg[ph]["to_planted"] = sum(r["spurious_planted"] for r in rr)
            if rows["deltas"]:
                for k in ("added_edges", "removed_edges", "flipped_edges"):
                    mean, sd = mean_std([d[k] for d in rows["deltas"]])
                    agg["deltas"][k + "_mean"] = round(mean, 3)
                    agg["deltas"][k + "_std"] = round(sd, 3)
                agg["deltas"]["nodes0_mean"] = round(mean_std([d["nodes_0"] for d in rows["deltas"]])[0], 2)
                agg["deltas"]["nodes1_mean"] = round(mean_std([d["nodes_1"] for d in rows["deltas"]])[0], 2)
            agg["ok0"] = rows["ok0"]
            agg["ok1"] = rows["ok1"]
            agg["deltas_per_run"] = rows["deltas"]
            agg["sidecars0"] = rows["sidecars0"]
            agg["sidecars1"] = rows["sidecars1"]
            c["rows"][pipeline] = agg
        summary["corpora"][corpus] = c
    return summary


def write_summary(summary, base_dir):
    (base_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# exp12: discovery vs lang5_evo on gen2 corpora (4+1 incremental, n=%d)" % summary["n"], "",
          f"- generated: {summary['generated_at']}", "",
          "Evaluation: edge P/R/F1 vs FULL gold; phase0 additionally horizon recall vs planted docs 01-04;",
          "spurious_hit X/Y = gen2 deterministic bait taken; DAG/conflicts/decycle/merge from sidecars.", "",
          "| corpus | pipeline | phase | ok | P (mean±sd) | R | F1 | horizonR | spurious | decycle | merged | prot | protP |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cname, c in summary["corpora"].items():
        for pipe, agg in c["rows"].items():
            for ph in ("phase0", "phase1"):
                a = agg.get(ph) or {}
                if not a:
                    md.append(f"| {cname} | {pipe} | {ph} | FAILED | - | - | - | - | - | - | - | - |")
                    continue
                sc = agg["sidecars0"] if ph == "phase0" else agg["sidecars1"]
                dc = [s["decycle"] for s in sc]
                mg = [s["merge_merged"] for s in sc]
                pt = [s["merge_protected"] for s in sc]
                pp = [s["merge_pattern"] for s in sc]
                md.append(
                    f"| {cname} | {pipe} | {ph} | {a['runs']}/{summary['n']} | "
                    f"{a['precision_mean']:.3f}±{a['precision_std']:.3f} | "
                    f"{a['recall_mean']:.3f}±{a['recall_std']:.3f} | "
                    f"{a['f1_mean']:.3f}±{a['f1_std']:.3f} | "
                    f"{a['horizon_recall_mean']:.3f} | "
                    f"{a['torun']}/{a['to_planted']} | "
                    f"{sum(dc)} | {sum(mg)} | {sum(pt)} | {sum(pp)} |")
            if agg.get("deltas"):
                d_ = agg["deltas"]
                md.append(f"| {cname} | {pipe} | Δ(0->1) | - | added {d_['added_edges_mean']}±{d_['added_edges_std']} | "
                          f"removed {d_['removed_edges_mean']}±{d_['removed_edges_std']} | "
                          f"flipped {d_['flipped_edges_mean']}±{d_['flipped_edges_std']} | - | - | - | - | - |")
    (base_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"\nsummary -> {base_dir / 'summary.json'} + summary.md")


def run(args):
    corpora = [args.corpus] if args.corpus else CORPORA
    python = resolve_python(args)
    if not args.api_key:
        raise SystemExit("Set DEEPSEEK_API_KEY or pass --api-key.")
    for corpus in corpora:
        mf = load_manifest(corpus, meta_root=args.meta_root)
        theme = mf["theme"]["extract_theme"]
        top4, doc05 = prep_inputs(corpus, args.base_dir, corpus_root=args.corpus_root)
        for pipeline, skip in (("discovery", args.skip_discovery),
                               ("lang5_evo", args.skip_lang5)):
            if skip:
                continue
            runner = DISCOVERY_RUNNER if pipeline == "discovery" else LANG5_EVO_RUNNER
            print(f"\n=== corpus={corpus} pipeline={pipeline} (n={args.n}) ===")
            for ri in range(1, args.n + 1):
                p0 = args.base_dir / corpus / pipeline / "phase0" / f"run_{ri:02d}"
                p1 = args.base_dir / corpus / pipeline / "phase1" / f"run_{ri:02d}"
                print(f"\n--- run {ri}/{args.n} phase0 ---")
                if pipeline == "discovery":
                    cmd0 = [python, str(runner), "--input-dir", str(top4),
                            "--output-dir", str(p0), "--model-base", args.model,
                            "--base-url", args.base_url, "--temperature", str(args.temperature),
                            "--max-docs", "4"]
                else:
                    cmd0 = [python, str(runner), "--input-dir", str(top4),
                            "--output-dir", str(p0), "--theme", theme,
                            "--model", args.model, "--base-url", args.base_url,
                            "--temperature", str(args.temperature),
                            "--max-steps", str(args.max_steps),
                            "--extraction-passes", str(args.extraction_passes),
                            "--propagate-threshold", str(args.propagate_threshold),
                            "--merge-threshold", str(args.merge_threshold)]
                ok0 = run_pipeline(args, python, cmd0, p0)
                g0 = latest_gml(p0)
                if not ok0 or not g0:
                    print(f"  phase0 failed; skipping phase1 for run {ri}")
                    continue
                print(f"--- run {ri}/{args.n} phase1 ---")
                if pipeline == "discovery":
                    cmd1 = [python, str(runner), "--input-dir", str((args.corpus_root or INPUT_GEN2) / corpus),
                            "--output-dir", str(p1), "--model-base", args.model,
                            "--base-url", args.base_url, "--temperature", str(args.temperature),
                            "--max-docs", "5"]
                else:
                    cmd1 = [python, str(runner), "--input-dir", str(doc05),
                            "--output-dir", str(p1), "--theme", theme,
                            "--init-graph", str(g0),
                            "--model", args.model, "--base-url", args.base_url,
                            "--temperature", str(args.temperature),
                            "--max-steps", str(args.max_steps),
                            "--extraction-passes", str(args.extraction_passes),
                            "--propagate-threshold", str(args.propagate_threshold),
                            "--merge-threshold", str(args.merge_threshold)]
                ok1 = run_pipeline(args, python, cmd1, p1)
                if not ok1:
                    print(f"  phase1 failed for run {ri}")
    summary = build_summary(args, corpora)
    write_summary(summary, args.base_dir)
    return 0


def main(argv=None):
    args = parse_args(argv)
    if args.evaluate_only:
        summary = build_summary(args, [args.corpus] if args.corpus else CORPORA)
        write_summary(summary, args.base_dir)
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
