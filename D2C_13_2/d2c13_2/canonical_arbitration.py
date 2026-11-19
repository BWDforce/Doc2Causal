# -*- coding: utf-8 -*-
"""Canonical pair replay and conservative arbitration views for D2C_13_2.

This module consumes the final entity/alias mapping in a compiled artifact. It
recomputes support for unordered canonical pairs, marks candidate conflicts and
candidate cycles, and exposes a reversible arbitration gate. It does not remove
edges or overwrite the legacy F1 views.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .candidate_risk import annotate_candidate_risks
from .constants import HYPOTHESIS_KINDS
from .util import as_text, stable_urn


DEFAULT_ARBITRATION_CONFIG: dict[str, Any] = {
    "enabled": True,
    "vote_mode": "canonical_document",
    "relation_role_guard": True,
    "candidate_conflict_policy": "needs_review",
    "candidate_cycle_policy": "needs_review",
    "require_canonical_support": False,
    "canonical_support_min_documents": 1,
    "canonical_support_require_direction": True,
    "max_cycle_length": 12,
    "max_cycles": 200,
}

_VOTE_MODES = {"assertion", "document", "canonical_document"}
_RISK_POLICIES = {"needs_review", "exclude", "keep"}
_CANDIDATE_RISK_DECISION_SOURCE = "canonical_arbitration.candidate_risk"


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return as_text(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_arbitration_config(payload: dict | None = None) -> dict[str, Any]:
    result = dict(DEFAULT_ARBITRATION_CONFIG)
    if isinstance(payload, dict):
        result.update(payload)
    result["enabled"] = _bool(result.get("enabled", True))
    vote_mode = as_text(result.get("vote_mode") or "canonical_document").strip().lower()
    result["vote_mode"] = vote_mode if vote_mode in _VOTE_MODES else "canonical_document"
    result["relation_role_guard"] = _bool(result.get("relation_role_guard", True))
    for key in ("candidate_conflict_policy", "candidate_cycle_policy"):
        value = as_text(result.get(key) or "needs_review").strip().lower()
        result[key] = value if value in _RISK_POLICIES else "needs_review"
    result["require_canonical_support"] = _bool(result.get("require_canonical_support", False))
    result["canonical_support_require_direction"] = _bool(
        result.get("canonical_support_require_direction", True)
    )
    result["canonical_support_min_documents"] = max(
        0, int(result.get("canonical_support_min_documents", 1) or 0)
    )
    result["max_cycle_length"] = max(3, int(result.get("max_cycle_length", 12) or 12))
    result["max_cycles"] = max(1, int(result.get("max_cycles", 200) or 200))
    return result


def _entity_index(artifact: dict) -> tuple[dict[str, dict], dict[str, str]]:
    entities = {as_text(entity.get("id")): entity for entity in artifact.get("entities", []) if entity.get("id")}
    aliases: dict[str, str] = {}
    for entity_id, entity in entities.items():
        values = [entity.get("label"), entity_id, *(entity.get("aliases") or [])]
        for value in values:
            text = as_text(value).strip()
            if text:
                aliases[text] = entity_id
                aliases[text.casefold()] = entity_id
    return entities, aliases


def _canonical_id(value: Any, aliases: dict[str, str]) -> str:
    text = as_text(value).strip()
    return aliases.get(text) or aliases.get(text.casefold()) or text


def _doc_id(assertion: dict) -> str:
    return as_text(assertion.get("doc_id") or assertion.get("document_id") or "")


def replay_canonical_pairs(artifact: dict) -> dict:
    """Recompute canonical-pair support from final assertions and alias IDs."""
    entities, aliases = _entity_index(artifact)
    records: dict[tuple[str, str], dict] = {}
    for assertion in artifact.get("assertions", []):
        source = _canonical_id(assertion.get("source"), aliases)
        target = _canonical_id(assertion.get("target"), aliases)
        if not source or not target or source == target:
            continue
        pair = tuple(sorted((source, target)))
        record = records.setdefault(pair, {
            "pair_id": stable_urn("canonical-pair", pair[0], pair[1]),
            "pair": list(pair),
            "support_doc_ids": set(),
            "support_assertion_ids": set(),
            "directions": {},
        })
        direction = f"{source}->{target}"
        direction_row = record["directions"].setdefault(direction, {
            "support_doc_ids": set(),
            "support_assertion_ids": set(),
        })
        doc_id = _doc_id(assertion)
        if doc_id:
            record["support_doc_ids"].add(doc_id)
            direction_row["support_doc_ids"].add(doc_id)
        assertion_id = as_text(assertion.get("id"))
        if assertion_id:
            record["support_assertion_ids"].add(assertion_id)
            direction_row["support_assertion_ids"].add(assertion_id)

    for edge in artifact.get("edges", []):
        source = _canonical_id(edge.get("source"), aliases)
        target = _canonical_id(edge.get("target"), aliases)
        if not source or not target or source == target:
            # Edges that cannot form a canonical pair (for example, a
            # residual self-loop after alias projection) still need a stable
            # support tier for diagnostics and downstream serialization.
            edge["canonical_support_tier"] = "none"
            continue
        pair = tuple(sorted((source, target)))
        record = records.get(pair)
        fallback_docs = {
            as_text(value) for value in (
                list(edge.get("support_doc_ids") or []) + list(edge.get("candidate_doc_ids") or [])
            ) if as_text(value)
        }
        fallback_assertions = {
            as_text(value) for value in (
                list(edge.get("support_assertion_ids") or []) + list(edge.get("candidate_assertion_ids") or [])
            ) if as_text(value)
        }
        pair_docs = set(record.get("support_doc_ids") or []) if record else set()
        pair_assertions = set(record.get("support_assertion_ids") or []) if record else set()
        if not pair_docs:
            pair_docs = fallback_docs
        if not pair_assertions:
            pair_assertions = fallback_assertions
        direction = f"{source}->{target}"
        direction_row = (record or {}).get("directions", {}).get(direction, {})
        direction_docs = set(direction_row.get("support_doc_ids") or [])
        direction_assertions = set(direction_row.get("support_assertion_ids") or [])
        if not direction_docs and fallback_docs:
            direction_docs = fallback_docs
        if not direction_assertions and fallback_assertions:
            direction_assertions = fallback_assertions
        edge["canonical_pair_id"] = (record or {}).get("pair_id") or stable_urn("canonical-pair", pair[0], pair[1])
        edge["canonical_pair"] = list(pair)
        edge["canonical_support_doc_ids"] = sorted(pair_docs)
        edge["canonical_support_assertion_ids"] = sorted(pair_assertions)
        edge["canonical_direction_doc_ids"] = sorted(direction_docs)
        edge["canonical_direction_assertion_ids"] = sorted(direction_assertions)
        edge["canonical_support_doc_count"] = len(pair_docs)
        edge["canonical_support_assertion_count"] = len(pair_assertions)
        edge["canonical_direction_doc_count"] = len(direction_docs)
        edge["canonical_multi_document_support"] = len(pair_docs) > 1
        if len(pair_docs) > 1:
            support_tier = "multi_document"
        elif pair_docs:
            support_tier = "single_document"
        elif pair_assertions:
            support_tier = "assertion_only"
        else:
            support_tier = "none"
        edge["canonical_support_tier"] = support_tier
        edge["canonical_opposition_doc_ids"] = sorted(
            doc_id
            for other_direction, row in ((record or {}).get("directions") or {}).items()
            if other_direction != direction
            for doc_id in (row.get("support_doc_ids") or [])
        )

    serializable = []
    for pair, record in sorted(records.items()):
        serializable.append({
            "pair_id": record["pair_id"],
            "pair": record["pair"],
            "support_doc_ids": sorted(record["support_doc_ids"]),
            "support_assertion_ids": sorted(record["support_assertion_ids"]),
            "multi_document": len(record["support_doc_ids"]) > 1,
            "directions": {
                direction: {
                    "support_doc_ids": sorted(row["support_doc_ids"]),
                    "support_assertion_ids": sorted(row["support_assertion_ids"]),
                }
                for direction, row in sorted(record["directions"].items())
            },
        })
    counts = Counter(
        edge.get("canonical_support_tier") or "none"
        for edge in artifact.get("edges", [])
    )
    return {
        "canonical_pairs": serializable,
        "counts": {
            "pairs": len(serializable),
            "multi_document_pairs": sum(row["multi_document"] for row in serializable),
            "edge_support_tiers": dict(sorted(counts.items())),
        },
    }


def arbitration_gate(edge: dict, policy: dict | None = None) -> dict[str, Any]:
    p = normalize_arbitration_config(policy)
    reasons: list[str] = []
    flags = set(edge.get("candidate_risk_flags") or [])
    if p["candidate_conflict_policy"] != "keep" and "candidate_conflict" in flags:
        reasons.append("candidate_conflict")
    if p["candidate_cycle_policy"] != "keep" and "candidate_cycle" in flags:
        reasons.append("candidate_cycle")
    if p["require_canonical_support"]:
        count = int(edge.get("canonical_support_doc_count") or 0)
        if count < int(p["canonical_support_min_documents"]):
            reasons.append("insufficient_canonical_support")
        if p["canonical_support_require_direction"] and int(edge.get("canonical_direction_doc_count") or 0) < int(p["canonical_support_min_documents"]):
            reasons.append("insufficient_direction_support")
    if reasons:
        return {"eligible": False, "status": "needs_review", "reason": ";".join(reasons)}
    return {"eligible": True, "status": "canonical_safe", "reason": "arbitration_clear"}


def annotate_arbitration_gates(artifact: dict, policy: dict | None = None) -> dict[str, int]:
    p = normalize_arbitration_config(policy)
    counts = Counter()
    for edge in artifact.get("edges", []):
        gate = arbitration_gate(edge, p)
        edge["arbitration_gate"] = gate
        edge["arbitration_eligible"] = bool(gate["eligible"])
        edge["arbitration_status"] = gate["status"]
        edge["arbitration_reason"] = gate["reason"]
        counts[gate["status"]] += 1
    return dict(counts)


def _replace_projected_risk_decisions(artifact: dict, key: str, rows: list[dict]) -> int:
    """Replace only decisions projected by this arbitration layer.

    Older artifacts may already contain the same deterministic IDs without the
    provenance marker, so IDs are also de-duplicated. Unrelated compiler or
    semantics decisions are preserved verbatim.
    """
    kept = [
        row for row in (artifact.get(key) or [])
        if not (
            row.get("provenance") == _CANDIDATE_RISK_DECISION_SOURCE
            or row.get("decision_source") == _CANDIDATE_RISK_DECISION_SOURCE
        )
    ]
    seen = {as_text(row.get("id")) for row in kept if row.get("id")}
    added = 0
    for row in rows:
        decision_id = as_text(row.get("id"))
        if decision_id and decision_id in seen:
            continue
        kept.append(row)
        if decision_id:
            seen.add(decision_id)
        added += 1
    artifact[key] = kept
    return added


def project_candidate_risk_decisions(artifact: dict, risk: dict) -> dict[str, int]:
    """Project candidate risks into the Q4/Q7 decision arrays.

    The projection is F1-neutral: it does not change edge kind, direction,
    promotion, or any core view. Q4/Q7 remain simple artifact queries, while
    JSON and RDF consumers continue to use the established decision shapes.
    """
    edge_by_id = {
        as_text(edge.get("id")): edge
        for edge in artifact.get("edges", [])
        if edge.get("id")
    }

    conflict_rows: list[dict] = []
    for conflict in risk.get("conflicts", []) or []:
        pair = sorted({as_text(value) for value in (conflict.get("pair") or []) if as_text(value)})
        if len(pair) != 2:
            continue
        edge_ids = sorted({as_text(value) for value in (conflict.get("edge_ids") or []) if as_text(value)})
        candidate_edge_ids = sorted({
            as_text(value) for value in (conflict.get("candidate_edge_ids") or []) if as_text(value)
        })
        conflict_rows.append({
            "id": stable_urn("conflict", "candidate-risk", *pair),
            "kind": "candidate_conflict",
            "pair": pair,
            "selected": None,
            "dropped": None,
            "rule": "candidate_conflict_needs_review",
            "vote_ids": [],
            "sources": [],
            "edge_ids": edge_ids,
            "candidate_edge_ids": candidate_edge_ids,
            "decision": "needs_review",
            "scope": "candidate",
            "provenance": _CANDIDATE_RISK_DECISION_SOURCE,
            "decision_source": _CANDIDATE_RISK_DECISION_SOURCE,
        })

    cycle_rows: list[dict] = []
    for cycle in risk.get("cycles", []) or []:
        cycle_id = as_text(cycle.get("cycle_id"))
        classification = as_text(cycle.get("classification")) or "candidate_cycle"
        edge_ids = sorted({
            as_text(value)
            for value in (cycle.get("edge_ids") or [])
            if as_text(value) and as_text(value) in edge_by_id
        })
        if not cycle_id or not edge_ids:
            continue
        cycle_edges = [edge_by_id[edge_id] for edge_id in edge_ids]
        cycle_rows.append({
            "id": stable_urn("cycle", "candidate-risk", cycle_id),
            "classification": classification,
            "pair": [],
            "removed_edge": None,
            "regime_dependent": bool(
                classification.startswith("regime")
                or any(
                    edge.get("regime_dependent")
                    or as_text(edge.get("kind")) in {"regime", "conditional"}
                    for edge in cycle_edges
                )
            ),
            "reason": f"{classification} retained for review; no edge removed",
            "quality": {"cycle_id": cycle_id, "edge_ids": edge_ids},
            "rule": "candidate_cycle_needs_review",
            "cycle_id": cycle_id,
            "edge_ids": edge_ids,
            "provenance": _CANDIDATE_RISK_DECISION_SOURCE,
            "decision_source": _CANDIDATE_RISK_DECISION_SOURCE,
        })

    return {
        "conflict_decisions": _replace_projected_risk_decisions(
            artifact, "conflict_decisions", conflict_rows
        ),
        "cycle_decisions": _replace_projected_risk_decisions(
            artifact, "cycle_decisions", cycle_rows
        ),
    }


def apply_canonical_arbitration(artifact: dict, config: dict | None = None) -> dict:
    """Apply replay and risk annotations without deleting or retyping edges."""
    cfg = normalize_arbitration_config(config)
    if not cfg["enabled"]:
        artifact["canonical_arbitration_config"] = cfg
        _replace_projected_risk_decisions(artifact, "conflict_decisions", [])
        _replace_projected_risk_decisions(artifact, "cycle_decisions", [])
        result = {"enabled": False, "version": "D2C_13_2"}
        artifact.setdefault("diagnostics", {})["canonical_arbitration"] = result
        return result
    artifact["canonical_arbitration_config"] = cfg
    replay = replay_canonical_pairs(artifact)
    risk = annotate_candidate_risks(
        artifact,
        candidate_conflict_policy=cfg["candidate_conflict_policy"],
        candidate_cycle_policy=cfg["candidate_cycle_policy"],
        max_cycles=cfg["max_cycles"],
        max_cycle_length=cfg["max_cycle_length"],
    )
    projected = project_candidate_risk_decisions(artifact, risk)
    gate_counts = annotate_arbitration_gates(artifact, cfg)
    result = {
        "enabled": True,
        "version": "D2C_13_2",
        "vote_mode": cfg["vote_mode"],
        "relation_role_guard": cfg["relation_role_guard"],
        "require_canonical_support": cfg["require_canonical_support"],
        "replay": replay["counts"],
        "candidate_risk": {key: value for key, value in risk.items() if key not in {"conflicts", "cycles"}},
        "query_decision_projection": projected,
        "arbitration_status_counts": gate_counts,
        "candidate_conflicts": risk["conflicts"],
        "candidate_cycles": risk["cycles"],
    }
    artifact["canonical_pair_replay"] = replay
    artifact.setdefault("diagnostics", {})["canonical_arbitration"] = result
    return result
