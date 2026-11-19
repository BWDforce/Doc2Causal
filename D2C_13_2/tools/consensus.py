# -*- coding: utf-8 -*-
"""Build generic multi-run consensus views for D2C_13_2 artifacts."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _edge_key(artifact: dict, edge: dict) -> tuple[str, str, str]:
    labels = {str(e.get("id")): str(e.get("label", e.get("id", ""))) for e in artifact.get("entities", [])}
    source = str(edge.get("source_name") or labels.get(str(edge.get("source")), edge.get("source")))
    target = str(edge.get("target_name") or labels.get(str(edge.get("target")), edge.get("target")))
    return source.lower().strip(), target.lower().strip(), str(edge.get("kind") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a multi-run D2C_13_2 consensus ledger")
    parser.add_argument("--run-root", required=True, help="Directory containing run_01, run_02, ...")
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-runs", type=int, default=2)
    args = parser.parse_args()

    run_root = Path(args.run_root)
    artifacts = []
    for run_dir in sorted(run_root.glob("run_*")):
        artifact_path = run_dir / "artifact.json"
        if artifact_path.exists():
            artifacts.append((run_dir.name, _load(artifact_path)))
    if not artifacts:
        raise SystemExit(f"no artifact.json found under {run_root}")

    counts: dict[tuple[str, str, str], dict] = {}
    for run_name, artifact in artifacts:
        by_id = {str(e.get("id")): e for e in artifact.get("edges", [])}
        for edge in artifact.get("edges", []):
            if edge.get("kind") == "rejected":
                continue
            key = _edge_key(artifact, edge)
            item = counts.setdefault(key, {"key": key, "runs": [], "views": set()})
            item["runs"].append(run_name)
            item["views"].update(
                view for view, rows in (artifact.get("views") or {}).items()
                if any(str(r.get("edge_id")) == str(edge.get("id")) for r in rows)
            )

    rows = []
    for item in counts.values():
        rows.append({
            "source": item["key"][0],
            "target": item["key"][1],
            "kind": item["key"][2],
            "runs": sorted(set(item["runs"])),
            "count": len(set(item["runs"])),
            "views": sorted(item["views"]),
        })
    rows.sort(key=lambda row: (-row["count"], row["source"], row["target"], row["kind"]))
    core = [row for row in rows if row["count"] >= args.min_runs and any(v in {"soft_core", "retained", "evidence_core"} for v in row["views"])]
    candidate = [row for row in rows if row["count"] < args.min_runs]
    support_aware = [row for row in rows if row["count"] >= args.min_runs and "support_aware_core" in row["views"]]
    verified = [row for row in rows if row["count"] >= args.min_runs and "verified_core" in row["views"]]
    payload = {
        "run_root": str(run_root.resolve()),
        "run_count": len(artifacts),
        "min_runs": args.min_runs,
        "consensus_core": core,
        "support_aware_consensus": support_aware,
        "verified_consensus": verified,
        "candidate_consensus": candidate,
        "all_edges": rows,
        "note": "Consensus views are multi-run diagnostics and are not mixed into single-run F1.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "run_count": len(artifacts),
        "all_edges": len(rows),
        "consensus_core": len(core),
        "candidate_consensus": len(candidate),
        "output": str(out.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
