# -*- coding: utf-8 -*-
"""Document-level, context-aware alias candidate discovery for D2C_13_2.

This module deliberately does not read the benchmark alias dictionary. It uses
only mentions, document context, local graph roles and entity metadata already
present in an artifact.
"""

from __future__ import annotations

from collections import defaultdict
import os
import re
from pathlib import Path
from typing import Any

import networkx as nx

from .entity_resolution import (
    _norm,
    _numbers,
    _state,
    _times,
    _tokens,
    entity_match,
)

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "because", "by", "for", "from",
    "has", "have", "in", "into", "is", "it", "of", "on", "or", "that", "the",
    "their", "this", "through", "to", "was", "were", "with", "within", "when",
    "which", "while", "after", "before", "then", "than", "under", "over",
}
CONTRAST_GROUPS = (
    {"total", "dissolved", "unspecified"},
    {"available", "unavailable"},
    {"inlet", "outlet"},
    {"active", "inactive", "enabled", "disabled", "on", "off"},
    {"present", "absent"},
    {"normal", "abnormal"},
    {"up", "down", "increase", "decrease"},
    {"high", "low"},
    {"local", "remote"},
)
GENERIC_TOKENS = {
    "level", "levels", "status", "state", "condition", "value", "values",
    "reading", "readings", "result", "results", "amount", "concentration",
    "concentrations", "measurement", "measure", "measurements", "titre", "titer",
    "data",
}
SCOPE_TOKENS = {"desktop", "network", "local", "remote", "inlet", "outlet"}
POLARITY_CUES = {"non", "not", "no", "without", "never", "inactive", "absent", "abnormal", "failed", "invalid", "disabled", "off", "false"}
DEFAULT_EMBEDDING_AUTO_THRESHOLD = 0.93
DEFAULT_EMBEDDING_REVIEW_THRESHOLD = 0.80


def _context_tokens(text: str) -> set[str]:
    return {token for token in _tokens(text) if token not in STOPWORDS and len(token) > 1}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _record(store: dict, entity_id: str, doc_id: str) -> dict:
    key = (entity_id, doc_id)
    if key not in store:
        store[key] = {
            "entity_id": entity_id,
            "doc_id": doc_id,
            "surfaces": set(),
            "contexts": [],
            "roles": set(),
            "role_counts": {"source": 0, "target": 0},
            "neighbors": set(),
            "types": set(),
        }
    return store[key]


def _add_context(record: dict, text: object) -> None:
    value = " ".join(str(text or "").split())
    if value and value not in record["contexts"]:
        record["contexts"].append(value)


def build_document_mentions(artifact: dict) -> dict[tuple[str, str], dict]:
    """Collect entity mentions and local context from an existing artifact."""
    entities = {str(e.get("id")): e for e in artifact.get("entities", [])}
    spans = {str(s.get("id")): s for s in artifact.get("spans", [])}
    records: dict[tuple[str, str], dict] = {}

    def add_entity_surfaces(entity_id: str, doc_id: str, role: str | None = None) -> dict:
        record = _record(records, entity_id, doc_id)
        entity = entities.get(entity_id, {})
        for surface in [entity.get("label")] + list(entity.get("aliases") or []):
            if surface:
                record["surfaces"].add(str(surface))
        if entity.get("entity_type"):
            record["types"].add(str(entity.get("entity_type")))
        if role in record["role_counts"]:
            record["role_counts"][role] += 1
        _add_context(record, entity.get("description"))
        return record

    # Prefer document-specific edge/assertion evidence. Edges retain surface names
    # from the extracted graph and can link an entity to the relevant document.
    for edge in artifact.get("edges", []):
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if not source or not target:
            continue
        docs = list(dict.fromkeys(
            list(edge.get("support_doc_ids") or []) + list(edge.get("candidate_doc_ids") or [])
        ))
        contexts = []
        for window in edge.get("evidence_windows") or []:
            if isinstance(window, dict):
                for key in ("text", "original_text"):
                    if window.get(key):
                        contexts.append(str(window[key]))
        for sid in list(edge.get("span_ids") or []) + list(edge.get("candidate_span_ids") or []):
            if sid in spans and spans[sid].get("text"):
                contexts.append(str(spans[sid]["text"]))
        for doc_id in docs:
            doc_id = str(doc_id)
            left = add_entity_surfaces(source, doc_id, "source")
            right = add_entity_surfaces(target, doc_id, "target")
            if edge.get("source_name"):
                left["surfaces"].add(str(edge["source_name"]))
            if edge.get("target_name"):
                right["surfaces"].add(str(edge["target_name"]))
            left["roles"].add("source")
            right["roles"].add("target")
            left["neighbors"].add(target)
            right["neighbors"].add(source)
            for text in contexts:
                _add_context(left, text)
                _add_context(right, text)

    for assertion in artifact.get("assertions", []):
        source = str(assertion.get("source") or "")
        target = str(assertion.get("target") or "")
        doc_id = str(assertion.get("doc_id") or "")
        if not source or not target or not doc_id:
            continue
        left = add_entity_surfaces(source, doc_id, "source")
        right = add_entity_surfaces(target, doc_id, "target")
        left["roles"].add("source")
        right["roles"].add("target")
        left["neighbors"].add(target)
        right["neighbors"].add(source)
        _add_context(left, assertion.get("mechanism"))
        _add_context(right, assertion.get("mechanism"))
        span = spans.get(str(assertion.get("span_id") or ""), {})
        if span.get("text"):
            _add_context(left, span.get("text"))
            _add_context(right, span.get("text"))

    # A small amount of document-wide fallback context helps when an entity was
    # attached by inventory rather than by an edge.
    for (entity_id, doc_id), record in list(records.items()):
        entity = entities.get(entity_id, {})
        for context in [entity.get("context_signature"), entity.get("description")]:
            _add_context(record, context)

    return records


def _entity_for_match(record: dict) -> dict:
    return {
        "id": record["entity_id"],
        "label": sorted(record["surfaces"])[0] if record["surfaces"] else record["entity_id"],
        "aliases": sorted(record["surfaces"])[1:],
        "entity_type": sorted(record["types"])[0] if record["types"] else "",
    }


def _qualifier_contrast(left: dict, right: dict) -> str | None:
    left_tokens = set(_tokens(" ".join(left.get("surfaces") or [])))
    right_tokens = set(_tokens(" ".join(right.get("surfaces") or [])))
    for group in CONTRAST_GROUPS:
        left_hit = left_tokens & group
        right_hit = right_tokens & group
        if left_hit and right_hit and left_hit != right_hit:
            return f"qualifier_conflict:{','.join(sorted(left_hit))}!={','.join(sorted(right_hit))}"
    return None


def _core_tokens(record: dict) -> set[str]:
    tokens = set(_tokens(" ".join(record.get("surfaces") or [])))
    return {
        token for token in tokens
        if token not in STOPWORDS and token not in GENERIC_TOKENS and not token.isdigit()
    }


def _scope_conflict(left: dict, right: dict) -> str | None:
    left_scope = _core_tokens(left) & SCOPE_TOKENS
    right_scope = _core_tokens(right) & SCOPE_TOKENS
    if left_scope != right_scope:
        return f"scope_conflict:{','.join(sorted(left_scope)) or 'none'}!={','.join(sorted(right_scope)) or 'none'}"
    return None


def _core_edge_graph(artifact: dict) -> nx.DiGraph:
    graph = nx.DiGraph()
    for edge in artifact.get("edges", []):
        if edge.get("kind") not in {"direct_core", "conditional", "regime", "transitive_derived"}:
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target and source != target:
            graph.add_edge(source, target)
    return graph


def _path_conflict(graph: nx.DiGraph, left_id: str, right_id: str) -> bool:
    if not left_id or not right_id or left_id == right_id:
        return False
    if left_id not in graph or right_id not in graph:
        return False
    return nx.has_path(graph, left_id, right_id) or nx.has_path(graph, right_id, left_id)


def _state_modifier_conflict(left: dict, right: dict) -> bool:
    """Return true when only one surface carries a state/polarity modifier."""
    left_state = _state(" ".join(left.get("surfaces") or []))
    right_state = _state(" ".join(right.get("surfaces") or []))
    return (left_state.get("status") is not None) != (right_state.get("status") is not None)


def _polarity_conflict(left: dict, right: dict) -> str | None:
    """Reject an alias that flips an explicit negation on the same head.

    This is deliberately narrower than a generic state comparison.  It only
    fires when one surface contains a polarity cue and the other does not, and
    the remaining content tokens substantially overlap.  For example,
    ``non-PostScript graphic`` must not collapse into ``PostScript graphic``.
    """
    left_tokens = set(_tokens(" ".join(left.get("surfaces") or [])))
    right_tokens = set(_tokens(" ".join(right.get("surfaces") or [])))
    left_negated = bool(left_tokens & POLARITY_CUES)
    right_negated = bool(right_tokens & POLARITY_CUES)
    if left_negated == right_negated:
        return None
    left_core = {token for token in left_tokens - POLARITY_CUES if len(token) >= 3}
    right_core = {token for token in right_tokens - POLARITY_CUES if len(token) >= 3}
    if not left_core or not right_core:
        return None
    overlap = left_core & right_core
    union = left_core | right_core
    if len(overlap) >= 2 and len(overlap) / len(union) >= 0.5:
        return "polarity_conflict"
    return None


CONDITION_ROLE_CUES = {
    "condition", "conditional", "trigger", "prerequisite", "input", "cause",
    "driver", "factor", "setting", "mode", "state",
}
OUTCOME_ROLE_CUES = {
    "outcome", "result", "effect", "consequence", "response", "output",
    "failure", "leak", "release", "emission", "incident", "damage",
}


def _role_hint(record: dict) -> str | None:
    text = " ".join(
        [str(value) for value in record.get("surfaces") or []]
        + [str(value) for value in record.get("types") or []]
    ).lower()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    condition = bool(tokens & CONDITION_ROLE_CUES)
    outcome = bool(tokens & OUTCOME_ROLE_CUES)
    if condition != outcome:
        return "condition" if condition else "outcome"
    return None


def _relation_role_conflict(left: dict, right: dict, min_observations: int = 2) -> str | None:
    """Flag a causal-role asymmetry before an alias can be auto-merged.

    The guard is structural first: two mentions observed repeatedly as sources
    and targets, respectively, but never in the complementary role, should not
    be silently identified. A generic condition/outcome label cue is used only
    as a secondary, conservative review signal.
    """
    lc = left.get("role_counts") or {}
    rc = right.get("role_counts") or {}
    left_source = int(lc.get("source", 0) or 0)
    left_target = int(lc.get("target", 0) or 0)
    right_source = int(rc.get("source", 0) or 0)
    right_target = int(rc.get("target", 0) or 0)
    if left_source >= min_observations and right_target >= min_observations and left_target == 0 and right_source == 0:
        return "source_target_role_asymmetry"
    if left_target >= min_observations and right_source >= min_observations and left_source == 0 and right_target == 0:
        return "source_target_role_asymmetry"
    left_hint, right_hint = _role_hint(left), _role_hint(right)
    if left_hint and right_hint and left_hint != right_hint:
        return f"condition_outcome_role_conflict:{left_hint}!={right_hint}"
    return None

_EMBEDDING_MODEL = None
_EMBEDDING_FAILED = False
_EMBEDDING_CACHE: dict[str, Any] = {}


def _surface_embedding_similarity(left: dict, right: dict) -> float | None:
    """Best surface-only embedding cosine between two mention records."""
    global _EMBEDDING_MODEL, _EMBEDDING_FAILED
    surfaces_left = sorted({str(s) for s in (left.get("surfaces") or []) if str(s).strip()})
    surfaces_right = sorted({str(s) for s in (right.get("surfaces") or []) if str(s).strip()})
    if not surfaces_left or not surfaces_right or _EMBEDDING_FAILED:
        return None
    try:
        if _EMBEDDING_MODEL is None:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer
            _EMBEDDING_MODEL = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        missing = [text for text in surfaces_left + surfaces_right if text not in _EMBEDDING_CACHE]
        if missing:
            vectors = _EMBEDDING_MODEL.encode(missing, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
            for text, vector in zip(missing, vectors):
                _EMBEDDING_CACHE[text] = vector
        best = -1.0
        for a in surfaces_left:
            for b in surfaces_right:
                best = max(best, float((_EMBEDDING_CACHE[a] * _EMBEDDING_CACHE[b]).sum()))
        return round(best, 4)
    except Exception:
        _EMBEDDING_FAILED = True
        return None


def score_mention_pair(
    left: dict,
    right: dict,
    *,
    embedding_auto_threshold: float = DEFAULT_EMBEDDING_AUTO_THRESHOLD,
    embedding_review_threshold: float = DEFAULT_EMBEDDING_REVIEW_THRESHOLD,
    relation_role_guard: bool = True,
) -> dict:
    """Score two document-level mention records without gold alias knowledge."""
    if left.get("doc_id") != right.get("doc_id"):
        return {"decision": "separate", "score": 0.0, "reason": "different_document", "compatible": False}
    a, b = _entity_for_match(left), _entity_for_match(right)
    contexts_a = " ".join(left.get("contexts") or [])
    contexts_b = " ".join(right.get("contexts") or [])
    match = entity_match(a, b)
    lexical = float(match.get("score") or 0.0)
    compatible = bool(match.get("compatible"))
    reasons = list(match.get("reasons") or [])
    features = dict(match.get("features") or {})
    context_match = entity_match(a, b, [contexts_a], [contexts_b])
    context_features = dict(context_match.get("features") or {})
    context_reason = context_features.get("reason")
    role_guard_reason = _relation_role_conflict(left, right) if relation_role_guard else None
    embedding_similarity = None if role_guard_reason else _surface_embedding_similarity(left, right)
    if not compatible:
        return {
            "decision": "separate", "score": 0.0, "reason": "conflict",
            "lexical": lexical, "context": 0.0, "neighbor_overlap": 0.0,
            "compatible": False, "reasons": reasons, "features": features,
        }
    contrast = _qualifier_contrast(left, right)
    if contrast:
        return {
            "decision": "separate", "score": 0.0, "reason": contrast,
            "lexical": lexical, "context": 0.0, "neighbor_overlap": 0.0,
            "compatible": False, "reasons": [contrast], "features": features,
        }
    scope_conflict = _scope_conflict(left, right)
    if scope_conflict:
        return {
            "decision": "separate", "score": 0.0, "reason": scope_conflict,
            "lexical": lexical, "context": 0.0, "neighbor_overlap": 0.0,
            "compatible": False, "reasons": [scope_conflict], "features": features,
        }
    polarity_conflict = _polarity_conflict(left, right)
    if polarity_conflict:
        return {
            "decision": "separate", "score": 0.0, "reason": polarity_conflict,
            "lexical": lexical, "context": 0.0, "neighbor_overlap": 0.0,
            "compatible": False, "reasons": [polarity_conflict], "features": features,
        }
    context_similarity = _jaccard(_context_tokens(contexts_a), _context_tokens(contexts_b))
    neighbor_overlap = _jaccard(set(left.get("neighbors") or []), set(right.get("neighbors") or []))
    lexical_similarity = lexical
    core_a, core_b = _core_tokens(left), _core_tokens(right)
    core_match = core_a == core_b or (core_a <= core_b and len(core_b - core_a) <= 1) or (core_b <= core_a and len(core_a - core_b) <= 1)
    # A strong document-level pair needs multiple independent signals. This is
    # intentionally stricter than label-only similarity.
    strong_document = (
        core_match
        and lexical_similarity >= 0.65
        and context_similarity >= 0.45
    )
    # Auto-merge remains precision-first. A generic document-context score is
    # a review signal unless multiple independent structural signals coincide.
    explicit_definition = context_reason == "context_definition"
    embedding_auto = (
        embedding_similarity is not None
        and embedding_similarity >= float(embedding_auto_threshold)
    )
    embedding_review = (
        embedding_similarity is not None
        and embedding_similarity >= float(embedding_review_threshold)
    )
    state_scope_review = _state_modifier_conflict(left, right)
    would_auto = features.get("reason") in {"exact", "normalized_exact"} or explicit_definition or strong_document or embedding_auto
    auto = would_auto
    if state_scope_review and not explicit_definition:
        auto = False
    if role_guard_reason:
        auto = False
    role_guard_suppressed_auto = bool(role_guard_reason and would_auto)
    review = (
        strong_document
        or explicit_definition
        or embedding_review
        or state_scope_review
        or features.get("reason") == "state_aware_review"
        or lexical_similarity >= 0.84
        or context_similarity >= 0.60
        or (lexical_similarity >= 0.55 and neighbor_overlap >= 0.50)
        or bool(role_guard_reason)
    )
    decision = "auto_merge" if auto else ("needs_review" if review else "separate")
    return {
        "decision": decision,
        "score": round(max(lexical_similarity, context_similarity, neighbor_overlap), 4),
        "reason": features.get("reason", "document_context"),
        "lexical": round(lexical_similarity, 4),
        "context": round(context_similarity, 4),
        "neighbor_overlap": round(neighbor_overlap, 4),
        "compatible": True,
        "reasons": reasons + ([role_guard_reason] if role_guard_reason else []),
        "relation_role_guard": bool(role_guard_reason),
        "relation_role_reason": role_guard_reason,
        "role_guard_suppressed_auto": role_guard_suppressed_auto,
        "features": {**features, "context_reason": context_reason, "embedding_similarity": embedding_similarity, "relation_role_reason": role_guard_reason} if (context_reason or embedding_similarity is not None or role_guard_reason) else features,
    }


def _filter_cycle_safe_auto_pairs(graph: nx.DiGraph, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Keep only auto merges that leave the core graph acyclic.

    A single pair may look harmless while a combination of merges contracts a
    multi-hop path into a cycle. Contract accepted pairs incrementally and
    demote any pair whose contraction would make the graph cyclic. This is a
    structural guard, not a corpus-specific rule.
    """
    if not rows or graph.number_of_edges() == 0 or not nx.is_directed_acyclic_graph(graph):
        return list(rows), []
    parent = {str(node): str(node) for node in graph.nodes()}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != node:
            nxt = parent[node]
            parent[node] = root
            node = nxt
        return root

    safe: list[dict] = []
    blocked: list[dict] = []
    for row in sorted(rows, key=lambda item: (item.get("score", 0.0), item.get("lexical", 0.0)), reverse=True):
        left = str(row.get("entity_a") or "")
        right = str(row.get("entity_b") or "")
        if left not in parent or right not in parent:
            safe.append(row)
            continue
        left_root, right_root = find(left), find(right)
        if left_root == right_root:
            safe.append(row)
            continue
        parent[right_root] = left_root
        mapping = {node: find(node) for node in graph.nodes()}
        contracted = nx.relabel_nodes(graph, mapping, copy=True)
        if nx.is_directed_acyclic_graph(contracted):
            safe.append(row)
        else:
            parent[right_root] = right_root
            blocked.append(row)
    return safe, blocked


def normalize_document_aliases(
    artifact: dict,
    max_review_per_entity: int = 2,
    embedding_auto_threshold: float = DEFAULT_EMBEDDING_AUTO_THRESHOLD,
    embedding_review_threshold: float = DEFAULT_EMBEDDING_REVIEW_THRESHOLD,
    relation_role_guard: bool = True,
) -> dict:
    """Discover document-level alias candidates and return forced/review pairs.

    The function does not mutate the artifact. The compiler passes auto pairs to
    ``resolve_entity_aliases`` and exposes review pairs in diagnostics.
    """
    records = build_document_mentions(artifact)
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for record in records.values():
        if record.get("surfaces"):
            by_doc[str(record.get("doc_id"))].append(record)

    auto_pairs: list[dict] = []
    review_rows: list[dict] = []
    path_conflicts: list[dict] = []
    core_graph = _core_edge_graph(artifact)
    seen = set()
    for doc_id, rows in sorted(by_doc.items()):
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                left, right = rows[i], rows[j]
                if left["entity_id"] == right["entity_id"]:
                    continue
                key = tuple(sorted((left["entity_id"], right["entity_id"])))
                if key in seen:
                    continue
                seen.add(key)
                result = score_mention_pair(
                    left,
                    right,
                    embedding_auto_threshold=embedding_auto_threshold,
                    embedding_review_threshold=embedding_review_threshold,
                    relation_role_guard=relation_role_guard,
                )
                if result["decision"] == "separate":
                    continue
                row = {
                    "entity_a": left["entity_id"],
                    "entity_b": right["entity_id"],
                    "doc_id": doc_id,
                    "a_surfaces": sorted(left["surfaces"]),
                    "b_surfaces": sorted(right["surfaces"]),
                    **result,
                }
                if result["decision"] == "auto_merge":
                    if _path_conflict(core_graph, left["entity_id"], right["entity_id"]):
                        row["reason"] = "path_conflict_alias_identity"
                        row["auto_merge_blocked"] = True
                        path_conflicts.append(row)
                        review_rows.append(row)
                    else:
                        auto_pairs.append(row)
                else:
                    review_rows.append(row)

    auto_pairs, cycle_blocked = _filter_cycle_safe_auto_pairs(core_graph, auto_pairs)
    for row in cycle_blocked:
        row["reason"] = "path_conflict_alias_component"
        row["auto_merge_blocked"] = True
        path_conflicts.append(row)
        review_rows.append(row)

    # Generic review-queue tightening: retain only each entity's best few
    # document-level review candidates. No corpus-specific absolute cap.
    review_rows.sort(key=lambda row: (row.get("score", 0.0), row.get("lexical", 0.0)), reverse=True)
    kept_review = []
    per_entity: dict[str, int] = defaultdict(int)
    for row in review_rows:
        a, b = row["entity_a"], row["entity_b"]
        if per_entity[a] >= max_review_per_entity and per_entity[b] >= max_review_per_entity:
            continue
        kept_review.append(row)
        per_entity[a] += 1
        per_entity[b] += 1

    diagnostics = {
        "mentions": len(records),
        "documents": len(by_doc),
        "auto_merge_candidates": len(auto_pairs),
        "path_conflict_auto_pairs": len(path_conflicts),
        "cycle_safe_blocked_auto_pairs": len(cycle_blocked),
        "relation_role_guard_blocked_auto_merges": sum(1 for row in review_rows if row.get("role_guard_suppressed_auto")),
        "review_candidates_total": len(review_rows),
        "review_candidates_kept": len(kept_review),
        "auto_pairs": [
            {
                "entity_a": row["entity_a"], "entity_b": row["entity_b"],
                "a_surfaces": row["a_surfaces"], "b_surfaces": row["b_surfaces"],
                "doc_id": row["doc_id"], "reason": row["reason"],
                "score": row.get("score"), "features": row.get("features"),
            }
            for row in auto_pairs
        ],
        "path_conflicts": [
            {
                "entity_a": row["entity_a"], "entity_b": row["entity_b"],
                "a_surfaces": row["a_surfaces"], "b_surfaces": row["b_surfaces"],
                "doc_id": row["doc_id"], "reason": row["reason"],
                "score": row.get("score"), "features": row.get("features"),
            }
            for row in path_conflicts
        ],
        "review_pairs": kept_review,
        "protocol": "document-level context + local causal neighborhood + time/number/state compatibility; no benchmark alias dictionary",
    }
    role_profiles: dict[str, dict[str, int]] = defaultdict(lambda: {"source": 0, "target": 0})
    for record in records.values():
        counts = record.get("role_counts") or {}
        profile = role_profiles[str(record.get("entity_id"))]
        profile["source"] += int(counts.get("source", 0) or 0)
        profile["target"] += int(counts.get("target", 0) or 0)
    return {
        "auto_pairs": {tuple(sorted((row["entity_a"], row["entity_b"]))) for row in auto_pairs},
        "review_candidates": kept_review,
        "role_profiles": dict(role_profiles),
        "diagnostics": diagnostics,
    }
