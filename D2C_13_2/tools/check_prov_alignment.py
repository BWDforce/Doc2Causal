# -*- coding: utf-8 -*-
"""Small PROV-O alignment experiment for D2C_13_2 artifacts.

The check re-exports each JSON artifact through the current RDF exporter, then
verifies that provenance-bearing records are explicitly typed as prov:Entity,
linked to a prov:Activity, and that retained edges expose
prov:wasDerivedFrom links to supporting assertions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF

from d2c13_2.constants import ONTOLOGY_PATH
from d2c13_2.rdfio import artifact_to_jsonld


ACC = Namespace("https://w3id.org/d2c/acc#")
PROV = Namespace("http://www.w3.org/ns/prov#")
PROV_O = URIRef("http://www.w3.org/ns/prov-o")
QUERY_DIR = Path(__file__).resolve().parents[1] / "sparql"
RECORD_TYPES = (
    "Document", "Span", "Entity", "Assertion", "Condition", "RetainedEdge",
    "Vote", "ConflictDecision", "CycleDecision",
)


def _artifact_paths(roots: list[Path], artifacts: list[Path]) -> list[Path]:
    paths = [p.resolve() for p in artifacts]
    for root in roots:
        for path in root.resolve().rglob("artifact.json"):
            text = str(path).lower()
            if any(part in text for part in ("\\roundtrip\\", "/roundtrip/", "\\acceptance\\", "/acceptance/", "pre_verification")):
                continue
            paths.append(path.resolve())
    return sorted(set(paths))


def _json_counts(artifact: dict[str, Any]) -> dict[str, int]:
    return {
        "Document": len(artifact.get("documents", [])),
        "Span": len(artifact.get("spans", [])),
        "Entity": len(artifact.get("entities", [])),
        "Assertion": len(artifact.get("assertions", [])),
        "Condition": len(artifact.get("conditions", [])),
        "RetainedEdge": len(artifact.get("edges", [])),
        "Vote": len(artifact.get("votes", [])),
        "ConflictDecision": len(artifact.get("conflict_decisions", [])),
        "CycleDecision": len(artifact.get("cycle_decisions", [])),
    }


def _query_rows(graph: Graph, name: str) -> list[dict[str, Any]]:
    text = (QUERY_DIR / name).read_text(encoding="utf-8")
    return [{str(k): v for k, v in row.asdict().items()} for row in graph.query(text)]


def check_artifact(path: Path) -> dict[str, Any]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    jsonld = artifact_to_jsonld(artifact)
    graph = Graph()
    graph.parse(data=json.dumps(jsonld, ensure_ascii=False), format="json-ld")

    counts = _json_counts(artifact)
    alignment_rows = _query_rows(graph, "prov_alignment.rq")
    by_type = {
        str(row["recordType"]).rsplit("#", 1)[-1]: {
            "records": int(row["records"]),
            "generated": int(row["generatedRecords"]),
        }
        for row in alignment_rows
    }

    edge_rows = _query_rows(graph, "prov_edge_derivation.rq")
    edges_with_assertion = sum(1 for row in edge_rows if int(row["assertionCount"]) > 0)
    edges_with_derivation = sum(1 for row in edge_rows if int(row["derivedAssertionCount"]) > 0)

    artifact_typed = (URIRef(artifact["artifact_id"]), RDF.type, PROV.Entity) in graph
    activity_ids = set(graph.subjects(RDF.type, PROV.Activity))
    artifact_generated = any(
        (URIRef(artifact["artifact_id"]), PROV.wasGeneratedBy, activity) in graph
        for activity in activity_ids
    )

    type_pass = all(
        by_type.get(kind, {}).get("records", 0) == expected or expected == 0
        for kind, expected in counts.items()
    )
    generated_pass = all(
        by_type.get(kind, {}).get("generated", 0) == expected or expected == 0
        for kind, expected in counts.items()
    )
    passes = {
        "artifact_prov_entity": artifact_typed,
        "artifact_generated_by_activity": artifact_generated,
        "records_prov_entity": type_pass,
        "records_generated_by_activity": generated_pass,
        "edge_derivation": edges_with_derivation == edges_with_assertion,
    }
    return {
        "artifact": str(path),
        "counts": counts,
        "prov": by_type,
        "activity_count": len(activity_ids),
        "edges_with_assertion": edges_with_assertion,
        "edges_with_derivation": edges_with_derivation,
        "checks": passes,
        "pass": all(passes.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check PROV-O alignment on D2C_13_2 JSON artifacts")
    parser.add_argument("--artifact-root", action="append", default=[], help="Root directory to scan for artifact.json")
    parser.add_argument("--artifact", action="append", default=[], help="Explicit artifact.json path")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    paths = _artifact_paths([Path(p) for p in args.artifact_root], [Path(p) for p in args.artifact])
    if not paths:
        raise SystemExit("No artifact.json files found")

    ontology = Graph()
    ontology.parse(str(ONTOLOGY_PATH), format="turtle")
    imports_prov_o = (ACC.ACCOntology, OWL.imports, PROV_O) in ontology
    subclass_entities = {
        kind: (ACC[kind], RDF.type, OWL.Class) in ontology
        and (ACC[kind], URIRef("http://www.w3.org/2000/01/rdf-schema#subClassOf"), PROV.Entity) in ontology
        for kind in RECORD_TYPES
    }

    rows = []
    errors = []
    for path in paths:
        try:
            rows.append(check_artifact(path))
        except Exception as exc:  # noqa: BLE001
            errors.append({"artifact": str(path), "error": f"{type(exc).__name__}: {exc}"})

    report = {
        "experiment": "prov-o-alignment",
        "version": "D2C_13_2",
        "ontology": str(ONTOLOGY_PATH),
        "ontology_imports_prov_o": imports_prov_o,
        "ontology_subclasses_prov_entity": subclass_entities,
        "artifact_count": len(paths),
        "passed_artifacts": sum(1 for row in rows if row["pass"]),
        "failed_artifacts": len(paths) - sum(1 for row in rows if row["pass"]) - len(errors),
        "errors": errors,
        "artifacts": rows,
        "summary": {
            "records": sum(sum(row["counts"].values()) for row in rows),
            "records_with_prov_entity": sum(
                sum(v["records"] for v in row["prov"].values()) for row in rows
            ),
            "records_generated": sum(
                sum(v["generated"] for v in row["prov"].values()) for row in rows
            ),
            "edges_with_assertion": sum(row["edges_with_assertion"] for row in rows),
            "edges_with_derivation": sum(row["edges_with_derivation"] for row in rows),
        },
    }
    report["pass"] = (
        imports_prov_o
        and all(subclass_entities.values())
        and not errors
        and report["passed_artifacts"] == report["artifact_count"]
    )

    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "prov_alignment_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "prov_alignment_summary.md").write_text(
        "# PROV-O Alignment Check\n\n"
        f"- Artifacts: {report['artifact_count']}\n"
        f"- Passed: {report['passed_artifacts']}\n"
        f"- Failed: {report['failed_artifacts']}\n"
        f"- Errors: {len(errors)}\n"
        f"- Ontology imports PROV-O: {imports_prov_o}\n"
        f"- Records: {report['summary']['records']}\n"
        f"- Records with prov:Entity: {report['summary']['records_with_prov_entity']}\n"
        f"- Records with prov:wasGeneratedBy: {report['summary']['records_generated']}\n"
        f"- Edges with support assertions: {report['summary']['edges_with_assertion']}\n"
        f"- Edges with prov:wasDerivedFrom: {report['summary']['edges_with_derivation']}\n"
        f"- Overall pass: {report['pass']}\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(out),
        "artifacts": report["artifact_count"],
        "passed": report["passed_artifacts"],
        "errors": len(errors),
        "pass": report["pass"],
        "summary": report["summary"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
