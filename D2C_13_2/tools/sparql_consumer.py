# -*- coding: utf-8 -*-
"""Independent SPARQL Q1-Q7 consumer for D2C_13_2 RDF artifacts.

The runtime imports only rdflib and the Python standard library. It never imports
the d2c13_2 producer package, reads artifact.json, or evaluates producer Python
query functions. The query files in D2C_13_2/sparql are the executable contract.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from rdflib import Graph


PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUERY_DIR = Path(__file__).resolve().parents[1] / "sparql"
QUERY_FILES = {
    "Q1": "q1_evidence.rq",
    "Q2": "q2_documents.rq",
    "Q3": "q3_votes.rq",
    "Q4": "q4_conflicts.rq",
    "Q5": "q5_conditions.rq",
    "Q6": "q6_status.rq",
    "Q7": "q7_cycles.rq",
}
SURFACE_QUERY = "q3_entity_surfaces.rq"


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _value(row: dict[str, Any], key: str, default: str | None = None) -> str | None:
    value = row.get(key)
    if value is None or str(value) == "":
        return default
    return str(value)


def _bool_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _int_value(value: object, default: int = 0) -> int:
    if value is None or str(value) == "":
        return default
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def _json_value(value: object, default: Any) -> Any:
    if value is None or str(value) == "":
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _unique_strings(values) -> list[str]:
    return sorted({str(value) for value in values if value is not None and str(value) != ""})


def _surface_key(value: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", _clean_text(value).casefold()).split())


def _surface_tokens(value: object) -> set[str]:
    return set(_surface_key(value).split())


def _surface_match(left: object, right: object) -> bool:
    left_key, right_key = _surface_key(left), _surface_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    left_tokens, right_tokens = set(left_key.split()), set(right_key.split())
    smaller = min(len(left_tokens), len(right_tokens))
    return bool(smaller) and len(left_tokens & right_tokens) / smaller >= 0.8


def _edge_direction(row: dict[str, Any]) -> str:
    source = _value(row, "edge_source_label") or _value(row, "edge_source") or ""
    target = _value(row, "edge_target_label") or _value(row, "edge_target") or ""
    return f"{source}->{target}"


def _read_query(name: str) -> str:
    return (QUERY_DIR / name).read_text(encoding="utf-8")


def _run_query(graph: Graph, name: str) -> list[dict[str, Any]]:
    result = graph.query(_read_query(name))
    rows: list[dict[str, Any]] = []
    for row in result:
        rows.append({str(key): value for key, value in row.asdict().items()})
    return rows


def _load_surfaces(graph: Graph) -> dict[str, set[str]]:
    surfaces: dict[str, set[str]] = {}
    for row in _run_query(graph, SURFACE_QUERY):
        entity = _value(row, "entity")
        if not entity:
            continue
        bucket = surfaces.setdefault(entity, set())
        for key in ("label", "alias"):
            text = _clean_text(_value(row, key))
            if text:
                bucket.add(text)
    return surfaces


def _edge_surfaces(
    surfaces: dict[str, set[str]],
    entity_id: str | None,
    fallback_label: str | None,
) -> list[str]:
    values = set(surfaces.get(str(entity_id or ""), set()))
    if _clean_text(fallback_label):
        values.add(_clean_text(fallback_label))
    return sorted(values)


def _relative_direction(
    direction: str | None,
    edge_source: str | None,
    edge_target: str | None,
    source_surfaces: list[str],
    target_surfaces: list[str],
) -> str:
    parts = [part.strip() for part in str(direction or "").split("->", 1)]
    if len(parts) != 2:
        return "other"
    left, right = parts
    if any(_surface_match(left, value) for value in source_surfaces) and any(
        _surface_match(right, value) for value in target_surfaces
    ):
        return "forward"
    if any(_surface_match(left, value) for value in target_surfaces) and any(
        _surface_match(right, value) for value in source_surfaces
    ):
        return "reverse"
    return "other"


def _q1_answers(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        edge_id = _value(row, "edge")
        if edge_id:
            grouped.setdefault(edge_id, []).append(row)
    answers: dict[str, dict[str, Any]] = {}
    for edge_id, edge_rows in grouped.items():
        first = edge_rows[0]
        evidence = []
        seen_assertions = set()
        for row in edge_rows:
            assertion_id = _value(row, "assertion")
            if not assertion_id or assertion_id in seen_assertions:
                continue
            seen_assertions.add(assertion_id)
            span_text = _clean_text(_value(row, "span_text"))
            mechanism = _clean_text(_value(row, "assertion_text"))
            evidence.append({
                "assertion_id": assertion_id,
                "assertion_text": mechanism,
                "channel": _value(row, "channel"),
                "doc_id": _value(row, "doc_id", "") or "",
                "mechanism": mechanism,
                "rung": _value(row, "rung"),
                "sign": _value(row, "sign"),
                "span_id": _value(row, "span", "") or "",
                "span_text": span_text,
                "span_text_missing": not bool(span_text),
            })
        evidence.sort(key=lambda item: (item["doc_id"], item["span_id"], item["assertion_id"]))
        answers[edge_id] = {
            "query": "Q1",
            "edge_id": edge_id,
            "assertion_count": len({item["assertion_id"] for item in evidence}),
            "assertion_ids": sorted({item["assertion_id"] for item in evidence}),
            "span_ids": sorted({item["span_id"] for item in evidence if item["span_id"]}),
            "doc_ids": sorted({item["doc_id"] for item in evidence if item["doc_id"]}),
            "evidence": evidence,
            "evidence_completeness": _value(first, "evidence_completeness"),
            "support_scope": _value(first, "support_scope"),
            "verifier_support_scope": _value(first, "verifier_support_scope"),
            "condition_grounding": _value(first, "condition_grounding"),
            "evidence_contract_complete": _bool_value(_value(first, "evidence_contract_complete")),
            "evidence_contract": _json_value(_value(first, "evidence_contract_json"), {}),
            "evidence_coverage": _json_value(_value(first, "evidence_coverage_json"), {}),
            "best_evidence_window_id": _value(first, "best_evidence_window_id"),
            "best_window_kind": _value(first, "best_window_kind"),
            "best_window_source": _value(first, "best_window_source"),
            "best_window_width": _int_value(_value(first, "best_window_width")),
            "evidence_exact_match": _bool_value(_value(first, "evidence_exact_match")),
            "context_window_complete": _bool_value(_value(first, "context_window_complete")),
        }
    return answers


def _q2_answers(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        edge_id = _value(row, "edge")
        if edge_id:
            grouped.setdefault(edge_id, []).append(row)
    answers: dict[str, dict[str, Any]] = {}
    for edge_id, edge_rows in grouped.items():
        documents = {}
        for row in edge_rows:
            document = _value(row, "document")
            if not document:
                continue
            doc_id = _value(row, "doc_id", "") or ""
            document_row = {
                "doc_id": doc_id,
                "id": document,
                "source_file": _value(row, "source_file"),
                "title": _value(row, "title"),
            }
            documents[(document, doc_id)] = document_row
        answers[edge_id] = {
            "query": "Q2",
            "edge_id": edge_id,
            "documents": [documents[key] for key in sorted(documents)],
        }
    return answers


def _q3_answers(
    rows: list[dict[str, Any]],
    surfaces: dict[str, set[str]],
    edge_meta: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        edge_id = _value(row, "edge")
        vote_id = _value(row, "vote")
        if edge_id and vote_id:
            grouped.setdefault((edge_id, vote_id), []).append(row)
    by_edge: dict[str, list[dict[str, Any]]] = {}
    for (edge_id, vote_id), vote_rows in sorted(grouped.items()):
        first = vote_rows[0]
        meta = edge_meta.get(edge_id, {})
        edge_source = _value(first, "edge_source") or meta.get("source")
        edge_target = _value(first, "edge_target") or meta.get("target")
        source_label = _value(first, "edge_source_label") or meta.get("source_label")
        target_label = _value(first, "edge_target_label") or meta.get("target_label")

        support_docs = _unique_strings(_value(row, "support_doc") for row in vote_rows)
        opposition_docs = _unique_strings(_value(row, "opposition_doc") for row in vote_rows)
        uncertain_docs = _unique_strings(_value(row, "uncertain_doc") for row in vote_rows)
        direction = _value(first, "direction")
        reference_orientation = _clean_text(_value(first, "reference_orientation") or "forward").lower()
        pair = sorted({str(edge_source or ""), str(edge_target or "")})
        edge_forward = bool(pair and str(edge_source or "") == pair[0])
        swap = bool(support_docs or opposition_docs) and (
            edge_forward != (reference_orientation != "reverse")
        )
        relative_support_docs = opposition_docs if swap else support_docs
        relative_opposition_docs = support_docs if swap else opposition_docs
        rich_direction = _clean_text(direction)
        if rich_direction == "mixed":
            relative_direction = "mixed"
        elif rich_direction in {"support", "opposition"}:
            relative_direction = (
                {"support": "opposition", "opposition": "support"}[rich_direction]
                if swap
                else rich_direction
            )
        elif rich_direction == "uncertain":
            relative_direction = "uncertain"
        else:
            relative_direction = _relative_direction(
                direction,
                edge_source,
                edge_target,
                _edge_surfaces(surfaces, edge_source, source_label),
                _edge_surfaces(surfaces, edge_target, target_label),
            )

        vote = {
            "vote_id": vote_id,
            "direction": direction,
            "relative_direction": relative_direction,
            "documents": _int_value(_value(first, "documents")),
            "assertions": _int_value(_value(first, "assertions")),
            "rule": _value(first, "rule"),
            "doc_vote_detail": _json_value(_value(first, "doc_vote_detail_json"), {}),
            "support_doc_ids": support_docs,
            "opposition_doc_ids": opposition_docs,
            "uncertain_doc_ids": uncertain_docs,
            "relative_support_doc_ids": relative_support_docs,
            "relative_opposition_doc_ids": relative_opposition_docs,
            "forward_doc_ids": _unique_strings(_value(row, "forward_doc") for row in vote_rows),
            "reverse_doc_ids": _unique_strings(_value(row, "reverse_doc") for row in vote_rows),
            "support_votes": _int_value(_value(first, "support_votes")),
            "opposition_votes": _int_value(_value(first, "opposition_votes")),
            "direction_rule": _value(first, "direction_rule"),
            "reference_orientation": reference_orientation,
            "edge_ids": _unique_strings(_value(row, "vote_edge_id") for row in vote_rows),
            "source_label": _value(first, "vote_source_label"),
            "target_label": _value(first, "vote_target_label"),
            "provenance": _value(first, "provenance"),
        }
        by_edge.setdefault(edge_id, []).append(vote)

    answers = {}
    for edge_id, meta in edge_meta.items():
        answers[edge_id] = {
            "query": "Q3",
            "edge_id": edge_id,
            "edge_direction": f"{meta.get('source_label') or meta.get('source') or ''}->"
            f"{meta.get('target_label') or meta.get('target') or ''}",
            "votes": sorted(by_edge.get(edge_id, []), key=lambda item: item["vote_id"]),
        }
    return answers


def _q4_answers(
    rows: list[dict[str, Any]],
    edge_meta: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        edge_id = _value(row, "edge")
        decision_id = _value(row, "decision")
        if edge_id and decision_id:
            grouped.setdefault((edge_id, decision_id), []).append(row)
    by_edge: dict[str, list[dict[str, Any]]] = {}
    for (edge_id, decision_id), decision_rows in sorted(grouped.items()):
        first = decision_rows[0]
        selected = _value(first, "selected")
        dropped = _value(first, "dropped")
        rule = _value(first, "rule")
        decision_value = selected or dropped or rule or "unresolved"
        by_edge.setdefault(edge_id, []).append({
            "decision_id": decision_id,
            "kind": _value(first, "kind"),
            "rule": rule,
            "decision_value": decision_value,
            "selected": selected,
            "dropped": dropped,
            "edge_ids": _unique_strings(_value(row, "decision_edge_id") for row in decision_rows),
            "edge_direction": _edge_direction(first),
        })
    answers = {}
    for edge_id, meta in edge_meta.items():
        answers[edge_id] = {
            "query": "Q4",
            "edge_id": edge_id,
            "edge_direction": f"{meta.get('source_label') or meta.get('source') or ''}->"
            f"{meta.get('target_label') or meta.get('target') or ''}",
            "decisions": sorted(by_edge.get(edge_id, []), key=lambda item: item["decision_id"]),
        }
    return answers


def _q5_answers(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        edge_id = _value(row, "edge")
        if edge_id:
            grouped.setdefault(edge_id, []).append(row)
    answers = {}
    for edge_id, edge_rows in grouped.items():
        first = edge_rows[0]
        condition_id = _value(first, "condition")
        support_units = {}
        for row in edge_rows:
            assertion_id = _value(row, "assertion")
            if not assertion_id or assertion_id in support_units:
                continue
            support_units[assertion_id] = {
                "assertion_id": assertion_id,
                "doc_id": _value(row, "doc_id", "") or "",
                "span_id": _value(row, "span", "") or "",
                "span_text": _clean_text(_value(row, "span_text")),
            }
        if condition_id:
            condition = {
                "id": condition_id,
                "raw": _value(first, "raw", "") or "",
                "normalized": _value(first, "normalized", "") or "",
                "equivalence_key": _value(first, "equivalence_key", "") or "",
                "condition_kind": _value(first, "condition_kind", "") or "",
            }
            raw = _clean_text(condition["raw"])
            normalized = _clean_text(condition["normalized"])
            grounding = []
            for unit in sorted(
                support_units.values(),
                key=lambda item: (item["doc_id"], item["span_id"], item["assertion_id"]),
            ):
                text = _clean_text(unit["span_text"]).casefold()
                match_types = []
                if raw and raw.casefold() in text:
                    match_types.append("raw")
                if normalized and normalized.casefold() in text:
                    match_types.append("normalized")
                if match_types:
                    grounding.append({**unit, "match_types": sorted(set(match_types))})
            answers[edge_id] = {
                "query": "Q5",
                "edge_id": edge_id,
                "condition": condition,
                "has_condition": True,
                "condition_id": condition_id,
                "condition_text": raw or normalized or None,
                "grounding_status": "grounded" if grounding else "ungrounded",
                "grounding_span_ids": sorted({row["span_id"] for row in grounding if row["span_id"]}),
                "grounding_doc_ids": sorted({row["doc_id"] for row in grounding if row["doc_id"]}),
                "grounding": grounding,
            }
        else:
            answers[edge_id] = {
                "query": "Q5",
                "edge_id": edge_id,
                "condition": None,
                "has_condition": False,
                "condition_id": None,
                "condition_text": None,
                "grounding_status": "not_applicable",
                "grounding_span_ids": [],
                "grounding_doc_ids": [],
                "grounding": [],
            }
    return answers


def _q6_answers(
    rows: list[dict[str, Any]],
    edge_meta: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        edge_id = _value(row, "edge")
        if edge_id:
            grouped.setdefault(edge_id, []).append(row)
    answers = {}
    for edge_id in sorted(set(edge_meta) | set(grouped)):
        first = (grouped.get(edge_id) or [{}])[0]
        kind = _value(first, "kind")
        answers[edge_id] = {
            "query": "Q6",
            "edge_id": edge_id,
            "kind": kind,
            "verification_status": _value(first, "verification_status"),
            "semantic_support": _value(first, "semantic_support"),
            "verifier_support": _value(first, "verifier_support"),
            "evidence_grade": _value(first, "evidence_grade"),
            "promotion_status": _value(first, "promotion_status"),
            "evidence_completeness": _value(first, "evidence_completeness"),
            "support_scope": _value(first, "support_scope"),
            "verifier_support_scope": _value(first, "verifier_support_scope"),
            "evidence_contract_complete": _bool_value(_value(first, "evidence_contract_complete")),
            "evidence_contract": _json_value(_value(first, "evidence_contract_json"), {}),
            "best_window_kind": _value(first, "best_window_kind"),
            "condition_grounding": _value(first, "condition_grounding"),
            "temporal_consistency": _value(first, "temporal_consistency"),
            "fraction_consistency": _value(first, "fraction_consistency"),
            "is_hypothesis": kind in {"candidate", "unsupported_hypothesis", "hypothesis"},
            "claim_scope": _value(first, "claim_scope"),
            "provenance": _value(first, "provenance"),
            "direct_support": _value(first, "direct_support"),
            "path_support": _value(first, "path_support"),
            "verifier_direction": _value(first, "verifier_direction"),
            "verifier_recommended_kind": _value(first, "verifier_recommended_kind"),
        }
    return answers


def _cycle_decision_value(classification: str | None, removed_edge: str | None) -> str:
    classification = _clean_text(classification)
    if classification == "conditional_preserved":
        return "preserve_conditionally"
    if classification in {"removed", "removed_edge"} or removed_edge:
        return "remove_edge"
    if classification in {"preserved", "kept"}:
        return "preserve"
    return classification or "unresolved"


def _q7_answers(
    rows: list[dict[str, Any]],
    edge_meta: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        edge_id = _value(row, "edge")
        decision_id = _value(row, "decision")
        if edge_id and decision_id:
            grouped.setdefault((edge_id, decision_id), []).append(row)
    by_edge: dict[str, list[dict[str, Any]]] = {}
    for (edge_id, decision_id), decision_rows in sorted(grouped.items()):
        first = decision_rows[0]
        classification = _value(first, "classification")
        removed_edge = _value(first, "removed_edge")
        by_edge.setdefault(edge_id, []).append({
            "decision_id": decision_id,
            "classification": classification,
            "decision": _cycle_decision_value(classification, removed_edge),
            "reason": _value(first, "reason"),
            "removed_edge": removed_edge,
            "regime_dependent": _bool_value(_value(first, "regime_dependent")),
            "edge_ids": _unique_strings(_value(row, "decision_edge_id") for row in decision_rows),
            "edge_direction": _edge_direction(first),
        })
    answers = {}
    for edge_id, meta in edge_meta.items():
        answers[edge_id] = {
            "query": "Q7",
            "edge_id": edge_id,
            "edge_direction": f"{meta.get('source_label') or meta.get('source') or ''}->"
            f"{meta.get('target_label') or meta.get('target') or ''}",
            "decisions": sorted(by_edge.get(edge_id, []), key=lambda item: item["decision_id"]),
        }
    return answers


def query_artifact(input_path: str | Path) -> dict[str, Any]:
    """Execute the seven independent SPARQL queries over one RDF artifact."""
    path = Path(input_path).resolve()
    graph = Graph()
    graph.parse(str(path), format=None)

    surfaces = _load_surfaces(graph)
    q6_rows = _run_query(graph, QUERY_FILES["Q6"])
    edge_meta: dict[str, dict[str, Any]] = {}
    for row in q6_rows:
        edge_id = _value(row, "edge")
        if not edge_id:
            continue
        edge_meta.setdefault(edge_id, {
            "source": _value(row, "edge_source"),
            "target": _value(row, "edge_target"),
            "source_label": _value(row, "edge_source_label"),
            "target_label": _value(row, "edge_target_label"),
        })

    q1 = _q1_answers(_run_query(graph, QUERY_FILES["Q1"]))
    q2 = _q2_answers(_run_query(graph, QUERY_FILES["Q2"]))
    q3 = _q3_answers(_run_query(graph, QUERY_FILES["Q3"]), surfaces, edge_meta)
    q4 = _q4_answers(_run_query(graph, QUERY_FILES["Q4"]), edge_meta)
    q5 = _q5_answers(_run_query(graph, QUERY_FILES["Q5"]))
    q6 = _q6_answers(q6_rows, edge_meta)
    q7 = _q7_answers(_run_query(graph, QUERY_FILES["Q7"]), edge_meta)

    edge_ids = sorted(set(edge_meta) | set(q1) | set(q2) | set(q5) | set(q6))
    answers: dict[str, dict[str, Any]] = {}
    for edge_id in edge_ids:
        answers[edge_id] = {
            "Q1": q1.get(edge_id) or {
                "query": "Q1", "edge_id": edge_id, "assertion_count": 0,
                "assertion_ids": [], "span_ids": [], "doc_ids": [], "evidence": [],
            },
            "Q2": q2.get(edge_id) or {"query": "Q2", "edge_id": edge_id, "documents": []},
            "Q3": q3.get(edge_id) or {
                "query": "Q3", "edge_id": edge_id,
                "edge_direction": f"{edge_meta.get(edge_id, {}).get('source_label') or edge_meta.get(edge_id, {}).get('source') or ''}->"
                f"{edge_meta.get(edge_id, {}).get('target_label') or edge_meta.get(edge_id, {}).get('target') or ''}",
                "votes": [],
            },
            "Q4": q4.get(edge_id) or {
                "query": "Q4", "edge_id": edge_id,
                "edge_direction": f"{edge_meta.get(edge_id, {}).get('source_label') or edge_meta.get(edge_id, {}).get('source') or ''}->"
                f"{edge_meta.get(edge_id, {}).get('target_label') or edge_meta.get(edge_id, {}).get('target') or ''}",
                "decisions": [],
            },
            "Q5": q5.get(edge_id) or {
                "query": "Q5", "edge_id": edge_id, "condition": None,
                "has_condition": False, "condition_id": None, "condition_text": None,
                "grounding_status": "not_applicable", "grounding_span_ids": [],
                "grounding_doc_ids": [], "grounding": [],
            },
            "Q6": q6.get(edge_id) or {"query": "Q6", "edge_id": edge_id},
            "Q7": q7.get(edge_id) or {
                "query": "Q7", "edge_id": edge_id,
                "edge_direction": f"{edge_meta.get(edge_id, {}).get('source_label') or edge_meta.get(edge_id, {}).get('source') or ''}->"
                f"{edge_meta.get(edge_id, {}).get('target_label') or edge_meta.get(edge_id, {}).get('target') or ''}",
                "decisions": [],
            },
        }

    return {
        "consumer": "rdflib-sparql-independent",
        "contract_version": "D2C_13_2-sparql-q1-q7-v1",
        "input": str(path),
        "triples": len(graph),
        "query_files": {query: str(QUERY_DIR / filename) for query, filename in QUERY_FILES.items()},
        "edge_count": len(answers),
        "answers": answers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run independent SPARQL Q1-Q7 over a D2C RDF artifact")
    parser.add_argument("--input", required=True, help="Turtle or JSON-LD artifact")
    parser.add_argument("--output", default=None, help="Output JSON path")
    parser.add_argument("--edge-id", default=None, help="Limit output to one edge")
    parser.add_argument("--query", choices=list(QUERY_FILES), default=None, help="Limit output to one competency question")
    args = parser.parse_args()

    payload = query_artifact(args.input)
    if args.edge_id:
        answers = payload.get("answers", {})
        if args.edge_id not in answers:
            raise SystemExit(f"edge not found: {args.edge_id}")
        payload["answers"] = {args.edge_id: answers[args.edge_id]}
    if args.query:
        payload["answers"] = {
            edge_id: {args.query: answer[args.query]}
            for edge_id, answer in payload.get("answers", {}).items()
        }

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(json.dumps({
            "consumer": payload["consumer"],
            "input": payload["input"],
            "triples": payload["triples"],
            "edges": payload["edge_count"],
            "output": str(Path(args.output).resolve()),
        }, ensure_ascii=False, indent=2))
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
