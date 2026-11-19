# -*- coding: utf-8 -*-
"""Score the canonical D2C_13_2 views against one gold manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from d2c13_2.evaluation import evaluate_unified_views
from d2c13_2.util import write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Report raw/pre-verification/span/assertion/evidence/soft/direct/verified scores")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--pre-verification", default=None)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--raw-gml", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    artifact = Path(args.artifact)
    pre = Path(args.pre_verification) if args.pre_verification else artifact.parent / "artifact.pre_verification.json"
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    alias = {}
    for document in manifest.get("docs", []):
        for item in document.get("aliases", []) or []:
            if item.get("surface") and item.get("canonical"):
                alias[str(item["surface"]).lower().strip()] = str(item["canonical"])
    gold_edges = set(tuple(edge) for edge in manifest.get("gold_edges", []))
    report = evaluate_unified_views(
        artifact, pre, manifest.get("gold_nodes", []), alias, gold_edges,
        raw_gml_path=args.raw_gml,
    )
    payload = {
        "artifact": str(artifact.resolve()),
        "pre_verification": str(pre.resolve()),
        "manifest": str(Path(args.manifest).resolve()),
        "raw_gml": str(Path(args.raw_gml).resolve()) if args.raw_gml else None,
        "views": report,
    }
    if args.output:
        write_json(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


