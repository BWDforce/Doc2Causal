# -*- coding: utf-8 -*-
"""Replay D2C_13_2 promotion policies on saved exp7 artifacts without API calls."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from d2c13_2.evaluation import align_node, edge_metrics
from d2c13_2.semantics import materialize_views, _refresh_edge_semantics


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _manifest(path: Path) -> tuple[list[str], dict[str, str], set[tuple[str, str]]]:
    payload = _load(path)
    aliases: dict[str, str] = {}
    for doc in payload.get("docs", []):
        for item in doc.get("aliases", []) or []:
            if item.get("surface") and item.get("canonical"):
                aliases[str(item["surface"]).lower().strip()] = str(item["canonical"])
    return payload["gold_nodes"], aliases, {tuple(edge) for edge in payload["gold_edges"]}


def _apply_saved_verdicts(artifact: dict, verdicts: list[dict]) -> int:
    by_id = {edge.get("id"): edge for edge in artifact.get("edges", [])}
    matched = 0
    mappings = {
        "verifier_support": "support",
        "verifier_direction": "direction",
        "direct_support": "direct_support",
        "assertion_support": "assertion_support",
        "path_support": "path_support",
        "verification_coverage": "coverage",
        "verification_note": "reason",
    }
    for row in verdicts:
        edge = by_id.get(row.get("edge_id"))
        if edge is None:
            continue
        matched += 1
        for target, source in mappings.items():
            if row.get(source) is not None:
                edge[target] = row[source]
        if row.get("support") == "partial" and row.get("direction") == "forward" and edge.get("candidate_tier") in {"high", "medium"}:
            edge["partial_forward_promotion"] = True
        _refresh_edge_semantics(edge)
    materialize_views(artifact)
    return matched


def _edge_pairs(artifact: dict, edge_ids: set[str], gold_nodes: list[str], aliases: dict[str, str], counter: dict[str, int]) -> set[tuple[str, str]]:
    labels = {str(e.get("id")): e.get("label", e.get("id", "")) for e in artifact.get("entities", [])}
    pairs: set[tuple[str, str]] = set()
    for edge in artifact.get("edges", []):
        if edge.get("id") not in edge_ids:
            continue
        source_label = edge.get("source_name") or labels.get(str(edge.get("source")), edge.get("source"))
        target_label = edge.get("target_name") or labels.get(str(edge.get("target")), edge.get("target"))
        left = align_node(source_label, gold_nodes, aliases)
        right = align_node(target_label, gold_nodes, aliases)
        if not left or not right:
            counter["unaligned"] += 1
            continue
        if left == right:
            counter["self_loops"] += 1
            continue
        pairs.add((left, right))
    return pairs


def _view_ids(artifact: dict, name: str) -> set[str]:
    return {str(row.get("edge_id")) for row in artifact.get("views", {}).get(name, [])}


def _metrics(artifact: dict, ids: set[str], gold_nodes: list[str], aliases: dict[str, str], gold: set[tuple[str, str]]) -> dict:
    counter = {"unaligned": 0, "self_loops": 0}
    pairs = _edge_pairs(artifact, ids, gold_nodes, aliases, counter)
    return edge_metrics(pairs, gold, set(), set(gold_nodes), counter["self_loops"], counter["unaligned"])


def _policy_variants(artifact: dict) -> dict[str, set[str]]:
    retained = _view_ids(artifact, "retained")
    soft = _view_ids(artifact, "soft_core")
    partial = _view_ids(artifact, "partial_forward_core")
    by_id = {str(e.get("id")): e for e in artifact.get("edges", [])}
    exclude_uncertain = set(retained)
    for eid in soft:
        edge = by_id.get(eid, {})
        if edge.get("verifier_support") != "uncertain":
            exclude_uncertain.add(eid)
    uncertain_review = {
        str(e.get("id")) for e in artifact.get("edges", [])
        if e.get("verifier_support") == "uncertain" or e.get("verification_status") == "needs_review"
    }
    return {
        "retained": retained,
        "soft_core": soft,
        "partial_forward_core": retained | partial,
        "soft_core_exclude_uncertain": exclude_uncertain,
        "uncertain_needs_review": uncertain_review,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay D2C_13_2 promotion policies on saved exp7 artifacts")
    parser.add_argument("--exp-root", default="nova_exp/exp7_d2c4")
    parser.add_argument("--manifest-root", default="gold_gen_text/output_gen5")
    parser.add_argument("--output", default="nova_exp/exp7_d2c4/policy_replay_d2c13_2.json")
    args = parser.parse_args()

    root = Path(args.exp_root)
    manifest_root = Path(args.manifest_root)
    records = []
    for corpus in ("water", "win95pts"):
        gold_nodes, aliases, gold = _manifest(manifest_root / corpus / "seed1" / "manifest.json")
        for run in ("run_01", "run_02", "run_03"):
            run_dir = root / corpus / run
            artifact_path = run_dir / "artifact.json"
            verdict_path = run_dir / "verification_results.json"
            if not artifact_path.exists():
                continue
            artifact = _load(artifact_path)
            verdicts = _load(verdict_path) if verdict_path.exists() else []
            matched = _apply_saved_verdicts(artifact, verdicts)
            variants = _policy_variants(artifact)
            row = {"corpus": corpus, "run": run, "verdicts_matched": matched, "variants": {}}
            for name, ids in variants.items():
                row["variants"][name] = _metrics(artifact, ids, gold_nodes, aliases, gold)
            records.append(row)

    print("PER-RUN F1")
    for row in records:
        print(row["corpus"], row["run"], "verdicts=", row["verdicts_matched"], " ".join(
            f"{name}={metrics['f1']:.4f}" for name, metrics in row["variants"].items()
        ))

    print("\nAGGREGATE")
    summary = {}
    for corpus in ("water", "win95pts"):
        subset = [r for r in records if r["corpus"] == corpus]
        if not subset:
            continue
        summary[corpus] = {}
        print(corpus, "n=", len(subset))
        for name in next(iter(subset))["variants"]:
            metrics = [r["variants"][name] for r in subset]
            summary[corpus][name] = {
                "n": len(metrics),
                "precision": round(statistics.mean(m["precision"] for m in metrics), 4),
                "recall": round(statistics.mean(m["recall"] for m in metrics), 4),
                "f1": round(statistics.mean(m["f1"] for m in metrics), 4),
                "f1_sd": round(statistics.stdev(m["f1"] for m in metrics), 4) if len(metrics) > 1 else 0.0,
            }
            print(f"  {name:30s} P={summary[corpus][name]['precision']:.4f} R={summary[corpus][name]['recall']:.4f} F1={summary[corpus][name]['f1']:.4f}")

    payload = {"protocol": "exp7 saved verification_results replay; no API", "records": records, "summary": summary}
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\nSaved:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
