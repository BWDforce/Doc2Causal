# -*- coding: utf-8 -*-
"""Executable promotion and evidence-preservation policies for D2C_13_2."""

from __future__ import annotations

from typing import Any

from .util import as_text


DEFAULT_PROMOTION_POLICY: dict[str, Any] = {
    "name": "balanced",
    "soft_core_tiers": ["high", "medium"],
    "support_aware_tiers": ["high", "medium"],
    "candidate_support_modes": [
        "direct_span",
        "assertion_only",
        "span_only",
        "weak_candidate",
    ],
    "exclude_low": True,
    "exclude_negated": True,
    "exclude_identity_conflict": True,
    "exclude_ambiguous_direction": True,
    "exclude_unresolved_path_summary": True,
    "require_verifier_for_provisional": True,
    "allow_partial_forward": True,
    "require_complete_evidence_contract": False,
    "require_complete_evidence_contract_for_acceptance": False,
    "exclude_candidate_conflict": True,
    "exclude_candidate_cycle": True,
    "require_canonical_support": False,
    "canonical_support_min_documents": 1,
}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return as_text(value).strip().lower() in {"1", "true", "yes", "on"}


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [as_text(item) for item in value if as_text(item)]


def normalize_policy(payload: dict | None = None) -> dict[str, Any]:
    """Return a complete policy while preserving an explicit policy name."""
    result = dict(DEFAULT_PROMOTION_POLICY)
    if isinstance(payload, dict):
        result.update(payload)
    result["name"] = as_text(result.get("name")) or "balanced"
    result["soft_core_tiers"] = _strings(result.get("soft_core_tiers")) or ["high", "medium"]
    result["support_aware_tiers"] = _strings(result.get("support_aware_tiers")) or ["high", "medium"]
    result["candidate_support_modes"] = _strings(result.get("candidate_support_modes")) or list(
        DEFAULT_PROMOTION_POLICY["candidate_support_modes"]
    )
    for key in (
        "exclude_low",
        "exclude_negated",
        "exclude_identity_conflict",
        "exclude_ambiguous_direction",
        "exclude_unresolved_path_summary",
        "require_verifier_for_provisional",
        "allow_partial_forward",
        "require_complete_evidence_contract",
        "require_complete_evidence_contract_for_acceptance",
        "exclude_candidate_conflict",
        "exclude_candidate_cycle",
        "require_canonical_support",
    ):
        result[key] = _bool(result.get(key))
    result["canonical_support_min_documents"] = max(0, int(result.get("canonical_support_min_documents", 1) or 0))
    return result


def policy_from_config(config: dict | None) -> dict[str, Any]:
    """Read the optional ``promotion`` block from config.json."""
    if not isinstance(config, dict):
        return normalize_policy()
    block = config.get("promotion") or {}
    return normalize_policy(block if isinstance(block, dict) else {})


def _is_accepted(edge: dict) -> bool:
    return (
        as_text(edge.get("verifier_support")).lower() in {"entailed", "conditional", "transitive"}
        or as_text(edge.get("verification_status")).lower() == "accepted"
    )


def _is_partial_forward(edge: dict) -> bool:
    return (
        as_text(edge.get("verifier_support")).lower() == "partial"
        and as_text(edge.get("verifier_direction")).lower() == "forward"
    )


def _has_assertion_evidence(edge: dict) -> bool:
    support = as_text(edge.get("assertion_support")).lower()
    grade = as_text(edge.get("evidence_grade") or "none")
    return support in {"entailed", "partial", "supported"} and grade in {
        "span_grounded",
        "assertion_grounded",
        "span_only",
        "derived_path",
    }


def promotion_gate(edge: dict, policy: dict | None = None) -> dict[str, Any]:
    """Classify one edge for the support-aware promotion view.

    The gate is intentionally conservative and corpus-independent. It uses
    exported semantic fields only; it never reads gold labels or document names.
    """
    p = normalize_policy(policy)
    kind = as_text(edge.get("kind"))
    grade = as_text(edge.get("evidence_grade") or "none")
    tier = as_text(edge.get("candidate_tier") or "none")
    mode = as_text(edge.get("candidate_support_mode") or edge.get("support_mode") or "")
    status = as_text(edge.get("verification_status") or "unverified").lower()
    reasons: list[str] = []

    if edge.get("source") and edge.get("source") == edge.get("target"):
        return {"eligible": False, "status": "excluded", "reason": "self_loop"}
    if p["exclude_identity_conflict"] and edge.get("identity_status") in {"conflict", "duplicate_conflict"}:
        return {"eligible": False, "status": "excluded", "reason": "identity_conflict"}
    if p["exclude_negated"] and edge.get("polarity_status") == "negated":
        return {"eligible": False, "status": "needs_review", "reason": "negated_claim"}
    if p["exclude_ambiguous_direction"] and edge.get("direction_status") in {"ambiguous", "role_conflict"}:
        return {"eligible": False, "status": "needs_review", "reason": "direction_ambiguous"}
    if edge.get("shortcut_risk") == "high":
        return {"eligible": False, "status": "needs_review", "reason": "shortcut_without_direct_span"}
    if p.get("require_complete_evidence_contract") and edge.get("evidence_contract_complete") is False:
        return {"eligible": False, "status": "needs_review", "reason": "incomplete_evidence_contract"}
    directness = edge.get("directness_class")
    if directness and directness not in {"direct_span", "direct_assertion", "span_only"}:
        if not (kind == "transitive_derived" and edge.get("path_summary_explicit_direct")):
            return {"eligible": False, "status": "needs_review", "reason": f"directness_{directness}"}
    if p["exclude_unresolved_path_summary"] and edge.get("path_summary") and not edge.get("path_summary_explicit_direct"):
        return {"eligible": False, "status": "needs_review", "reason": "path_summary_without_direct_cue"}
    if edge.get("provisional_recovery") and kind in {"direct_core", "conditional", "regime", "transitive_derived"}:
        if not _is_accepted(edge):
            return {"eligible": False, "status": "needs_review", "reason": "provisional_without_verifier"}

    if kind in {"direct_core", "conditional", "regime", "transitive_derived"}:
        if grade == "none":
            reasons.append("no_evidence")
        if kind == "transitive_derived" and not edge.get("derivation_edge_ids"):
            reasons.append("missing_derivation_path")
        if status in {"unsupported", "rejected"}:
            reasons.append("verifier_rejected")
        if reasons:
            return {"eligible": False, "status": "needs_review", "reason": ";".join(reasons)}
        return {"eligible": True, "status": "support_aware_core", "reason": "retained_with_evidence"}

    if kind != "candidate":
        return {"eligible": False, "status": "excluded", "reason": "non_promotable_kind"}

    if tier not in p["support_aware_tiers"]:
        reasons.append("tier_not_support_aware")
    if grade == "none":
        reasons.append("no_candidate_evidence")
    if mode not in p["candidate_support_modes"]:
        reasons.append("unsupported_candidate_mode")
    if status in {"unsupported", "rejected"}:
        reasons.append("verifier_rejected")
    if status == "needs_review" and not (
        (p["allow_partial_forward"] and _is_partial_forward(edge))
        or _has_assertion_evidence(edge)
    ):
        reasons.append("verification_needs_review")
    if p["require_verifier_for_provisional"] and edge.get("provisional_recovery"):
        if not (_is_accepted(edge) or (p["allow_partial_forward"] and _is_partial_forward(edge))):
            reasons.append("provisional_without_verifier")
    if reasons:
        return {"eligible": False, "status": "needs_review", "reason": ";".join(reasons)}

    # A strong pre-verifier candidate may enter the support-aware view. A
    # verifier-demoted candidate must have an explicit partial-forward signal.
    accepted = _is_accepted(edge)
    strong_pre_verifier = (
        status == "unverified"
        and tier in p["support_aware_tiers"]
        and mode in {"direct_span", "assertion_only"}
        and grade in {"span_grounded", "assertion_grounded"}
    )
    if not accepted and not strong_pre_verifier and not (p["allow_partial_forward"] and _is_partial_forward(edge)):
        return {"eligible": False, "status": "needs_review", "reason": "insufficient_promotion_signal"}
    return {"eligible": True, "status": "support_aware_core", "reason": "candidate_evidence_gate_passed"}


def annotate_promotion_gates(edges: list[dict], policy: dict | None = None) -> dict[str, int]:
    """Attach a machine-readable gate decision to every edge."""
    counts = {"support_aware_core": 0, "needs_review": 0, "excluded": 0}
    for edge in edges:
        gate = promotion_gate(edge, policy)
        edge["promotion_gate"] = gate
        edge["support_aware_eligible"] = bool(gate["eligible"])
        edge["support_aware_status"] = gate["status"]
        edge["support_aware_reason"] = gate["reason"]
        counts[gate["status"]] = counts.get(gate["status"], 0) + 1
    return counts

