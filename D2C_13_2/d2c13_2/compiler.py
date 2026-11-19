# -*- coding: utf-8 -*-
"""Compile a Doc2Causal run into a D2C_13_2 typed ACC artifact.

The compiler is read-only with respect to the source run. It consumes the GML
and existing sidecars, emits a canonical JSON artifact, and then delegates
chain/entity/condition classification to :mod:`d2c13_2.semantics`.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import networkx as nx

from .alias_guard import sanitize_graph_aliases
from .canonical_arbitration import apply_canonical_arbitration
from .entity_resolution import resolve_entity_aliases
from .document_alias import normalize_document_aliases
from .path_control import apply_path_aware_control
from .constants import VERSION
from .evidence_completeness import package_evidence
from .util import (
    as_list,
    as_text,
    latest_file,
    norm_text,
    now_iso,
    read_json,
    safe_rel,
    sha1,
    stable_urn,
    truthy,
    write_json,
)


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
    """Conservatively reject aliases that change identity-bearing semantics."""
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


def _find_entity(name: str, entities: list[dict], aliases: dict[str, str]) -> str | None:
    raw = as_text(name).strip()
    if not raw:
        return None
    if raw in aliases:
        return aliases[raw]
    key = norm_text(raw)
    if key in aliases:
        return aliases[key]
    best, score = None, 0.0
    for entity in entities:
        candidates = [entity.get("label", "")] + list(entity.get("aliases") or [])
        for cand in candidates:
            if _identity_conflict(raw, cand):
                continue
            s = SequenceMatcher(None, key, norm_text(cand)).ratio()
            if s > score:
                best, score = entity["id"], s
    return best if score >= 0.92 else None


def _build_entity_index(entities: list[dict]) -> dict[str, str]:
    index: dict[str, str] = {}
    for entity in entities:
        for value in [entity.get("label", "")] + list(entity.get("aliases") or []):
            text = as_text(value).strip()
            if not text:
                continue
            index.setdefault(text, entity["id"])
            index.setdefault(norm_text(text), entity["id"])
    return index


def _make_entity(name: str, entity_type: str = "", description: str = "", provenance: str = "evidence_mention") -> dict:
    label = as_text(name).strip()
    times = sorted(set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", label)))
    signature = _state_signature(label)
    return {
        "id": stable_urn("entity", label),
        "label": label,
        "description": as_text(description),
        "entity_type": as_text(entity_type),
        "values": [],
        "aliases": [],
        "context_signature": "",
        "time_indices": times,
        "state_signature": signature,
        "identity_key": sha1("|".join([norm_text(label), str(signature), str(times)])),
        "provenance": provenance,
    }


def _ensure_entity(name: str, entities: list[dict], aliases: dict[str, str], entity_type: str = "", description: str = "") -> tuple[str | None, bool]:
    found = _find_entity(name, entities, aliases)
    if found:
        return found, False
    label = as_text(name).strip()
    if not label:
        return None, False
    key = norm_text(label)
    for entity in entities:
        if norm_text(entity.get("label", "")) == key and not _identity_conflict(label, entity.get("label", "")):
            entity_id = entity["id"]
            aliases[label] = entity_id
            aliases[key] = entity_id
            return entity_id, False
    entity = _make_entity(label, entity_type=entity_type, description=description)
    entities.append(entity)
    aliases[label] = entity["id"]
    aliases[key] = entity["id"]
    return entity["id"], True


def _parse_span(span: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", as_text(span))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _condition_record(raw: str) -> dict | None:
    raw = as_text(raw).strip()
    if not raw:
        return None
    normalized = re.sub(r"^(if|when|whenever|while|during)\s+", "", norm_text(raw)).strip(" .,:;")
    key = sha1(normalized)
    lower = raw.lower()
    if any(word in lower for word in ("regime", "operating mode", "during", "mode ")):
        condition_kind = "regime"
    elif any(word in lower for word in ("depends", "moderator", "interaction", "jointly", "whether")):
        condition_kind = "moderator"
    else:
        condition_kind = "conditional"
    return {
        "id": stable_urn("condition", normalized),
        "raw": raw,
        "normalized": normalized,
        "equivalence_key": key,
        "condition_kind": condition_kind,
    }


def _load_documents(docs_dir: Path | None, doc_ids: set[str]) -> tuple[list[dict], dict[str, str], dict[str, int]]:
    documents: list[dict] = []
    texts: dict[str, str] = {}
    prefix_offsets: dict[str, int] = {}
    files: list[Path] = []
    if docs_dir and Path(docs_dir).exists():
        files = sorted(
            p for p in Path(docs_dir).glob("*.json")
            if p.name != "provenance.json" and not p.name.startswith("_")
        )
    for i, doc_id in enumerate(sorted(doc_ids, key=lambda x: (len(x), x))):
        path = files[i] if i < len(files) else None
        payload = read_json(path, {}) if path else {}
        title = ""
        if isinstance(payload.get("title"), dict):
            title = as_text(payload["title"].get("eng") or payload["title"].get("en") or "")
        elif payload.get("title"):
            title = as_text(payload.get("title"))
        text = as_text(payload.get("summary") or "")
        documents.append({
            "id": stable_urn("document", doc_id),
            "doc_id": doc_id,
            "source_file": path.name if path else "",
            "title": title,
            "text_length": len(text),
        })
        texts[doc_id] = text
        prefix_offsets[doc_id] = len(f"Title: {title}\n - \nContent:\n") if title else 0
    return documents, texts, prefix_offsets


def _span_boundary_score(text: str, start: int, end: int) -> int:
    if not text or start < 0 or end <= start or end > len(text):
        return -100
    def word(ch: str) -> bool:
        return bool(ch) and (ch.isalnum() or ch == "_")
    left_ok = start == 0 or not (word(text[start - 1]) and word(text[start]))
    right_ok = end == len(text) or not (word(text[end - 1]) and word(text[end]))
    sentence_left = start == 0 or text[start - 1] in ".!?;\n"
    sentence_right = end == len(text) or text[end - 1] in ".!?;"
    return int(left_ok) * 2 + int(right_ok) * 2 + int(sentence_left) + int(sentence_right)


def _span_record(doc_id: str, start: int, end: int, texts: dict[str, str]) -> dict:
    text = texts.get(doc_id, "")
    snippet = text[start:end] if text and 0 <= start <= end <= len(text) else ""
    return {
        "id": stable_urn("span", doc_id, start, end),
        "doc_id": doc_id,
        "document_id": stable_urn("document", doc_id),
        "start": start,
        "end": end,
        "text_hash": sha1(snippet) if snippet else "",
        "text": snippet,
        "context": text[max(0, start - 240):min(len(text), end + 240)] if text else "",
    }


def _edge_kind_from_attrs(attrs: dict) -> str:
    edge_type = as_text(attrs.get("edge_type")).lower()
    node_type = as_text(attrs.get("type")).lower()
    if edge_type == "hypothesis" or truthy(attrs.get("hypothesis")):
        # D2C_13_2 resolves this into candidate vs unsupported after support is
        # known; candidate is the conservative intermediate kind.
        return "candidate"
    if truthy(attrs.get("regime_dependent")):
        return "regime"
    if any(cue in edge_type or cue in node_type for cue in ("conditional", "moderator", "moderating", "depends")):
        return "conditional"
    if any(cue in edge_type or cue in node_type for cue in ("indirect", "transitive", "derived")):
        return "transitive_derived"
    return "direct_core"


def _claim_scope_from_edge(kind: str, attrs: dict) -> str:
    """Return the semantic claim kind independently from publication status."""
    if kind == "conditional":
        return "conditional"
    if kind == "regime":
        return "regime"
    if kind == "transitive_derived":
        return "composed"
    if kind in {"candidate", "unsupported_hypothesis", "hypothesis"}:
        text = (as_text(attrs.get("type")) + " " + as_text(attrs.get("edge_type"))).lower()
        if any(cue in text for cue in ("indirect", "transitive", "derived", "mechanism")):
            return "composed"
        if any(cue in text for cue in ("conditional", "moderator", "moderating")):
            return "conditional"
        return "unknown"
    return "direct"


def _evidence_grade_from_fields(
    support_ids: list[str],
    span_ids: list[str],
    candidate_assertion_ids: list[str],
    candidate_span_ids: list[str],
    candidate_doc_ids: list[str],
    derivation_ids: list[str],
) -> str:
    if derivation_ids:
        return "derived_path"
    if (support_ids and span_ids) or (candidate_assertion_ids and candidate_span_ids):
        return "span_grounded"
    if support_ids or candidate_assertion_ids:
        return "assertion_grounded"
    if span_ids or candidate_span_ids:
        return "span_only"
    if candidate_doc_ids:
        return "context_only"
    return "none"


def _candidate_tier(evidence_grade: str, support_mode: str) -> str:
    if evidence_grade in {"span_grounded", "derived_path"}:
        return "high"
    if evidence_grade in {"assertion_grounded", "span_only"} or support_mode == "weak_candidate":
        return "medium"
    if evidence_grade == "context_only":
        return "low"
    return "none"


def _attr_payload(attrs: dict) -> dict:
    keys = (
        "description", "type", "confidence", "strength", "polarity", "explicit",
        "edge_type", "mechanism", "rung", "condition", "regime_dependent",
        "support_docs", "support_assertions", "span_hit",
        "alias_guard_rebound", "alias_guard_alias",
    )
    return {key: attrs[key] for key in keys if key in attrs}


def _normalize_legacy_span_offsets(
    evidence: dict,
    texts: dict[str, str],
    prefix_offsets: dict[str, int],
) -> dict:
    """Convert legacy LangExtract source_text offsets to summary coordinates."""
    stats = {"legacy_source_text_coordinates": False, "corrected": 0, "skipped": 0}
    meta = evidence.setdefault("meta", {})
    if meta.get("span_coordinate_system") == "document_summary" or not prefix_offsets:
        return stats
    stats["legacy_source_text_coordinates"] = True
    for entry in (evidence.get("entries") or {}).values():
        for assertion in entry.get("assertions", []) or []:
            raw = as_text(assertion.get("span"))
            if ":" not in raw:
                continue
            left, right = raw.split(":", 1)
            try:
                start, end = int(left), int(right)
            except ValueError:
                continue
            doc_id = as_text(assertion.get("doc_id"))
            offset = int(prefix_offsets.get(doc_id, 0) or 0)
            length = len(texts.get(doc_id, ""))
            if offset and 0 <= start - offset < end - offset <= length:
                assertion["span"] = f"{start - offset}:{end - offset}"
                stats["corrected"] += 1
            else:
                stats["skipped"] += 1
    meta["span_coordinate_system"] = "document_summary"
    meta["span_offset_normalization"] = stats
    return stats


def compile_run(
    run_dir: str | Path,
    docs_dir: str | Path | None = None,
    corpus: str | None = None,
    method: str | None = None,
    run: int | None = None,
    chain_mode: str = "safe",
    repair_chain: bool = True,
    strict_core: bool = True,
    allow_unsupported_core: bool = False,
    evidence_dirs: list[str | Path] | None = None,
    document_alias_config: dict | None = None,
    canonical_arbitration_config: dict | None = None,
) -> dict:
    """Compile one run directory into a typed D2C_13_2 artifact."""
    run_dir = Path(run_dir).resolve()
    if not run_dir.exists():
        raise FileNotFoundError(run_dir)
    gml_path = latest_file(run_dir, "*.gml")
    if gml_path is None:
        raise FileNotFoundError(f"no GML in {run_dir}")

    graph = nx.read_gml(gml_path)
    alias_guard_report = sanitize_graph_aliases(graph)
    evidence = read_json(run_dir / "evidence.json", {}) or {}
    evidence_entries = dict(evidence.get("entries") or {})
    evidence_sources = [str(run_dir / "evidence.json")]
    for extra_dir in evidence_dirs or []:
        extra_path = Path(extra_dir) / "evidence.json"
        extra = read_json(extra_path, {}) or {}
        evidence_entries.update(extra.get("entries") or {})
        evidence_sources.append(str(extra_path))
    if evidence_entries:
        evidence["entries"] = evidence_entries
    conflicts = read_json(run_dir / "conflicts.json", {}) or {}
    decycle = read_json(run_dir / "decycle.json", {}) or {}
    completion = read_json(run_dir / "neighbor_completion.json", {}) or {}
    merge_stats = read_json(run_dir / "merge_stats.json", {}) or {}
    score = read_json(run_dir / "score.json", {}) or {}

    doc_ids: set[str] = set()
    for entry in (evidence.get("entries") or {}).values():
        for assertion in entry.get("assertions", []) or []:
            if assertion.get("doc_id"):
                doc_ids.add(str(assertion["doc_id"]))
    documents, texts, span_prefix_offsets = _load_documents(Path(docs_dir) if docs_dir else None, doc_ids)
    span_normalization = _normalize_legacy_span_offsets(evidence, texts, span_prefix_offsets)

    entities: list[dict] = []
    node_to_entity: dict[str, str] = {}
    for node, attrs in sorted(graph.nodes(data=True), key=lambda item: str(item[0])):
        label = str(node)
        aliases = as_list(attrs.get("merged_aliases"))
        aliases = [as_text(a) for a in aliases if as_text(a) and as_text(a) != label]
        entity = {
            "id": stable_urn("entity", label),
            "label": label,
            "description": as_text(attrs.get("description")),
            "entity_type": as_text(attrs.get("type")),
            "values": [as_text(v) for v in as_list(attrs.get("values"))],
            "aliases": sorted(set(aliases)),
            "context_signature": as_text(attrs.get("contextual_information")),
            "time_indices": sorted(set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", label))),
            "state_signature": _state_signature(label),
            "identity_key": sha1("|".join([
                norm_text(label),
                str(_state_signature(label)),
                str(sorted(set(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", label)))),
            ])),
        }
        entities.append(entity)
        node_to_entity[label] = entity["id"]
    aliases = _build_entity_index(entities)

    spans: dict[str, dict] = {}
    assertions: list[dict] = []

    def add_span(doc_id: str, span_text: str, normalize_legacy: bool = False) -> str | None:
        parsed = _parse_span(span_text)
        if not parsed:
            return None
        start, end = parsed
        if normalize_legacy:
            offset = int(span_prefix_offsets.get(doc_id, 0) or 0)
            text = texts.get(doc_id, "")
            if offset and 0 <= start - offset < end - offset <= len(text):
                original_score = _span_boundary_score(text, start, end)
                shifted_score = _span_boundary_score(text, start - offset, end - offset)
                if shifted_score > original_score:
                    start, end = start - offset, end - offset
        record = _span_record(doc_id, start, end, texts)
        spans.setdefault(record["id"], record)
        return record["id"]

    mechanism_by_assertion: dict[str, str] = {}
    recovered_entities = 0
    for key, entry in (evidence.get("entries") or {}).items():
        names = key.split(" || ", 1)
        if len(names) != 2:
            continue
        source_id, source_created = _ensure_entity(
            names[0], entities, aliases,
            entity_type=as_text(entry.get("type")),
            description=as_text(entry.get("description")),
        )
        target_id, target_created = _ensure_entity(
            names[1], entities, aliases,
            entity_type=as_text(entry.get("type")),
            description=as_text(entry.get("description")),
        )
        recovered_entities += int(source_created) + int(target_created)
        if not source_id or not target_id:
            continue
        mechanisms = entry.get("mechanism") or []
        mechanism = as_text(mechanisms[0][0]) if mechanisms and isinstance(mechanisms[0], list) else ""
        signs = entry.get("sign") or []
        rungs = entry.get("rung") or []
        sign = as_text(signs[0][0]) if signs and isinstance(signs[0], list) else ""
        rung = as_text(rungs[0][0]) if rungs and isinstance(rungs[0], list) else ""
        for item in entry.get("assertions", []) or []:
            doc_id = as_text(item.get("doc_id"))
            span_id = add_span(doc_id, as_text(item.get("span"))) if item.get("span") else None
            source = as_text(item.get("source"))
            channel = as_text(item.get("channel"))
            order = item.get("order")
            assertion_id = stable_urn("assertion", source_id, target_id, doc_id, source, channel, order, span_id)
            record = {
                "id": assertion_id,
                "source": source_id,
                "target": target_id,
                "doc_id": doc_id,
                "document_id": stable_urn("document", doc_id),
                "span_id": span_id,
                "channel": channel,
                "source_record": source,
                "order": order,
                "sign": sign,
                "rung": rung,
                "mechanism": mechanism,
                "direction": "u->v",
            }
            assertions.append(record)
            mechanism_by_assertion[assertion_id] = mechanism

    conditions: dict[str, dict] = {}
    for _, _, attrs in graph.edges(data=True):
        cond = _condition_record(as_text(attrs.get("condition")))
        if cond:
            conditions.setdefault(cond["id"], cond)

    edges: list[dict] = []
    for index, (u, v, attrs) in enumerate(sorted(graph.edges(data=True), key=lambda x: (str(x[0]), str(x[1])))):
        source_id = node_to_entity[str(u)]
        target_id = node_to_entity[str(v)]
        support_assertions = [a for a in assertions if a["source"] == source_id and a["target"] == target_id]
        span_ids = {a["span_id"] for a in support_assertions if a.get("span_id")}
        span_payload = attrs.get("span_ids_json")
        if isinstance(span_payload, str) and span_payload:
            try:
                parsed = json.loads(span_payload)
            except json.JSONDecodeError:
                parsed = []
            for item in parsed if isinstance(parsed, list) else []:
                doc_id = as_text(item.get("doc"))
                span_text = as_text(item.get("span"))
                sid = add_span(doc_id, span_text, normalize_legacy=bool(span_normalization["legacy_source_text_coordinates"]))
                if sid:
                    span_ids.add(sid)
        support_docs = {spans[sid]["doc_id"] for sid in span_ids if sid in spans}
        for a in support_assertions:
            if a.get("doc_id"):
                support_docs.add(a["doc_id"])
        original_kind = _edge_kind_from_attrs(attrs)
        kind = original_kind
        condition = _condition_record(as_text(attrs.get("condition")))
        condition_id = condition["id"] if condition else None
        candidate_assertion_ids = sorted({a["id"] for a in support_assertions})
        candidate_span_ids = sorted(span_ids)
        candidate_doc_ids = sorted(support_docs)
        has_candidate_evidence = bool(candidate_assertion_ids or candidate_span_ids or candidate_doc_ids)
        if (
            strict_core
            and not allow_unsupported_core
            and kind == "direct_core"
            and (not span_ids or not support_assertions)
        ):
            kind = "candidate" if has_candidate_evidence else "unsupported_hypothesis"
        if kind == "regime" and not condition_id:
            kind = "candidate" if has_candidate_evidence else "unsupported_hypothesis"
        if kind == "conditional" and not condition_id:
            kind = "candidate" if has_candidate_evidence else "unsupported_hypothesis"
        if kind == "candidate" and not has_candidate_evidence:
            kind = "unsupported_hypothesis"
        support_ids = sorted({a["id"] for a in support_assertions})
        support_docs_final = sorted(support_docs)
        span_ids_final = sorted(span_ids)
        if kind in {"candidate", "unsupported_hypothesis", "hypothesis"}:
            # Candidate evidence is retained under candidate_* only. It is not
            # documentary authority for a core edge.
            support_ids = []
            support_docs_final = []
            span_ids_final = []
        support_mode = "direct_span" if support_ids and span_ids_final else (
            "assertion_only" if support_ids else (
                "span_only" if span_ids_final else ("weak_candidate" if has_candidate_evidence else "none")
            )
        )
        claim_scope = _claim_scope_from_edge(original_kind, attrs)
        evidence_grade = _evidence_grade_from_fields(
            support_ids, span_ids_final, candidate_assertion_ids, candidate_span_ids,
            candidate_doc_ids, [],
        )
        candidate_support_mode = support_mode
        candidate_support_strength = (
            "strong" if support_mode == "direct_span" else
            "medium" if support_mode in {"assertion_only", "span_only"} else
            "weak" if support_mode == "weak_candidate" else "none"
        )
        edge_id = stable_urn("edge", source_id, target_id, kind, index)
        edge = {
            "id": edge_id,
            "source": source_id,
            "target": target_id,
            "source_name": str(u),
            "target_name": str(v),
            "kind": kind,
            "edge_kind": kind,
            "support_assertion_ids": support_ids,
            "support_doc_ids": support_docs_final,
            "span_ids": span_ids_final,
            "candidate_assertion_ids": candidate_assertion_ids,
            "candidate_span_ids": candidate_span_ids,
            "candidate_doc_ids": candidate_doc_ids,
            "candidate_derivation_ids": [],
            "condition_id": condition_id,
            "derivation_edge_ids": [],
            "confidence": as_text(attrs.get("confidence")) or "unknown",
            "strength": as_text(attrs.get("strength")) or "unknown",
            "direction_status": "unverified",
            "support_mode": support_mode,
            "support_strength": candidate_support_strength,
            "candidate_support_mode": candidate_support_mode,
            "candidate_support_strength": candidate_support_strength,
            "candidate_origin": "compiler",
            "candidate_tier": _candidate_tier(evidence_grade, candidate_support_mode),
            "candidate_evidence_ids": sorted(set(candidate_assertion_ids + candidate_span_ids + candidate_doc_ids)),
            "claim_scope": claim_scope,
            "evidence_grade": evidence_grade,
            "evidence_union_ids": sorted(set(span_ids_final + candidate_span_ids)),
            "verification_status": "unverified",
            "direct_support": "untested",
            "path_support": "untested",
            "condition_support": "untested",
            "view_tags": [],
            "semantic_support": "candidate" if kind == "candidate" else ("derived" if kind == "hypothesis" else "untested"),
            "provenance": (
                "document_grounded" if kind == "direct_core" else
                "candidate_evidence" if kind == "candidate" else
                "unsupported" if kind == "unsupported_hypothesis" else
                "derived_or_unresolved"
            ),
            "attributes": _attr_payload(attrs),
        }
        edges.append(edge)

    votes: list[dict] = []
    conflict_decisions: list[dict] = []
    for index, record in enumerate(conflicts.get("records", []) or []):
        pair = record.get("pair") or []
        pair_ids = [_find_entity(str(p), entities, aliases) for p in pair[:2]]
        selected = _find_entity(as_text(record.get("decision")), entities, aliases)
        dropped = _find_entity(as_text(record.get("dropped")), entities, aliases)
        vote_ids = []
        for direction, counts in (record.get("votes") or {}).items():
            vote_id = stable_urn("vote", index, direction)
            votes.append({
                "id": vote_id,
                "pair": pair_ids,
                "direction": direction,
                "assertions": counts.get("assertions", 0),
                "documents": counts.get("documents", 0),
                "doc_vote_detail": record.get("doc_vote_detail") or {},
                "rule": record.get("rule"),
            })
            vote_ids.append(vote_id)
        conflict_decisions.append({
            "id": stable_urn("conflict", index, pair, record.get("decision"), record.get("dropped")),
            "kind": record.get("kind"),
            "pair": pair_ids,
            "selected": selected,
            "dropped": dropped,
            "rule": record.get("rule"),
            "vote_ids": vote_ids,
            "sources": record.get("sources") or {},
        })

    cycle_decisions: list[dict] = []
    for index, record in enumerate((decycle.get("records") or []) + (decycle.get("conditional_records") or [])):
        pair = record.get("pair") or []
        pair_ids = [_find_entity(str(p), entities, aliases) for p in pair[:2]]
        cycle_decisions.append({
            "id": stable_urn("cycle", index, pair, record.get("classification")),
            "classification": record.get("classification") or record.get("kind") or "cycle",
            "pair": pair_ids,
            "removed_edge": record.get("removed_edge") or record.get("edge"),
            "regime_dependent": bool(record.get("regime_dependent")),
            "reason": record.get("reason") or "",
            "quality": record.get("quality") or {},
            "rule": record.get("rule") or "",
        })

    hypotheses: list[dict] = []
    for index, item in enumerate(completion.get("added_edges", []) or []):
        cause = _find_entity(as_text(item.get("cause")), entities, aliases)
        effect = _find_entity(as_text(item.get("effect")), entities, aliases)
        hypotheses.append({
            "id": stable_urn("hypothesis", index, cause, effect),
            "source": cause,
            "target": effect,
            "mechanism": as_text(item.get("mechanism")),
            "effect_sign": as_text(item.get("effect_sign")),
            "span_hit": bool(item.get("span_hit")),
            "status": "derived",
        })

    artifact = {
        "artifact_id": stable_urn("artifact", run_dir.name, gml_path.name),
        "version": VERSION,
        "created_at": now_iso(),
        "source": {
            "run_dir": safe_rel(run_dir, Path.cwd()),
            "gml": safe_rel(gml_path, Path.cwd()),
            "corpus": corpus or (run_dir.parent.parent.name if len(run_dir.parents) > 1 else ""),
            "method": method or run_dir.parent.name,
            "run": run if run is not None else (int(re.search(r"(\d+)$", run_dir.name).group(1)) if re.search(r"(\d+)$", run_dir.name) else None),
            "document_count": len(documents),
            "score": score,
        },
        "provenance": {
            "producer": "Doc2Causal",
            "artifact_version": VERSION,
            "chain_mode": chain_mode,
            "strict_core": strict_core,
            "merge_stats": merge_stats,
            "evidence_sources": evidence_sources,
            "generated_at": now_iso(),
        },
        "documents": documents,
        "spans": sorted(spans.values(), key=lambda x: x["id"]),
        "entities": sorted(entities, key=lambda x: x["id"]),
        "conditions": sorted(conditions.values(), key=lambda x: x["id"]),
        "assertions": sorted(assertions, key=lambda x: x["id"]),
        "edges": sorted(edges, key=lambda x: x["id"]),
        "votes": votes,
        "conflict_decisions": conflict_decisions,
        "cycle_decisions": cycle_decisions,
        "hypotheses": hypotheses,
        "views": {},
        "diagnostics": {},
    }

    from .semantics import (
        classify_edge_directness,
        classify_edge_kinds,
        materialize_views,
        normalize_transitive_derivation,
        recover_assertion_edges,
        relation_role_direction_guard,
        repair_chain_edges,
        repair_condition_edges,
        repair_core_graph,
        repair_entity_coverage,
        repair_identity_edges,
        shortcut_guard,
        temporal_direction_guard,
        textual_direction_guard,
    )
    classify_edge_kinds(artifact, mode=chain_mode)
    repair_stats = repair_chain_edges(artifact) if repair_chain else {}
    transitive_stats = normalize_transitive_derivation(artifact)
    textual_stats = textual_direction_guard(artifact)
    role_stats = relation_role_direction_guard(artifact)
    temporal_stats = temporal_direction_guard(artifact)
    directness_stats = classify_edge_directness(artifact)
    shortcut_stats = shortcut_guard(artifact)
    entity_coverage_stats = repair_entity_coverage(artifact)
    alias_config = document_alias_config or {}
    arbitration_config = canonical_arbitration_config or {}
    relation_role_guard = bool(alias_config.get("relation_role_guard", arbitration_config.get("relation_role_guard", True)))
    max_review_per_entity = int(alias_config.get("max_review_per_entity", 2))
    if alias_config.get("enabled", True):
        document_alias_stats = normalize_document_aliases(
            artifact,
            max_review_per_entity=max_review_per_entity,
            embedding_auto_threshold=float(alias_config.get("embedding_auto_threshold", 0.93)),
            embedding_review_threshold=float(alias_config.get("embedding_review_threshold", 0.80)),
            relation_role_guard=relation_role_guard,
        )
    else:
        document_alias_stats = {
            "auto_pairs": set(),
            "review_candidates": [],
            "role_profiles": {},
            "diagnostics": {"enabled": False, "mentions": 0, "documents": 0},
        }
    alias_stats = resolve_entity_aliases(
        artifact,
        forced_pairs=document_alias_stats["auto_pairs"],
        extra_review_candidates=document_alias_stats["review_candidates"],
        max_review_per_entity=max_review_per_entity,
        role_profiles=document_alias_stats.get("role_profiles") or {},
        relation_role_guard=relation_role_guard,
    )
    # Entity merges can turn a previously valid DAG into a cycle. Repair the
    # core view before downstream path and evidence packaging passes.
    alias_core_repair = repair_core_graph(artifact)
    path_aware_stats = apply_path_aware_control(artifact)
    assertion_recovery_stats = recover_assertion_edges(artifact)
    condition_stats = repair_condition_edges(artifact)
    identity_stats = repair_identity_edges(artifact)
    path_aware_stats = apply_path_aware_control(artifact)
    evidence_packaging_stats = package_evidence(artifact)
    canonical_arbitration_stats = apply_canonical_arbitration(artifact, arbitration_config)
    materialize_views(artifact)
    artifact.setdefault("diagnostics", {}).update({
        "edge_counts": {kind: sum(1 for e in artifact["edges"] if e["kind"] == kind) for kind in sorted({e["kind"] for e in artifact["edges"]})},
        "entity_count": len(artifact["entities"]),
        "entity_recovery": {"added": recovered_entities, "strategy": "evidence_mention_exact_then_fuzzy"},
        "span_count": len(artifact["spans"]),
        "span_offset_normalization": span_normalization,
        "assertion_count": len(artifact["assertions"]),
        "repair": repair_stats,
        "transitive_normalization": transitive_stats,
        "textual_direction": textual_stats,
        "relation_role_direction": role_stats,
        "temporal_direction": temporal_stats,
        "directness": directness_stats,
        "shortcut_guard": shortcut_stats,
        "entity_coverage": entity_coverage_stats,
        "alias_resolution": alias_stats,
        "alias_guard": alias_guard_report,
        "document_alias": document_alias_stats["diagnostics"],
        "canonical_arbitration": canonical_arbitration_stats,
        "alias_core_repair": alias_core_repair,
        "path_aware": path_aware_stats,
        "assertion_recovery": assertion_recovery_stats,
        "condition_repair": condition_stats,
        "identity_repair": identity_stats,
        "evidence_packaging": evidence_packaging_stats,
    })
    return artifact


def compile_to_file(run_dir: str | Path, output_path: str | Path, **kwargs: Any) -> dict:
    artifact = compile_run(run_dir, **kwargs)
    write_json(Path(output_path), artifact)
    return artifact




