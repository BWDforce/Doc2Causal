# -*- coding: utf-8 -*-
import json
from pathlib import Path

from d2c13_2.cli import main


def _write_docs(root: Path):
    root.mkdir()
    (root / "01_manual.json").write_text(json.dumps({
        "title": {"eng": "Demo Manual"},
        "summary": "A causes B. B causes C. A causes C."
    }), encoding="utf-8")
    (root / "02_fault.json").write_text(json.dumps({
        "title": {"eng": "Demo Fault Report"},
        "summary": "A causes B in the normal regime."
    }), encoding="utf-8")


def _write_extractions(root: Path):
    payload = {
        "extractions": {
            "01_manual.json": {
                "entities": [
                    {"name": "A", "type": "condition", "description": "A", "aliases": ["Alpha"], "quote": "A", "span": "0:1"},
                    {"name": "B", "type": "condition", "description": "B", "aliases": ["Beta"], "quote": "B", "span": "11:12"},
                    {"name": "C", "type": "condition", "description": "C", "aliases": ["Gamma"], "quote": "C", "span": "23:24"},
                ],
                "links": [
                    {"cause": "A", "effect": "B", "quote": "A causes B", "span": "0:10", "sign": "+", "rung": 2, "condition": "", "mechanism": "A -> B", "direct_evidence": True},
                    {"cause": "B", "effect": "C", "quote": "B causes C", "span": "12:22", "sign": "+", "rung": 2, "condition": "", "mechanism": "B -> C", "direct_evidence": True},
                    {"cause": "A", "effect": "C", "quote": "A causes C", "span": "24:34", "sign": "+", "rung": 2, "condition": "", "mechanism": "A -> B -> C", "direct_evidence": True},
                ],
                "chains": [{"steps": ["A", "B", "C"], "quote": "A causes B. B causes C.", "span": "0:22"}],
            },
            "02_fault.json": {
                "entities": [{"name": "A", "type": "condition", "description": "A", "quote": "A", "span": "0:1"}],
                "links": [{"cause": "A", "effect": "B", "quote": "A causes B", "span": "0:10", "sign": "+", "rung": 2, "condition": "normal regime", "mechanism": "A -> B", "direct_evidence": True}],
                "chains": [],
            },
        }
    }
    path = root / "extractions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_standalone_mock_run(tmp_path):
    docs = tmp_path / "docs"
    _write_docs(docs)
    ext = _write_extractions(tmp_path)
    out = tmp_path / "out"
    code = main([
        "run", "--input-dir", str(docs), "--out-dir", str(out),
        "--from-extractions", str(ext), "--model", "mock", "--no-shacl",
    ])
    assert code == 0
    artifact = json.loads((out / "artifact.json").read_text(encoding="utf-8"))
    assert artifact["version"] == "D2C_13_2"
    assert artifact["documents"]
    assert artifact["views"]["direct_core"]
    assert (out / "roundtrip" / "roundtrip_report.json").exists()
    report = json.loads((out / "roundtrip" / "roundtrip_report.json").read_text(encoding="utf-8"))
    assert report["json_roundtrip"] is True
    assert report["rdf_roundtrip"] is True
    assert report["query_roundtrip"] is True


def test_standalone_dry_run(tmp_path):
    docs = tmp_path / "docs"
    _write_docs(docs)
    out = tmp_path / "dry"
    code = main(["run", "--input-dir", str(docs), "--out-dir", str(out), "--dry-run"])
    assert code == 0
    assert (out / "dry_run.json").exists()
    assert list((out / "prompts").glob("*.prompt.txt"))

