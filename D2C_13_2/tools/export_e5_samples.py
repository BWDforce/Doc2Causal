# -*- coding: utf-8 -*-
"""Export contract-complete E5 samples from D2C_13_2 artifacts.

This replaces the old E5 sampler that could select truncated or empty spans.
Only text-contract-complete edges are included, and the full best evidence
window is written into the sample instead of a truncated span fragment.
"""

from __future__ import annotations
import argparse
import json
import random
from pathlib import Path

from d2c13_2.util import read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
CORPORA = ("water", "win95pts")


def _best_window(edge: dict) -> dict:
    windows = [row for row in (edge.get("evidence_windows") or []) if isinstance(row, dict)]
    complete = [row for row in windows if row.get("complete") and row.get("text")]
    if complete:
        return sorted(complete, key=lambda row: (row.get("exact_match", False), -int(row.get("window_width") or 0), len(row.get("text") or "")), reverse=True)[0]
    return {}


def _sample(corpus: str, run_dir: Path, artifact: dict, edge: dict) -> dict:
    labels = {str(entity.get("id")): entity.get("label", entity.get("id", "")) for entity in artifact.get("entities", [])}
    best = _best_window(edge)
    contract = edge.get("evidence_contract") or {}
    return {
        "sample_id": f"{corpus}-{run_dir.name}-{edge.get('id')}-contract",
        "corpus": corpus,
        "run": run_dir.name,
        "artifact": str((run_dir / "artifact.json").resolve()),
        "edge_id": edge.get("id"),
        "source": labels.get(str(edge.get("source")), edge.get("source")),
        "target": labels.get(str(edge.get("target")), edge.get("target")),
        "kind": edge.get("kind"),
        "evidence_grade": edge.get("evidence_grade"),
        "semantic_support": edge.get("semantic_support"),
        "verification_status": edge.get("verification_status"),
        "evidence_completeness": edge.get("evidence_completeness"),
        "support_scope": edge.get("support_scope"),
        "evidence_contract_complete": bool(edge.get("evidence_contract_complete")),
        "evidence_contract": contract,
        "evidence_window_text": best.get("text") or "",
        "evidence_window_kind": best.get("window_kind") or edge.get("best_window_kind"),
        "evidence_window_id": best.get("evidence_window_id") or edge.get("best_evidence_window_id"),
        "evidence_source": best.get("source_surface") or "",
        "evidence_target": best.get("target_surface") or "",
        "evidence_quality": "contract_complete",
        "label": "",
        "annotator": "",
        "notes": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export evidence-contract-complete E5 samples")
    parser.add_argument("--exp-root", default=str(ROOT / "nova_exp" / "exp17"))
    parser.add_argument("--out", default=str(ROOT / "nova_exp" / "exp17" / "e5_contract_samples.json"))
    parser.add_argument("--per-corpus", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    root = Path(args.exp_root)
    rng = random.Random(args.seed)
    selected = []
    counts = {}
    for corpus in CORPORA:
        pool = []
        for run_dir in sorted((root / corpus).glob("run_*")):
            artifact_path = run_dir / "artifact.json"
            artifact = read_json(artifact_path, {}) or {}
            for edge in artifact.get("edges", []):
                if not edge.get("evidence_contract_complete"):
                    continue
                contract = edge.get("evidence_contract") or {}
                if not contract.get("text_complete"):
                    continue
                if not _best_window(edge):
                    continue
                pool.append(_sample(corpus, run_dir, artifact, edge))
        counts[corpus] = len(pool)
        selected.extend(rng.sample(pool, min(args.per_corpus, len(pool))))
    payload = {
        "experiment": "E5-contract",
        "protocol": "D2C_13_2 evidence_contract_complete && text_complete && full best window",
        "seed": args.seed,
        "per_corpus": args.per_corpus,
        "available": counts,
        "n": len(selected),
        "samples": selected,
    }
    write_json(args.out, payload)
    print(json.dumps({"n": len(selected), "available": counts, "output": str(args.out)}, indent=2))
    return 0 if selected else 1


if __name__ == "__main__":
    raise SystemExit(main())
