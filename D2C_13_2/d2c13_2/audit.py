# -*- coding: utf-8 -*-
"""Corpus-independent evidence, identity and condition audits."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from .constants import DIRECT_CUES
from .util import as_text, norm_text


def _entity_values(entity: dict) -> list[str]:
    values = [entity.get("label", "")] + list(entity.get("aliases") or [])
    return [norm_text(value) for value in values if len(norm_text(value)) >= 3]


def _span_index(artifact: dict) -> dict[str, dict]:
    return {str(span.get("id")): span for span in artifact.get("spans", [])}


def _mention_count(text: str, values: list[str]) -> int:
    normalized = norm_text(text)
    return sum(1 for value in values if value and value in normalized)


def _has_direct_cue(text: str) -> bool:
    lower = as_text(text).lower()
    return any(re.search(rf"\b{re.escape(cue)}", lower) for cue in DIRECT_CUES)


def _verification_supported(edge: dict, path_complete: bool) -> bool:
    accepted_status = {"accepted", "conditional", "transitive"}
    supported_values = {"entailed", "conditional", "transitive", "derived", "supported", "forward"}
    if as_text(edge.get("verification_status")).lower() in accepted_status:
        return True
    if as_text(edge.get("semantic_support")).lower() in supported_values:
        return True
    if as_text(edge.get("verifier_support")).lower() in supported_values:
        return True
    if path_complete and as_text(edge.get("path_support")).lower() in supported_values:
        return True
    return False


def evidence_attribution_report(artifact: dict) -> dict[str, Any]:
    """Measure evidence support without requiring an explicit causal phrase.

    The redesigned metric treats assertion-backed and cross-sentence evidence as
    first-class evidence for implicit causal knowledge. Lexical cue coverage is
    retained only as a diagnostic. No explicit-predicate contract is used as a
    quality gate.
    """
    entities = {str(entity.get("id")): entity for entity in artifact.get("entities", [])}
    assertions = {str(assertion.get("id")): assertion for assertion in artifact.get("assertions", [])}
    spans = _span_index(artifact)
    by_kind: dict[str, Counter] = defaultdict(Counter)
    invalid_assertion_refs = 0
    invalid_span_refs = 0
    invalid_condition_refs = 0
    edges_with_assertion = 0
    edges_with_span = 0
    edges_with_both_endpoints_in_span = 0
    edges_with_lexical_cue = 0
    edges_with_assertion_span = 0
    edges_with_complete_path = 0
    edges_condition_required = 0
    edges_condition_present = 0
    edges_grounded = 0
    edges_verified = 0
    edges_cross_sentence = 0
    edges_implicit_verified = 0
    edges_total = 0
    transitive_total = 0
    condition_ids = {c.get("id") for c in artifact.get("conditions", [])}
    all_edge_ids = {e.get("id") for e in artifact.get("edges", [])}

    for edge in artifact.get("edges", []):
        kind = as_text(edge.get("kind") or "unknown")
        by_kind[kind]["total"] += 1
        if kind in {"rejected", "unsupported_hypothesis"}:
            continue
        edges_total += 1
        assertion_ids = list(edge.get("support_assertion_ids") or []) + list(edge.get("candidate_assertion_ids") or [])
        span_ids = list(edge.get("span_ids") or []) + list(edge.get("candidate_span_ids") or [])
        assertion_ids = list(dict.fromkeys(as_text(x) for x in assertion_ids if as_text(x)))
        span_ids = list(dict.fromkeys(as_text(x) for x in span_ids if as_text(x)))
        valid_assertion_ids = [aid for aid in assertion_ids if aid in assertions]
        valid_span_ids = [sid for sid in span_ids if sid in spans]
        invalid_assertion_refs += len(assertion_ids) - len(valid_assertion_ids)
        invalid_span_refs += len(span_ids) - len(valid_span_ids)

        if valid_assertion_ids:
            edges_with_assertion += 1
            by_kind[kind]["assertion"] += 1
        if valid_span_ids:
            edges_with_span += 1
            by_kind[kind]["span"] += 1
        if edge.get("condition_id") and edge.get("condition_id") not in condition_ids:
            invalid_condition_refs += 1
        if kind in {"conditional", "regime"}:
            edges_condition_required += 1
            if edge.get("condition_id"):
                edges_condition_present += 1

        referenced = {str(x) for x in edge.get("derivation_edge_ids") or []}
        path_complete = bool(referenced and referenced.issubset(all_edge_ids))
        if kind == "transitive_derived":
            transitive_total += 1
            if path_complete:
                edges_with_complete_path += 1

        source_values = _entity_values(entities.get(str(edge.get("source")), {}))
        target_values = _entity_values(entities.get(str(edge.get("target")), {}))
        span_texts = [as_text((spans.get(sid) or {}).get("text")) for sid in valid_span_ids]
        assertion_span_ids = [as_text((assertions.get(aid) or {}).get("span_id")) for aid in valid_assertion_ids]
        if any(sid in spans for sid in assertion_span_ids):
            edges_with_assertion_span += 1
        if any(_mention_count(text, source_values) and _mention_count(text, target_values) for text in span_texts):
            edges_with_both_endpoints_in_span += 1
            by_kind[kind]["endpoints_in_span"] += 1

        claim_text = " ".join(span_texts + [
            as_text((edge.get("attributes") or {}).get("description")),
            as_text((edge.get("attributes") or {}).get("mechanism")),
        ])
        lexical_cue = _has_direct_cue(claim_text)
        if lexical_cue:
            edges_with_lexical_cue += 1
            by_kind[kind]["lexical_cue"] += 1

        evidence_present = bool(valid_assertion_ids or valid_span_ids or path_complete)
        if valid_assertion_ids or path_complete:
            source_attested = target_attested = True
        else:
            source_attested = any(_mention_count(text, source_values) for text in span_texts)
            target_attested = any(_mention_count(text, target_values) for text in span_texts)
        condition_grounded = True
        if kind in {"conditional", "regime"}:
            condition_grounded = bool(
                edge.get("condition_id")
                and edge.get("condition_id") in condition_ids
                and as_text(edge.get("condition_grounding")) in {"grounded", "context_grounded"}
            )
        consistency_ok = (
            as_text(edge.get("temporal_consistency")) != "inconsistent"
            and as_text(edge.get("fraction_consistency")) != "inconsistent"
        )
        grounded = bool(evidence_present and source_attested and target_attested and condition_grounded and consistency_ok)
        verified = bool(grounded and _verification_supported(edge, path_complete))
        cross_sentence = bool(
            verified and (
                len(valid_span_ids) > 1
                or len(valid_assertion_ids) > 1
                or int(edge.get("best_window_width") or 0) > 1
                or bool(edge.get("context_window_complete"))
            )
        )
        implicit_verified = bool(verified and not lexical_cue)

        if grounded:
            edges_grounded += 1
            by_kind[kind]["grounded"] += 1
        if verified:
            edges_verified += 1
            by_kind[kind]["verified"] += 1
        if cross_sentence:
            edges_cross_sentence += 1
            by_kind[kind]["cross_sentence"] += 1
        if implicit_verified:
            edges_implicit_verified += 1
            by_kind[kind]["implicit_verified"] += 1

    def rate(value: int, total: int) -> float:
        return round(value / total, 4) if total else 0.0

    return {
        "protocol": "intrinsic evidence support audit; no explicit-predicate requirement and no gold labels",
        "edges_total": edges_total,
        "assertion_coverage": rate(edges_with_assertion, edges_total),
        "span_coverage": rate(edges_with_span, edges_total),
        "assertion_span_coverage": rate(edges_with_assertion_span, edges_total),
        "endpoint_span_coverage": rate(edges_with_both_endpoints_in_span, edges_total),
        "grounded_evidence_coverage": rate(edges_grounded, edges_total),
        "verified_evidence_coverage": rate(edges_verified, edges_total),
        "cross_sentence_evidence_coverage": rate(edges_cross_sentence, edges_total),
        "implicit_verified_coverage": rate(edges_implicit_verified, edges_total),
        "lexical_cue_coverage": rate(edges_with_lexical_cue, edges_total),
        "condition_coverage": rate(edges_condition_present, edges_condition_required),
        "complete_path_rate": rate(edges_with_complete_path, transitive_total),
        "invalid_assertion_refs": invalid_assertion_refs,
        "invalid_span_refs": invalid_span_refs,
        "invalid_condition_refs": invalid_condition_refs,
        "by_kind": {kind: dict(counter) for kind, counter in sorted(by_kind.items())},
    }


def entity_identity_report(artifact: dict) -> dict[str, Any]:
    """Report serialized identity coverage and possible over/under-merging."""
    entities = artifact.get("entities", [])
    identities = []
    missing_identity = 0
    for entity in entities:
        key = as_text(entity.get("identity_key"))
        if not key:
            missing_identity += 1
        identities.append((
            key,
            norm_text(entity.get("label", "")),
            tuple(entity.get("time_indices") or []),
            bool((entity.get("state_signature") or {}).get("negation")),
            tuple((entity.get("state_signature") or {}).get("state_terms") or []),
        ))
    key_groups: dict[str, list[tuple]] = defaultdict(list)
    label_groups: dict[str, list[tuple]] = defaultdict(list)
    for item in identities:
        key_groups[item[0]].append(item[1:])
        label_groups[item[1]].append(item[0])
    duplicate_keys = {key: len(values) for key, values in key_groups.items() if key and len(values) > 1}
    duplicate_labels = {label: sorted(set(keys)) for label, keys in label_groups.items() if len(keys) > 1}
    same_label_split = sum(1 for keys in duplicate_labels.values() if len(keys) > 1)
    return {
        "protocol": "intrinsic entity identity audit; no gold labels",
        "entities": len(entities),
        "identity_key_coverage": round((len(entities) - missing_identity) / len(entities), 4) if entities else 0.0,
        "missing_identity_keys": missing_identity,
        "duplicate_identity_keys": duplicate_keys,
        "same_label_split_entities": same_label_split,
        "status": "review" if duplicate_keys or same_label_split else "ok",
    }


def condition_equivalence_report(artifact: dict) -> dict[str, Any]:
    """Report condition normalization coverage and intrinsic equivalence stability."""
    conditions = artifact.get("conditions", [])
    by_key: dict[str, list[dict]] = defaultdict(list)
    token_groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for condition in conditions:
        key = as_text(condition.get("equivalence_key"))
        normal = norm_text(condition.get("normalized") or condition.get("raw") or "")
        tokens = tuple(sorted(set(t for t in normal.split() if t)))
        by_key[key].append(condition)
        token_groups[tokens].append(key)
    collisions = {key: len({norm_text(c.get("normalized")) for c in rows}) for key, rows in by_key.items() if key and len(rows) > 1}
    fragmentation = {
        " ".join(tokens): sorted(set(keys))
        for tokens, keys in token_groups.items()
        if tokens and len(set(keys)) > 1
    }
    total = len(conditions)
    cluster_count = len(token_groups) if token_groups else 0
    fragmentation_rate = round(max(0, total - cluster_count) / total, 4) if total else 0.0
    collision_rate = round(sum(1 for value in collisions.values() if value > 1) / len(by_key), 4) if by_key else 0.0
    required = [e for e in artifact.get("edges", []) if e.get("kind") in {"conditional", "regime"}]
    covered = [e for e in required if e.get("condition_id")]
    return {
        "protocol": "intrinsic condition-equivalence audit; no gold labels",
        "conditions": total,
        "equivalence_keys": len(by_key),
        "fragmentation_rate": fragmentation_rate,
        "key_collisions": sum(1 for value in collisions.values() if value > 1),
        "key_collision_rate": collision_rate,
        "fragmented_groups": fragmentation,
        "conditional_edge_coverage": round(len(covered) / len(required), 4) if required else None,
        "conditional_edges": len(required),
    }


def artifact_audit(artifact: dict) -> dict[str, Any]:
    """Return all non-gold audits together for a pipeline summary."""
    return {
        "evidence_attribution": evidence_attribution_report(artifact),
        "entity_identity": entity_identity_report(artifact),
        "condition_equivalence": condition_equivalence_report(artifact),
    }
