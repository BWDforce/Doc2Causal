# -*- coding: utf-8 -*-
"""Deterministic evidence packaging and completeness assessment for D2C_13_2."""

from __future__ import annotations

import re
from collections import Counter
from copy import deepcopy
from typing import Any

from .constants import DIRECT_CUES, CONDITIONAL_CUES
from .util import as_text, norm_text, stable_urn

EVIDENCE_COMPLETENESS = (
    "exact", "context_assisted", "partial", "assertion_only",
    "candidate_only", "path_only", "absent",
)
EVIDENCE_PACKAGING_VERSION = "D2C_13_2-evidence-v3"
EXACT_WINDOW_KINDS = {"exact_span", "exact_window"}
EVIDENCE_FIELDS = (
    "evidence_completeness", "support_scope", "condition_grounding",
    "temporal_consistency", "fraction_consistency",
    "missing_evidence_components", "evidence_coverage",
    "evidence_windows", "evidence_window_count",
    "best_evidence_window_id", "best_window_kind",
    "best_window_source", "best_window_width",
    "evidence_exact_match", "context_window_complete",
    "evidence_contract_complete", "evidence_contract",
    "evidence_packaging_version",
)
PRE_VERIFICATION_PREFIX = "pre_verification_"
PREDICATE_STEMS = tuple(dict.fromkeys(DIRECT_CUES + CONDITIONAL_CUES + (
    "drive", "drives", "drove", "driven", "contribute", "contributes", "contributed",
    "support", "supports", "supported", "maintain", "maintains", "maintained",
    "associate", "associated", "interaction", "interact", "interacts",
    "follow", "followed", "precede", "precedes", "preceded", "because", "therefore",
    "thereby", "dependent", "depend", "depends", "bound", "bounds", "set", "sets",
)))
TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")
FRACTION_TERMS = {"total", "dissolved", "unspecified", "filtered", "particulate", "soluble", "organic", "inorganic", "kjeldahl", "nitrogenous", "bod", "fraction"}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", as_text(value)).strip()


def _tokens(value: Any) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", norm_text(as_text(value))))


def _surface_values(artifact: dict, edge: dict, side: str) -> list[str]:
    entity_id = as_text(edge.get(side))
    entity = next((row for row in artifact.get("entities", []) if as_text(row.get("id")) == entity_id), {})
    values = [edge.get(f"{side}_name"), entity.get("label")]
    values.extend(entity.get("aliases") or [])
    return [as_text(value) for value in values if _clean(value)]


def _surface_hit_detail(text: str, surfaces: list[str]) -> tuple[bool, str, str]:
    key = norm_text(text)
    text_tokens = _tokens(text)
    for surface in surfaces:
        needle = norm_text(surface)
        if not needle:
            continue
        if needle in key:
            return True, surface, "exact"
        surface_tokens = _tokens(surface)
        if len(surface_tokens) < 2:
            continue
        surface_times = _times(surface)
        if surface_times and not surface_times <= _times(text):
            continue
        surface_fractions = _fractions(surface)
        if surface_fractions and not surface_fractions <= _fractions(text):
            continue
        if len(surface_tokens & text_tokens) / len(surface_tokens) >= 0.8:
            return True, surface, "token"
    return False, "", "none"


def _surface_hit(text: str, surfaces: list[str]) -> tuple[bool, str]:
    hit, surface, _ = _surface_hit_detail(text, surfaces)
    return hit, surface


def _predicate_hit(text: str) -> bool:
    key = norm_text(text)
    tokens = set(key.split())
    for stem in PREDICATE_STEMS:
        stem = norm_text(stem)
        if not stem:
            continue
        if (" " in stem and stem in key) or stem in tokens:
            return True
        if len(stem) >= 4 and any(token.startswith(stem[: max(4, len(stem) - 2)]) for token in tokens):
            return True
    return False


def _condition_value(artifact: dict, edge: dict) -> str:
    cid = edge.get("condition_id")
    if not cid:
        return ""
    condition = next((row for row in artifact.get("conditions", []) if row.get("id") == cid), {})
    return _clean(condition.get("raw") or condition.get("normalized"))


def _condition_hit_detail(text: str, condition: str) -> tuple[bool, str]:
    if not condition:
        return True, "not_applicable"
    key, needle = norm_text(text), norm_text(condition)
    if needle and needle in key:
        return True, "exact"
    condition_tokens = _tokens(condition)
    if condition_tokens and len(condition_tokens & _tokens(text)) / len(condition_tokens) >= 0.8:
        return True, "token"
    return False, "none"


def _condition_hit(text: str, condition: str) -> bool:
    return _condition_hit_detail(text, condition)[0]


def _times(value: str) -> set[str]:
    return set(TIME_RE.findall(as_text(value)))


def _fractions(value: str) -> set[str]:
    return {token for token in _tokens(value) if token in FRACTION_TERMS}


def _consistency(side_a: str, side_b: str, text: str, extractor) -> str:
    expected = extractor(side_a) | extractor(side_b)
    if not expected:
        return "not_applicable"
    observed = extractor(text)
    if expected <= observed:
        return "consistent"
    if expected & observed:
        return "partial"
    return "inconsistent"


def _sentence_windows(context: str, span_text: str) -> list[dict]:
    span_text, context = _clean(span_text), _clean(context)
    values = [{"text": span_text, "window_source": "span", "window_width": 0}] if span_text else []
    if not context:
        return values
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n{2,}", context) if part.strip()]
    if not parts:
        return values + [{"text": context, "window_source": "full_context", "window_width": 1}]
    span_norm = norm_text(span_text)
    anchor = next((index for index, part in enumerate(parts) if span_norm and span_norm in norm_text(part)), -1)
    if anchor < 0 and span_text:
        scores = [(len(_tokens(span_text) & _tokens(part)), index) for index, part in enumerate(parts)]
        if scores:
            anchor = max(scores)[1]
    anchor = max(anchor, 0)
    for width in (1, 2, 3):
        for start in range(max(0, anchor - width + 1), min(len(parts), anchor + 1)):
            window = " ".join(parts[start:start + width]).strip()
            if window:
                values.append({"text": window, "window_source": "sentence_window", "window_width": width})
    values.append({"text": context, "window_source": "full_context", "window_width": len(parts)})
    deduped = {}
    for value in values:
        text = _clean(value["text"])
        if not text:
            continue
        previous = deduped.get(text)
        if previous is None or value["window_width"] < previous["window_width"]:
            deduped[text] = {**value, "text": text}
    return list(deduped.values())


def _evaluate_window(artifact: dict, edge: dict, text: str, source_surfaces: list[str], target_surfaces: list[str], condition: str, *, window_source: str = "span", window_width: int = 0) -> dict:
    source_hit, source_surface, source_match = _surface_hit_detail(text, source_surfaces)
    target_hit, target_surface, target_match = _surface_hit_detail(text, target_surfaces)
    predicate_hit = _predicate_hit(text)
    condition_hit, condition_match = _condition_hit_detail(text, condition)
    required = 3 + (1 if condition else 0)
    hit_count = int(source_hit) + int(target_hit) + int(predicate_hit) + (int(condition_hit) if condition else 0)
    complete = source_hit and target_hit and predicate_hit and (condition_hit if condition else True)
    temporal = _consistency(as_text(edge.get("source_name")), as_text(edge.get("target_name")), text, _times)
    fraction = _consistency(as_text(edge.get("source_name")), as_text(edge.get("target_name")), text, _fractions)
    exact_match = bool(
        complete
        and source_match == "exact"
        and target_match == "exact"
        and condition_match in {"exact", "not_applicable"}
        and temporal in {"consistent", "not_applicable"}
        and fraction in {"consistent", "not_applicable"}
    )
    return {
        "text": _clean(text),
        "window_source": window_source,
        "window_width": window_width,
        "source_hit": bool(source_hit),
        "target_hit": bool(target_hit),
        "predicate_hit": bool(predicate_hit),
        "condition_hit": bool(condition_hit) if condition else True,
        "condition_required": bool(condition),
        "complete": bool(complete),
        "exact_match": exact_match,
        "coverage_count": hit_count,
        "coverage_ratio": round(hit_count / required, 4) if required else 0.0,
        "source_surface": source_surface,
        "target_surface": target_surface,
        "source_match": source_match,
        "target_match": target_match,
        "condition_match": condition_match,
        "temporal_consistency": temporal,
        "fraction_consistency": fraction,
    }


def _window_rank(row: dict) -> int:
    kind = row.get("window_kind")
    if kind == "exact_span":
        return 5
    if kind == "exact_window":
        return 4
    if kind == "context" and row.get("complete"):
        return 3
    if kind in {"partial", "context_partial", "original"}:
        return 2
    if kind == "assertion_only":
        return 1
    return 0

def _choose_window(artifact: dict, edge: dict, original_text: str, context: str, source_surfaces: list[str], target_surfaces: list[str], condition: str) -> dict:
    original = _evaluate_window(artifact, edge, original_text, source_surfaces, target_surfaces, condition)
    original["window_kind"] = "exact_span" if original["exact_match"] else "original"
    if original["exact_match"]:
        return original
    candidates = []
    for source in _sentence_windows(context, original_text):
        if _clean(source["text"]) == _clean(original_text):
            continue
        evaluated = _evaluate_window(
            artifact, edge, source["text"], source_surfaces, target_surfaces, condition,
            window_source=source["window_source"], window_width=source["window_width"],
        )
        if evaluated["exact_match"] and source["window_source"] == "sentence_window" and source["window_width"] <= 2:
            evaluated["window_kind"] = "exact_window"
        elif evaluated["complete"]:
            evaluated["window_kind"] = "context"
        else:
            evaluated["window_kind"] = "context_partial"
        candidates.append(evaluated)
    if not candidates:
        return original
    candidates.sort(
        key=lambda row: (
            _window_rank(row),
            row["complete"],
            row["coverage_ratio"],
            row["coverage_count"],
            -row["window_width"],
            -len(row["text"]),
        ),
        reverse=True,
    )
    best = candidates[0]
    if best["window_kind"] == "exact_window" or best["complete"]:
        return best
    if original["coverage_count"] >= 2:
        original["window_kind"] = "partial"
        return original
    best["window_kind"] = "partial"
    return best


def _assertion_rows(artifact: dict, edge: dict) -> list[dict]:
    assertions = {as_text(row.get("id")): row for row in artifact.get("assertions", [])}
    spans = {as_text(row.get("id")): row for row in artifact.get("spans", [])}
    rows = []
    for role, key in (("support", "support_assertion_ids"), ("candidate", "candidate_assertion_ids")):
        for aid in dict.fromkeys(edge.get(key) or []):
            assertion = assertions.get(as_text(aid), {})
            span = spans.get(as_text(assertion.get("span_id")), {})
            rows.append({"role": role, "assertion_id": as_text(aid), "span_id": as_text(assertion.get("span_id") or ""), "doc_id": as_text(assertion.get("doc_id") or span.get("doc_id") or ""), "channel": as_text(assertion.get("channel") or ""), "original_text": _clean(span.get("text")), "context": _clean(span.get("context")), "mechanism": _clean(assertion.get("mechanism"))})
    known_spans = {row["span_id"] for row in rows if row["span_id"]}
    for role, key in (("support", "span_ids"), ("candidate", "candidate_span_ids")):
        for sid in dict.fromkeys(edge.get(key) or []):
            sid = as_text(sid)
            if sid in known_spans:
                continue
            span = spans.get(sid, {})
            rows.append({"role": role, "assertion_id": "", "span_id": sid, "doc_id": as_text(span.get("doc_id") or ""), "channel": "span", "original_text": _clean(span.get("text")), "context": _clean(span.get("context")), "mechanism": ""})
    return rows


def _window_record(artifact: dict, edge: dict, row: dict, source_surfaces: list[str], target_surfaces: list[str], condition: str) -> dict:
    original_text = row["original_text"]
    if original_text:
        selected = _choose_window(artifact, edge, original_text, row["context"], source_surfaces, target_surfaces, condition)
        original = _evaluate_window(artifact, edge, original_text, source_surfaces, target_surfaces, condition)
        selected = {**selected, "original_text": original_text, "original_exact": bool(original.get("exact_match"))}
    elif row.get("mechanism"):
        selected = _evaluate_window(artifact, edge, row["mechanism"], source_surfaces, target_surfaces, condition)
        selected.update({"window_kind": "assertion_only", "window_source": "assertion", "window_width": 0, "original_text": "", "original_exact": False})
    else:
        selected = {"text": "", "source_hit": False, "target_hit": False, "predicate_hit": False, "condition_hit": not bool(condition), "condition_required": bool(condition), "complete": False, "coverage_count": 0, "coverage_ratio": 0.0, "source_surface": "", "target_surface": "", "temporal_consistency": "inconsistent", "fraction_consistency": "inconsistent", "window_kind": "absent", "window_source": "none", "window_width": 0, "original_text": "", "original_exact": False}
    window_id = stable_urn("evidence_window", edge.get("id"), row.get("assertion_id"), row.get("span_id"), selected["text"])
    return {"evidence_window_id": window_id, "assertion_id": row.get("assertion_id") or None, "span_id": row.get("span_id") or None, "doc_id": row.get("doc_id") or None, "channel": row.get("channel") or None, "role": row["role"], **selected}


def _refresh_evidence_diagnostics(artifact: dict, *, stage: str = "pre_verification_packaged") -> dict:
    counts = Counter(
        as_text(edge.get("evidence_completeness") or "absent")
        for edge in artifact.get("edges", [])
    )
    artifact.setdefault("diagnostics", {})["evidence_completeness_counts"] = dict(sorted(counts.items()))
    artifact["diagnostics"]["evidence_packaging"] = {
        "version": EVIDENCE_PACKAGING_VERSION,
        "stage": stage,
        "total_edges": len(artifact.get("edges", [])),
        "counts": dict(sorted(counts.items())),
    }
    return dict(sorted(counts.items()))


def package_evidence(artifact: dict) -> dict:
    """Attach deterministic evidence windows and completeness fields to every edge.

    Exact evidence is either the source span itself or the smallest one/two
    sentence window that exactly covers both endpoints, the relation cue and
    any condition. A complete non-exact context window is retained as
    ``context_assisted`` rather than being discarded or mislabeled as exact.
    """
    counts = Counter()
    for edge in artifact.get("edges", []):
        source_surfaces = _surface_values(artifact, edge, "source")
        target_surfaces = _surface_values(artifact, edge, "target")
        condition = _condition_value(artifact, edge)
        windows = [_window_record(artifact, edge, row, source_surfaces, target_surfaces, condition) for row in _assertion_rows(artifact, edge)]
        windows.sort(
            key=lambda row: (
                _window_rank(row),
                bool(row.get("complete")),
                float(row.get("coverage_ratio") or 0.0),
                int(row.get("coverage_count") or 0),
                -int(row.get("window_width") or 0),
                -len(row.get("text") or ""),
                row.get("role") != "support",
            ),
            reverse=True,
        )
        support = [row for row in windows if row.get("role") == "support"]
        candidate = [row for row in windows if row.get("role") == "candidate"]
        best = (support or candidate or [{}])[0]
        exact = next((row for row in support if row.get("complete") and row.get("window_kind") in EXACT_WINDOW_KINDS), None)
        context = next((row for row in support if row.get("complete") and row.get("window_kind") == "context"), None)
        assertion_only = next((row for row in support if row.get("window_kind") == "assertion_only" and row.get("coverage_count", 0) >= 2), None)
        partial = next((row for row in support if (row.get("text") or row.get("original_text")) and not row.get("complete")), None)
        candidate_best = next((row for row in candidate if row.get("complete")), None) or (candidate[0] if candidate else None)
        if exact:
            completeness = "exact"
        elif context:
            completeness = "context_assisted"
        elif assertion_only:
            completeness = "assertion_only"
        elif partial:
            completeness = "partial"
        elif edge.get("derivation_edge_ids"):
            completeness = "path_only"
        elif candidate_best:
            completeness = "candidate_only"
        else:
            completeness = "absent"
        reference = exact or context or candidate_best
        if not condition:
            condition_grounding = "not_applicable"
        elif exact and exact.get("condition_hit"):
            condition_grounding = "grounded"
        elif context and context.get("condition_hit"):
            condition_grounding = "context_grounded"
        else:
            condition_grounding = "ungrounded"
        missing = [key for key in ("source_hit", "target_hit", "predicate_hit") if not best.get(key)]
        if condition and not best.get("condition_hit"):
            missing.append("condition_hit")
        if completeness == "absent":
            missing.append("non_empty_evidence")
        if exact and exact.get("window_kind") == "exact_span":
            support_scope = "exact_span"
        elif exact:
            support_scope = "exact_sentence_window"
        else:
            support_scope = {
                "context_assisted": "context_assisted",
                "partial": "partial_window",
                "assertion_only": "assertion_only",
                "candidate_only": "candidate",
                "path_only": "path_derived",
                "absent": "none",
            }[completeness]
        coverage = reference or best
        edge["evidence_completeness"] = completeness
        edge["support_scope"] = support_scope
        edge["condition_grounding"] = condition_grounding
        edge["temporal_consistency"] = coverage.get("temporal_consistency", "inconsistent")
        edge["fraction_consistency"] = coverage.get("fraction_consistency", "inconsistent")
        edge["missing_evidence_components"] = list(dict.fromkeys(missing))
        edge["evidence_coverage"] = {
            "source": bool(coverage.get("source_hit")),
            "target": bool(coverage.get("target_hit")),
            "predicate": bool(coverage.get("predicate_hit")),
            "condition": bool(coverage.get("condition_hit")),
            "score": float(coverage.get("coverage_ratio") or 0.0),
        }
        edge["evidence_windows"] = windows[:12]
        edge["evidence_window_count"] = len(windows)
        edge["best_evidence_window_id"] = best.get("evidence_window_id")
        edge["best_window_kind"] = best.get("window_kind", "none")
        edge["best_window_source"] = best.get("window_source", "none")
        edge["best_window_width"] = int(best.get("window_width") or 0)
        edge["evidence_exact_match"] = bool(exact)
        edge["context_window_complete"] = bool(context)
        # A verifier may say "entailed" from a partial span or an
        # assertion-only record.  That is not enough to make the evidence
        # auditable.  Text evidence is complete only when a complete
        # exact/context support window covers source, target, predicate and
        # any required condition with compatible time/fraction values.
        evidence_ok = bool(exact or context) and condition_grounding != "ungrounded"
        evidence_ok = evidence_ok and edge.get("temporal_consistency") not in {"inconsistent"}
        evidence_ok = evidence_ok and edge.get("fraction_consistency") not in {"inconsistent"}
        path_complete = completeness == "path_only" and bool(edge.get("derivation_edge_ids"))
        contract_complete = bool(evidence_ok or path_complete)
        edge["evidence_contract_complete"] = contract_complete
        edge["evidence_contract"] = {
            "complete": contract_complete,
            "text_complete": bool(evidence_ok),
            "path_complete": path_complete,
            "components": {
                "source": bool(coverage.get("source_hit")),
                "target": bool(coverage.get("target_hit")),
                "predicate": bool(coverage.get("predicate_hit")),
                "condition": bool(coverage.get("condition_hit")),
            },
            "temporal_consistency": coverage.get("temporal_consistency", "inconsistent"),
            "fraction_consistency": coverage.get("fraction_consistency", "inconsistent"),
            "support_scope": support_scope,
            "evidence_completeness": completeness,
        }
        edge["evidence_packaging_version"] = EVIDENCE_PACKAGING_VERSION
        counts[completeness] += 1
    result = dict(sorted(counts.items()))
    _refresh_evidence_diagnostics(artifact, stage="pre_verification_packaged")
    return result


def snapshot_pre_verification_evidence(artifact: dict) -> dict:
    """Freeze canonical evidence before verifier-driven promotion changes."""
    captured = 0
    for edge in artifact.get("edges", []):
        if edge.get(PRE_VERIFICATION_PREFIX + "evidence_completeness") is not None:
            continue
        if edge.get("evidence_completeness") is None:
            continue
        for field in EVIDENCE_FIELDS:
            edge[PRE_VERIFICATION_PREFIX + field] = deepcopy(edge.get(field))
        edge["evidence_packaging_stage"] = "pre_verification"
        captured += 1
    artifact.setdefault("diagnostics", {})["pre_verification_evidence_snapshot"] = {
        "version": EVIDENCE_PACKAGING_VERSION,
        "edges_captured": captured,
    }
    return {"version": EVIDENCE_PACKAGING_VERSION, "edges_captured": captured}


def restore_pre_verification_evidence(artifact: dict) -> dict:
    """Restore canonical evidence fields after verifier packaging.

    Promotion status and verifier verdicts remain on the edge as independent
    audit fields. Only the canonical evidence ledger is restored, so a
    candidate demotion cannot erase exact/context evidence views.
    """
    restored = 0
    for edge in artifact.get("edges", []):
        if edge.get(PRE_VERIFICATION_PREFIX + "evidence_completeness") is None:
            edge["evidence_packaging_stage"] = "post_verification_packaged"
            continue
        for field in EVIDENCE_FIELDS:
            edge[field] = deepcopy(edge.get(PRE_VERIFICATION_PREFIX + field))
        edge["evidence_packaging_stage"] = "pre_verification_canonical"
        restored += 1
    counts = _refresh_evidence_diagnostics(artifact, stage="pre_verification_canonical")
    artifact["diagnostics"]["pre_verification_evidence_restore"] = {
        "version": EVIDENCE_PACKAGING_VERSION,
        "edges_restored": restored,
        "counts": counts,
    }
    return {"version": EVIDENCE_PACKAGING_VERSION, "edges_restored": restored, "counts": counts}

def has_complete_text_contract(edge: dict) -> bool:
    """Whether a text window covers the complete causal claim."""
    contract = edge.get("evidence_contract") or {}
    return bool(edge.get("evidence_contract_complete")) and bool(contract.get("text_complete"))


def has_complete_evidence_contract(edge: dict) -> bool:
    """Whether text evidence or a complete derivation path is available."""
    return bool(edge.get("evidence_contract_complete"))


def is_exact_support_core(edge: dict) -> bool:
    """Evidence-audit membership independent of final promotion kind."""
    return (
        edge.get("evidence_completeness") == "exact"
        and has_complete_text_contract(edge)
        and edge.get("condition_grounding") != "ungrounded"
        and edge.get("direction_status") not in {"ambiguous", "role_conflict"}
        and edge.get("identity_status", "ok") != "conflict"
        and edge.get("source") != edge.get("target")
    )


def is_context_assisted_core(edge: dict) -> bool:
    """Evidence-aware membership independent of final promotion kind."""
    return (
        edge.get("evidence_completeness") in {"exact", "context_assisted"}
        and has_complete_text_contract(edge)
        and edge.get("condition_grounding") != "ungrounded"
        and edge.get("direction_status") not in {"ambiguous", "role_conflict"}
        and edge.get("identity_status", "ok") != "conflict"
        and edge.get("source") != edge.get("target")
    )