# -*- coding: utf-8 -*-
"""Standalone D2C_13_2 builder from document extractions.

This path does not read lang7 GML, score sidecars, or any existing experiment
directory. Input is a document directory plus either a DeepSeek extractor, an
offline mock, or a saved extraction JSON.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import networkx as nx

from .constants import VERSION
from .evidence_completeness import package_evidence
from .extractor import Document, PROMPT_VERSION
from .semantics import classify_edge_kinds, discover_span_chain_candidates, materialize_views, repair_chain_edges, repair_core_graph
from .util import as_list, as_text, now_iso, norm_text, read_json, safe_rel, sha1, stable_urn, truthy


def _condition_record(raw: str) -> dict | None:
    raw = as_text(raw).strip()
    if not raw:
        return None
    normalized = re.sub(r"^(if|when|whenever|while|during)\s+", "", norm_text(raw)).strip(" .,:;")
    return {
        "id": stable_urn("condition", normalized),
        "raw": raw,
        "normalized": normalized,
        "equivalence_key": sha1(normalized),
    }


def _parse_span(span: str, text: str, quote: str = "") -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", as_text(span))
    if match:
        start, end = int(match.group(1)), int(match.group(2))
        if 0 <= start <= end <= len(text):
            return start, end
    quote = as_text(quote).strip()
    if quote:
        start = text.lower().find(quote.lower())
        if start >= 0:
            return start, start + len(quote)
    return None


def _span_record(doc_id: str, document_id: str, text: str, start: int, end: int) -> dict:
    snippet = text[start:end]
    return {
        "id": stable_urn("span", doc_id, start, end),
        "doc_id": doc_id,
        "document_id": document_id,
        "start": start,
        "end": end,
        "text_hash": sha1(snippet),
        "text": snippet,
    }


def _find_entity_id(name: str, aliases: dict[str, str], entities: dict[str, dict]) -> str | None:
    key = norm_text(name)
    if key in aliases:
        return aliases[key]
    best, score = None, 0.0
    for eid, entity in entities.items():
        values = [entity.get("label", "")] + list(entity.get("aliases") or [])
        current = max((SequenceMatcher(None, key, norm_text(v)).ratio() for v in values if as_text(v)), default=0.0)
        if current > score:
            best, score = eid, current
    return best if score >= 0.94 else None


def _new_entity(name: str, entities: dict[str, dict], aliases: dict[str, str], type_hint: str = "", description: str = "") -> dict:
    key = norm_text(name)
    eid = stable_urn("entity", key)
    if eid not in entities:
        entities[eid] = {
            "id": eid, "label": as_text(name), "description": as_text(description),
            "entity_type": as_text(type_hint), "values": [], "aliases": [],
            "context_signature": "",
        }
    aliases.setdefault(key, eid)
    return entities[eid]


def _edge_record(
    artifact: dict,
    source: str,
    target: str,
    kind: str,
    assertions: list[dict],
    condition_id: str | None,
    attrs: dict,
    source_name: str,
    target_name: str,
    edge_index: int,
) -> dict:
    spans = sorted({a.get("span_id") for a in assertions if a.get("span_id")})
    docs = sorted({a.get("doc_id") for a in assertions if a.get("doc_id")})
    edge_id = stable_urn("edge", source, target, kind, edge_index, condition_id or "")
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "source_name": source_name,
        "target_name": target_name,
        "kind": kind,
        "edge_kind": kind,
        "support_assertion_ids": sorted({a["id"] for a in assertions}),
        "support_doc_ids": docs,
        "span_ids": spans,
        "condition_id": condition_id,
        "derivation_edge_ids": [],
        "confidence": as_text(attrs.get("confidence")) or "medium",
        "strength": as_text(attrs.get("strength")) or "unknown",
        "direction_status": "unverified",
        "support_mode": "direct_span" if spans and assertions else ("assertion_only" if assertions else "none"),
        "support_strength": "strong" if spans and assertions else ("medium" if assertions else "none"),
        "claim_scope": "conditional" if kind == "conditional" else ("regime" if kind == "regime" else ("composed" if kind == "transitive_derived" else ("hypothesis" if kind == "hypothesis" else "direct"))),
        "evidence_grade": "span_grounded" if spans and assertions else ("assertion_grounded" if assertions else "none"),
        "candidate_support_mode": "direct_span" if spans and assertions else ("assertion_only" if assertions else "none"),
        "candidate_support_strength": "strong" if spans and assertions else ("medium" if assertions else "none"),
        "candidate_origin": "standalone",
        "candidate_tier": "high" if spans and assertions else ("medium" if assertions else "none"),
        "candidate_evidence_ids": sorted({a["id"] for a in assertions} | set(spans) | set(docs)),
        "evidence_union_ids": sorted(set(spans)),
        "verification_status": "unverified",
        "direct_support": "untested",
        "path_support": "untested",
        "condition_support": "untested",
        "view_tags": [],
        "semantic_support": "untested" if kind in {"direct_core", "conditional", "regime"} else ("derived" if kind == "hypothesis" else "contradicted"),
        "provenance": "document_grounded" if spans else "derived_or_unresolved",
        "attributes": {
            "description": as_text(attrs.get("quote") or attrs.get("description")),
            "mechanism": as_text(attrs.get("mechanism")),
            "condition": as_text(attrs.get("condition")),
            "direct_evidence": bool(attrs.get("direct_evidence", bool(spans))),
            "rung": attrs.get("rung", 1),
            "sign": attrs.get("sign", "0"),
        },
    }


def _resolve_core_cycles(artifact: dict) -> list[dict]:
    """Move weakest direct-core edges out until the core is a DAG."""
    records = []
    while True:
        core = [e for e in artifact.get("edges", []) if e.get("kind") == "direct_core"]
        graph = nx.DiGraph()
        for edge in core:
            graph.add_edge(edge["source"], edge["target"], edge_id=edge["id"])
        if nx.is_directed_acyclic_graph(graph):
            break
        # Pick the first cyclic component and the weakest edge in it.
        cycle_nodes = next((set(c) for c in nx.strongly_connected_components(graph) if len(c) > 1), set())
        candidates = [e for e in core if e["source"] in cycle_nodes and e["target"] in cycle_nodes]
        if not candidates:
            break
        weakest = min(candidates, key=lambda e: (len(e.get("support_doc_ids") or []), len(e.get("support_assertion_ids") or []), e.get("id", "")))
        weakest["kind"] = "rejected"
        weakest["edge_kind"] = "rejected"
        weakest["semantic_support"] = "contradicted"
        weakest["verification_status"] = "rejected"
        weakest["provenance"] = "cycle_removed"
        records.append({
            "id": stable_urn("cycle", weakest["id"], "standalone"),
            "classification": "contradiction_removed",
            "pair": [weakest["source"], weakest["target"]],
            "removed_edge": weakest["id"],
            "regime_dependent": False,
            "reason": "standalone greedy DAG guarantee",
            "quality": {},
            "rule": "support_then_assertion_lexical_tiebreak",
        })
    artifact.setdefault("cycle_decisions", []).extend(records)
    materialize_views(artifact)
    return records


def build_artifact_from_extractions(
    docs: list[Document],
    extractions: dict[str, dict],
    input_dir: str | Path | None = None,
    model: str = "mock",
    chain_mode: str = "safe",
    repair_chain: bool = True,
) -> dict:
    doc_map = {d.doc_id: d for d in docs}
    entities: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    alias_conflicts = []
    conditions: dict[str, dict] = {}
    spans: dict[str, dict] = {}
    assertions: list[dict] = []
    link_records = []

    # Entity registry across all documents.
    def payload_for(doc):
        return extractions.get(doc.doc_id) or extractions.get(doc.source_file) or {}

    for doc in docs:
        payload = payload_for(doc)
        for item in payload.get("entities", []) or []:
            name = as_text(item.get("name") or item.get("surface")).strip()
            if not name:
                continue
            entity = _new_entity(name, entities, aliases, item.get("type"), item.get("description"))
            for alias in [name] + list(item.get("aliases") or []):
                alias = as_text(alias).strip()
                if not alias:
                    continue
                key = norm_text(alias)
                previous = aliases.get(key)
                if previous and previous != entity["id"]:
                    alias_conflicts.append({"alias": alias, "previous": previous, "current": entity["id"]})
                else:
                    aliases[key] = entity["id"]
                if alias != entity["label"] and alias not in entity["aliases"]:
                    entity["aliases"].append(alias)

    # Build links and per-document assertions.
    def payload_for(doc):
        return extractions.get(doc.doc_id) or extractions.get(doc.source_file) or {}

    for doc in docs:
        payload = payload_for(doc)
        text = doc.text
        for item in payload.get("links", []) or []:
            cause = as_text(item.get("cause")).strip()
            effect = as_text(item.get("effect")).strip()
            if not cause or not effect or norm_text(cause) == norm_text(effect):
                continue
            source_entity = _find_entity_id(cause, aliases, entities) or _new_entity(cause, entities, aliases)["id"]
            target_entity = _find_entity_id(effect, aliases, entities) or _new_entity(effect, entities, aliases)["id"]
            condition = _condition_record(item.get("condition"))
            if condition:
                conditions.setdefault(condition["id"], condition)
            parsed = _parse_span(item.get("span"), text, item.get("quote"))
            span_id = None
            if parsed:
                span = _span_record(doc.doc_id, stable_urn("document", doc.doc_id), text, *parsed)
                spans.setdefault(span["id"], span)
                span_id = span["id"]
            assertion_id = stable_urn("assertion", source_entity, target_entity, doc.doc_id, item.get("quote"), item.get("span"), len(assertions))
            assertion = {
                "id": assertion_id, "source": source_entity, "target": target_entity,
                "doc_id": doc.doc_id, "document_id": stable_urn("document", doc.doc_id),
                "span_id": span_id, "channel": "d2c13_2_extract", "order": len(assertions),
                "sign": as_text(item.get("sign")), "rung": as_text(item.get("rung")),
                "mechanism": as_text(item.get("mechanism")), "direction": "u->v",
            }
            assertions.append(assertion)
            link_records.append({
                "source": source_entity, "target": target_entity, "doc_id": doc.doc_id,
                "assertion": assertion, "condition": condition,
                "attrs": item,
            })

    # Group by unordered pair, then resolve direction conflicts.
    by_pair = defaultdict(list)
    for item in link_records:
        by_pair[tuple(sorted((item["source"], item["target"])))].append(item)
    votes, conflict_decisions, edges = [], [], []
    edge_index = 0
    for pair, items in sorted(by_pair.items()):
        directions = {
            (item["source"], item["target"]): item
            for item in items
        }
        if len(directions) > 1:
            vote_map = {}
            for item in items:
                vote_map[(item["source"], item["target"])] = vote_map.get((item["source"], item["target"]), 0) + 1
                vote_id = stable_urn("vote", pair, item["source"], item["target"], item["doc_id"])
                votes.append({
                    "id": vote_id, "pair": list(pair), "direction": f"{item['source']}->{item['target']}",
                    "assertions": 1, "documents": 1, "doc_vote_detail": {item["doc_id"]: "u->v"},
                    "rule": "document_majority_then_earliest",
                })
            selected = sorted(vote_map.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))[0][0]
            dropped = next((d for d in directions if d != selected), None)
            selected_condition = next((i for i in items if (i["source"], i["target"]) == selected and i["condition"]), None)
            dropped_condition = next((i for i in items if dropped and (i["source"], i["target"]) == dropped and i["condition"]), None)
            regime = bool(selected_condition and dropped_condition and selected_condition["condition"]["equivalence_key"] != dropped_condition["condition"]["equivalence_key"])
            conflict_decisions.append({
                "id": stable_urn("conflict", pair, selected, dropped, "standalone"),
                "kind": "direction", "pair": list(pair), "selected": selected[0], "dropped": dropped[1] if dropped else None,
                "rule": "document_majority_then_earliest", "sources": {},
            })
            if regime:
                for item in items:
                    if not item["condition"]:
                        continue
                    edges.append(_edge_record(
                        artifact={"edges": edges}, source=item["source"], target=item["target"],
                        kind="regime", assertions=[item["assertion"]], condition_id=item["condition"]["id"],
                        attrs=item["attrs"], source_name=entities[item["source"]]["label"],
                        target_name=entities[item["target"]]["label"], edge_index=edge_index,
                    ))
                    edge_index += 1
            else:
                selected_items = [i for i in items if (i["source"], i["target"]) == selected]
                for item in selected_items:
                    kind = "conditional" if item["condition"] else ("direct_core" if item["attrs"].get("direct_evidence", bool(item["assertion"].get("span_id"))) else "hypothesis")
                    edges.append(_edge_record(
                        artifact={"edges": edges}, source=item["source"], target=item["target"],
                        kind=kind, assertions=[item["assertion"]], condition_id=item["condition"]["id"] if item["condition"] else None,
                        attrs=item["attrs"], source_name=entities[item["source"]]["label"],
                        target_name=entities[item["target"]]["label"], edge_index=edge_index,
                    ))
                    edge_index += 1
        else:
            for direction, item in directions.items():
                kind = "conditional" if item["condition"] else ("direct_core" if item["attrs"].get("direct_evidence", bool(item["assertion"].get("span_id"))) else "hypothesis")
                edges.append(_edge_record(
                    artifact={"edges": edges}, source=item["source"], target=item["target"],
                    kind=kind, assertions=[item["assertion"]], condition_id=item["condition"]["id"] if item["condition"] else None,
                    attrs=item["attrs"], source_name=entities[item["source"]]["label"],
                    target_name=entities[item["target"]]["label"], edge_index=edge_index,
                ))
                edge_index += 1

    artifact = {
        "artifact_id": stable_urn("artifact", input_dir or "standalone", model, len(docs)),
        "version": VERSION,
        "created_at": now_iso(),
        "source": {
            "input_dir": safe_rel(Path(input_dir), Path.cwd()) if input_dir else "",
            "extractor": model,
            "document_count": len(docs),
            "prompt_version": PROMPT_VERSION,
        },
        "provenance": {
            "producer": "D2C_13_2-standalone",
            "artifact_version": VERSION,
            "extractor": model,
            "prompt_version": PROMPT_VERSION,
            "generated_at": now_iso(),
        },
        "documents": [
            {"id": stable_urn("document", d.doc_id), "doc_id": d.doc_id, "source_file": d.source_file, "title": d.title, "text_length": len(d.text)}
            for d in docs
        ],
        "spans": sorted(spans.values(), key=lambda x: x["id"]),
        "entities": sorted(entities.values(), key=lambda x: x["id"]),
        "conditions": sorted(conditions.values(), key=lambda x: x["id"]),
        "assertions": sorted(assertions, key=lambda x: x["id"]),
        "edges": edges,
        "votes": votes,
        "conflict_decisions": conflict_decisions,
        "cycle_decisions": [],
        "hypotheses": [],
        "views": {},
        "diagnostics": {
            "alias_conflicts": alias_conflicts,
            "edge_counts": {},
        },
    }
    # Chain-aware classification and conservative repair.
    classify_edge_kinds(artifact, mode=chain_mode)
    discover_span_chain_candidates(artifact)
    repair_stats = repair_chain_edges(artifact) if repair_chain else {}
    core_repair_stats = repair_core_graph(artifact)
    evidence_packaging = package_evidence(artifact)
    materialize_views(artifact)
    artifact["diagnostics"].update({
        "edge_counts": {kind: sum(1 for e in artifact["edges"] if e.get("kind") == kind) for kind in sorted({e.get("kind") for e in artifact["edges"]})},
        "repair": repair_stats,
        "core_repair": core_repair_stats,
        "evidence_packaging": evidence_packaging,
        "entity_count": len(artifact["entities"]),
        "span_count": len(artifact["spans"]),
        "assertion_count": len(artifact["assertions"]),
    })
    return artifact


