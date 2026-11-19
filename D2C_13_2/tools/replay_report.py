# -*- coding: utf-8 -*-
"""Aggregate deterministic D2C_13_2 saved-verdict replays against the source D2C_12 artifacts."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from d2c13_2.evaluation import evaluate_unified_views
from d2c13_2.util import write_json

ROOT = Path(__file__).resolve().parents[2]
COMPARISON_VIEWS = [
    "raw", "pre_verification", "soft_core_balanced", "support_aware_core",
    "direct_core", "verified_core",
]
NEW_ONLY_VIEWS = [
    "exact_support_core", "context_assisted_core", "contract_support_aware_core",
    "contract_verified_core", "evidence_contract_core",
]
VIEWS = COMPARISON_VIEWS + NEW_ONLY_VIEWS


def _manifest(corpus: str):
    path = ROOT / "gold_gen_text" / "output_gen5" / corpus / "seed1" / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    aliases = {}
    for document in data.get("docs", []):
        for item in document.get("aliases", []) or []:
            if item.get("surface") and item.get("canonical"):
                aliases[str(item["surface"]).lower().strip()] = str(item["canonical"])
    return list(data.get("gold_nodes", [])), aliases, {tuple(edge) for edge in data.get("gold_edges", []) if len(edge) == 2}


def _score_run(run_dir: Path, corpus: str, lang7_dir: Path) -> dict:
    gold_nodes, aliases, gold_edges = _manifest(corpus)
    gmls = sorted(lang7_dir.glob("*.gml"))
    return evaluate_unified_views(
        run_dir / "artifact.json",
        run_dir / "artifact.pre_verification.json",
        gold_nodes, aliases, gold_edges,
        raw_gml_path=gmls[-1] if gmls else None,
    )


def _load_rows(root: Path, source: Path) -> list[dict]:
    rows = []
    for path in sorted(root.glob("replay_summary*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        rows.extend(payload.get("runs", []))
    dedup = {}
    for row in rows:
        corpus = row.get("corpus")
        run = row.get("run")
        if not corpus or not run:
            continue
        replay_run = root / corpus / run
        source_run = source / corpus / run
        if (replay_run / "artifact.json").exists() and (source_run / "lang7").exists():
            row["score"] = _score_run(replay_run, corpus, source_run / "lang7")
        dedup[(corpus, run)] = row
    return sorted(dedup.values(), key=lambda row: (row.get("corpus"), row.get("run")))


def _interop(run_dir: Path) -> dict:
    pipeline_path = run_dir / "pipeline_summary.json"
    validation_path = run_dir / "validation_report.json"
    consumer_path = run_dir / "consumer_report.json"
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8-sig")) if pipeline_path.exists() else {}
    validation = json.loads(validation_path.read_text(encoding="utf-8-sig")) if validation_path.exists() else {}
    consumer = json.loads(consumer_path.read_text(encoding="utf-8-sig")) if consumer_path.exists() else {}
    return {
        "validation_valid": bool(validation.get("valid")),
        "shacl_conforms": bool((validation.get("shacl") or {}).get("conforms")),
        "query_failed": int(pipeline.get("query_failed") or 0),
        "roundtrip_json": bool(pipeline.get("roundtrip_json")),
        "roundtrip_rdf": bool(pipeline.get("roundtrip_rdf")),
        "roundtrip_edge_semantics": bool(pipeline.get("roundtrip_edge_semantics")),
        "roundtrip_entity_identity": bool(pipeline.get("roundtrip_entity_identity")),
        "roundtrip_queries": bool(pipeline.get("roundtrip_queries")),
        "consumer_cross_tool_equal": bool(consumer.get("cross_tool_equal")),
    }


def _aggregate(rows: list[dict], view: str) -> dict:
    selected = [row for row in rows if view in row.get("score", {})]
    if not selected:
        return {"n": 0}
    p = [float(row["score"][view]["precision"]) for row in selected]
    r = [float(row["score"][view]["recall"]) for row in selected]
    f = [float(row["score"][view]["f1"]) for row in selected]
    tp = sum(int(row["score"][view]["tp"]) for row in selected)
    fp = sum(int(row["score"][view]["fp"]) for row in selected)
    fn = sum(int(row["score"][view]["fn"]) for row in selected)
    micro_p = tp / (tp + fp) if tp + fp else 0.0
    micro_r = tp / (tp + fn) if tp + fn else 0.0
    micro_f = 2 * micro_p * micro_r / (micro_p + micro_r) if micro_p + micro_r else 0.0
    return {
        "n": len(selected),
        "precision_mean": round(statistics.mean(p), 4),
        "precision_sd": round(statistics.stdev(p), 4) if len(p) > 1 else 0.0,
        "recall_mean": round(statistics.mean(r), 4),
        "recall_sd": round(statistics.stdev(r), 4) if len(r) > 1 else 0.0,
        "f1_mean": round(statistics.mean(f), 4),
        "f1_sd": round(statistics.stdev(f), 4) if len(f) > 1 else 0.0,
        "micro_precision": round(micro_p, 4),
        "micro_recall": round(micro_r, 4),
        "micro_f1": round(micro_f, 4),
        "tp": tp, "fp": fp, "fn": fn,
    }


def _table(rows: list[list[object]], headers: list[str]) -> str:
    body = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        body.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate D2C_13_2 saved-verdict replays")
    parser.add_argument("--replay-root", required=True)
    parser.add_argument("--source-exp-root", default="nova_exp/exp17")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    root = Path(args.replay_root).resolve()
    source = Path(args.source_exp_root).resolve()
    replay_rows = _load_rows(root, source)
    results = {"replay_root": str(root), "source_exp_root": str(source), "corpora": {}, "runs": []}
    for corpus in ("water", "win95pts"):
        corpus_replay = [row for row in replay_rows if row.get("corpus") == corpus]
        baseline_rows = []
        for row in corpus_replay:
            run = row["run"]
            baseline = _score_run(source / corpus / run, corpus, source / corpus / run / "lang7")
            baseline_rows.append({"corpus": corpus, "run": run, "score": baseline})
        interop_rows = [_interop(root / corpus / row["run"]) for row in corpus_replay]
        results["corpora"][corpus] = {
            "n_replay": len(corpus_replay),
            "source_artifact_same_extraction": {view: _aggregate(baseline_rows, view) for view in VIEWS},
            "D2C_13_2_saved_verdict_replay": {view: _aggregate(corpus_replay, view) for view in VIEWS},
            "interop": {
                "runs": len(interop_rows),
                "validation_valid": sum(1 for row in interop_rows if row["validation_valid"]),
                "shacl_conforms": sum(1 for row in interop_rows if row["shacl_conforms"]),
                "query_failed_total": sum(row["query_failed"] for row in interop_rows),
                "roundtrip_all": sum(1 for row in interop_rows if all(row[key] for key in ("roundtrip_json", "roundtrip_rdf", "roundtrip_edge_semantics", "roundtrip_entity_identity", "roundtrip_queries"))),
                "consumer_cross_tool_equal": sum(1 for row in interop_rows if row["consumer_cross_tool_equal"]),
            },
            "mapping": {
                "mapped_total": sum(int(row.get("mapped") or 0) for row in corpus_replay),
                "request_total": sum(int(row.get("new_requests") or 0) for row in corpus_replay),
                "unmapped_total": sum(int(row.get("unmapped") or 0) for row in corpus_replay),
            },
        }
        results["runs"].extend(corpus_replay)
    out = Path(args.out) if args.out else root / "replay_comparison.json"
    write_json(out, results)
    table_rows = []
    for corpus in ("water", "win95pts"):
        block = results["corpora"][corpus]
        for view in COMPARISON_VIEWS:
            old = block["source_artifact_same_extraction"].get(view, {})
            new = block["D2C_13_2_saved_verdict_replay"].get(view, {})
            if not old.get("n") or not new.get("n"):
                continue
            table_rows.append([
                corpus, view,
                old["micro_precision"], old["micro_recall"], old["micro_f1"],
                new["micro_precision"], new["micro_recall"], new["micro_f1"],
                round(new["micro_f1"] - old["micro_f1"], 4),
                f"{new['f1_mean']:.4f}+/-{new['f1_sd']:.4f}",
            ])
    md = "# D2C_13_2 verified saved-verdict replay\n\n"
    md += "Same upstream lang7 extraction; the source baseline is the exp17 D2C_12 artifact. Saved exp17 verifier verdicts are remapped to D2C_13_2 edges. No extraction or verifier API calls.\n\n"
    md += _table(table_rows, ["Corpus", "View", "D2C_12 P", "R", "F1", "D2C_13_2 P", "R", "F1", "Delta F1", "mean+/-sd"])
    strict_rows = []
    for corpus in ("water", "win95pts"):
        new_block = results["corpora"][corpus]["D2C_13_2_saved_verdict_replay"]
        for view in NEW_ONLY_VIEWS:
            new = new_block.get(view, {})
            if new.get("n"):
                strict_rows.append([corpus, view, new["micro_precision"], new["micro_recall"], new["micro_f1"], new["tp"], new["fp"], new["fn"]])
    md += "\n\n## D2C_13_2 evidence-aware views (new only)\n\n"
    md += _table(strict_rows, ["Corpus", "View", "P", "R", "F1", "TP", "FP", "FN"])
    md += "\nThese views depend on D2C_13_2 evidence-contract fields or contract-aware promotion; they are not compared against zero-valued source fields.\n"
    md += "\n## Verdict mapping coverage\n\n"
    for corpus in ("water", "win95pts"):
        m = results["corpora"][corpus]["mapping"]
        md += f"- {corpus}: {m['mapped_total']}/{m['request_total']} requests mapped; {m['unmapped_total']} left unverified (new alias-induced edges).\n"
    md += "\n## Interoperability checks\n\n"
    for corpus in ("water", "win95pts"):
        io = results["corpora"][corpus]["interop"]
        md += f"- {corpus}: validation {io['validation_valid']}/{io['runs']}, SHACL {io['shacl_conforms']}/{io['runs']}, roundtrip {io['roundtrip_all']}/{io['runs']}, consumer equality {io['consumer_cross_tool_equal']}/{io['runs']}, query failures {io['query_failed_total']}.\n"
    (root / "replay_comparison.md").write_text(md + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "markdown": str(root / "replay_comparison.md"), "runs": len(replay_rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
