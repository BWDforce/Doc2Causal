# -*- coding: utf-8 -*-
"""Project canonical-audit vote records into artifact Q3 records."""

from __future__ import annotations

from typing import Any

from .util import as_text, stable_urn

PROVENANCE = "canonical_audit"


def _unique(values) -> list[str]:
    return sorted({as_text(value) for value in values or [] if as_text(value)})


def _direction(support_docs: list[str], opposition_docs: list[str]) -> str:
    if support_docs and opposition_docs:
        return "mixed"
    if support_docs:
        return "support"
    if opposition_docs:
        return "opposition"
    return "uncertain"


def _doc_detail(support_docs: list[str], opposition_docs: list[str], uncertain_docs: list[str]) -> dict[str, str]:
    detail = {doc: "support" for doc in support_docs}
    detail.update({doc: "opposition" for doc in opposition_docs})
    detail.update({doc: "uncertain" for doc in uncertain_docs})
    return dict(sorted(detail.items()))


def project_canonical_votes(artifact: dict, payload: dict) -> dict[str, Any]:
    """Replace prior canonical projections and expose vote rows through Q3.

    The projection is F1-neutral: it changes only artifact-level vote records,
    never edges, directions, edge kinds, promotion status, or F1 views.
    """
    projected: list[dict] = []
    projected_pairs: set[tuple[str, str]] = set()
    for row in payload.get("votes", []) or []:
        pair = _unique(row.get("pair") or [])
        if len(pair) != 2:
            continue
        support_docs = _unique(row.get("support_doc_ids") or [])
        opposition_docs = _unique(row.get("opposition_doc_ids") or [])
        uncertain_docs = _unique(row.get("uncertain_doc_ids") or [])
        forward_docs = _unique(row.get("forward_doc_ids") or [])
        reverse_docs = _unique(row.get("reverse_doc_ids") or [])
        edge_ids = _unique(row.get("edge_ids") or [])
        pair_key = tuple(pair)
        projected_pairs.add(pair_key)
        projected.append({
            "id": stable_urn("vote", "canonical-audit", *pair),
            "pair": pair,
            "edge_ids": edge_ids,
            "direction": _direction(support_docs, opposition_docs),
            "assertions": int(row.get("assertion_count") or 0),
            "documents": len(set(support_docs) | set(opposition_docs) | set(uncertain_docs)),
            "doc_vote_detail": _doc_detail(support_docs, opposition_docs, uncertain_docs),
            "rule": as_text(row.get("direction_rule")),
            "direction_rule": as_text(row.get("direction_rule")),
            "support_doc_ids": support_docs,
            "opposition_doc_ids": opposition_docs,
            "uncertain_doc_ids": uncertain_docs,
            "forward_doc_ids": forward_docs,
            "reverse_doc_ids": reverse_docs,
            "support_votes": int(row.get("support_votes") or len(support_docs)),
            "opposition_votes": int(row.get("opposition_votes") or len(opposition_docs)),
            "uncertain_votes": len(uncertain_docs),
            "reference_orientation": as_text(row.get("reference_orientation")) or "forward",
            "source_label": as_text(row.get("source_label")),
            "target_label": as_text(row.get("target_label")),
            "provenance": PROVENANCE,
        })

    existing = []
    for vote in artifact.get("votes", []) or []:
        vote_pair = tuple(_unique(vote.get("pair") or []))
        if as_text(vote.get("provenance")) == PROVENANCE:
            continue
        if vote_pair and vote_pair in projected_pairs:
            continue
        existing.append(vote)
    artifact["votes"] = existing + projected
    summary = {
        "source": PROVENANCE,
        "canonical_vote_rows": len(payload.get("votes", []) or []),
        "projected_votes": len(projected),
        "preserved_legacy_votes": len(existing),
        "f1_neutral": True,
    }
    artifact.setdefault("diagnostics", {})["canonical_vote_projection"] = summary
    return summary
