# -*- coding: utf-8 -*-
"""Offline canonical-arbitration replay over an existing D2C artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

from d2c13_2.canonical_arbitration import apply_canonical_arbitration
from d2c13_2.config import load_config
from d2c13_2.semantics import materialize_views
from d2c13_2.util import read_json, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay D2C_13_2 canonical arbitration without extraction or verifier calls")
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config.json"))
    args = parser.parse_args()
    artifact = read_json(Path(args.artifact), {}) or {}
    config = load_config(args.config).get("canonical_arbitration") or {}
    summary = apply_canonical_arbitration(artifact, config)
    materialize_views(artifact)
    write_json(Path(args.output), artifact)
    write_json(Path(args.output).with_suffix(".arbitration.json"), summary)
    print({"output": str(Path(args.output).resolve()), "canonical_arbitration": summary})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())