# -*- coding: utf-8 -*-
"""Typed edge semantics, chain classification/repair, and verifier handoff."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any

import networkx as nx

from .constants import CONDITIONAL_CUES, CORE_EDGE_KINDS, DIRECT_CUES, EDGE_KINDS, HYPOTHESIS_KINDS, RETAINED_EDGE_KINDS
from .canonical_arbitration import arbitration_gate, normalize_arbitration_config
from .evidence_completeness import has_complete_evidence_contract, has_complete_text_contract, is_context_assisted_core, is_exact_support_core
from .policy import annotate_promotion_gates, normalize_policy
from .util import as_list, as_text, norm_text, sha1, stable_urn, truthy


NEGATION_TOKENS = {
    "non", "not", "no", "without", "inactive", "absent", "abnormal",
    "failed", "invalid", "disabled", "off", "false",
}
STATE_TOKENS = {
    "active", "present", "normal", "ok", "valid", "enabled", "on",
    "true", "connected", "healthy", "available",
}


def _state_signature(text: str) -> dict:
    tokens = set(re.findall(r"[a-z0-9]+", as_text(text).lower()))
    return {
        "negation": bool(tokens & NEGATION_TOKENS),
        "state_terms": sorted(tokens & STATE_TOKENS),
    }


def _identity_conflict(left: str, right: str) -> bool:
    left_text, right_text = as_text(left), as_text(right)
    left_times = set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", left_text))
    right_times = set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", right_text))
    if left_times and right_times and left_times != right_times:
        return True
    left_nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", left_text))
    right_nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", right_text))
    if left_nums and right_nums and left_nums != right_nums:
        return True
    left_state = _state_signature(left_text)
    right_state = _state_signature(right_text)
    if left_state["negation"] != right_state["negation"]:
        return True
    if left_state["state_terms"] and right_state["state_terms"] and left_state["state_terms"] != right_state["state_terms"]:
        return True
    return False


def _entity_candidates(artifact: dict) -> dict[str, tuple[str, list[str]]]:
    result = {}
    for entity in artifact.get("entities", []):
        values = [entity.get("label", "")] + list(entity.get("aliases") or [])
        result[entity["id"]] = (norm_text(entity.get("label", "")), [norm_text(v) for v in values if as_text(v)])
    return result


def entity_label(artifact: dict, entity_id: str) -> str:
    for entity in artifact.get("entities", []):
        if entity.get("id") == entity_id:
            return entity.get("label", entity_id)
    return entity_id


def resolve_chain(mechanism: str, artifact: dict) -> list[str]:
    """Resolve a mechanism string such as A -> B -> C to entity IDs."""
    raw = as_text(mechanism)
    if not raw or "->" not in raw:
        return []
    tokens = re.split(r"\s*->\s*", raw)
    candidates = _entity_candidates(artifact)
    resolved: list[str] = []
    for token in tokens:
        token = re.sub(r"\([^)]*\)", " ", token)
        token = re.sub(r"\b(UP|DOWN|PRESENT|ABSENT|ACTIVE|INACTIVE|NORMAL|ABNORMAL|OK|OKAY|CONFIRMED|VALID|INVALID)\b", " ", token, flags=re.I)
        key = norm_text(token)
        if not key:
            continue
        best, score = None, 0.0
        for entity_id, (label, aliases) in candidates.items():
            values = [label] + aliases
            current = max((SequenceMatcher(None, key, value).ratio() for value in values), default=0.0)
            if current > score:
                best, score = entity_id, current
        if best and score >= 0.74:
            if not resolved or resolved[-1] != best:
                resolved.append(best)
    return resolved


def has_direct_cue(text: str) -> bool:
    lower = as_text(text).lower()
    return any(re.search(rf"\b{re.escape(cue)}", lower) for cue in DIRECT_CUES)


def has_conditional_cue(text: str) -> bool:
    lower = as_text(text).lower()
    return any(cue in lower for cue in CONDITIONAL_CUES)


def _claim_scope_for(kind: str, attributes: dict | None = None) -> str:
    if kind == "conditional":
        return "conditional"
    if kind == "regime":
        return "regime"
    if kind == "transitive_derived":
        return "composed"
    if kind in HYPOTHESIS_KINDS:
        attrs = attributes or {}
        text = (as_text(attrs.get("type")) + " " + as_text(attrs.get("edge_type"))).lower()
        if any(cue in text for cue in ("indirect", "transitive", "derived", "mechanism")):
            return "composed"
        if any(cue in text for cue in ("conditional", "moderator", "moderating")):
            return "conditional"
        return "unknown"
    return "direct"


def _evidence_grade_for(edge: dict) -> str:
    if edge.get("derivation_edge_ids") or edge.get("candidate_derivation_ids"):
        return "derived_path"
    authority_assertions = edge.get("support_assertion_ids") or []
    authority_spans = edge.get("span_ids") or []
    candidate_assertions = edge.get("candidate_assertion_ids") or []
    candidate_spans = edge.get("candidate_span_ids") or []
    if (authority_assertions and authority_spans) or (candidate_assertions and candidate_spans):
        return "span_grounded"
    if authority_assertions or candidate_assertions:
        return "assertion_grounded"
    if authority_spans or candidate_spans:
        return "span_only"
    if edge.get("support_doc_ids") or edge.get("candidate_doc_ids"):
        return "context_only"
    return "none"


def _candidate_tier_for(edge: dict) -> str:
    grade = edge.get("evidence_grade") or _evidence_grade_for(edge)
    mode = edge.get("candidate_support_mode") or edge.get("support_mode") or ""
    # A verifier-demoted edge that only had a generic candidate mode should
    # not become high-confidence merely because a span was attached.
    if mode in {"candidate", "none", ""}:
        return "low" if grade not in {"none", ""} else "none"
    if grade in {"span_grounded", "derived_path"}:
        return "high"
    if grade in {"assertion_grounded", "span_only"} or mode == "weak_candidate":
        return "medium"
    if grade == "context_only":
        return "low"
    return "none"


def _refresh_edge_semantics(edge: dict, claim_scope: str | None = None) -> None:
    if claim_scope and not edge.get("claim_scope"):
        edge["claim_scope"] = claim_scope
    if not edge.get("claim_scope"):
        kind = str(edge.get("kind") or "")
        source_kind = str(edge.get("previous_kind") or kind) if kind in HYPOTHESIS_KINDS else kind
        edge["claim_scope"] = _claim_scope_for(source_kind, edge.get("attributes") or {})
    if edge.get("claim_scope") == "unknown":
        previous = str(edge.get("previous_kind") or "")
        if previous:
            edge["claim_scope"] = _claim_scope_for(previous, edge.get("attributes") or {})
    edge["evidence_grade"] = _evidence_grade_for(edge)
    if edge.get("claim_scope") == "unknown" and edge["evidence_grade"] == "derived_path":
        edge["claim_scope"] = "composed"
    if not edge.get("candidate_support_mode"):
        edge["candidate_support_mode"] = edge.get("support_mode", "")
    if not edge.get("candidate_support_strength"):
        edge["candidate_support_strength"] = edge.get("support_strength", "")
    edge.setdefault("candidate_origin", edge.get("provenance", "unknown"))
    edge["candidate_tier"] = _candidate_tier_for(edge)
    edge.setdefault("verification_status", "unverified")
    edge.setdefault("direct_support", "untested")
    edge.setdefault("assertion_support", "untested")
    edge.setdefault("direction_audit", "")
    edge.setdefault("path_summary", False)
    edge.setdefault("path_summary_explicit_direct", False)
    edge.setdefault("path_support", "untested")
    edge.setdefault("condition_support", "untested")
    edge.setdefault("identity_status", "ok")
    edge.setdefault("polarity_status", "")
    edge.setdefault("provisional_recovery", False)
    edge.setdefault("assertion_recovery", False)
    edge.setdefault("support_aware_eligible", False)
    edge.setdefault("support_aware_status", "excluded")
    edge.setdefault("support_aware_reason", "not_evaluated")
    edge.setdefault("relation_role_source", "unknown")
    edge.setdefault("relation_role_target", "unknown")
    edge.setdefault("role_direction", "abstain")
    edge.setdefault("role_conflict", False)
    edge.setdefault("role_override", False)
    edge.setdefault("textual_span_forward_votes", 0)
    edge.setdefault("textual_span_reverse_votes", 0)
    edge.setdefault("textual_span_negated_votes", 0)
    edge.setdefault("textual_attribute_forward_votes", 0)
    edge.setdefault("textual_attribute_reverse_votes", 0)
    edge.setdefault("textual_attribute_negated_votes", 0)
    edge.setdefault("textual_vote_source", "")
    edge.setdefault("temporal_mode", "unknown")
    edge.setdefault("temporal_abstained", False)
    edge.setdefault("directness_class", "")
    edge.setdefault("directness_reason", "")
    edge.setdefault("shortcut_risk", "low")
    edge.setdefault("shortcut_path_edge_ids", [])
    edge.setdefault("view_tags", [])


def _is_evidence_core_edge(edge: dict) -> bool:
    kind = str(edge.get("kind") or "")
    grade = edge.get("evidence_grade") or _evidence_grade_for(edge)
    if kind in {"direct_core", "conditional", "regime", "transitive_derived"}:
        return grade != "none"
    if kind == "candidate":
        # weak_candidate is the pre-verifier compilation tier.  A verifier
        # demotion sets support_mode=candidate while preserving the original
        # mode in candidate_support_mode.
        return edge.get("support_mode") in {"weak_candidate", "assertion_only", "span_only"} and grade != "none"
    return False


_TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")


def _time_value(text: object) -> int | None:
    match = _TIME_RE.search(as_text(text))
    if not match:
        return None
    hour, minute = match.group(0).split(":", 1)
    return int(hour) * 60 + int(minute)


REVERSE_TEMPORAL_CUES = (
    "feedback",
    "control loop",
    "closed loop",
    "closed-loop",
    "reverse order",
    "reverse-order",
    "back corrected",
    "back-corrected",
    "back corrected record",
    "back-corrected record",
    "backfill",
    "backfilled",
    "time lag",
    "time-lag",
    "lagged",
    "two way",
    "two-way",
    "bidirectional temporal",
)


def temporal_mode_for_edge(artifact: dict, edge: dict) -> str:
    attrs = edge.get("attributes") or {}
    entity_text = []
    for entity_id in (edge.get("source"), edge.get("target")):
        entity = next((e for e in artifact.get("entities", []) if e.get("id") == entity_id), {})
        entity_text.extend([as_text(entity.get("label")), as_text(entity.get("description"))])
    span_ids = set(edge.get("span_ids") or []) | set(edge.get("candidate_span_ids") or [])
    spans = {str(s.get("id")): s for s in artifact.get("spans", [])}
    text = " ".join(entity_text + [
        as_text(attrs.get("description")), as_text(attrs.get("mechanism")),
    ] + [as_text((spans.get(sid) or {}).get("context")) for sid in span_ids])
    lower = re.sub(r"\s+", " ", text.lower())
    if any(cue in lower for cue in REVERSE_TEMPORAL_CUES):
        return "reverse_order_or_feedback"
    source_time = _time_value(edge.get("source_name") or entity_label(artifact, edge.get("source", "")))
    target_time = _time_value(edge.get("target_name") or entity_label(artifact, edge.get("target", "")))
    return "chronological" if source_time is not None and target_time is not None else "unknown"


def temporal_direction_guard(artifact: dict) -> dict:
    """Prefer chronological direction for time-indexed variables.

    A later time point cannot normally cause an earlier time point. The guard
    only acts when both endpoint labels contain explicit clock times; all other
    edges are left untouched.
    """
    corrected = 0
    abstained = 0
    decisions = []
    for edge in artifact.get("edges", []):
        source_label = edge.get("source_name") or entity_label(artifact, edge.get("source", ""))
        target_label = edge.get("target_name") or entity_label(artifact, edge.get("target", ""))
        source_time = _time_value(source_label)
        target_time = _time_value(target_label)
        if source_time is None or target_time is None or source_time <= target_time:
            continue
        mode = temporal_mode_for_edge(artifact, edge)
        edge["temporal_mode"] = mode
        if mode != "chronological":
            abstained += 1
            edge["temporal_abstained"] = True
            decisions.append({
                "edge_id": edge.get("id"),
                "action": "abstained",
                "source_time": source_label,
                "target_time": target_label,
                "temporal_mode": mode,
                "rule": "do_not_apply_chronological_flip_in_feedback_or_backcorrected_context",
            })
            continue
        edge.setdefault("original_source", edge.get("source"))
        edge.setdefault("original_target", edge.get("target"))
        edge["source"], edge["target"] = edge.get("target"), edge.get("source")
        edge["source_name"], edge["target_name"] = target_label, source_label
        edge["direction_status"] = "corrected"
        edge["direction_audit"] = "temporal_order"
        edge["provenance"] = "temporal_order_guard"
        edge["temporal_abstained"] = False
        corrected += 1
        decisions.append({
            "edge_id": edge.get("id"),
            "action": "corrected",
            "original_source": edge.get("original_source"),
            "original_target": edge.get("original_target"),
            "corrected_source": edge.get("source"),
            "corrected_target": edge.get("target"),
            "source_time": source_label,
            "target_time": target_label,
            "temporal_mode": mode,
            "rule": "earlier_time_to_later_time",
        })
    materialize_views(artifact)
    artifact.setdefault("diagnostics", {})["temporal_direction"] = {
        "corrected": corrected,
        "abstained": abstained,
        "decisions": decisions,
    }
    return {"corrected": corrected, "abstained": abstained, "decisions": decisions}


PASSIVE_CAUSAL_CUES = (
    "caused by",
    "results from",
    "resulted from",
    "produced by",
    "generated by",
    "triggered by",
    "enabled by",
    "driven by",
    "influenced by",
    "determined by",
    "caused due to",
    "due to",
    "attributable to",
    "arises from",
    "stems from",
)

_NEGATED_CUE_RE = re.compile(
    r"\b(?:not|never|no|without|cannot|can't|doesn't|didn't|isn't|wasn't)\s*(?:\w+\s+){0,2}$"
)


def _claim_entity_values(artifact: dict, entity_id: object, fallback: object = "") -> list[str]:
    entity = next((e for e in artifact.get("entities", []) if e.get("id") == entity_id), {})
    values = [as_text(fallback), as_text(entity.get("label"))]
    values.extend(as_text(v) for v in entity.get("aliases") or [])
    result = []
    for value in values:
        key = norm_text(value)
        if len(key) >= 3 and key not in result:
            result.append(key)
    return result


def _mention_positions(text: str, values: list[str]) -> list[tuple[int, int]]:
    positions = []
    for value in values:
        if not value:
            continue
        try:
            for match in re.finditer(rf"(?<!\w){re.escape(value)}(?!\w)", text):
                positions.append((match.start(), match.end()))
        except re.error:
            continue
    return sorted(set(positions))


def _nearest_before(positions: list[tuple[int, int]], end: int) -> tuple[int, int] | None:
    values = [item for item in positions if item[1] <= end]
    return max(values, key=lambda item: item[1]) if values else None


def _nearest_after(positions: list[tuple[int, int]], start: int) -> tuple[int, int] | None:
    values = [item for item in positions if item[0] >= start]
    return min(values, key=lambda item: item[0]) if values else None


def _cue_is_negated(text: str, cue_start: int) -> bool:
    prefix = text[max(0, cue_start - 48):cue_start].strip()
    return bool(_NEGATED_CUE_RE.search(prefix))


def _scan_causal_votes(text: str, source_values: list[str], target_values: list[str]) -> dict[str, int]:
    normalized = norm_text(text)
    if not normalized:
        return {"forward": 0, "reverse": 0, "negated": 0}
    source_pos = _mention_positions(normalized, source_values)
    target_pos = _mention_positions(normalized, target_values)
    if not source_pos or not target_pos:
        return {"forward": 0, "reverse": 0, "negated": 0}
    forward = reverse = negated = 0
    for cue in DIRECT_CUES:
        needle = norm_text(cue)
        if not needle:
            continue
        for match in re.finditer(rf"(?<!\w){re.escape(needle)}(?!\w)", normalized):
            if _cue_is_negated(normalized, match.start()):
                negated += 1
                continue
            following = normalized[match.end():].lstrip()
            if any(following.startswith(prefix) for prefix in ("by ", "from ", "due to ", "attributable to ")):
                continue
            source_before = _nearest_before(source_pos, match.start())
            target_after = _nearest_after(target_pos, match.end())
            target_before = _nearest_before(target_pos, match.start())
            source_after = _nearest_after(source_pos, match.end())
            if source_before and target_after:
                forward += 1
            if target_before and source_after:
                reverse += 1
    for cue in PASSIVE_CAUSAL_CUES:
        needle = norm_text(cue)
        if not needle:
            continue
        for match in re.finditer(rf"(?<!\w){re.escape(needle)}(?!\w)", normalized):
            if _cue_is_negated(normalized, match.start()):
                negated += 1
                continue
            target_before = _nearest_before(target_pos, match.start())
            source_after = _nearest_after(source_pos, match.end())
            source_before = _nearest_before(source_pos, match.start())
            target_after = _nearest_after(target_pos, match.end())
            if target_before and source_after:
                forward += 1
            if source_before and target_after:
                reverse += 1
    return {"forward": forward, "reverse": reverse, "negated": negated}



def textual_direction_guard(artifact: dict) -> dict:
    """Audit orientation with explicit cues, preferring direct-span votes."""
    spans = {str(span.get("id")): span for span in artifact.get("spans", [])}
    corrected = supported = ambiguous = negated_count = 0
    decisions = []
    for edge in artifact.get("edges", []):
        if edge.get("kind") == "rejected":
            continue
        span_ids = list(edge.get("span_ids") or []) + list(edge.get("candidate_span_ids") or [])
        span_texts = [as_text((spans.get(sid) or {}).get("text")) for sid in span_ids]
        attrs = edge.get("attributes") or {}
        attr_texts = [as_text(attrs.get("description")), as_text(attrs.get("mechanism"))]
        source_values = _claim_entity_values(artifact, edge.get("source"), edge.get("source_name"))
        target_values = _claim_entity_values(artifact, edge.get("target"), edge.get("target_name"))
        span_forward = span_reverse = span_negated = 0
        for text in span_texts:
            votes = _scan_causal_votes(text, source_values, target_values)
            span_forward += votes["forward"]
            span_reverse += votes["reverse"]
            span_negated += votes["negated"]
        attr_forward = attr_reverse = attr_negated = 0
        for text in attr_texts:
            votes = _scan_causal_votes(text, source_values, target_values)
            attr_forward += votes["forward"]
            attr_reverse += votes["reverse"]
            attr_negated += votes["negated"]
        if span_forward or span_reverse or span_negated:
            forward, reverse, negated = span_forward, span_reverse, span_negated
            vote_source = "span"
        else:
            forward, reverse, negated = attr_forward, attr_reverse, attr_negated
            vote_source = "attribute"
        edge["textual_span_forward_votes"] = span_forward
        edge["textual_span_reverse_votes"] = span_reverse
        edge["textual_span_negated_votes"] = span_negated
        edge["textual_attribute_forward_votes"] = attr_forward
        edge["textual_attribute_reverse_votes"] = attr_reverse
        edge["textual_attribute_negated_votes"] = attr_negated
        edge["textual_vote_source"] = vote_source
        if not (forward or reverse or negated):
            continue
        if reverse > forward:
            edge.setdefault("original_source", edge.get("source"))
            edge.setdefault("original_target", edge.get("target"))
            edge["source"], edge["target"] = edge.get("target"), edge.get("source")
            edge["source_name"], edge["target_name"] = edge.get("target_name"), edge.get("source_name")
            edge["direction_status"] = "corrected"
            edge["direction_audit"] = "textual_cue"
            corrected += 1
            decision = "corrected"
        elif forward > reverse:
            edge["direction_status"] = "supported"
            edge["direction_audit"] = "textual_cue"
            supported += 1
            decision = "supported"
        elif forward and reverse:
            edge["direction_status"] = "ambiguous"
            edge["direction_audit"] = "textual_cue_conflict"
            edge["verification_status"] = "needs_review"
            if edge.get("kind") in RETAINED_EDGE_KINDS:
                _demote_to_candidate_or_unsupported(
                    edge, "textual_direction_conflict", semantic_support="candidate",
                    direction_status="ambiguous", verification_status="needs_review",
                )
            ambiguous += 1
            decision = "ambiguous"
        else:
            edge["polarity_status"] = "negated"
            edge["verification_status"] = "needs_review"
            if edge.get("kind") in RETAINED_EDGE_KINDS:
                _demote_to_candidate_or_unsupported(
                    edge, "negated_causal_claim", semantic_support="candidate",
                    verification_status="needs_review",
                )
            negated_count += 1
            decision = "negated"
        decisions.append({
            "edge_id": edge.get("id"),
            "decision": decision,
            "vote_source": vote_source,
            "span_forward_votes": span_forward,
            "span_reverse_votes": span_reverse,
            "attribute_forward_votes": attr_forward,
            "attribute_reverse_votes": attr_reverse,
            "negated_votes": negated,
            "rule": "span_first_explicit_causal_cue",
        })
    artifact.setdefault("diagnostics", {})["textual_direction"] = {
        "corrected": corrected,
        "supported": supported,
        "ambiguous": ambiguous,
        "negated": negated_count,
        "decisions": decisions,
    }
    materialize_views(artifact)
    return artifact["diagnostics"]["textual_direction"]


ROLE_PATTERNS = (
    ("measurement", ("measurement", "concentration", "temperature", "pressure", "flow", "speed", "rate", "level")),
    ("capability", ("capability", "accepts", "supports", "capable", "ability", "can ", "enabled", "ready")),
    ("condition", ("connected", "attached", "installed", "configured", "present", "available", "valid", "active")),
    ("outcome", ("healthy", "normal", "functioning", "working", "rendering", "output", "performance", "quality", "stable", "ok")),
    ("event", ("trigger", "failure", "error", "fault", "change", "increase", "decrease", "process", "action", "truncation")),
)


def infer_relation_role(artifact: dict, entity_id: object, fallback: object = "") -> str:
    entity = next((e for e in artifact.get("entities", []) if e.get("id") == entity_id), {})
    values = [
        as_text(entity.get("entity_type")),
        as_text(entity.get("label")),
        as_text(entity.get("description")),
        as_text(fallback),
    ]
    text = " ".join(values).lower()
    for role, cues in ROLE_PATTERNS:
        if any(cue in text for cue in cues):
            return role
    if re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", text):
        return "measurement"
    return "unknown"


def role_direction_prior(source_role: str, target_role: str) -> str:
    outcome_like = {"outcome"}
    prerequisite_like = {"condition", "capability"}
    if source_role in outcome_like and target_role in prerequisite_like:
        return "reverse"
    if source_role in prerequisite_like and target_role in outcome_like:
        return "forward"
    return "abstain"


def relation_role_direction_guard(artifact: dict) -> dict:
    """Use generic endpoint roles to audit prerequisite/outcome orientation."""
    corrected = role_conflicts = role_supported = 0
    decisions = []
    for edge in artifact.get("edges", []):
        if edge.get("kind") == "rejected":
            continue
        source_role = infer_relation_role(artifact, edge.get("source"), edge.get("source_name"))
        target_role = infer_relation_role(artifact, edge.get("target"), edge.get("target_name"))
        role_direction = role_direction_prior(source_role, target_role)
        edge["relation_role_source"] = source_role
        edge["relation_role_target"] = target_role
        edge["role_direction"] = role_direction
        span_forward = int(edge.get("textual_span_forward_votes") or 0)
        span_reverse = int(edge.get("textual_span_reverse_votes") or 0)
        attr_forward = int(edge.get("textual_attribute_forward_votes") or 0)
        attr_reverse = int(edge.get("textual_attribute_reverse_votes") or 0)
        if role_direction == "abstain":
            decisions.append({"edge_id": edge.get("id"), "decision": "abstain", "source_role": source_role, "target_role": target_role})
            continue
        if role_direction == "reverse" and span_forward:
            edge["direction_status"] = "ambiguous"
            edge["direction_audit"] = "role_text_conflict"
            edge["role_conflict"] = True
            _demote_to_candidate_or_unsupported(
                edge, "relation_role_text_conflict", semantic_support="candidate",
                direction_status="ambiguous", verification_status="needs_review",
            )
            role_conflicts += 1
            decision = "role_conflict"
        elif role_direction == "forward" and span_reverse:
            edge["direction_status"] = "ambiguous"
            edge["direction_audit"] = "role_text_conflict"
            edge["role_conflict"] = True
            _demote_to_candidate_or_unsupported(
                edge, "relation_role_text_conflict", semantic_support="candidate",
                direction_status="ambiguous", verification_status="needs_review",
            )
            role_conflicts += 1
            decision = "role_conflict"
        elif role_direction == "reverse":
            edge.setdefault("original_source", edge.get("source"))
            edge.setdefault("original_target", edge.get("target"))
            edge["source"], edge["target"] = edge.get("target"), edge.get("source")
            edge["source_name"], edge["target_name"] = edge.get("target_name"), edge.get("source_name")
            edge["relation_role_source"], edge["relation_role_target"] = target_role, source_role
            edge["direction_status"] = "corrected"
            edge["direction_audit"] = "relation_role"
            edge["role_override"] = True
            corrected += 1
            decision = "corrected"
        else:
            if edge.get("direction_status") not in {"corrected", "ambiguous"}:
                edge["direction_status"] = "supported"
                edge["direction_audit"] = edge.get("direction_audit") or "relation_role"
            role_supported += 1
            decision = "supported"
        decisions.append({
            "edge_id": edge.get("id"),
            "decision": decision,
            "source_role": source_role,
            "target_role": target_role,
            "role_direction": role_direction,
            "span_forward_votes": span_forward,
            "span_reverse_votes": span_reverse,
            "attribute_forward_votes": attr_forward,
            "attribute_reverse_votes": attr_reverse,
        })
    artifact.setdefault("diagnostics", {})["relation_role_direction"] = {
        "corrected": corrected,
        "role_conflicts": role_conflicts,
        "supported": role_supported,
        "decisions": decisions,
    }
    materialize_views(artifact)
    return artifact["diagnostics"]["relation_role_direction"]


def _edge_evidence_texts(artifact: dict, edge: dict) -> tuple[list[str], list[str]]:
    spans = {str(span.get("id")): span for span in artifact.get("spans", [])}
    span_ids = list(edge.get("span_ids") or []) + list(edge.get("candidate_span_ids") or [])
    span_texts = [as_text((spans.get(sid) or {}).get("text")) for sid in span_ids]
    attrs = edge.get("attributes") or {}
    attr_texts = [as_text(attrs.get("description")), as_text(attrs.get("mechanism"))]
    return span_texts, attr_texts


def _has_causal_cue(text: str) -> bool:
    return bool(text) and has_direct_cue(text)


def _edge_direct_span(artifact: dict, edge: dict) -> bool:
    span_texts, _ = _edge_evidence_texts(artifact, edge)
    source_values = _claim_entity_values(artifact, edge.get("source"), edge.get("source_name"))
    target_values = _claim_entity_values(artifact, edge.get("target"), edge.get("target_name"))
    for text in span_texts:
        normalized = norm_text(text)
        if not normalized:
            continue
        if not _mention_positions(normalized, source_values) or not _mention_positions(normalized, target_values):
            continue
        if _has_causal_cue(text):
            return True
    return False


def _edge_direct_assertion(artifact: dict, edge: dict) -> bool:
    assertion_ids = list(edge.get("support_assertion_ids") or []) + list(edge.get("candidate_assertion_ids") or [])
    if not assertion_ids:
        return False
    _, attr_texts = _edge_evidence_texts(artifact, edge)
    return any(_has_causal_cue(text) for text in attr_texts)


def classify_edge_directness(artifact: dict) -> dict:
    """Classify whether an edge is direct, path-derived, or merely contextual."""
    counts = Counter()
    for edge in artifact.get("edges", []):
        if edge.get("kind") == "rejected":
            edge["directness_class"] = "none"
            continue
        if _edge_direct_span(artifact, edge):
            directness = "direct_span"
            reason = "span_contains_both_endpoints_and_causal_cue"
        elif _edge_direct_assertion(artifact, edge):
            directness = "direct_assertion"
            reason = "assertion_present_with_explicit_causal_text"
        elif edge.get("span_ids") or edge.get("candidate_span_ids"):
            directness = "span_only"
            reason = "span_present_without_complete_direct_cue"
        elif edge.get("derivation_edge_ids") or edge.get("candidate_derivation_ids") or edge.get("path_summary"):
            directness = "path_only"
            reason = "path_evidence_without_direct_cue"
        elif edge.get("support_doc_ids") or edge.get("candidate_doc_ids"):
            directness = "context_only"
            reason = "document_context_only"
        else:
            directness = "none"
            reason = "no_evidence"
        edge["directness_class"] = directness
        edge["directness_reason"] = reason
        counts[directness] += 1
    artifact.setdefault("diagnostics", {})["directness"] = dict(counts)
    materialize_views(artifact)
    return dict(counts)


def shortcut_guard(artifact: dict) -> dict:
    """Mark direct-looking edges that only have alternate-path support."""
    retained_kinds = {"direct_core", "conditional", "regime", "transitive_derived"}
    edges = [e for e in artifact.get("edges", []) if e.get("kind") in retained_kinds]
    graph = nx.DiGraph()
    for edge in edges:
        graph.add_edge(edge["source"], edge["target"], edge_id=edge["id"])
    high = medium = 0
    decisions = []
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source not in graph or target not in graph or not graph.has_edge(source, target):
            edge["shortcut_risk"] = "low"
            continue
        graph.remove_edge(source, target)
        try:
            path = nx.shortest_path(graph, source, target) if nx.has_path(graph, source, target) else None
        finally:
            graph.add_edge(source, target, edge_id=edge.get("id"))
        if not path or len(path) < 3:
            edge["shortcut_risk"] = "low"
            edge["shortcut_path_edge_ids"] = []
            continue
        directness = edge.get("directness_class", "none")
        risk = "high" if directness in {"path_only", "context_only", "none"} else "medium"
        edge["shortcut_risk"] = risk
        edge["shortcut_path_edge_ids"] = _path_edge_ids(artifact, path)
        if risk == "high":
            high += 1
            edge["path_summary"] = True
            edge["path_summary_explicit_direct"] = False
        else:
            medium += 1
        decisions.append({
            "edge_id": edge.get("id"),
            "risk": risk,
            "path": path,
            "path_edge_ids": edge["shortcut_path_edge_ids"],
            "directness_class": directness,
        })
    artifact.setdefault("diagnostics", {})["shortcut_guard"] = {
        "high_risk": high,
        "medium_risk": medium,
        "decisions": decisions,
    }
    materialize_views(artifact)
    return {"high_risk": high, "medium_risk": medium}


def _edge_index(artifact: dict) -> dict[tuple[str, str], str]:
    return {
        (e["source"], e["target"]): e["id"]
        for e in artifact.get("edges", [])
        if e.get("kind") not in {"rejected", "candidate", "unsupported_hypothesis", "hypothesis"}
    }


def _edge_by_id(artifact: dict) -> dict[str, dict]:
    return {e["id"]: e for e in artifact.get("edges", [])}


def _path_edge_ids(artifact: dict, path: list[str]) -> list[str]:
    index = _edge_index(artifact)
    return [index[(u, v)] for u, v in zip(path, path[1:]) if (u, v) in index]


def classify_edge_kinds(artifact: dict, mode: str = "safe") -> dict:
    """Reclassify direct candidates into chain/conditional/regime/hypothesis views.

    safe: classify a shortcut when an alternate path exists and either the
          mechanism contains the intermediate sequence or the edge shares a
          source span with the path.
    aggressive: classify any direct edge that has an alternate same-direction
          path. This is useful for diagnostics, not for primary scoring.
    off: do not reclassify; only record candidates.
    """
    if mode not in {"safe", "aggressive", "off"}:
        raise ValueError("chain_mode must be safe, aggressive, or off")
    edges = artifact.get("edges", [])
    by_id = _edge_by_id(artifact)
    graph = nx.DiGraph()
    for edge in edges:
        if edge.get("kind") in {"direct_core", "conditional", "regime"}:
            graph.add_edge(edge["source"], edge["target"], edge_id=edge["id"])
    candidates = []
    for edge in edges:
        if edge.get("kind") != "direct_core":
            continue
        source, target = edge["source"], edge["target"]
        if source == target:
            continue
        if not graph.has_node(source) or not graph.has_node(target):
            continue
        # Remove the edge itself from the diagnostic graph.
        graph.remove_edge(source, target)
        path = None
        try:
            if nx.has_path(graph, source, target):
                path = nx.shortest_path(graph, source, target)
        finally:
            graph.add_edge(source, target, edge_id=edge["id"])
        if not path or len(path) < 3:
            continue
        path_ids = _path_edge_ids(artifact, path)
        chain = resolve_chain(edge.get("attributes", {}).get("mechanism", ""), artifact)
        chain_matches = bool(chain and chain[0] == source and chain[-1] == target and len(chain) >= 3)
        path_spans = set()
        for eid in path_ids:
            path_spans.update(by_id[eid].get("span_ids") or [])
        edge_spans = set(edge.get("span_ids") or [])
        span_overlap = bool(edge_spans and path_spans and edge_spans.intersection(path_spans))
        candidates.append({
            "edge_id": edge["id"],
            "source": source,
            "target": target,
            "path": path,
            "path_edge_ids": path_ids,
            "chain_matches": chain_matches,
            "span_overlap": span_overlap,
        })
        should_classify = False
        if mode == "aggressive":
            should_classify = True
        elif mode == "safe":
            should_classify = chain_matches or span_overlap
        if should_classify and path_ids:
            edge["kind"] = "transitive_derived"
            edge["edge_kind"] = "transitive_derived"
            edge["claim_scope"] = "composed"
            edge["evidence_grade"] = "derived_path"
            edge["verification_status"] = "unverified"
            edge["derivation_edge_ids"] = path_ids
            edge["path_summary"] = True
            text = " ".join([
                as_text((edge.get("attributes") or {}).get("description", "")),
                as_text((edge.get("attributes") or {}).get("mechanism", "")),
            ])
            edge["path_summary_explicit_direct"] = has_direct_cue(text)
            edge["provenance"] = "derived_chain"
            edge["semantic_support"] = "derived"
            edge["chain_candidate"] = True
            _refresh_edge_semantics(edge)
    artifact.setdefault("diagnostics", {})["chain_candidates"] = candidates
    materialize_views(artifact)
    return {"candidates": len(candidates), "classified": sum(1 for e in edges if e.get("kind") == "transitive_derived")}


def _span_texts(artifact: dict) -> dict[str, str]:
    return {s["id"]: as_text(s.get("text")) for s in artifact.get("spans", [])}


def discover_span_chain_candidates(artifact: dict) -> list[dict]:
    """Find conservative A->B->C candidates inside one evidence span.

    Requires at least one adjacent edge to already exist, so a generic sentence
    with many entities does not create a combinatorial chain explosion.
    """
    spans = _span_texts(artifact)
    entities = artifact.get("entities", [])
    values = []
    for entity in entities:
        for value in [entity.get("label", "")] + list(entity.get("aliases") or []):
            value = as_text(value).strip()
            if len(value) >= 4:
                values.append((norm_text(value), entity["id"]))
    existing = {
        (e["source"], e["target"]) for e in artifact.get("edges", [])
        if e.get("kind") not in {"rejected", "candidate", "unsupported_hypothesis", "hypothesis"}
    }
    rows = []
    seen = set()
    for edge in artifact.get("edges", []):
        if edge.get("kind") != "direct_core" or not edge.get("span_ids"):
            continue
        for sid in edge.get("span_ids", []):
            text = norm_text(spans.get(sid, ""))
            if not text:
                continue
            positions = {}
            for value, entity_id in values:
                if not value or entity_id in {edge["source"], edge["target"]}:
                    continue
                pos = text.find(value)
                if pos >= 0:
                    positions[entity_id] = min(pos, positions.get(entity_id, pos))
            source_pos = min((text.find(value) for value, entity_id in values if entity_id == edge["source"] and value and text.find(value) >= 0), default=None)
            target_pos = min((text.find(value) for value, entity_id in values if entity_id == edge["target"] and value and text.find(value) >= 0), default=None)
            mentioned = set(positions)
            for mid in mentioned:
                # The intermediate must be textually between the endpoints.
                if source_pos is not None and target_pos is not None:
                    lo, hi = sorted((source_pos, target_pos))
                    if not (lo < positions[mid] < hi):
                        continue
                left_exists = (edge["source"], mid) in existing
                right_exists = (mid, edge["target"]) in existing
                if not (left_exists or right_exists):
                    continue
                key = (edge["id"], mid, sid)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "edge_id": edge["id"],
                    "source": edge["source"],
                    "target": edge["target"],
                    "intermediate": mid,
                    "source_to_intermediate": left_exists,
                    "intermediate_to_target": right_exists,
                    "span_id": sid,
                })
    artifact.setdefault("diagnostics", {})["span_chain_candidates"] = rows
    return rows


def _assertion_index(artifact: dict) -> dict[tuple[str, str], list[dict]]:
    out = defaultdict(list)
    for assertion in artifact.get("assertions", []):
        out[(assertion.get("source"), assertion.get("target"))].append(assertion)
    return out


def _condition_for_raw(artifact: dict, raw: str) -> str | None:
    raw = as_text(raw).strip()
    if not raw:
        return None
    normalized = re.sub(r"^(if|when|whenever|while|during)\s+", "", norm_text(raw)).strip(" .,:;")
    cid = stable_urn("condition", normalized)
    found = next((c for c in artifact.get("conditions", []) if c["id"] == cid), None)
    if found is None:
        lower = raw.lower()
        if any(word in lower for word in ("regime", "operating mode", "during", "mode ")):
            condition_kind = "regime"
        elif any(word in lower for word in ("depends", "moderator", "interaction", "jointly", "whether")):
            condition_kind = "moderator"
        else:
            condition_kind = "conditional"
        artifact.setdefault("conditions", []).append({
            "id": cid,
            "raw": raw,
            "normalized": normalized,
            "equivalence_key": sha1(normalized),
            "condition_kind": condition_kind,
        })
    return cid


def add_edge(artifact: dict, source: str, target: str, kind: str, evidence_assertions: list[dict], **extra: Any) -> dict:
    existing = next((e for e in artifact.get("edges", []) if e.get("source") == source and e.get("target") == target and e.get("kind") == kind), None)
    if existing:
        return existing
    if source == target:
        raise ValueError("refusing to add self-loop")
    edge_id = stable_urn("edge", source, target, kind, extra.get("repair_of", ""), len(artifact.get("edges", [])))
    spans = sorted({a.get("span_id") for a in evidence_assertions if a.get("span_id")})
    docs = sorted({a.get("doc_id") for a in evidence_assertions if a.get("doc_id")})
    support_ids = sorted({a["id"] for a in evidence_assertions})
    hypothesis_kind = kind in HYPOTHESIS_KINDS
    edge = {
        "id": edge_id,
        "source": source,
        "target": target,
        "source_name": entity_label(artifact, source),
        "target_name": entity_label(artifact, target),
        "kind": kind,
        "edge_kind": kind,
        "support_assertion_ids": [] if hypothesis_kind else support_ids,
        "support_doc_ids": [] if hypothesis_kind else docs,
        "span_ids": [] if hypothesis_kind else spans,
        "candidate_assertion_ids": support_ids,
        "candidate_doc_ids": docs,
        "candidate_span_ids": spans,
        "candidate_derivation_ids": extra.get("candidate_derivation_ids", []),
        "condition_id": extra.get("condition_id"),
        "derivation_edge_ids": extra.get("derivation_edge_ids", []),
        "confidence": extra.get("confidence", "medium"),
        "strength": extra.get("strength", "unknown"),
        "direction_status": extra.get("direction_status", "unverified"),
        "support_mode": "direct_span" if not hypothesis_kind and support_ids and spans else ("candidate" if hypothesis_kind else "weak"),
        "support_strength": "strong" if not hypothesis_kind and support_ids and spans else "weak",
        "claim_scope": extra.get("claim_scope", _claim_scope_for(kind, extra.get("attributes") or {})),
        "evidence_grade": "none",
        "candidate_support_mode": "direct_span" if not hypothesis_kind and support_ids and spans else ("candidate" if hypothesis_kind else "weak"),
        "candidate_support_strength": "strong" if not hypothesis_kind and support_ids and spans else "weak",
        "candidate_origin": extra.get("candidate_origin", extra.get("provenance", "chain_repair")),
        "candidate_tier": "medium" if hypothesis_kind else "high",
        "candidate_evidence_ids": sorted({a["id"] for a in evidence_assertions} | set(spans) | set(docs)),
        "evidence_union_ids": sorted(set(spans)),
        "verification_status": "unverified",
        "direct_support": "untested",
        "path_support": "untested",
        "condition_support": "untested",
        "view_tags": [],
        "semantic_support": "candidate" if kind == "candidate" else extra.get("semantic_support", "untested"),
        "provenance": extra.get("provenance", "chain_repair"),
        "attributes": extra.get("attributes", {}),
    }
    edge.update({k: v for k, v in extra.items() if k not in edge})
    _refresh_edge_semantics(edge)
    artifact.setdefault("edges", []).append(edge)
    return edge




def repair_entity_coverage(artifact: dict) -> dict:
    """Add stubs only for endpoint IDs already referenced by evidence."""
    entities = artifact.setdefault("entities", [])
    known = {as_text(entity.get("id")) for entity in entities}
    added = 0

    def add_stub(entity_id: object, label: object = "") -> None:
        nonlocal added
        entity_id = as_text(entity_id)
        if not entity_id or entity_id in known:
            return
        text = as_text(label) or entity_id
        times = sorted(set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", text)))
        signature = _state_signature(text)
        entities.append({
            "id": entity_id,
            "label": text,
            "description": "",
            "entity_type": "",
            "values": [],
            "aliases": [],
            "context_signature": "",
            "time_indices": times,
            "state_signature": signature,
            "identity_key": sha1("|".join([norm_text(text), str(signature), str(times)])),
            "provenance": "evidence_reference_recovery",
        })
        known.add(entity_id)
        added += 1

    for edge in artifact.get("edges", []):
        add_stub(edge.get("source"), edge.get("source_name"))
        add_stub(edge.get("target"), edge.get("target_name"))
    for assertion in artifact.get("assertions", []):
        add_stub(assertion.get("source"))
        add_stub(assertion.get("target"))
    entities.sort(key=lambda entity: as_text(entity.get("id")))
    artifact.setdefault("diagnostics", {})["entity_coverage_repair"] = {"added": added}
    return {"added": added}


def repair_identity_edges(artifact: dict) -> dict:
    """Reject canonicalized self-loops and flag duplicate entity identities."""
    entities = {as_text(entity.get("id")): entity for entity in artifact.get("entities", [])}
    rejected = 0
    duplicates = []
    for edge in artifact.get("edges", []):
        if edge.get("kind") == "rejected" or edge.get("source") == edge.get("target"):
            if edge.get("kind") != "rejected":
                edge["kind"] = "rejected"
                edge["edge_kind"] = "rejected"
                edge["identity_status"] = "self_loop"
                edge["verification_status"] = "rejected"
                edge["provenance"] = "identity_self_loop_guard"
                rejected += 1
            continue
        source = entities.get(as_text(edge.get("source")), {})
        target = entities.get(as_text(edge.get("target")), {})
        source_label = norm_text(source.get("label") or edge.get("source_name"))
        target_label = norm_text(target.get("label") or edge.get("target_name"))
        if source_label and source_label == target_label and not _identity_conflict(source_label, target_label):
            edge["identity_status"] = "duplicate_identity"
            edge["kind"] = "rejected"
            edge["edge_kind"] = "rejected"
            edge["verification_status"] = "rejected"
            edge["provenance"] = "identity_duplicate_guard"
            duplicates.append({"edge_id": edge.get("id"), "source": edge.get("source"), "target": edge.get("target")})
            rejected += 1
    artifact.setdefault("diagnostics", {})["identity_repair"] = {
        "rejected": rejected,
        "duplicate_identity_edges": duplicates,
    }
    materialize_views(artifact)
    return {"rejected": rejected, "duplicate_identity_edges": duplicates}


def recover_assertion_edges(artifact: dict, limit: int = 100) -> dict:
    """Propose missing edges from assertion evidence without granting authority.

    Recovered edges are provisional: they enter verification and the audit
    view, but not soft-core until evidence supports them.
    """
    existing = {(as_text(e.get("source")), as_text(e.get("target"))) for e in artifact.get("edges", []) if e.get("kind") != "rejected"}
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for assertion in artifact.get("assertions", []):
        source = as_text(assertion.get("source"))
        target = as_text(assertion.get("target"))
        if source and target and source != target:
            by_pair[(source, target)].append(assertion)
    added = 0
    for (source, target), rows in sorted(by_pair.items()):
        if added >= limit:
            break
        if (source, target) in existing or (target, source) in existing:
            continue
        spans = [row for row in rows if row.get("span_id")]
        mode = "direct_span" if spans else "assertion_only"
        tier = "high" if spans else "medium"
        edge = add_edge(
            artifact, source, target, "candidate", rows,
            provenance="assertion_recovery",
            candidate_support_mode=mode,
            candidate_support_strength="strong" if spans else "medium",
            candidate_tier=tier,
            provisional_recovery=True,
            assertion_recovery=True,
            semantic_support="candidate",
            verification_status="needs_review",
        )
        edge["verification_status"] = "needs_review"
        edge["support_aware_status"] = "needs_review"
        _refresh_edge_semantics(edge)
        existing.add((source, target))
        added += 1
    artifact.setdefault("diagnostics", {})["assertion_recovery"] = {"added": added, "limit": limit}
    materialize_views(artifact)
    return {"added": added}


def repair_condition_edges(artifact: dict) -> dict:
    """Resolve explicit conditions and quarantine conditionless modal edges."""
    resolved = 0
    demoted = 0
    for edge in artifact.get("edges", []):
        if edge.get("kind") not in {"conditional", "regime"}:
            continue
        raw = as_text((edge.get("attributes") or {}).get("condition"))
        current = next((c for c in artifact.get("conditions", []) if c.get("id") == edge.get("condition_id")), None)
        if current is None and raw:
            cid = _condition_for_raw(artifact, raw)
            if cid:
                edge["condition_id"] = cid
                edge["condition_status"] = "explicit"
                resolved += 1
                continue
        if current is not None:
            edge["condition_status"] = "explicit"
            continue
        edge["condition_status"] = "missing"
        _demote_to_candidate_or_unsupported(
            edge, "conditionless_modal_claim", semantic_support="candidate",
            verification_status="needs_review",
        )
        demoted += 1
    artifact.setdefault("diagnostics", {})["condition_repair"] = {"resolved": resolved, "demoted": demoted}
    materialize_views(artifact)
    return {"resolved": resolved, "demoted": demoted}

def repair_chain_edges(artifact: dict) -> dict:
    """Add missing adjacent links for classified transitive chains.

    If an adjacent pair is already in the assertion ledger, the repaired edge is
    direct_core and carries that evidence. Otherwise it is a hypothesis with
    provenance=chain_repair and must be verified before promotion.
    """
    assertions = _assertion_index(artifact)
    by_id = _edge_by_id(artifact)
    existing_pairs = {(e["source"], e["target"]) for e in artifact.get("edges", []) if e.get("kind") != "rejected"}
    repaired_direct = repaired_hyp = 0
    for edge in list(artifact.get("edges", [])):
        if edge.get("kind") != "transitive_derived":
            continue
        chain = resolve_chain(edge.get("attributes", {}).get("mechanism", ""), artifact)
        if len(chain) < 3:
            chain = []
            for eid in edge.get("derivation_edge_ids", []):
                dep = by_id.get(eid)
                if dep:
                    if not chain:
                        chain.append(dep["source"])
                    chain.append(dep["target"])
        if len(chain) < 3:
            continue
        for u, v in zip(chain, chain[1:]):
            if u == v or (u, v) in existing_pairs:
                continue
            support = assertions.get((u, v), [])
            kind = "direct_core" if support else "candidate"
            add_edge(
                artifact, u, v, kind, support,
                repair_of=edge["id"],
                candidate_derivation_ids=[edge["id"]],
                provenance="chain_repair",
                semantic_support="entailed" if support else "candidate",
                confidence="medium",
            )
            existing_pairs.add((u, v))
            if kind == "direct_core":
                repaired_direct += 1
            else:
                repaired_hyp += 1
    # Conservative in-span repair: if A->C and C share a span with B, and at
    # least one adjacent link exists, propose the missing adjacent link as a
    # hypothesis. This is a proposal, not core evidence.
    candidates = artifact.get("diagnostics", {}).get("span_chain_candidates", [])
    for candidate in candidates[:40]:
        u, mid, v = candidate["source"], candidate["intermediate"], candidate["target"]
        for a, b in ((u, mid), (mid, v)):
            if a == b or (a, b) in existing_pairs:
                continue
            support = assertions.get((a, b), [])
            kind = "direct_core" if support else "candidate"
            add_edge(
                artifact, a, b, kind, support,
                repair_of=candidate["edge_id"],
                candidate_derivation_ids=[candidate["edge_id"]],
                provenance="span_chain_repair",
                semantic_support="entailed" if support else "candidate",
                confidence="medium",
            )
            existing_pairs.add((a, b))
            if kind == "direct_core":
                repaired_direct += 1
            else:
                repaired_hyp += 1
    materialize_views(artifact)
    return {"repaired_direct": repaired_direct, "repaired_hypothesis": repaired_hyp}


def normalize_transitive_derivation(artifact: dict) -> dict:
    """Ensure every transitive edge has a usable derivation path.

    Edges imported from an upstream GML may be labelled transitive without
    carrying path edge IDs.  Recover the path from the mechanism when possible;
    otherwise demote the edge to a hypothesis and preserve its candidate
    authority fields.
    """
    by_id = _edge_by_id(artifact)
    index = _edge_index(artifact)
    repaired = 0
    demoted = 0
    for edge in list(artifact.get("edges", [])):
        if edge.get("kind") != "transitive_derived" or edge.get("derivation_edge_ids"):
            continue
        mechanism = (edge.get("attributes") or {}).get("mechanism", "")
        chain = resolve_chain(mechanism, artifact)
        path_ids: list[str] = []
        if len(chain) >= 3:
            path_ids = [index[(u, v)] for u, v in zip(chain, chain[1:]) if (u, v) in index]
        if path_ids and len(path_ids) == len(chain) - 1:
            edge["derivation_edge_ids"] = sorted(set(path_ids))
            edge["claim_scope"] = "composed"
            edge["evidence_grade"] = "derived_path"
            edge["verification_status"] = edge.get("verification_status", "unverified")
            _refresh_edge_semantics(edge)
            repaired += 1
        else:
            _demote_to_candidate_or_unsupported(
                edge, "transitive_without_path", semantic_support="candidate",
                verification_status="needs_review",
            )
            demoted += 1
    materialize_views(artifact)
    return {"repaired": repaired, "demoted": demoted}


def _edge_ref(edge: dict) -> dict:
    return {
        "edge_id": edge["id"],
        "source": edge["source"],
        "target": edge["target"],
        "claim_scope": edge.get("claim_scope", "unknown"),
        "evidence_grade": edge.get("evidence_grade", "none"),
        "verification_status": edge.get("verification_status", "unverified"),
        "support_mode": edge.get("support_mode", ""),
        "candidate_tier": edge.get("candidate_tier", "none"),
        "promotion_status": edge.get("promotion_status", "none"),
        "evidence_completeness": edge.get("evidence_completeness", "absent"),
        "support_scope": edge.get("support_scope", "none"),
        "best_window_kind": edge.get("best_window_kind", "none"),
        "evidence_packaging_stage": edge.get("evidence_packaging_stage", ""),
        "evidence_contract_complete": bool(edge.get("evidence_contract_complete")),
        "evidence_contract": edge.get("evidence_contract") or {},
        "canonical_pair_id": edge.get("canonical_pair_id"),
        "canonical_support_tier": edge.get("canonical_support_tier", "none"),
        "canonical_support_doc_count": int(edge.get("canonical_support_doc_count", 0) or 0),
        "canonical_direction_doc_count": int(edge.get("canonical_direction_doc_count", 0) or 0),
        "canonical_multi_document_support": bool(edge.get("canonical_multi_document_support")),
        "candidate_risk_flags": list(edge.get("candidate_risk_flags") or []),
        "candidate_risk_status": edge.get("candidate_risk_status", "clear"),
        "arbitration_enabled": bool(edge.get("arbitration_enabled", True)),
        "arbitration_eligible": bool(edge.get("arbitration_eligible", True)),
        "arbitration_status": edge.get("arbitration_status", "canonical_safe"),
        "arbitration_reason": edge.get("arbitration_reason", ""),
    }


def _partial_forward_eligible(edge: dict) -> bool:
    return (
        edge.get("kind") == "candidate"
        and edge.get("verifier_support") == "partial"
        and edge.get("verifier_direction") == "forward"
        and edge.get("evidence_grade") in {"span_grounded", "assertion_grounded", "derived_path"}
        and edge.get("candidate_tier") in {"high", "medium"}
    )


def _is_soft_core_lenient_edge(edge: dict) -> bool:
    if edge.get("provisional_recovery"):
        accepted = (
            as_text(edge.get("verification_status")).lower() == "accepted"
            or as_text(edge.get("verifier_support")).lower() in {"entailed", "conditional", "transitive"}
            or _partial_forward_eligible(edge)
        )
        if not accepted:
            return False
    if edge.get("kind") in RETAINED_EDGE_KINDS and edge.get("evidence_grade") != "none":
        return True
    return edge.get("kind") == "candidate" and edge.get("candidate_tier") in {"high", "medium"}


def _is_soft_core_edge(edge: dict) -> bool:
    if not _is_soft_core_lenient_edge(edge):
        return False
    if edge.get("direction_status") in {"ambiguous", "role_conflict"}:
        return False
    if edge.get("shortcut_risk") == "high":
        return False
    return True


def _is_soft_core_precision_edge(edge: dict) -> bool:
    if not _is_soft_core_edge(edge):
        return False
    directness = edge.get("directness_class")
    if not directness:
        return True
    if edge.get("kind") == "transitive_derived":
        return bool(edge.get("path_summary_explicit_direct"))
    return directness in {"direct_span", "direct_assertion", "span_only"}


def materialize_views(artifact: dict) -> dict:
    """Build atomic and evidence-aware views without mutating the ledger.

    The legacy views remain for compatibility.  D2C_13_2 adds:
    * span_core: authoritative exact-span edges;
    * assertion_core: evidence-bearing edges whose span is missing/partial;
    * derived_core: supported composed paths;
    * evidence_core: span_core + assertion_core + derived_core;
    * candidate_high/medium/low: candidate evidence tiers;
    * needs_review: low-authority or ambiguous records requiring inspection.
    """
    edges = artifact.get("edges", [])
    for edge in edges:
        _refresh_edge_semantics(edge)
    policy = normalize_policy(artifact.get("promotion_policy"))
    promotion_counts = annotate_promotion_gates(edges, policy)
    arbitration_policy = normalize_arbitration_config(artifact.get("canonical_arbitration_config"))
    for edge in edges:
        if arbitration_policy.get("enabled", True):
            gate = arbitration_gate(edge, arbitration_policy)
        else:
            gate = {"eligible": True, "status": "canonical_safe", "reason": "arbitration_disabled"}
        edge["arbitration_gate"] = gate
        edge["arbitration_enabled"] = bool(arbitration_policy.get("enabled", True))
        edge["arbitration_eligible"] = bool(gate["eligible"])
        edge["arbitration_status"] = gate["status"]
        edge["arbitration_reason"] = gate["reason"]

    views: dict[str, list[dict]] = {kind: [] for kind in EDGE_KINDS}
    for edge in edges:
        kind = edge.get("kind")
        if kind in views:
            views[kind].append(_edge_ref(edge))

    retained = [e for e in edges if e.get("kind") in RETAINED_EDGE_KINDS and e.get("evidence_grade") != "none"]
    grounded = [e for e in edges if e.get("kind") in CORE_EDGE_KINDS and e.get("evidence_grade") != "none"]
    span_core = [
        e for e in edges
        if e.get("kind") in CORE_EDGE_KINDS
        and e.get("evidence_grade") == "span_grounded"
        and has_complete_text_contract(e)
    ]
    assertion_core = [
        e for e in edges
        if _is_evidence_core_edge(e) and e.get("evidence_grade") in {"assertion_grounded", "span_only"}
    ]
    derived_core = [
        e for e in edges
        if _is_evidence_core_edge(e) and (e.get("kind") == "transitive_derived" or e.get("evidence_grade") == "derived_path")
    ]
    evidence_core = [e for e in edges if _is_evidence_core_edge(e)]
    evidence_contract_core = [
        e for e in edges
        if e.get("kind") in RETAINED_EDGE_KINDS and has_complete_evidence_contract(e)
    ]
    soft_core = [e for e in edges if _is_soft_core_edge(e)]
    soft_core_lenient = [e for e in edges if _is_soft_core_lenient_edge(e)]
    partial_forward_core = retained + [e for e in edges if _partial_forward_eligible(e)]
    path_core = [
        e for e in edges
        if e.get("kind") == "transitive_derived" and e.get("derivation_edge_ids")
    ]
    path_review = [
        e for e in edges
        if e.get("path_summary") and not e.get("path_summary_explicit_direct")
    ]
    soft_core_precision = [
        e for e in edges if _is_soft_core_precision_edge(e)
    ]
    support_aware_core = [e for e in edges if e.get("support_aware_eligible")]
    assertion_recovery = [e for e in edges if e.get("assertion_recovery")]
    identity_review = [
        e for e in edges
        if e.get("identity_status") in {"self_loop", "duplicate_identity", "conflict", "duplicate_conflict"}
    ]
    condition_review = [
        e for e in edges
        if e.get("condition_status") == "missing"
        or (e.get("kind") in {"conditional", "regime"} and not e.get("condition_id"))
    ]
    directness_review = [
        e for e in edges
        if e.get("kind") not in {"rejected", "unsupported_hypothesis"}
        and e.get("directness_class") in {"span_only", "path_only", "context_only", "none"}
    ]
    shortcut_review = [e for e in edges if e.get("shortcut_risk") in {"high", "medium"}]
    verified_core = [
        e for e in edges
        if e.get("kind") == "direct_core"
        and e.get("semantic_support") == "entailed"
        and e.get("direction_status") in {"supported", "corrected"}
    ]
    contract_verified_core = [
        e for e in verified_core if has_complete_evidence_contract(e)
    ]
    contract_support_aware_core = [
        e for e in support_aware_core if has_complete_evidence_contract(e)
    ]
    canonical_soft_core = [e for e in soft_core if e.get("arbitration_eligible", True)]
    canonical_verified_core = [e for e in verified_core if e.get("arbitration_eligible", True)]
    canonical_multi_document_core = [
        e for e in canonical_soft_core if e.get("canonical_multi_document_support")
    ]
    candidate_safe_core = [
        e for e in support_aware_core
        if e.get("kind") == "candidate"
        and e.get("candidate_tier") in {"high", "medium"}
        and e.get("arbitration_eligible", True)
    ]
    candidate_risk_review = [
        e for e in edges if e.get("candidate_risk_flags")
    ]
    arbitration_review = [
        e for e in edges
        if not e.get("arbitration_eligible", True) or e.get("candidate_risk_flags")
    ]
    for edge in edges:
        edge["canonical_soft_core_eligible"] = edge in canonical_soft_core
        edge["canonical_verified_core_eligible"] = edge in canonical_verified_core
        edge["canonical_multi_document_core_eligible"] = edge in canonical_multi_document_core
        edge["candidate_safe_core_eligible"] = edge in candidate_safe_core
    for edge in edges:
        if edge.get("kind") in RETAINED_EDGE_KINDS and edge.get("evidence_grade") != "none":
            edge["promotion_status"] = "core"
            edge["soft_core_eligible"] = True
            edge["partial_forward_promotion"] = False
        elif edge.get("kind") == "candidate" and edge.get("candidate_tier") in {"high", "medium"}:
            edge["promotion_status"] = "soft_core"
            edge["soft_core_eligible"] = True
            edge["partial_forward_promotion"] = _partial_forward_eligible(edge)
        elif _partial_forward_eligible(edge):
            edge["promotion_status"] = "partial_forward_core"
            edge["soft_core_eligible"] = False
            edge["partial_forward_promotion"] = True
        elif edge.get("verifier_support") == "uncertain" or edge.get("verification_status") == "needs_review":
            edge["promotion_status"] = "needs_review"
            edge["soft_core_eligible"] = False
            edge["partial_forward_promotion"] = False
        else:
            edge["promotion_status"] = "excluded"
            edge["soft_core_eligible"] = False
            edge["partial_forward_promotion"] = False
    needs_review = [
        e for e in edges
        if e.get("promotion_status") == "needs_review"
        or e.get("verifier_support") == "uncertain"
        or e.get("verification_status") in {"needs_review", "unsupported"}
        or (e.get("kind") == "candidate" and e.get("candidate_tier") == "low")
        or e.get("support_aware_status") == "needs_review"
    ]
    direction_review = [
        e for e in edges
        if e.get("direction_status") == "ambiguous" and e.get("kind") in CORE_EDGE_KINDS
    ]

    views["retained"] = [_edge_ref(e) for e in retained]
    views["grounded"] = [_edge_ref(e) for e in grounded]
    views["span_core"] = [_edge_ref(e) for e in span_core]
    views["assertion_core"] = [_edge_ref(e) for e in assertion_core]
    views["derived_core"] = [_edge_ref(e) for e in derived_core]
    views["evidence_core"] = [_edge_ref(e) for e in evidence_core]
    views["evidence_contract_core"] = [_edge_ref(e) for e in evidence_contract_core]
    views["soft_core"] = [_edge_ref(e) for e in soft_core]
    views["soft_core_lenient"] = [_edge_ref(e) for e in soft_core_lenient]
    views["directness_review"] = [_edge_ref(e) for e in directness_review]
    views["shortcut_review"] = [_edge_ref(e) for e in shortcut_review]
    views["partial_forward_core"] = [_edge_ref(e) for e in partial_forward_core]
    views["path_core"] = [_edge_ref(e) for e in path_core]
    views["path_review"] = [_edge_ref(e) for e in path_review]
    views["soft_core_precision"] = [_edge_ref(e) for e in soft_core_precision]
    views["support_aware_core"] = [_edge_ref(e) for e in support_aware_core]
    views["contract_support_aware_core"] = [_edge_ref(e) for e in contract_support_aware_core]
    views["assertion_recovery"] = [_edge_ref(e) for e in assertion_recovery]
    views["identity_review"] = [_edge_ref(e) for e in identity_review]
    views["condition_review"] = [_edge_ref(e) for e in condition_review]
    views["verified_core"] = [_edge_ref(e) for e in verified_core]
    views["contract_verified_core"] = [_edge_ref(e) for e in contract_verified_core]
    views["canonical_soft_core"] = [_edge_ref(e) for e in canonical_soft_core]
    views["canonical_verified_core"] = [_edge_ref(e) for e in canonical_verified_core]
    views["canonical_multi_document_core"] = [_edge_ref(e) for e in canonical_multi_document_core]
    views["candidate_safe_core"] = [_edge_ref(e) for e in candidate_safe_core]
    views["candidate_risk_review"] = [_edge_ref(e) for e in candidate_risk_review]
    views["arbitration_review"] = [_edge_ref(e) for e in arbitration_review]
    views["candidate_high"] = [_edge_ref(e) for e in edges if e.get("kind") == "candidate" and e.get("candidate_tier") == "high"]
    views["candidate_medium"] = [_edge_ref(e) for e in edges if e.get("kind") == "candidate" and e.get("candidate_tier") == "medium"]
    views["candidate_low"] = [_edge_ref(e) for e in edges if e.get("kind") == "candidate" and e.get("candidate_tier") == "low"]
    views["needs_review"] = [_edge_ref(e) for e in needs_review]
    views["direction_review"] = [_edge_ref(e) for e in direction_review]

    tag_map: dict[str, set[str]] = {e["id"]: set() for e in edges}
    for name, rows in views.items():
        if name in EDGE_KINDS:
            for row in rows:
                tag_map.setdefault(row["edge_id"], set()).add(name)
    for name in ("span_core", "assertion_core", "derived_core", "evidence_core", "evidence_contract_core", "soft_core", "soft_core_lenient", "soft_core_precision", "support_aware_core", "contract_support_aware_core", "canonical_soft_core", "canonical_verified_core", "canonical_multi_document_core", "candidate_safe_core", "candidate_risk_review", "arbitration_review", "assertion_recovery", "identity_review", "condition_review", "directness_review", "shortcut_review", "partial_forward_core", "path_core", "path_review", "verified_core", "contract_verified_core", "candidate_high", "candidate_medium", "candidate_low", "needs_review"):
        for row in views.get(name, []):
            tag_map.setdefault(row["edge_id"], set()).add(name)
    for edge in edges:
        edge["view_tags"] = sorted(tag_map.get(edge["id"], set()))

    for rows in views.values():
        rows.sort(key=lambda row: (row["source"], row["target"], row["edge_id"]))
    artifact["views"] = views
    artifact.setdefault("diagnostics", {})["view_counts"] = {
        name: len(rows) for name, rows in views.items()
    }
    artifact["diagnostics"]["promotion_gate_counts"] = promotion_counts
    artifact["diagnostics"]["promotion_policy"] = policy
    return views



_original_materialize_views = materialize_views

def repair_core_graph(artifact: dict) -> dict:
    """Remove self-loops and break cycles in the direct-core view.

    This is an artifact-level repair. It never changes the evidence ledger.
    Removed edges remain in the artifact as ``rejected`` with a decision record.
    """
    import networkx as nx

    removed_self = 0
    decisions = artifact.setdefault("cycle_decisions", [])
    for edge in artifact.get("edges", []):
        if edge.get("kind") in RETAINED_EDGE_KINDS and edge.get("source") == edge.get("target"):
            edge["kind"] = "rejected"
            edge["edge_kind"] = "rejected"
            edge["semantic_support"] = "contradicted"
            edge["verification_status"] = "rejected"
            edge["provenance"] = "self_loop_removed"
            decisions.append({
                "id": stable_urn("cycle", edge["id"], "self_loop"),
                "classification": "self_loop_removed",
                "pair": [edge["source"], edge["target"]],
                "removed_edge": edge["id"],
                "regime_dependent": False,
                "reason": "retained self-loop violates the core DAG contract",
                "quality": {},
                "rule": "reject_self_loop",
            })
            removed_self += 1

    removed_cycle = 0
    while True:
        core = [e for e in artifact.get("edges", []) if e.get("kind") == "direct_core"]
        graph = nx.DiGraph()
        for edge in core:
            graph.add_edge(edge["source"], edge["target"], edge_id=edge["id"])
        if nx.is_directed_acyclic_graph(graph):
            break
        cycle_nodes = next((set(c) for c in nx.strongly_connected_components(graph) if len(c) > 1), set())
        if not cycle_nodes:
            break
        candidates = [
            e for e in core
            if e["source"] in cycle_nodes and e["target"] in cycle_nodes
        ]
        if not candidates:
            break
        weakest = min(
            candidates,
            key=lambda e: (
                len(e.get("support_doc_ids") or []),
                len(e.get("support_assertion_ids") or []),
                e.get("id", ""),
            ),
        )
        weakest["kind"] = "rejected"
        weakest["edge_kind"] = "rejected"
        weakest["semantic_support"] = "contradicted"
        weakest["verification_status"] = "rejected"
        weakest["provenance"] = "cycle_removed"
        decisions.append({
            "id": stable_urn("cycle", weakest["id"], "core_repair"),
            "classification": "contradiction_removed",
            "pair": [weakest["source"], weakest["target"]],
            "removed_edge": weakest["id"],
            "regime_dependent": False,
            "reason": "weakest direct-core edge removed to satisfy the DAG contract",
            "quality": {},
            "rule": "support_then_assertion_lexical_tiebreak",
        })
        removed_cycle += 1
    materialize_views(artifact)
    return {"self_loops_removed": removed_self, "cycle_edges_removed": removed_cycle}


def entity_similarity_candidates(artifact: dict, threshold: float = 0.9) -> list[dict]:
    entities = artifact.get("entities", [])
    rows = []
    for i, a in enumerate(entities):
        for b in entities[i + 1:]:
            va = [a.get("label", "")] + list(a.get("aliases") or [])
            vb = [b.get("label", "")] + list(b.get("aliases") or [])
            best = 0.0
            for x in va:
                for y in vb:
                    best = max(best, SequenceMatcher(None, norm_text(x), norm_text(y)).ratio())
            if best >= threshold:
                conflict = _identity_conflict(a.get("label", ""), b.get("label", ""))
                rows.append({
                    "a": a["id"], "b": b["id"],
                    "a_label": a.get("label"), "b_label": b.get("label"),
                    "similarity": round(best, 4),
                    "status": "identity_conflict" if conflict else "needs_adjudication",
                    "identity_conflict": conflict,
                })
    return sorted(rows, key=lambda x: (-x["similarity"], x["a_label"], x["b_label"]))


def _collect_edge_evidence(artifact: dict, edge: dict) -> tuple[list[str], list[str], list[dict]]:
    """Collect direct, candidate, assertion and path evidence for one claim."""
    spans_by_id = {s["id"]: s for s in artifact.get("spans", [])}
    assertions_by_id = {a["id"]: a for a in artifact.get("assertions", [])}
    edges_by_id = {e["id"]: e for e in artifact.get("edges", [])}
    span_ids: list[str] = []
    assertion_ids: list[str] = []
    path_edges: list[dict] = []

    def add_span(sid: object) -> None:
        text = as_text(sid)
        if text and text not in span_ids:
            span_ids.append(text)

    def add_assertion(aid: object) -> None:
        text = as_text(aid)
        if not text:
            return
        if text not in assertion_ids:
            assertion_ids.append(text)
        assertion = assertions_by_id.get(text) or {}
        add_span(assertion.get("span_id"))

    for sid in edge.get("span_ids") or []:
        add_span(sid)
    for sid in edge.get("candidate_span_ids") or []:
        add_span(sid)
    for sid in edge.get("evidence_union_ids") or []:
        add_span(sid)
    for aid in (edge.get("support_assertion_ids") or []) + (edge.get("candidate_assertion_ids") or []):
        add_assertion(aid)
    for dep_id in edge.get("derivation_edge_ids") or []:
        dep = edges_by_id.get(dep_id)
        if not dep:
            continue
        path_edges.append(dep)
        for sid in (dep.get("span_ids") or []) + (dep.get("candidate_span_ids") or []):
            add_span(sid)
        for aid in (dep.get("support_assertion_ids") or []) + (dep.get("candidate_assertion_ids") or []):
            add_assertion(aid)
    return span_ids, assertion_ids, path_edges


def build_verification_requests(artifact: dict) -> list[dict]:
    """Build evidence-union requests for direct, composed and conditional claims."""
    entities = {e["id"]: e for e in artifact.get("entities", [])}
    spans = {s["id"]: s for s in artifact.get("spans", [])}
    assertions = {a["id"]: a for a in artifact.get("assertions", [])}
    edge_by_id = {e["id"]: e for e in artifact.get("edges", [])}
    requests = []
    for edge in artifact.get("edges", []):
        if edge.get("kind") not in {"direct_core", "conditional", "regime", "transitive_derived", "candidate"}:
            continue
        span_ids, assertion_ids, path_edges = _collect_edge_evidence(artifact, edge)
        has_candidate = bool(
            edge.get("candidate_assertion_ids") or edge.get("candidate_span_ids")
            or edge.get("candidate_doc_ids") or edge.get("candidate_derivation_ids")
        )
        if not span_ids and not assertion_ids and not has_candidate:
            continue
        claim_scope = edge.get("claim_scope") or _claim_scope_for(str(edge.get("kind") or ""), edge.get("attributes") or {})
        path_required = claim_scope == "composed" or bool(edge.get("derivation_edge_ids") or edge.get("candidate_derivation_ids"))
        path_rows = [
            {
                "edge_id": dep["id"],
                "source": entities.get(dep.get("source"), {}).get("label", dep.get("source")),
                "target": entities.get(dep.get("target"), {}).get("label", dep.get("target")),
                "kind": dep.get("kind"),
                "claim_scope": dep.get("claim_scope", "unknown"),
                "evidence_grade": dep.get("evidence_grade", "none"),
                "verification_status": dep.get("verification_status", "unverified"),
            }
            for dep in path_edges
        ]
        evidence_windows = (edge.get("evidence_windows") or [])[:6]
        best_window_id = edge.get("best_evidence_window_id")
        best_window = next((row for row in evidence_windows if row.get("evidence_window_id") == best_window_id), None)
        if best_window is None:
            best_window = next((row for row in evidence_windows if row.get("complete")), (evidence_windows[0] if evidence_windows else None))
        request = {
            "edge_id": edge["id"],
            "source": entities.get(edge.get("source"), {}).get("label", edge.get("source")),
            "target": entities.get(edge.get("target"), {}).get("label", edge.get("target")),
            "kind": edge.get("kind"),
            "claim_scope": claim_scope,
            "evidence_grade": edge.get("evidence_grade", "none"),
            "evidence_completeness": edge.get("evidence_completeness", "absent"),
            "support_scope": edge.get("support_scope", "none"),
            "condition_grounding": edge.get("condition_grounding", "not_applicable"),
            "evidence_coverage": edge.get("evidence_coverage") or {},
            "missing_evidence_components": edge.get("missing_evidence_components") or [],
            "evidence_windows": evidence_windows,
            "best_evidence_window": best_window,
            "best_window_kind": edge.get("best_window_kind", "none"),
            "best_window_source": edge.get("best_window_source", "none"),
            "best_window_width": int(edge.get("best_window_width", 0) or 0),
            "evidence_exact_match": bool(edge.get("evidence_exact_match", False)),
            "context_window_complete": bool(edge.get("context_window_complete", False)),
            "evidence_packaging_stage": edge.get("evidence_packaging_stage", ""),
            "support_mode": edge.get("support_mode", ""),
            "candidate_support_mode": edge.get("candidate_support_mode", edge.get("support_mode", "")),
            "candidate_tier": edge.get("candidate_tier", "none"),
            "relation_role_source": edge.get("relation_role_source", "unknown"),
            "relation_role_target": edge.get("relation_role_target", "unknown"),
            "role_direction": edge.get("role_direction", "abstain"),
            "directness_class": edge.get("directness_class", "none"),
            "shortcut_risk": edge.get("shortcut_risk", "low"),
            "temporal_mode": edge.get("temporal_mode", "unknown"),
            "direction_candidates": [
                {
                    "candidate_id": "forward",
                    "source": entities.get(edge.get("source"), {}).get("label", edge.get("source")),
                    "target": entities.get(edge.get("target"), {}).get("label", edge.get("target")),
                    "claim": (edge.get("attributes") or {}).get("description", "") or (edge.get("attributes") or {}).get("mechanism", ""),
                },
                {
                    "candidate_id": "reverse",
                    "source": entities.get(edge.get("target"), {}).get("label", edge.get("target")),
                    "target": entities.get(edge.get("source"), {}).get("label", edge.get("source")),
                    "claim": "Reverse structural candidate; evidence must establish target -> source.",
                },
            ],
            "path_required": path_required,
            "path_complete": bool(path_edges) and len(path_edges) == len(edge.get("derivation_edge_ids") or []),
            "path_edge_ids": [dep["id"] for dep in path_edges],
            "path": path_rows,
            "direct_evidence_available": bool(edge.get("span_ids") or edge.get("candidate_span_ids")),
            "assertion_evidence_available": bool(edge.get("support_assertion_ids") or edge.get("candidate_assertion_ids")),
            "description": (edge.get("attributes") or {}).get("description", ""),
            "mechanism": (edge.get("attributes") or {}).get("mechanism", ""),
            "claim_text": (edge.get("attributes") or {}).get("description", "") or (edge.get("attributes") or {}).get("mechanism", ""),
            "condition": next((c.get("raw") for c in artifact.get("conditions", []) if c.get("id") == edge.get("condition_id")), ""),
            "spans": [
                {
                    "span_id": sid,
                    "doc_id": spans.get(sid, {}).get("doc_id"),
                    "text": spans.get(sid, {}).get("text", ""),
                    "context": spans.get(sid, {}).get("context", ""),
                    "role": "direct_or_path",
                }
                for sid in span_ids
            ],
            "assertions": [
                {
                    "assertion_id": aid,
                    "source": assertions.get(aid, {}).get("source"),
                    "target": assertions.get(aid, {}).get("target"),
                    "doc_id": assertions.get(aid, {}).get("doc_id"),
                    "span_id": assertions.get(aid, {}).get("span_id"),
                    "channel": assertions.get(aid, {}).get("channel"),
                    "source_record": assertions.get(aid, {}).get("source_record"),
                    "order": assertions.get(aid, {}).get("order"),
                    "sign": assertions.get(aid, {}).get("sign"),
                    "rung": assertions.get(aid, {}).get("rung"),
                    "mechanism": assertions.get(aid, {}).get("mechanism"),
                    "text": spans.get(assertions.get(aid, {}).get("span_id"), {}).get("text", ""),
                }
                for aid in assertion_ids
            ],
            "evidence_union_ids": sorted(set(span_ids) | set(assertion_ids)),
            "verification_goal": "path" if path_required else "direct",
            "verdict": "untested",
            "support": "untested",
            "direct_support": "untested",
            "assertion_support": "untested",
            "path_support": "untested",
            "direction": "unspecified",
            "direction_support_forward": 0,
            "direction_support_reverse": 0,
            "coverage": "unknown",
        }
        requests.append(request)
    artifact.setdefault("diagnostics", {})["verification_request_count"] = len(requests)
    return requests

def _unique_values(*values: object) -> list[str]:
    merged: set[str] = set()
    for value in values:
        if isinstance(value, (list, tuple, set)):
            merged.update(as_text(v) for v in value if as_text(v))
        elif as_text(value):
            merged.add(as_text(value))
    return sorted(merged)


def _move_authority_to_candidates(edge: dict) -> None:
    """Retain evidence as candidates while preserving its provenance tier."""
    edge.setdefault("candidate_support_mode", edge.get("support_mode", ""))
    edge.setdefault("candidate_support_strength", edge.get("support_strength", ""))
    edge.setdefault("candidate_origin", edge.get("provenance", "unknown"))
    pairs = (
        ("support_assertion_ids", "candidate_assertion_ids"),
        ("support_doc_ids", "candidate_doc_ids"),
        ("span_ids", "candidate_span_ids"),
    )
    for authority, candidate in pairs:
        edge[candidate] = _unique_values(edge.get(candidate), edge.get(authority))
        edge[authority] = []
    edge["candidate_evidence_ids"] = _unique_values(
        edge.get("candidate_evidence_ids"),
        edge.get("candidate_assertion_ids"),
        edge.get("candidate_span_ids"),
        edge.get("candidate_doc_ids"),
        edge.get("candidate_derivation_ids"),
    )


def _restore_candidates_to_authority(edge: dict) -> None:
    """Promote candidate evidence only after a verifier accepts the edge."""
    pairs = (
        ("candidate_assertion_ids", "support_assertion_ids"),
        ("candidate_doc_ids", "support_doc_ids"),
        ("candidate_span_ids", "span_ids"),
    )
    for candidate, authority in pairs:
        edge[authority] = _unique_values(edge.get(authority), edge.get(candidate))
    if edge.get("support_assertion_ids") and edge.get("span_ids"):
        edge["support_mode"] = "direct_span"
        edge["support_strength"] = "strong"
    elif edge.get("support_assertion_ids"):
        edge["support_mode"] = "assertion_only"
        edge["support_strength"] = "medium"
    elif edge.get("span_ids"):
        edge["support_mode"] = "span_only"
        edge["support_strength"] = "medium"
    _refresh_edge_semantics(edge)


def _backfill_exact_direction_assertions(
    edge: dict,
    assertion_index: dict[tuple[str, str], list[dict]],
    span_index: dict[str, dict],
) -> list[str]:
    """Rebind assertion evidence after alias canonicalization.

    Only assertions in the final edge direction and within the edge's known
    document/span scope are eligible. The backfill is candidate-stage evidence;
    promotion still goes through ``_restore_candidates_to_authority``.
    """
    if edge.get("support_assertion_ids") or edge.get("candidate_assertion_ids"):
        return []

    doc_ids = set(edge.get("support_doc_ids") or edge.get("candidate_doc_ids") or [])
    span_ids = set(edge.get("span_ids") or edge.get("candidate_span_ids") or [])
    for span_id in span_ids:
        span = span_index.get(span_id) or {}
        if span.get("doc_id"):
            doc_ids.add(as_text(span["doc_id"]))
    if not doc_ids and not span_ids:
        return []

    preferred_ids = set(
        as_text(value)
        for value in (
            (edge.get("canonical_support_assertion_ids") or [])
            + (edge.get("canonical_direction_assertion_ids") or [])
        )
        if as_text(value)
    )
    recovered: list[str] = []
    for assertion in assertion_index.get((edge.get("source"), edge.get("target")), []):
        assertion_id = as_text(assertion.get("id"))
        if not assertion_id:
            continue
        if preferred_ids and assertion_id not in preferred_ids:
            continue
        assertion_doc = as_text(assertion.get("doc_id"))
        if doc_ids and assertion_doc and assertion_doc not in doc_ids:
            continue
        assertion_span = as_text(assertion.get("span_id"))
        if span_ids and assertion_span and assertion_span not in span_ids:
            continue
        recovered.append(assertion_id)

    recovered = sorted(set(recovered))
    if recovered:
        edge["candidate_assertion_ids"] = _unique_values(
            edge.get("candidate_assertion_ids"), recovered
        )
        edge["candidate_evidence_ids"] = _unique_values(
            edge.get("candidate_evidence_ids"),
            edge.get("candidate_assertion_ids"),
            edge.get("candidate_span_ids"),
            edge.get("candidate_doc_ids"),
            edge.get("candidate_derivation_ids"),
        )
        edge["verifier_assertion_backfill_ids"] = recovered
    return recovered


def _has_candidate_evidence(edge: dict) -> bool:
    return bool(
        edge.get("candidate_assertion_ids")
        or edge.get("candidate_span_ids")
        or edge.get("candidate_doc_ids")
        or edge.get("candidate_derivation_ids")
    )


def _demote_to_candidate_or_unsupported(
    edge: dict,
    provenance: str,
    semantic_support: str = "candidate",
    direction_status: str | None = None,
    verification_status: str | None = None,
) -> None:
    if edge.get("kind") not in HYPOTHESIS_KINDS and "previous_kind" not in edge:
        edge["previous_kind"] = edge.get("kind")
    original_mode = edge.get("candidate_support_mode") or edge.get("support_mode", "")
    original_strength = edge.get("candidate_support_strength") or edge.get("support_strength", "")
    _move_authority_to_candidates(edge)
    has_candidate = _has_candidate_evidence(edge)
    edge["candidate_support_mode"] = original_mode or edge.get("candidate_support_mode", "")
    edge["candidate_support_strength"] = original_strength or edge.get("candidate_support_strength", "")
    edge["kind"] = "candidate" if has_candidate else "unsupported_hypothesis"
    edge["edge_kind"] = edge["kind"]
    edge["semantic_support"] = "candidate" if has_candidate else "untested"
    edge["support_mode"] = "candidate" if has_candidate else "none"
    edge["support_strength"] = "weak" if has_candidate else "none"
    edge["verification_status"] = verification_status or ("needs_review" if has_candidate else "unsupported")
    edge["provenance"] = provenance
    if direction_status:
        edge["direction_status"] = direction_status
    _refresh_edge_semantics(edge)


def _support_scope_value(item: dict, edge: dict) -> str:
    raw = as_text(
        item.get("support_scope")
        or item.get("supportScope")
        or edge.get("verifier_support_scope")
        or edge.get("support_scope")
        or "none"
    ).lower()
    aliases = {
        "exact": "exact_span",
        "span": "exact_span",
        "exact_window": "exact_sentence_window",
        "sentence_window": "exact_sentence_window",
        "context_window": "context_assisted",
        "context_assisted": "context_assisted",
        "partial": "partial_window",
        "path": "path_derived",
        "candidate_only": "candidate",
    }
    return aliases.get(raw, raw)


def _has_complete_scoped_evidence(edge: dict) -> bool:
    """Whether canonical evidence supports a complete exact/context scope."""
    if edge.get("evidence_completeness") not in {"exact", "context_assisted"}:
        return False
    if edge.get("condition_grounding") == "ungrounded":
        return False
    coverage = edge.get("evidence_coverage") or {}
    return all(bool(coverage.get(key)) for key in ("source", "target", "predicate", "condition"))

def _support_value(item: dict) -> str:
    support = as_text(item.get("support") or item.get("verdict") or item.get("status") or "untested").lower()
    if support in {"supported", "support", "entailed"}:
        return "entailed"
    if support in {"conditional", "moderated"}:
        return "conditional"
    if support in {"transitive", "derived"}:
        return "transitive"
    if support in {"partial", "uncertain", "underdetermined", "unsupported"}:
        return support if support in {"partial", "uncertain"} else "unsupported"
    if support in {"contradicted", "rejected"}:
        return "contradicted"
    return support


def _direction_value(item: dict) -> str:
    direction = as_text(item.get("direction")).lower()
    if direction in {"u->v", "source->target", "forward", "supported"}:
        return "forward"
    if direction in {"v->u", "target->source", "reverse", "reversed"}:
        return "reverse"
    if direction in {"ambiguous", "unknown", "uncertain"}:
        return "ambiguous"
    forward = item.get("direction_support_forward")
    reverse = item.get("direction_support_reverse")
    try:
        forward = float(forward) if forward is not None else None
        reverse = float(reverse) if reverse is not None else None
    except (TypeError, ValueError):
        forward = reverse = None
    if forward is not None and reverse is not None and forward != reverse:
        return "forward" if forward > reverse else "reverse"
    return direction or "unspecified"


def _recommended_kind(edge: dict, support: str, direction: str, policy: str) -> str:
    if support == "entailed":
        if direction == "ambiguous" and policy == "strict":
            return "candidate" if _has_candidate_evidence(edge) else "unsupported_hypothesis"
        if edge.get("claim_scope") == "composed" and edge.get("derivation_edge_ids"):
            return "transitive_derived"
        return "direct_core"
    if support == "conditional":
        return "conditional"
    if support == "transitive":
        return "transitive_derived" if edge.get("derivation_edge_ids") else "candidate"
    if support == "contradicted":
        return "rejected"
    return "candidate" if _has_candidate_evidence(edge) else "unsupported_hypothesis"


def apply_verdicts(
    artifact: dict,
    verdicts: list[dict] | dict,
    direction_policy: str = "balanced",
) -> dict:
    """Apply independent support/direction verdicts.

    D2C_13_2 keeps claim scope, evidence grade, publication kind and verification
    status separate.  A verifier decision changes the last two, not the
    original semantic claim type.
    """
    if direction_policy not in {"strict", "balanced", "advisory"}:
        raise ValueError("direction_policy must be strict, balanced, or advisory")
    if isinstance(verdicts, dict):
        verdicts = [{"edge_id": k, **(v if isinstance(v, dict) else {"verdict": v})} for k, v in verdicts.items()]
    by_id = _edge_by_id(artifact)
    policy_payload = artifact.get("promotion_policy")
    if not isinstance(policy_payload, dict) or not policy_payload:
        policy_payload = (artifact.get("diagnostics") or {}).get("promotion_policy")
    promotion_policy = normalize_policy(policy_payload if isinstance(policy_payload, dict) else {})
    require_contract_for_acceptance = bool(
        promotion_policy.get("require_complete_evidence_contract_for_acceptance")
    )
    applied = 0
    demoted = 0
    assertion_backfilled = 0
    assertion_index = _assertion_index(artifact)
    span_index = {as_text(span.get("id")): span for span in artifact.get("spans", []) if span.get("id")}
    for item in verdicts:
        edge = by_id.get(item.get("edge_id"))
        if not edge:
            continue
        support = _support_value(item)
        if support in {"", "untested"}:
            fallback = item.get("path_support") or item.get("direct_support") or item.get("assertion_support") or "untested"
            support = _support_value({"support": fallback})
        direction = _direction_value(item)
        edge["direct_support"] = as_text(item.get("direct_support") or item.get("directSupport") or "untested").lower()
        edge["assertion_support"] = as_text(item.get("assertion_support") or item.get("assertionSupport") or "untested").lower()
        edge["path_support"] = as_text(item.get("path_support") or item.get("pathSupport") or "untested").lower()
        edge["direction_support_forward"] = item.get("direction_support_forward", item.get("directionSupportForward", 0))
        edge["direction_support_reverse"] = item.get("direction_support_reverse", item.get("directionSupportReverse", 0))
        edge["verification_coverage"] = as_text(item.get("coverage") or item.get("coverage_status") or "unknown").lower()
        edge["verifier_support_scope"] = _support_scope_value(item, edge)
        edge["verifier_window_kind"] = as_text(item.get("best_window_kind") or item.get("window_kind") or edge.get("best_window_kind") or "none").lower()
        edge["verifier_window_id"] = as_text(item.get("evidence_window_id") or item.get("best_evidence_window_id") or edge.get("best_evidence_window_id") or "") or None
        recommended = _recommended_kind(edge, support, direction, direction_policy)
        edge["verifier_support"] = support
        edge["verifier_direction"] = direction
        edge["verifier_recommended_kind"] = recommended
        edge["verification_note"] = as_text(item.get("reason") or item.get("note") or "")
        applied += 1
        if direction_policy == "advisory":
            edge["verification_status"] = "advisory"
            _refresh_edge_semantics(edge)
            continue

        if direction == "ambiguous":
            edge["direction_status"] = "ambiguous"
        elif direction == "reverse":
            edge["original_source"], edge["original_target"] = edge["source"], edge["target"]
            edge["source"], edge["target"] = edge["target"], edge["source"]
            edge["source_name"], edge["target_name"] = edge["target_name"], edge["source_name"]
            edge["direction_status"] = "corrected"
        elif direction == "forward":
            edge["direction_status"] = "supported"

        if require_contract_for_acceptance and support == "entailed" and not has_complete_evidence_contract(edge):
            _demote_to_candidate_or_unsupported(
                edge, "verifier_entailed_without_complete_evidence_contract",
                semantic_support="candidate", verification_status="needs_review",
            )
            demoted += 1
            _refresh_edge_semantics(edge)
            continue
        if require_contract_for_acceptance and support == "conditional" and not has_complete_evidence_contract(edge):
            _demote_to_candidate_or_unsupported(
                edge, "verifier_conditional_without_complete_evidence_contract",
                semantic_support="candidate", verification_status="needs_review",
            )
            demoted += 1
            _refresh_edge_semantics(edge)
            continue
        if support == "entailed":
            if direction == "ambiguous" and direction_policy == "strict":
                _demote_to_candidate_or_unsupported(
                    edge, "verifier_ambiguous_direction",
                    semantic_support="candidate", direction_status="ambiguous",
                    verification_status="needs_review",
                )
                demoted += 1
            else:
                composed_claim = edge.get("claim_scope") == "composed" or bool(edge.get("derivation_edge_ids"))
                path_ok = edge.get("path_support") in {"entailed", "supported", "forward"}
                scoped_ok = (
                    _has_complete_scoped_evidence(edge)
                    and edge.get("verifier_support_scope") in {"exact_span", "exact_sentence_window", "context_assisted"}
                )
                direct_ok = edge.get("direct_support") in {"entailed", "untested", ""} or scoped_ok
                assertion_ok = edge.get("assertion_support") in {"entailed", "partial"}
                if composed_claim:
                    if path_ok:
                        _restore_candidates_to_authority(edge)
                        edge["kind"] = "transitive_derived"
                        edge["edge_kind"] = "transitive_derived"
                        edge["claim_scope"] = "composed"
                        edge["evidence_grade"] = "derived_path"
                        edge["semantic_support"] = "derived"
                        edge["verification_status"] = "accepted"
                    elif assertion_ok:
                        _demote_to_candidate_or_unsupported(
                            edge, "verifier_assertion_only_composed",
                            semantic_support="candidate", verification_status="needs_review",
                        )
                        demoted += 1
                    else:
                        _demote_to_candidate_or_unsupported(
                            edge, "verifier_composed_without_path_support",
                            semantic_support="candidate", verification_status="needs_review",
                        )
                        demoted += 1
                elif direct_ok:
                    if _backfill_exact_direction_assertions(edge, assertion_index, span_index):
                        assertion_backfilled += 1
                    _restore_candidates_to_authority(edge)
                    if edge.get("support_assertion_ids") and edge.get("span_ids"):
                        edge["kind"] = "direct_core"
                        edge["edge_kind"] = "direct_core"
                        edge["evidence_grade"] = _evidence_grade_for(edge)
                        edge["semantic_support"] = "entailed"
                        edge["verification_status"] = "accepted"
                    else:
                        _demote_to_candidate_or_unsupported(
                            edge, "verifier_entailed_without_direct_core_evidence",
                            semantic_support="candidate", verification_status="needs_review",
                        )
                        demoted += 1
                else:
                    _demote_to_candidate_or_unsupported(
                        edge, "verifier_assertion_only_direct",
                        semantic_support="candidate", verification_status="needs_review",
                    )
                    demoted += 1
                if direction == "ambiguous":
                    edge["provenance"] = "verifier_direction_ambiguous"
        elif support == "conditional":
            cid = _condition_for_raw(artifact, as_text(item.get("condition")))
            if not cid:
                _demote_to_candidate_or_unsupported(
                    edge, "verifier_conditional_without_condition",
                    verification_status="needs_review",
                )
                demoted += 1
            else:
                _restore_candidates_to_authority(edge)
                edge["kind"] = "conditional"
                edge["edge_kind"] = "conditional"
                edge["claim_scope"] = "conditional"
                edge["condition_id"] = cid
                edge["semantic_support"] = "conditional"
                edge["verification_status"] = "accepted"
        elif support == "transitive":
            if edge.get("derivation_edge_ids"):
                _restore_candidates_to_authority(edge)
                edge["kind"] = "transitive_derived"
                edge["edge_kind"] = "transitive_derived"
                edge["claim_scope"] = "composed"
                edge["evidence_grade"] = "derived_path"
                edge["semantic_support"] = "derived"
                edge["verification_status"] = "accepted"
            else:
                _demote_to_candidate_or_unsupported(
                    edge, "verifier_transitive_without_path",
                    semantic_support="candidate", verification_status="needs_review",
                )
                demoted += 1
        elif support in {"partial", "uncertain", "unsupported"}:
            assertion_ok = edge.get("assertion_support") in {"entailed", "partial"}
            _demote_to_candidate_or_unsupported(
                edge, f"verifier_{support}", semantic_support="candidate",
                verification_status="needs_review" if support in {"partial", "uncertain"} or assertion_ok else "unsupported",
            )
            if support == "partial" and direction == "forward":
                edge["verification_status"] = "needs_review"
            if assertion_ok and edge.get("evidence_grade") == "assertion_grounded":
                edge["verification_status"] = "needs_review"
            demoted += 1
        elif support == "contradicted":
            _move_authority_to_candidates(edge)
            edge["kind"] = "rejected"
            edge["edge_kind"] = "rejected"
            edge["semantic_support"] = "contradicted"
            edge["verification_status"] = "rejected"
            edge["provenance"] = "verifier_contradicted"
        _refresh_edge_semantics(edge)

    normalize_transitive_derivation(artifact)
    materialize_views(artifact)
    diagnostics = artifact.setdefault("diagnostics", {})
    diagnostics["verdicts_applied"] = applied
    diagnostics["verdicts_demoted"] = demoted
    diagnostics["verifier_assertion_backfilled"] = assertion_backfilled
    diagnostics["verification_direction_policy"] = direction_policy
    diagnostics["verification_status_counts"] = {
        status: sum(1 for e in artifact.get("edges", []) if e.get("verification_status") == status)
        for status in sorted({e.get("verification_status", "unverified") for e in artifact.get("edges", [])})
    }
    diagnostics["evidence_grade_counts"] = {
        grade: sum(1 for e in artifact.get("edges", []) if e.get("evidence_grade") == grade)
        for grade in sorted({e.get("evidence_grade", "none") for e in artifact.get("edges", [])})
    }
    diagnostics["assertion_support_counts"] = {
        value: sum(1 for e in artifact.get("edges", []) if e.get("assertion_support") == value)
        for value in sorted({e.get("assertion_support", "untested") for e in artifact.get("edges", [])})
    }
    return {
        "applied": applied,
        "demoted": demoted,
        "assertion_backfilled": assertion_backfilled,
        "direction_policy": direction_policy,
    }












def _is_soft_core_balanced_edge(edge: dict) -> bool:
    return _is_soft_core_lenient_edge(edge)


def materialize_views(artifact: dict) -> dict:
    result = _original_materialize_views(artifact)
    edges = artifact.get('edges', [])
    views = artifact.setdefault('views', {})
    balanced = [edge for edge in edges if _is_soft_core_balanced_edge(edge)]
    exact_core = [edge for edge in edges if is_exact_support_core(edge)]
    context_core = [edge for edge in edges if is_context_assisted_core(edge)]
    partial_review = [edge for edge in edges if edge.get('evidence_completeness') in {'partial', 'assertion_only'}]
    path_core = [edge for edge in edges if edge.get('evidence_completeness') == 'path_only']
    no_evidence = [edge for edge in edges if edge.get('evidence_completeness') == 'absent']
    safe_balanced = [edge for edge in balanced if edge.get("arbitration_eligible", True)]
    tags = {
        'soft_core_balanced': balanced,
        'canonical_soft_core': safe_balanced,
        'canonical_multi_document_core': [
            edge for edge in safe_balanced if edge.get("canonical_multi_document_support")
        ],
        'exact_support_core': exact_core,
        'context_assisted_core': context_core,
        'partial_evidence_review': partial_review,
        'path_support_core': path_core,
        'no_evidence_queue': no_evidence,
    }
    for edge in edges:
        edge["canonical_soft_core_eligible"] = edge in safe_balanced
        edge["canonical_multi_document_core_eligible"] = (
            edge in tags['canonical_multi_document_core']
        )
    for name, rows in tags.items():
        views[name] = [_edge_ref(edge) for edge in rows]
        for edge in rows:
            edge.setdefault('view_tags', [])
            if name not in edge['view_tags']:
                edge['view_tags'].append(name)
    diagnostics = artifact.setdefault('diagnostics', {})
    for name, rows in tags.items():
        diagnostics.setdefault('view_counts', {})[name] = len(rows)
    return result
