# -*- coding: utf-8 -*-
"""Paired policy ablation on saved pre-verification artifacts; no API calls."""

from __future__ import annotations

import argparse
import copy
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from d2c13_2.evaluation import align_node, edge_metrics
from d2c13_2.evidence_completeness import package_evidence, restore_pre_verification_evidence, snapshot_pre_verification_evidence
from d2c13_2.policy import normalize_policy
from d2c13_2.semantics import apply_verdicts, materialize_views


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _manifest(path: Path) -> tuple[list[str], dict[str, str], set[tuple[str, str]]]:
    payload = _load(path)
    aliases: dict[str, str] = {}
    for document in payload.get("docs", []):
        for item in document.get("aliases", []) or []:
            if item.get("surface") and item.get("canonical"):
                aliases[str(item["surface"]).lower().strip()] = str(item["canonical"])
    return payload["gold_nodes"], aliases, {tuple(edge) for edge in payload["gold_edges"]}


def _pairs(artifact: dict, view: str, gold_nodes: list[str], aliases: dict[str, str]) -> tuple[set[tuple[str, str]], int, int]:
    by_id = {str(edge.get("id")): edge for edge in artifact.get("edges", [])}
    labels = {str(entity.get("id")): entity.get("label", entity.get("id", "")) for entity in artifact.get("entities", [])}
    pairs: set[tuple[str, str]] = set()
    unaligned = self_loops = 0
    for row in artifact.get("views", {}).get(view, []):
        edge = by_id.get(str(row.get("edge_id")))
        if not edge:
            continue
        source = edge.get("source_name") or labels.get(str(edge.get("source")), edge.get("source"))
        target = edge.get("target_name") or labels.get(str(edge.get("target")), edge.get("target"))
        left = align_node(source, gold_nodes, aliases)
        right = align_node(target, gold_nodes, aliases)
        if not left or not right:
            unaligned += 1
            continue
        if left == right:
            self_loops += 1
            continue
        pairs.add((left, right))
    return pairs, unaligned, self_loops


def _score(artifact: dict, view: str, gold_nodes: list[str], aliases: dict[str, str], gold: set[tuple[str, str]]) -> dict:
    pairs, unaligned, self_loops = _pairs(artifact, view, gold_nodes, aliases)
    return edge_metrics(pairs, gold, set(), set(gold_nodes), self_loops, unaligned)


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay strict/balanced/advisory policies on saved verdicts")
    parser.add_argument("--exp-root", required=True)
    parser.add_argument("--manifest-root", default="gold_gen_text/output_gen5")
    parser.add_argument("--corpora", default="water,win95pts")
    parser.add_argument("--policies", default="strict,balanced,advisory")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    root = Path(args.exp_root)
    manifest_root = Path(args.manifest_root)
    policies = [item.strip() for item in args.policies.split(",") if item.strip()]
    records = []
    for corpus in [item.strip() for item in args.corpora.split(",") if item.strip()]:
        manifest = manifest_root / corpus / "seed1" / "manifest.json"
        if not manifest.exists():
            continue
        gold_nodes, aliases, gold = _manifest(manifest)
        for run_dir in sorted((root / corpus).glob("run_*")):
            pre_path = run_dir / "artifact.pre_verification.json"
            verdict_path = run_dir / "verification_results.json"
            if not pre_path.exists():
                continue
            base = _load(pre_path)
            package_evidence(base)
            snapshot_pre_verification_evidence(base)
            base["promotion_policy"] = normalize_policy(base.get("promotion_policy"))
            materialize_views(base)
            pre_scores = {
                view: _score(base, view, gold_nodes, aliases, gold)
                for view in ("soft_core", "soft_core_lenient", "support_aware_core", "evidence_core", "needs_review")
            }
            verdicts = _load(verdict_path) if verdict_path.exists() else []
            for policy in policies:
                artifact = copy.deepcopy(base)
                apply_verdicts(artifact, verdicts, direction_policy=policy)
                package_evidence(artifact)
                restore_pre_verification_evidence(artifact)
                materialize_views(artifact)
                scores = {
                    view: _score(artifact, view, gold_nodes, aliases, gold)
                    for view in ("soft_core", "soft_core_lenient", "soft_core_precision", "support_aware_core", "verified_core", "needs_review")
                }
                records.append({
                    "corpus": corpus,
                    "run": run_dir.name,
                    "policy": policy,
                    "verdicts": len(verdicts),
                    "pre": pre_scores,
                    "post": scores,
                })

    if not records:
        raise SystemExit("no artifact.pre_verification.json runs found")

    summary: dict[str, dict[str, dict]] = {}
    for corpus in sorted({row["corpus"] for row in records}):
        summary[corpus] = {}
        for policy in policies:
            subset = [row for row in records if row["corpus"] == corpus and row["policy"] == policy]
            if not subset:
                continue
            summary[corpus][policy] = {}
            for split in ("pre", "post"):
                for view in subset[0][split]:
                    metrics = [row[split][view] for row in subset]
                    summary[corpus][policy][f"{split}.{view}"] = {
                        "n": len(metrics),
                        "precision": round(statistics.mean(m["precision"] for m in metrics), 4),
                        "recall": round(statistics.mean(m["recall"] for m in metrics), 4),
                        "f1": round(statistics.mean(m["f1"] for m in metrics), 4),
                        "f1_sd": round(statistics.stdev(m["f1"] for m in metrics), 4) if len(metrics) > 1 else 0.0,
                    }

    print("PAIRED POLICY ABLATION")
    for corpus, policies_summary in summary.items():
        print(corpus)
        for policy, views in policies_summary.items():
            print(" ", policy, " ".join(
                f"{name}=P{value['precision']:.4f}/R{value['recall']:.4f}/F1{value['f1']:.4f}"
                for name, value in views.items() if name.startswith("post.")
            ))
    print("protocol: saved pre-verification artifacts + saved verdicts; no API")
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "protocol": "paired saved-verdict policy ablation; no API",
            "records": records,
            "summary": summary,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("saved:", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

