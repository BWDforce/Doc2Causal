# -*- coding: utf-8 -*-
"""Q1-Q7 traceability queries over a D2C_13_2 artifact."""

from __future__ import annotations


def _edge(artifact: dict, edge_id: str) -> dict:
    for edge in artifact.get("edges", []):
        if edge.get("id") == edge_id:
            return edge
    raise KeyError(f"unknown edge: {edge_id}")


def _entity_map(artifact: dict) -> dict[str, dict]:
    return {e["id"]: e for e in artifact.get("entities", [])}


def _span_map(artifact: dict) -> dict[str, dict]:
    return {s["id"]: s for s in artifact.get("spans", [])}


def _assertion_map(artifact: dict) -> dict[str, dict]:
    return {a["id"]: a for a in artifact.get("assertions", [])}


def _document_map(artifact: dict) -> dict[str, dict]:
    return {d["id"]: d for d in artifact.get("documents", [])}


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _unique_strings(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value is not None and str(value) != ""))


def _pair_matches(edge: dict, row: dict) -> bool:
    return set(filter(None, row.get("pair") or [])) == set(filter(None, [edge.get("source"), edge.get("target")]))


def _vote_matches(edge: dict, vote: dict) -> bool:
    edge_ids = {str(value) for value in (vote.get("edge_ids") or []) if value}
    if edge_ids:
        return str(edge.get("id")) in edge_ids
    return _pair_matches(edge, vote)


def _conflict_decision_matches(edge: dict, decision: dict) -> bool:
    edge_ids = {str(value) for value in (decision.get("edge_ids") or []) if value}
    if edge_ids:
        return str(edge.get("id")) in edge_ids
    return _pair_matches(edge, decision)


def _cycle_decision_matches(edge: dict, decision: dict) -> bool:
    edge_ids = {str(value) for value in (decision.get("edge_ids") or []) if value}
    if edge_ids:
        return str(edge.get("id")) in edge_ids
    return _pair_matches(edge, decision) or decision.get("removed_edge") == edge.get("id")


def _edge_direction(edge: dict) -> str:
    source = edge.get("source_name") or edge.get("source") or ""
    target = edge.get("target_name") or edge.get("target") or ""
    return f"{source}->{target}"


def _surface_key(value: object) -> str:
    import re
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


def _entity_surfaces(artifact: dict, edge: dict) -> tuple[list[str], list[str]]:
    entities = _entity_map(artifact)
    def surfaces(entity_id: object, fallback: object) -> list[str]:
        entity = entities.get(str(entity_id), {})
        values = [fallback, entity.get("label")]
        values.extend(entity.get("aliases") or [])
        return [str(value) for value in values if _clean_text(value)]
    return surfaces(edge.get("source"), edge.get("source_name")), surfaces(edge.get("target"), edge.get("target_name"))


def _relative_direction(artifact: dict, edge: dict, direction: object) -> str:
    parts = [part.strip() for part in str(direction or "").split("->", 1)]
    if len(parts) != 2:
        return "other"
    source_surfaces, target_surfaces = _entity_surfaces(artifact, edge)
    left, right = parts
    if any(_surface_match(left, value) for value in source_surfaces) and any(_surface_match(right, value) for value in target_surfaces):
        return "forward"
    if any(_surface_match(left, value) for value in target_surfaces) and any(_surface_match(right, value) for value in source_surfaces):
        return "reverse"
    return "other"


def _support_units(artifact: dict, edge: dict) -> list[dict]:
    amap, smap = _assertion_map(artifact), _span_map(artifact)
    rows = []
    seen = set()
    for aid in edge.get("support_assertion_ids") or []:
        aid = str(aid)
        if not aid or aid in seen:
            continue
        seen.add(aid)
        assertion = amap.get(aid, {})
        span = smap.get(assertion.get("span_id"), {})
        text = _clean_text(span.get("text"))
        mechanism = _clean_text(assertion.get("mechanism"))
        rows.append({
            "assertion_id": aid,
            "doc_id": str(assertion.get("doc_id") or ""),
            "channel": assertion.get("channel"),
            "span_id": str(assertion.get("span_id") or ""),
            "span_text": text,
            "span_text_missing": not bool(text),
            "assertion_text": mechanism,
            "sign": assertion.get("sign"),
            "rung": assertion.get("rung"),
            "mechanism": assertion.get("mechanism"),
        })
    return sorted(rows, key=lambda row: (row["doc_id"], row["span_id"], row["assertion_id"]))


def q1_evidence(artifact: dict, edge_id: str) -> dict:
    edge = _edge(artifact, edge_id)
    amap, smap = _assertion_map(artifact), _span_map(artifact)
    raw_ids = [str(aid) for aid in (edge.get("support_assertion_ids") or []) if aid]
    empty_span_ids = []
    for aid in dict.fromkeys(raw_ids):
        assertion = amap.get(aid, {})
        span = smap.get(assertion.get("span_id"), {})
        if not _clean_text(span.get("text")):
            empty_span_ids.append(aid)
    evidence = _support_units(artifact, edge)
    support_scope = str(edge.get("support_scope") or edge.get("evidence_completeness") or "")
    if support_scope != "assertion_only":
        # In span/context modes, keep Q1 focused on evidence units that have
        # actual span text. Assertion-only rows remain available for
        # assertion-only edges and are still reported through empty_span_ids.
        evidence = [row for row in evidence if not row.get("span_text_missing")]
    assertion_ids = sorted({row["assertion_id"] for row in evidence})
    span_ids = sorted({row["span_id"] for row in evidence if row["span_id"]})
    doc_ids = sorted({row["doc_id"] for row in evidence if row["doc_id"]})
    duplicate_ids = sorted({aid for aid in raw_ids if raw_ids.count(aid) > 1})
    return {
        "query": "Q1",
        "edge_id": edge_id,
        "assertion_count": len(assertion_ids),
        "assertion_ids": assertion_ids,
        "span_ids": span_ids,
        "doc_ids": doc_ids,
        "evidence": evidence,
        "evidence_completeness": edge.get("evidence_completeness"),
        "support_scope": edge.get("support_scope"),
        "verifier_support_scope": edge.get("verifier_support_scope"),
        "condition_grounding": edge.get("condition_grounding"),
        "evidence_coverage": edge.get("evidence_coverage") or {},
        "evidence_contract_complete": edge.get("evidence_contract_complete"),
        "evidence_contract": edge.get("evidence_contract") or {},
        "best_evidence_window_id": edge.get("best_evidence_window_id"),
        "best_window_kind": edge.get("best_window_kind"),
        "best_window_source": edge.get("best_window_source"),
        "best_window_width": edge.get("best_window_width"),
        "evidence_exact_match": edge.get("evidence_exact_match"),
        "context_window_complete": edge.get("context_window_complete"),
        "diagnostics": {
            "raw_support_count": len(raw_ids),
            "unique_raw_support_count": len(set(raw_ids)),
            "duplicate_support_ids": duplicate_ids,
            "empty_span_assertion_ids": sorted(empty_span_ids),
        },
    }


def q2_supporting_documents(artifact: dict, edge_id: str) -> dict:
    edge = _edge(artifact, edge_id)
    docs = _document_map(artifact)
    return {
        "query": "Q2",
        "edge_id": edge_id,
        "documents": [
            {"doc_id": doc_id, **(docs.get(stable_document_id(doc_id), {}) or {})}
            for doc_id in dict.fromkeys(edge.get("support_doc_ids") or [])
        ],
    }


def stable_document_id(doc_id: str) -> str:
    from .util import stable_urn
    return stable_urn("document", doc_id)


def q3_votes(artifact: dict, edge_id: str) -> dict:
    edge = _edge(artifact, edge_id)
    votes = []
    pair = sorted({str(value) for value in (edge.get("source"), edge.get("target")) if value})
    edge_forward = bool(pair and str(edge.get("source")) == pair[0])
    for vote in artifact.get("votes", []):
        if not _vote_matches(edge, vote):
            continue
        detail = vote.get("doc_vote_detail") or {}
        support_docs = sorted(str(value) for value in (vote.get("support_doc_ids") or []) if value)
        opposition_docs = sorted(str(value) for value in (vote.get("opposition_doc_ids") or []) if value)
        uncertain_docs = sorted(str(value) for value in (vote.get("uncertain_doc_ids") or []) if value)
        reference_orientation = _clean_text(vote.get("reference_orientation") or "forward").lower()
        swap = bool(support_docs or opposition_docs) and (edge_forward != (reference_orientation != "reverse"))
        relative_support_docs = opposition_docs if swap else support_docs
        relative_opposition_docs = support_docs if swap else opposition_docs
        rich_direction = _clean_text(vote.get("direction"))
        if rich_direction == "mixed":
            relative_direction = "mixed"
        elif rich_direction in {"support", "opposition"}:
            relative_direction = ({"support": "opposition", "opposition": "support"}[rich_direction] if swap else rich_direction)
        elif rich_direction == "uncertain":
            relative_direction = "uncertain"
        else:
            relative_direction = _relative_direction(artifact, edge, vote.get("direction"))
        votes.append({
            "vote_id": str(vote.get("id") or ""),
            "direction": rich_direction,
            "relative_direction": relative_direction,
            "documents": int(vote.get("documents") or len(set(support_docs) | set(opposition_docs) | set(uncertain_docs))),
            "assertions": int(vote.get("assertions") or 0),
            "rule": _clean_text(vote.get("rule") or vote.get("direction_rule")),
            "doc_vote_detail": {str(key): str(value) for key, value in sorted(detail.items())} if isinstance(detail, dict) else {},
            "support_doc_ids": support_docs,
            "opposition_doc_ids": opposition_docs,
            "uncertain_doc_ids": uncertain_docs,
            "relative_support_doc_ids": relative_support_docs,
            "relative_opposition_doc_ids": relative_opposition_docs,
            "forward_doc_ids": sorted(str(value) for value in (vote.get("forward_doc_ids") or []) if value),
            "reverse_doc_ids": sorted(str(value) for value in (vote.get("reverse_doc_ids") or []) if value),
            "support_votes": int(vote.get("support_votes") or len(support_docs)),
            "opposition_votes": int(vote.get("opposition_votes") or len(opposition_docs)),
            "direction_rule": _clean_text(vote.get("direction_rule") or vote.get("rule")),
            "reference_orientation": reference_orientation,
            "edge_ids": sorted(str(value) for value in (vote.get("edge_ids") or []) if value),
            "source_label": _clean_text(vote.get("source_label")),
            "target_label": _clean_text(vote.get("target_label")),
            "provenance": _clean_text(vote.get("provenance")),
        })
    return {
        "query": "Q3",
        "edge_id": edge_id,
        "edge_direction": _edge_direction(edge),
        "votes": sorted(votes, key=lambda row: row["vote_id"]),
    }


def _conflict_value(decision: dict) -> str:
    if decision.get("selected"):
        return _clean_text(decision.get("selected"))
    if decision.get("dropped"):
        return _clean_text(decision.get("dropped"))
    return _clean_text(decision.get("rule")) or "unresolved"


def _competing_directions(decision: dict) -> list[str]:
    sources = decision.get("sources") or {}
    if isinstance(sources, dict):
        values = sources.keys()
    elif isinstance(sources, list):
        values = sources
    else:
        values = []
    return sorted(_unique_strings(values))


def q4_conflict_decision(artifact: dict, edge_id: str) -> dict:
    edge = _edge(artifact, edge_id)
    decisions = []
    for decision in artifact.get("conflict_decisions", []):
        if not _conflict_decision_matches(edge, decision):
            continue
        decisions.append({
            "decision_id": str(decision.get("id") or ""),
            "kind": _clean_text(decision.get("kind")),
            "rule": _clean_text(decision.get("rule")),
            "decision_value": _conflict_value(decision),
            "selected": decision.get("selected"),
            "dropped": decision.get("dropped"),
            "vote_ids": sorted(_unique_strings(decision.get("vote_ids") or [])),
            "competing_directions": _competing_directions(decision),
            "edge_ids": sorted(str(value) for value in (decision.get("edge_ids") or []) if value),
            "candidate_edge_ids": sorted(str(value) for value in (decision.get("candidate_edge_ids") or []) if value),
            "edge_direction": _edge_direction(edge),
        })
    return {
        "query": "Q4",
        "edge_id": edge_id,
        "edge_direction": _edge_direction(edge),
        "decisions": sorted(decisions, key=lambda row: row["decision_id"]),
    }


def _condition_grounding(artifact: dict, edge: dict) -> dict:
    cid = edge.get("condition_id")
    condition = next((c for c in artifact.get("conditions", []) if c.get("id") == cid), None)
    if not cid:
        return {
            "has_condition": False,
            "condition_id": None,
            "condition_text": None,
            "grounding_status": "not_applicable",
            "grounding_span_ids": [],
            "grounding_doc_ids": [],
            "grounding": [],
        }
    raw = _clean_text((condition or {}).get("raw"))
    normalized = _clean_text((condition or {}).get("normalized"))
    needle_raw = raw.casefold()
    needle_norm = normalized.casefold()
    grounding = []
    for unit in _support_units(artifact, edge):
        text = unit["span_text"].casefold()
        match_types = []
        if needle_raw and needle_raw in text:
            match_types.append("raw")
        if needle_norm and needle_norm in text:
            match_types.append("normalized")
        if match_types:
            grounding.append({
                "assertion_id": unit["assertion_id"],
                "span_id": unit["span_id"],
                "doc_id": unit["doc_id"],
                "match_types": sorted(set(match_types)),
                "span_text": unit["span_text"],
            })
    return {
        "has_condition": True,
        "condition_id": str(cid),
        "condition_text": raw or normalized or None,
        "grounding_status": "grounded" if grounding else "ungrounded",
        "grounding_span_ids": sorted({row["span_id"] for row in grounding if row["span_id"]}),
        "grounding_doc_ids": sorted({row["doc_id"] for row in grounding if row["doc_id"]}),
        "grounding": sorted(grounding, key=lambda row: (row["doc_id"], row["span_id"], row["assertion_id"])),
    }


def q5_condition(artifact: dict, edge_id: str) -> dict:
    edge = _edge(artifact, edge_id)
    cid = edge.get("condition_id")
    condition = next((c for c in artifact.get("conditions", []) if c.get("id") == cid), None)
    return {"query": "Q5", "edge_id": edge_id, "condition": condition, **_condition_grounding(artifact, edge)}


def q6_hypothesis_status(artifact: dict, edge_id: str) -> dict:
    edge = _edge(artifact, edge_id)
    linked = []
    for hypothesis in artifact.get("hypotheses", []):
        if hypothesis.get("source") == edge.get("source") and hypothesis.get("target") == edge.get("target"):
            linked.append(hypothesis)
    return {
        "query": "Q6",
        "edge_id": edge_id,
        "kind": edge.get("kind"),
        "verification_status": edge.get("verification_status"),
        "semantic_support": edge.get("semantic_support"),
        "verifier_support": edge.get("verifier_support"),
        "evidence_grade": edge.get("evidence_grade"),
        "promotion_status": edge.get("promotion_status"),
        "evidence_completeness": edge.get("evidence_completeness"),
        "support_scope": edge.get("support_scope"),
        "verifier_support_scope": edge.get("verifier_support_scope"),
        "evidence_contract_complete": edge.get("evidence_contract_complete"),
        "evidence_contract": edge.get("evidence_contract") or {},
        "best_window_kind": edge.get("best_window_kind"),
        "condition_grounding": edge.get("condition_grounding"),
        "temporal_consistency": edge.get("temporal_consistency"),
        "fraction_consistency": edge.get("fraction_consistency"),
        "is_hypothesis": edge.get("kind") in {"candidate", "unsupported_hypothesis", "hypothesis"},
        "claim_scope": edge.get("claim_scope"),
        "provenance": edge.get("provenance"),
        "direct_support": edge.get("direct_support"),
        "path_support": edge.get("path_support"),
        "verifier_direction": edge.get("verifier_direction"),
        "verifier_recommended_kind": edge.get("verifier_recommended_kind"),
        "hypothesis_records": linked,
    }


def _cycle_decision_value(decision: dict) -> str:
    classification = _clean_text(decision.get("classification"))
    if classification == "conditional_preserved":
        return "preserve_conditionally"
    if classification in {"removed", "removed_edge"} or decision.get("removed_edge"):
        return "remove_edge"
    if classification in {"preserved", "kept"}:
        return "preserve"
    return classification or "unresolved"


def q7_cycle_decision(artifact: dict, edge_id: str) -> dict:
    edge = _edge(artifact, edge_id)
    decisions = []
    for decision in artifact.get("cycle_decisions", []):
        if not _cycle_decision_matches(edge, decision):
            continue
        decisions.append({
            "decision_id": str(decision.get("id") or ""),
            "classification": _clean_text(decision.get("classification")),
            "decision": _cycle_decision_value(decision),
            "reason": _clean_text(decision.get("reason")),
            "removed_edge": decision.get("removed_edge"),
            "regime_dependent": bool(decision.get("regime_dependent")),
            "edge_ids": sorted(str(value) for value in (decision.get("edge_ids") or []) if value),
            "edge_direction": _edge_direction(edge),
        })
    return {
        "query": "Q7",
        "edge_id": edge_id,
        "edge_direction": _edge_direction(edge),
        "decisions": sorted(decisions, key=lambda row: row["decision_id"]),
    }


QUERY_FUNCTIONS = {
    "Q1": q1_evidence,
    "Q2": q2_supporting_documents,
    "Q3": q3_votes,
    "Q4": q4_conflict_decision,
    "Q5": q5_condition,
    "Q6": q6_hypothesis_status,
    "Q7": q7_cycle_decision,
}


def answer(artifact: dict, query_id: str, edge_id: str) -> dict:
    if query_id not in QUERY_FUNCTIONS:
        raise ValueError(f"unknown query: {query_id}")
    return QUERY_FUNCTIONS[query_id](artifact, edge_id)


def _q1_expected_nonempty(artifact: dict, edge: dict) -> bool:
    """Whether Q1 should return a visible evidence unit for this edge.

    Candidate-only edges may carry assertion IDs whose spans are empty.  They
    are still valid audit records, but Q1 should not claim that a span-backed
    evidence unit exists.  Assertion-only edges are the explicit exception:
    their assertion text itself is the evidence unit.
    """
    if not edge.get("support_assertion_ids"):
        return False
    if str(edge.get("support_scope") or edge.get("evidence_completeness") or "") == "assertion_only":
        return True
    assertions = _assertion_map(artifact)
    spans = _span_map(artifact)
    for assertion_id in edge.get("support_assertion_ids") or []:
        assertion = assertions.get(str(assertion_id), {})
        span = spans.get(str(assertion.get("span_id") or ""), {})
        if _clean_text(span.get("text")):
            return True
    return False


def generate_query_cases(artifact: dict, limit: int = 30) -> list[dict]:
    edges = artifact.get("edges", [])
    selected: list[dict] = []
    per_query: dict[str, int] = {}
    ranked = sorted(
        edges,
        key=lambda e: (
            len(e.get("support_assertion_ids") or []),
            bool(e.get("condition_id")),
            bool(e.get("derivation_edge_ids")),
            e.get("kind") != "direct_core",
        ),
        reverse=True,
    )

    def add(query: str, edge: dict, expected_nonempty: bool) -> None:
        if per_query.get(query, 0) >= max(1, limit):
            return
        selected.append({
            "query": query,
            "edge_id": edge["id"],
            "expected_nonempty": expected_nonempty,
        })
        per_query[query] = per_query.get(query, 0) + 1

    for edge in ranked:
        add("Q1", edge, _q1_expected_nonempty(artifact, edge))
        if edge.get("support_doc_ids"):
            add("Q2", edge, True)
        if edge.get("condition_id"):
            add("Q5", edge, True)
        add("Q6", edge, True)

    for edge in ranked:
        pair = {edge.get("source"), edge.get("target")}
        if any(_vote_matches(edge, vote) for vote in artifact.get("votes", [])):
            add("Q3", edge, True)
        if any(_conflict_decision_matches(edge, decision) for decision in artifact.get("conflict_decisions", [])):
            add("Q4", edge, True)
        if any(_cycle_decision_matches(edge, decision) for decision in artifact.get("cycle_decisions", [])):
            add("Q7", edge, True)
    return selected


def execute_query_cases(artifact: dict, cases: list[dict]) -> dict:
    results = []
    for case in cases:
        try:
            answer_payload = answer(artifact, case["query"], case["edge_id"])
            nonempty_key = {
                "Q1": "evidence", "Q2": "documents", "Q3": "votes", "Q4": "decisions",
                "Q5": "condition", "Q6": "kind", "Q7": "decisions",
            }[case["query"]]
            value = answer_payload.get(nonempty_key)
            actual_nonempty = bool(value)
            ok = (not case.get("expected_nonempty")) or actual_nonempty
            results.append({"case": case, "ok": ok, "answer": answer_payload})
        except Exception as exc:  # noqa: BLE001 - query report should preserve failures
            results.append({"case": case, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


