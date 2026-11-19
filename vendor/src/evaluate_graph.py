#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Causal graph quantitative evaluation.

Evaluates one or more causal graphs (GML produced by Causal Cartographer)
along four orthogonal dimensions and produces a single composite score:

  1. Single-graph causal quality  (Q): acyclicity, evidence grounding,
     explicitness, confidence, polarity coverage, connectivity.
  2. Document coverage completeness (C): how much of the source document's
     content is captured by the extracted causal variables / relations.
  3. Duplication (D): exact / fuzzy duplicate variables and duplicate edges.
  4. Reproducibility (R): agreement of repeated runs (node/edge overlap,
     direction agreement).

All sub-scores are in [0, 1] (higher is better).  The composite score is a
weighted sum, reported on a 0-100 scale.

Usage:
    python src/evaluate_graph.py \
        --graphs test_output/causal_graph_2026-09-16_16-42-21.gml \
                 test_output/causal_graph_2026-09-16_20-05-45.gml \
        --document data/Test-text/input/case1.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

import networkx as nx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIDENCE_SCORE = {
    "high": 1.0,
    "medium": 0.6,
    "low": 0.3,
    "none": 0.5,
    None: 0.5,
}

# Weights for the single-graph quality composite.
DEFAULT_QUALITY_WEIGHTS = {
    "acyclic": 0.25,
    "grounded": 0.20,
    "explicit": 0.15,
    "confident": 0.15,
    "polarity": 0.10,
    "connected": 0.15,
}

# Weights for the coverage composite.
DEFAULT_COVERAGE_WEIGHTS = {
    "span": 0.40,
    "sentence": 0.40,
    "term": 0.20,
}

# Weights for the reproducibility composite.
DEFAULT_RELIABILITY_WEIGHTS = {
    "node": 0.40,
    "edge": 0.40,
    "direction": 0.20,
}

# Weights for the overall composite (Q, C, D, R).
DEFAULT_OVERALL_WEIGHTS = {
    "quality": 0.35,
    "coverage": 0.25,
    "duplication": 0.15,
    "reliability": 0.25,
}

# Lightweight English stop-word set (domain neutral).
STOPWORDS = frozenset(
    """
    a an and are as at be been by for from has have he her his i in is it its
    of on or she that the their they this to was were will with you your
    if then when while after before during upon under over above below between
    through into than not no may can could should would must also more most
    some such each every other another same any all both only very just even
    """.split()
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_label(value: Any) -> str:
    """Lower-case, keep alphanumerics, collapse whitespace."""
    text = str(value or "")
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def content_words(value: Any) -> list[str]:
    """Return meaningful tokens (stopwords and single chars removed)."""
    tokens = normalize_label(value).split()
    words: list[str] = []
    for token in tokens:
        if token in STOPWORDS or len(token) <= 1:
            continue
        # Very light stemming: strip a trailing plural 's'.
        if token.endswith("s") and len(token) > 3:
            token = token[:-1]
        words.append(token)
    return words


def token_overlap(a: Any, b: Any) -> float:
    """Jaccard overlap of content words between two strings."""
    set_a = set(content_words(a))
    set_b = set(content_words(b))
    if not set_a and not set_b:
        return 0.0
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Document loading / text helpers
# ---------------------------------------------------------------------------

def load_document(path: Path) -> tuple[str, str]:
    """Load the source document and return (source_text, content_text).

    Mirrors test.load_source_document so evidence_span offsets stay aligned.
    `content_text` is the body used for key-term / sentence analysis.
    """
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")
    with path.open("r", encoding="utf-8-sig") as file:
        document = json.load(file)

    if isinstance(document, dict) and "text" in document:
        content = str(document["text"])
        return f"Content:\n{content}", content

    title = str((document.get("title") or {}).get("eng") or "")
    summary = str(document.get("summary") or "")
    source_text = f"Title: {title}\n - \nContent:\n{summary}"
    return source_text, summary


def split_sentences(text: str) -> list[str]:
    """Split on sentence boundaries (dependency-free)."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def extract_key_terms(text: str, top_k: int = 60) -> list[str]:
    """RAKE-lite key-term extraction (deterministic, dependency-free)."""
    sentences = split_sentences(text)
    word_freq: Counter[str] = Counter()
    co_occur: dict[str, set[str]] = {}
    phrases: list[list[str]] = []

    for sentence in sentences:
        words = normalize_label(sentence).split()
        # Split the sentence into stopword-delimited candidate phrases.
        chunk: list[str] = []
        for word in words:
            if word in STOPWORDS or len(word) <= 1:
                if len(chunk) >= 2:
                    phrases.append(chunk)
                chunk = []
            else:
                chunk.append(word)
        if len(chunk) >= 2:
            phrases.append(chunk)
        for word in words:
            if word not in STOPWORDS and len(word) > 1:
                word_freq[word] += 1

    if not phrases:
        return []

    for phrase in phrases:
        for i, word in enumerate(phrase):
            co_occur.setdefault(word, set())
            for j, other in enumerate(phrase):
                if i != j:
                    co_occur[word].add(other)

    scored: list[tuple[float, str]] = []
    for phrase in phrases:
        phrase_score = 0.0
        for word in phrase:
            deg = len(co_occur.get(word, set()))
            freq = word_freq.get(word, 1)
            phrase_score += deg / freq if freq else 0.0
        if phrase_score > 0:
            # Prefer longer phrases slightly.
            scored.append((phrase_score * (1 + 0.05 * (len(phrase) - 2)),
                           " ".join(phrase)))

    ranked = sorted(set(scored), key=lambda item: (item[0], item[1]), reverse=True)
    return [term for _, term in ranked[:top_k]]


# ---------------------------------------------------------------------------
# Graph-level data extraction
# ---------------------------------------------------------------------------

def graph_vocabulary(graph: nx.DiGraph) -> str:
    """All textual content attached to a graph (for term matching)."""
    pieces: list[str] = []
    for node, data in graph.nodes(data=True):
        pieces.append(str(data.get("name", node)))
        pieces.append(str(data.get("description", "")))
        for world_key in ("world_0", "world_1"):
            world = data.get(world_key) or {}
            snippets = world.get("supporting_text_snippets") or []
            if isinstance(snippets, list):
                pieces.extend(str(s) for s in snippets)
            pieces.append(str(world.get("contextual_information", "")))
    for _, _, data in graph.edges(data=True):
        pieces.append(str(data.get("description", "")))
        pieces.append(str(data.get("contextual_information", "")))
    return " ".join(pieces)


def parse_span(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    text = str(value)
    if ":" not in text:
        return None
    try:
        start, end = text.split(":", 1)
        return int(start), int(end)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1. Single-graph causal quality
# ---------------------------------------------------------------------------

def quality_metrics(graph: nx.DiGraph) -> dict[str, Any]:
    n_nodes = graph.number_of_nodes()
    n_edges = graph.number_of_edges()

    # Acyclicity: score 1.0 for DAG, else 1 - cycle_edges / edges.
    # Cycle edges are found via strongly connected components (an edge lies
    # on a directed cycle iff both endpoints belong to the same non-trivial
    # SCC).  This avoids enumerating all simple cycles, which can explode on
    # cyclic graphs.
    is_dag = bool(nx.is_directed_acyclic_graph(graph))
    cycle_edges: set[tuple[Any, Any]] = set()
    cycle_components = 0
    if not is_dag:
        for scc in nx.strongly_connected_components(graph):
            members = list(scc)
            non_trivial = len(members) > 1 or (
                len(members) == 1 and graph.has_edge(members[0], members[0])
            )
            if not non_trivial:
                continue
            cycle_components += 1
            for u in members:
                for v in members:
                    if graph.has_edge(u, v):
                        cycle_edges.add((u, v))
    n_cycle_edges = len(cycle_edges)
    acyclic_score = 1.0 if n_edges == 0 else clamp(1.0 - n_cycle_edges / n_edges)

    grounded = 0
    explicit = 0
    confidence_values: list[float] = []
    polarity = 0
    for _, _, data in graph.edges(data=True):
        if parse_span(data.get("evidence_span")) is not None:
            grounded += 1
        if str(data.get("explicit", "")).lower() == "true":
            explicit += 1
        confidence_values.append(
            CONFIDENCE_SCORE.get(str(data.get("confidence", "none")).lower(), 0.5)
        )
        if data.get("polarity") in ("increase", "decrease", "positive", "negative"):
            polarity += 1

    isolated = sum(
        1 for node in graph.nodes()
        if graph.degree(node) == 0
    )

    metrics = {
        "n_nodes": n_nodes,
        "n_edges": n_edges,
        "is_dag": is_dag,
        "cycle_count": cycle_components,
        "cycle_edges": n_cycle_edges,
        "acyclic": acyclic_score,
        "grounded": grounded / n_edges if n_edges else 0.0,
        "explicit": explicit / n_edges if n_edges else 0.0,
        "confident": mean(confidence_values),
        "polarity": polarity / n_edges if n_edges else 0.0,
        "connected": 1.0 - isolated / n_nodes if n_nodes else 0.0,
    }
    edge_keys: set[str] = set()
    for _, _, data in graph.edges(data=True):
        edge_keys.update(data.keys())
    metrics["has_grounding"] = "evidence_span" in edge_keys
    metrics["has_explicit"] = "explicit" in edge_keys
    metrics["has_polarity"] = "polarity" in edge_keys
    metrics["quality"] = sum(
        DEFAULT_QUALITY_WEIGHTS[key] * metrics[key]
        for key in DEFAULT_QUALITY_WEIGHTS
    )
    return metrics


# ---------------------------------------------------------------------------
# 2. Document coverage completeness
# ---------------------------------------------------------------------------

def coverage_metrics(graph: nx.DiGraph, source_text: str,
                     content_text: str) -> dict[str, Any]:
    text_len = len(source_text)

    # 2a. Union of grounded evidence spans over the full source text.
    spans: list[tuple[int, int]] = []
    for _, _, data in graph.edges(data=True):
        interval = parse_span(data.get("evidence_span"))
        if interval is not None:
            spans.append(interval)

    span_union = 0
    if spans:
        spans.sort()
        merged: list[list[int]] = []
        for start, end in spans:
            start = clamp(start, 0, text_len)
            end = clamp(end, 0, text_len)
            if end <= start:
                continue
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        span_union = sum(end - start for start, end in merged)
    span_coverage = span_union / text_len if text_len else 0.0

    # 2b. Sentence coverage: a sentence is "covered" if it intersects an
    # evidence span or contains a matched variable label.
    vocab = normalize_label(graph_vocabulary(graph))
    sentences = split_sentences(content_text)
    covered_sentences = 0
    for sentence in sentences:
        offset = source_text.find(sentence)
        covered = False
        if offset != -1:
            s_start, s_end = offset, offset + len(sentence)
            covered = any(
                start < s_end and end > s_start for start, end in spans
            )
        if not covered:
            # Fall back to label matching: require a meaningful fraction of
            # the sentence's content words to appear in the graph vocabulary.
            meaningful = [
                w for w in normalize_label(sentence).split()
                if w not in STOPWORDS
            ]
            matched = sum(1 for w in meaningful if w in vocab)
            covered = bool(meaningful) and (matched / len(meaningful)) >= 0.5
        if covered:
            covered_sentences += 1
    sentence_coverage = covered_sentences / len(sentences) if sentences else 0.0

    # 2c. Key-term coverage (how many salient document terms are represented
    # in the graph vocabulary).
    key_terms = extract_key_terms(content_text)
    covered_terms = 0
    for term in key_terms:
        term_norm = normalize_label(term)
        words = [w for w in term_norm.split() if w not in STOPWORDS]
        if not words:
            continue
        # Loose match: all content words appear in the graph vocabulary.
        if all(word in vocab for word in words):
            covered_terms += 1
    term_coverage = covered_terms / len(key_terms) if key_terms else 0.0

    metrics = {
        "source_chars": text_len,
        "span_union_chars": span_union,
        "span": clamp(span_coverage),
        "n_sentences": len(sentences),
        "sentence_covered": covered_sentences,
        "sentence": sentence_coverage,
        "n_key_terms": len(key_terms),
        "terms_covered": covered_terms,
        "term": term_coverage,
    }
    metrics["coverage"] = sum(
        DEFAULT_COVERAGE_WEIGHTS[key] * metrics[key]
        for key in DEFAULT_COVERAGE_WEIGHTS
    )
    return metrics


# ---------------------------------------------------------------------------
# 3. Duplication
# ---------------------------------------------------------------------------

def duplication_metrics(graph: nx.DiGraph,
                        fuzzy_threshold: float = 0.8) -> dict[str, Any]:
    nodes = list(graph.nodes(data=True))
    names = [str(data.get("name", node)) for node, data in nodes]

    # Exact duplicate detection on normalized names.
    norm_counter = Counter(normalize_label(name) for name in names)
    exact_duplicates = sum(c - 1 for c in norm_counter.values() if c > 1)

    # Fuzzy duplicate clusters via token-overlap union-find.
    parent = list(range(len(names)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, j in combinations(range(len(names)), 2):
        if token_overlap(names[i], names[j]) >= fuzzy_threshold:
            union(i, j)

    cluster_of = {}
    for i in range(len(names)):
        cluster_of.setdefault(find(i), []).append(i)

    fuzzy_clusters = [
        cluster for cluster in cluster_of.values() if len(cluster) > 1
    ]
    fuzzy_duplicate_nodes = sum(len(c) - 1 for c in fuzzy_clusters)
    unique_clusters = len(cluster_of)
    redundant_node_ratio = (
        1.0 - unique_clusters / len(names) if names else 0.0
    )

    # Duplicate edges: parallel / bidirectional pairs.
    seen: Counter[tuple[str, str]] = Counter()
    for u, v in graph.edges():
        seen[(normalize_label(u), normalize_label(v))] += 1
    parallel_edges = sum(c - 1 for c in seen.values() if c > 1)
    bidirectional = sum(
        1 for (a, b) in seen if (b, a) in seen and a < b
    )

    metrics = {
        "n_nodes": len(names),
        "n_edges": graph.number_of_edges(),
        "exact_duplicate_nodes": exact_duplicates,
        "fuzzy_clusters": len(fuzzy_clusters),
        "fuzzy_duplicate_nodes": fuzzy_duplicate_nodes,
        "redundant_node_ratio": redundant_node_ratio,
        "parallel_edges": parallel_edges,
        "bidirectional_pairs": bidirectional,
    }
    # Higher is better: 1.0 means no duplicate variables.
    metrics["duplication"] = clamp(1.0 - redundant_node_ratio)
    return metrics


# ---------------------------------------------------------------------------
# 4. Reproducibility across repeated runs
# ---------------------------------------------------------------------------

def _node_name_sets(graph: nx.DiGraph) -> dict[str, str]:
    return {
        normalize_label(data.get("name", node)): str(data.get("name", node))
        for node, data in graph.nodes(data=True)
    }


def _edge_key(graph: nx.DiGraph) -> set[tuple[str, str]]:
    return {
        (normalize_label(u), normalize_label(v))
        for u, v in graph.edges()
    }


def pairwise_reliability(g1: nx.DiGraph, g2: nx.DiGraph) -> dict[str, Any]:
    n1 = _node_name_sets(g1)
    n2 = _node_name_sets(g2)
    e1 = _edge_key(g1)
    e2 = _edge_key(g2)

    n_inter = set(n1) & set(n2)
    n_union = set(n1) | set(n2)
    e_inter = e1 & e2
    e_union = e1 | e2

    node_jaccard = len(n_inter) / len(n_union) if n_union else 0.0
    edge_jaccard = len(e_inter) / len(e_union) if e_union else 0.0
    node_dice = (
        2 * len(n_inter) / (len(n1) + len(n2)) if (len(n1) + len(n2)) else 0.0
    )
    edge_dice = (
        2 * len(e_inter) / (len(e1) + len(e2)) if (len(e1) + len(e2)) else 0.0
    )

    # Direction agreement over edges whose node-pair exists in both graphs.
    pairs1 = {(a, b) for a, b in e1}
    pairs2 = {(a, b) for a, b in e2}
    common_pairs = pairs1 & pairs2
    reverse_pairs = {
        (a, b) for (a, b) in pairs1 if (b, a) in pairs2 and a != b
    }
    n_common = len(common_pairs)
    n_reverse = len(reverse_pairs)
    direction_agreement = n_common / (n_common + n_reverse) if (n_common + n_reverse) else 1.0

    # Fuzzy node match (best token-overlap) as a softer overlap measure.
    matched = 0
    remaining = dict(n2)
    for key1, name1 in n1.items():
        best = None
        best_score = 0.0
        for key2, name2 in remaining.items():
            score = token_overlap(name1, name2)
            if score > best_score:
                best_score = score
                best = key2
        if best is not None and best_score >= 0.8:
            matched += 1
            remaining.pop(best, None)
    fuzzy_node_match = matched / max(len(n1), 1)

    return {
        "node_jaccard": node_jaccard,
        "edge_jaccard": edge_jaccard,
        "node_dice": node_dice,
        "edge_dice": edge_dice,
        "node": node_dice,
        "edge": edge_dice,
        "direction": direction_agreement,
        "common_directed_edges": n_common,
        "reversed_direction_edges": n_reverse,
        "fuzzy_node_match": fuzzy_node_match,
    }


def reliability_metrics(graphs: list[nx.DiGraph]) -> dict[str, Any]:
    if len(graphs) < 2:
        return {
            "available": False,
            "n_graphs": len(graphs),
            "message": "reproducibility requires at least 2 graphs",
            "reliability": None,
        }

    pair_metrics = []
    for g1, g2 in combinations(graphs, 2):
        pair_metrics.append(pairwise_reliability(g1, g2))

    agg = {
        key: mean(metric[key] for metric in pair_metrics)
        for key in (
            "node_jaccard", "edge_jaccard", "node_dice", "edge_dice",
            "node", "edge", "direction", "fuzzy_node_match",
            "common_directed_edges", "reversed_direction_edges",
        )
    }
    agg["available"] = True
    agg["n_graphs"] = len(graphs)
    agg["n_pairs"] = len(pair_metrics)
    agg["pairs"] = pair_metrics
    agg["reliability"] = sum(
        DEFAULT_RELIABILITY_WEIGHTS[key] * agg[key]
        for key in DEFAULT_RELIABILITY_WEIGHTS
    )
    return agg


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

def overall_score(quality: dict[str, Any], coverage: dict[str, Any],
                  duplication: dict[str, Any],
                  reliability: dict[str, Any]) -> float:
    rel = reliability.get("reliability")
    weights = dict(DEFAULT_OVERALL_WEIGHTS)

    if rel is None:
        # Redistribute the reliability weight over the available dimensions.
        available = {
            "quality": quality["quality"],
            "coverage": coverage["coverage"],
            "duplication": duplication["duplication"],
        }
        total_weight = sum(
            weights[key] for key in available
        )
        score = sum(
            weights[key] * available[key] for key in available
        ) / total_weight if total_weight else 0.0
    else:
        score = (
            weights["quality"] * quality["quality"]
            + weights["coverage"] * coverage["coverage"]
            + weights["duplication"] * duplication["duplication"]
            + weights["reliability"] * rel
        )
    return clamp(score)


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _bar(value: float | None, width: int = 20) -> str:
    if value is None:
        return "-" * width
    filled = int(round(clamp(value) * width))
    return "#" * filled + "-" * (width - filled)


def print_report(results: dict[str, Any]) -> None:
    print("\n" + "=" * 70)
    print("Causal graph evaluation report")
    print("=" * 70)

    graphs = results["graphs"]
    print(f"\n[Inputs] graphs={len(graphs)} document={results['document']}")
    for item in graphs:
        print(f"  - {item['path']}")

    for item in graphs:
        q = item["quality"]
        print("\n" + "-" * 70)
        print(f"Graph: {Path(item['path']).name}")
        print(f"  nodes={q['n_nodes']}  edges={q['n_edges']}  "
              f"DAG={q['is_dag']}  cycles={q['cycle_count']}")
        flags = []
        if not q.get("has_grounding"):
            flags.append("no grounding spans")
        if not q.get("has_explicit"):
            flags.append("no explicit flag")
        if not q.get("has_polarity"):
            flags.append("no polarity flag")
        if flags:
            print(f"  schema note: {', '.join(flags)} (sub-scores read 0)")
        print(f"  quality            {_pct(q['quality']):>8}  {_bar(q['quality'])}")
        print(f"    acyclic          {_pct(q['acyclic']):>8}")
        print(f"    evidence grounded {_pct(q['grounded']):>7}")
        print(f"    explicit evidence {_pct(q['explicit']):>7}")
        print(f"    mean confidence  {_pct(q['confident']):>8}")
        print(f"    polarity defined {_pct(q['polarity']):>7}")
        print(f"    non-isolated     {_pct(q['connected']):>7}")

        c = item["coverage"]
        print(f"  coverage           {_pct(c['coverage']):>8}  {_bar(c['coverage'])}")
        print(f"    span union       {_pct(c['span']):>8}  "
              f"({c['span_union_chars']}/{c['source_chars']} chars)")
        print(f"    sentence cover   {_pct(c['sentence']):>8}  "
              f"({c['sentence_covered']}/{c['n_sentences']})")
        print(f"    key-term cover   {_pct(c['term']):>8}  "
              f"({c['terms_covered']}/{c['n_key_terms']})")

        d = item["duplication"]
        print(f"  no-duplication     {_pct(d['duplication']):>8}  {_bar(d['duplication'])}")
        print(f"    exact dup nodes  {d['exact_duplicate_nodes']}")
        print(f"    fuzzy clusters   {d['fuzzy_clusters']}  "
              f"(redundant nodes={d['fuzzy_duplicate_nodes']})")
        print(f"    redundant ratio  {_pct(d['redundant_node_ratio'])}")
        print(f"    parallel edges   {d['parallel_edges']}  "
              f"bidirectional pairs={d['bidirectional_pairs']}")

    r = results["reliability"]
    if r.get("available"):
        print("\n" + "-" * 70)
        print(f"Reproducibility across {r['n_graphs']} runs "
              f"({r['n_pairs']} pairs)")
        print(f"  reliability        {_pct(r['reliability']):>8}  {_bar(r['reliability'])}")
        print(f"    node Dice/F1     {_pct(r['node']):>8}")
        print(f"    node Jaccard     {_pct(r['node_jaccard']):>8}")
        print(f"    edge Dice/F1     {_pct(r['edge']):>8}")
        print(f"    edge Jaccard     {_pct(r['edge_jaccard']):>8}")
        print(f"    direction agree  {_pct(r['direction']):>8}  "
              f"(reversed={r['reversed_direction_edges']:.0f})")
        print(f"    fuzzy node match {_pct(r['fuzzy_node_match']):>8}")
    else:
        print("\n" + "-" * 70)
        print(f"Reproducibility: {r.get('message', 'n/a')}")

    print("\n" + "=" * 70)
    print(f"OVERALL SCORE: {results['overall'] * 100:.1f} / 100")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate(graph_paths: Iterable[Path | str],
             document_path: Path | str | None = None,
             fuzzy_threshold: float = 0.8) -> dict[str, Any]:
    """Evaluate graphs and return a structured result dictionary."""
    graphs: list[nx.DiGraph] = []
    for path in graph_paths:
        graphs.append(nx.read_gml(str(path)))

    source_text, content_text = "", ""
    doc_display = "none"
    if document_path is not None and Path(document_path).exists():
        source_text, content_text = load_document(Path(document_path))
        doc_display = str(document_path)

    graph_results = []
    for path, graph in zip(graph_paths, graphs):
        graph_results.append({
            "path": str(path),
            "quality": quality_metrics(graph),
            "coverage": coverage_metrics(graph, source_text, content_text),
            "duplication": duplication_metrics(graph, fuzzy_threshold),
        })

    reliability = reliability_metrics(graphs)
    overall = overall_score(
        graph_results[0]["quality"],
        graph_results[0]["coverage"],
        graph_results[0]["duplication"],
        reliability,
    ) if graph_results else 0.0

    return {
        "document": doc_display,
        "graphs": graph_results,
        "reliability": reliability,
        "overall": overall,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quantitatively evaluate causal graphs."
    )
    parser.add_argument(
        "--graphs", nargs="+", required=True,
        help="One or more GML graph files to evaluate.",
    )
    parser.add_argument(
        "--document", default=None,
        help="Source document JSON used for coverage metrics.",
    )
    parser.add_argument(
        "--fuzzy-threshold", type=float, default=0.8,
        help="Token-overlap threshold for fuzzy duplicate nodes.",
    )
    parser.add_argument(
        "--json", default=None,
        help="Write the full structured result to this JSON path.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = evaluate(
        graph_paths=args.graphs,
        document_path=args.document,
        fuzzy_threshold=args.fuzzy_threshold,
    )

    print_report(results)

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as file:
            json.dump(results, file, ensure_ascii=False, indent=2)
        print(f"Structured report written to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
