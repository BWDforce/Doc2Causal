#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Multi-document alignment test runner - LangExtract-enhanced, CausalCoT edition.

Differences from `run_multi_langextract_1.py`:
1. CausalCoT-style extraction prompt:
   - "mechanism first, then edges": the model writes a one-line mechanism chain
     before emitting each link, so multi-step mechanisms keep their intermediate
     variables and their direction.
   - Every `causal_relation` is annotated with `effect_sign` (+/-/0) and `rung`
     (1=associational / 2=interventional / 3=counterfactual, per the CLadder /
     Pearl causal-hierarchy convention).
2. `attach_relation_meta`: copies `effect_sign`, `rung` and `mechanism` from the
   grounded LangExtract extractions onto the matching pre-graph edges, so the
   metadata survives into the merged GML (edges are machine-readable for the
   downstream inference / evaluation step).

Retains the exp4 improvements (canonical variable names, no maintenance-action
nodes, post-hoc near-duplicate merge + action-node filter). Temperature defaults
to 0.7 (same as exp4).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
LANGEXTRACT_ROOT = PROJECT_ROOT / "langextract"

sys.path.insert(0, str(LANGEXTRACT_ROOT))
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(PROJECT_ROOT))

import networkx as nx  # noqa: E402

import pipeline  # noqa: E402  (pipeline.py at repo root)

# Guard against Windows GBK console/pipes: the LLM output may contain
# characters such as the superscript 2 (m2) that cannot be encoded in cp936,
# which would crash rich/smolagents logging (UnicodeEncodeError).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

INPUT_DIR = PROJECT_ROOT / "data" / "Test-text" / "input" / "multi_text"
OUTPUT_DIR = PROJECT_ROOT / "data" / "Test-text" / "output" / "multi_text"


# ---------------------------------------------------------------------------
# Prompts / examples
# ---------------------------------------------------------------------------

PROMPT_DESCRIPTION_V2 = """
Extract causal variables and explicit causal relationships from industrial
equipment documentation, following a causal chain-of-thought protocol.

Think in two phases for every causal statement before writing it down:
1. MECHANISM CHAINS: identify the full one-line mechanism the text describes,
   e.g. "Oil Flow DOWN -> Oil Film Quality DOWN -> Friction UP". Use only the
   steps that are explicitly present in the text; do not import your own
   domain physics.
2. EDGES: emit one causal_relation per link of each chain. Never collapse a
   multi-step chain into a direct relation when the intermediate variable is
   explicitly present in the text.

Rules:
1. Every extraction_text must be an exact span from the source text.
2. Use extraction_class "causal_variable" for causal variables or conditions.
3. Use extraction_class "causal_relation" for one explicit causal relation.
4. Do not collapse a multi-step mechanism into a direct relation when the
   intermediate variable is explicitly present in the source.
5. Do not add common-sense relations that are not supported by the source.
6. Preserve the direction of causality and the polarity of the relationship.
7. Maintenance actions and human interventions are NOT causal variables. Do
   not extract "condenser tube cleaning", "tightening a fitting",
   replacements, inspections, measurements or similar actions. If an action
   changes a physical condition, extract the physical condition itself
   (e.g. Condenser Fouling) and only link the action to it when the text
   explicitly states that causal link.
8. Use ONE consistent canonical name per physical variable across the whole
   document. Never create two variables that are near-synonyms:
   "Compressor Power Draw" and "Compressor Power Consumption" are the same
   variable. Keep names short, consistent and technical.

Causal-level annotation (rung) for each causal_relation. Choose the level of
evidence the source text actually provides:
- rung 1 / associational: the text only reports an observed association,
  correlation, co-occurrence or descriptive reading, without any controlled
  change or manipulation.
- rung 2 / interventional: the text reports a controlled intervention, test
  setting, adjustment, or a general physical mechanism ("X causes Y",
  "increasing X raises Y"). Industrial equipment mechanisms and run-tests
  normally belong here.
- rung 3 / counterfactual: the text argues about what would have happened
  differently in the past ("if X had not occurred, Y would not have...").

For causal_variable, provide attributes:
  canonical_name, entity_type, variable_role, polarity.

For causal_relation, provide attributes:
  cause, effect, relation_type, polarity, explicit, confidence, effect_sign, rung, mechanism.

effect_sign convention (monotonic direction of the mechanism, independent of
the specific measured values in the sentence):
- "+" means: when the cause increases, the effect increases.
- "-" means: when the cause increases, the effect decreases.
- "0" means: no monotonic link, or the sign is ambiguous.
mechanism is the one-line chain this link belongs to, e.g.
"Oil Flow DOWN -> Oil Film Quality DOWN -> Friction UP".
""".strip()


def make_example_v2() -> Any:
    """One-shot example matching PROMPT_DESCRIPTION_V2 (incl. effect_sign/rung/mechanism)."""
    lx_data = pipeline.lx_data
    text = (
        "Low oil flow reduces oil film quality. "
        "Reduced oil film quality increases friction. "
        "Increased friction raises bearing temperature."
    )
    mechanism = "Oil Flow DOWN -> Oil Film Quality DOWN -> Friction UP -> Bearing Temperature UP"
    extractions = [
        lx_data.Extraction(
            extraction_class="causal_variable",
            extraction_text="Low oil flow",
            attributes={
                "canonical_name": "Oil Flow",
                "entity_type": "condition",
                "variable_role": "cause",
                "polarity": "decrease",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_relation",
            extraction_text="Low oil flow reduces oil film quality.",
            attributes={
                "cause": "Oil Flow",
                "effect": "Oil Film Quality",
                "relation_type": "physical_mechanism",
                "polarity": "decrease",
                "explicit": "true",
                "confidence": "high",
                "effect_sign": "+",
                "rung": "2",
                "mechanism": mechanism,
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_variable",
            extraction_text="oil film quality",
            attributes={
                "canonical_name": "Oil Film Quality",
                "entity_type": "condition",
                "variable_role": "effect",
                "polarity": "decrease",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_relation",
            extraction_text="Reduced oil film quality increases friction.",
            attributes={
                "cause": "Oil Film Quality",
                "effect": "Friction",
                "relation_type": "physical_mechanism",
                "polarity": "increase",
                "explicit": "true",
                "confidence": "high",
                "effect_sign": "-",
                "rung": "2",
                "mechanism": mechanism,
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_variable",
            extraction_text="friction",
            attributes={
                "canonical_name": "Friction",
                "entity_type": "measurement",
                "variable_role": "effect",
                "polarity": "increase",
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_relation",
            extraction_text="Increased friction raises bearing temperature.",
            attributes={
                "cause": "Friction",
                "effect": "Bearing Temperature",
                "relation_type": "physical_mechanism",
                "polarity": "increase",
                "explicit": "true",
                "confidence": "high",
                "effect_sign": "+",
                "rung": "2",
                "mechanism": mechanism,
            },
        ),
        lx_data.Extraction(
            extraction_class="causal_variable",
            extraction_text="bearing temperature",
            attributes={
                "canonical_name": "Bearing Temperature",
                "entity_type": "measurement",
                "variable_role": "effect",
                "polarity": "increase",
            },
        ),
    ]
    return lx_data.ExampleData(text=text, extractions=extractions)


# ---------------------------------------------------------------------------
# Causal metadata helpers (effect_sign / rung / mechanism)
# ---------------------------------------------------------------------------

VALID_SIGNS = {"+", "-", "0"}
VALID_RUNGS = {"1", "2", "3"}


def iter_relation_meta(extractions: list[Any]) -> Any:
    for extraction in extractions:
        if getattr(extraction, "extraction_class", "") != "causal_relation":
            continue
        attributes = getattr(extraction, "attributes", None) or {}
        cause = pipeline.value_to_text(attributes.get("cause")).strip()
        effect = pipeline.value_to_text(attributes.get("effect")).strip()
        if not cause or not effect:
            continue
        yield cause, effect, attributes


def attach_relation_meta(graph: nx.DiGraph, extractions: list[Any]) -> int:
    """Copy effect_sign / rung / mechanism from grounded extractions onto edges."""
    annotated = 0
    for cause, effect, attributes in iter_relation_meta(extractions):
        if not graph.has_edge(cause, effect):
            continue
        edge = graph.edges[cause, effect]

        sign = pipeline.value_to_text(attributes.get("effect_sign")).strip()
        if sign in VALID_SIGNS:
            edge["effect_sign"] = sign

        rung = pipeline.value_to_text(attributes.get("rung")).strip()
        if rung in VALID_RUNGS:
            edge["rung"] = int(rung)

        mechanism = pipeline.value_to_text(attributes.get("mechanism")).strip()
        if mechanism:
            edge["mechanism"] = mechanism

        annotated += 1
    return annotated


def edge_meta_stats(graph: nx.DiGraph) -> tuple[Counter, Counter]:
    signs: Counter = Counter()
    rungs: Counter = Counter()
    for _, _, data in graph.edges(data=True):
        sign = data.get("effect_sign")
        if sign in VALID_SIGNS:
            signs[sign] += 1
        rung = data.get("rung")
        if rung in (1, 2, 3):
            rungs[rung] += 1
    return signs, rungs


# ---------------------------------------------------------------------------
# Duplicate merging / action filtering (unchanged from the v1 runner)
# ---------------------------------------------------------------------------

ACTION_PATTERNS = [
    r"\bclean(ed|ing)?\b",
    r"\btight(en(ed|ing)?|ening)?\b",
    r"\breplac(e|ed|ing|ement)\b",
    r"\brepair(ed|ing|s)?\b",
    r"\bmaintenance\b",
    r"\binspect(ed|ing|ion)\b",
    r"\bmeasur(e|ed|es|ement|ing)\b",
    r"\brecord(ed|ing|s)?\b",
    r"\btop[ -]?up\b",
    r"\brestart(ed|ing)?\b",
    r"\bshut ?down\b",
    r"\bcheck(ed|ing)?\b",
    r"\bmonitor(ed|ing)?\b",
]


def normalize_name(name):
    text = re.sub(r"[^0-9a-zA-Z]+", " ", str(name or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def name_tokens(name):
    stop = {"a", "an", "and", "are", "at", "be", "by", "for", "from", "has",
            "have", "in", "is", "it", "its", "of", "on", "or", "the", "their",
            "this", "to", "was", "were", "will", "with", "if", "then", "when",
            "while", "after", "before", "during", "under", "over", "above",
            "below", "between", "through", "into", "than", "not", "no", "may",
            "can", "could", "should", "would", "also", "more", "most", "same",
            "any", "all", "both", "only", "very", "just", "even"}
    return {w for w in normalize_name(name).split() if w not in stop and len(w) > 1}


def name_overlap(a, b):
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_action_name(name):
    low = str(name or "").lower()
    return any(re.search(pat, low) for pat in ACTION_PATTERNS)


def filter_action_nodes(graph):
    """Remove maintenance-action / intervention nodes and their edges."""
    if graph.number_of_nodes() == 0:
        return graph
    drop = {n for n in graph.nodes() if is_action_name(str(n))}
    if not drop:
        return graph
    kept = [n for n in graph.nodes() if n not in drop]
    if not kept:
        return nx.DiGraph()
    return nx.induced_subgraph(graph, kept).copy()


def _conf_level(edge_data):
    conf = str(edge_data.get("confidence", "medium")).lower()
    return {"high": 3, "medium": 2, "low": 1}.get(conf, 2)


def merge_duplicate_nodes(graph, threshold=0.7):
    """Merge near-duplicate variables (token-overlap >= threshold)."""
    if graph.number_of_nodes() < 2:
        return graph
    nodes = list(graph.nodes(data=True))
    parent = list(range(len(nodes)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if name_overlap(str(nodes[i][0]), str(nodes[j][0])) >= threshold:
                union(i, j)

    groups = {}
    for i in range(len(nodes)):
        groups.setdefault(find(i), []).append(i)

    canonical_attrs = {}
    mapping = {}
    merged_alias_count = 0
    for members in groups.values():
        if len(members) == 1:
            continue
        merged_alias_count += len(members) - 1
        rep = max(members, key=lambda i: len(str(nodes[i][0])))
        rep_node = nodes[rep][0]
        attrs = dict(nodes[rep][1])
        attrs["merged_aliases"] = [str(nodes[i][0]) for i in members]
        values = []
        for i in members:
            v = nodes[i][1].get("values")
            if isinstance(v, list):
                values.extend(v)
        if values:
            attrs["values"] = sorted(set(values))
        canonical_attrs[rep_node] = attrs
        for i in members:
            mapping[nodes[i][0]] = rep_node

    merged = nx.DiGraph()
    for node, data in graph.nodes(data=True):
        target = mapping.get(node, node)
        if target in merged:
            continue
        merged.add_node(target, **canonical_attrs.get(target, data))
    for u, v, data in graph.edges(data=True):
        a, b = mapping.get(u, u), mapping.get(v, v)
        if a == b:
            continue
        if merged.has_edge(a, b):
            old = merged[a][b]
            if _conf_level(data) > _conf_level(old):
                merged[a][b].update(data)
        else:
            merged.add_edge(a, b, **data)
    merged.graph["merged_duplicate_nodes"] = merged_alias_count
    return merged


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LangExtract+Agent pipeline (CausalCoT edition) over multi_text."
    )
    parser.add_argument(
        "--api-key", default=os.environ.get("DEEPSEEK_API_KEY"),
        help="DeepSeek API key (defaults to DEEPSEEK_API_KEY).",
    )
    parser.add_argument(
        "--model", default="deepseek-v4-flash",
        help="Model id used by LangExtract and the original agent.",
    )
    parser.add_argument(
        "--base-url", default="https://api.deepseek.com",
        help="OpenAI-compatible API base URL.",
    )
    parser.add_argument(
        "--temperature", type=float, default=0.7,
        help="Temperature for the original discovery agent (default 0.7).",
    )
    parser.add_argument(
        "--extraction-passes", type=int, default=2,
        help="LangExtract extraction passes.",
    )
    parser.add_argument(
        "--max-char-buffer", type=int, default=1800,
        help="LangExtract chunking max char buffer.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR,
        help="Output directory for the final merged graph.",
    )
    parser.add_argument(
        "--evaluate", action="store_true",
        help="Score the final graph with src/evaluate_graph.py.",
    )
    parser.add_argument(
        "--merge-threshold", type=float, default=0.7,
        help="Token-overlap threshold for merging near-duplicate variables.",
    )
    parser.add_argument(
        "--no-merge", action="store_true",
        help="Disable post-hoc merging of near-duplicate variables.",
    )
    parser.add_argument(
        "--no-filter", action="store_true",
        help="Disable removal of maintenance-action / intervention nodes.",
    )
    return parser.parse_args()


def combined_document(output_dir: Path) -> Path:
    docs = sorted(INPUT_DIR.glob("*.json"))
    summaries: list[str] = []
    for path in docs:
        with path.open("r", encoding="utf-8-sig") as f:
            doc = json.load(f)
        summaries.append(f"{doc['title']['eng']}. {doc['summary']}")
    combined = {
        "title": {"eng": "CL-300 Water-Cooled Screw Chiller - Combined Multi-Document Corpus"},
        "summary": "\n\n".join(summaries),
    }
    out = output_dir / "_combined_document.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    args = parse_args()

    if not args.api_key:
        raise SystemExit(
            "DeepSeek API key is missing. Set DEEPSEEK_API_KEY or pass --api-key."
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    docs = sorted(INPUT_DIR.glob("*.json"))
    if not docs:
        raise SystemExit(f"No JSON documents found in {INPUT_DIR}")

    # CausalCoT prompt + example (mechanism-first, effect_sign / rung / mechanism).
    pipeline.PROMPT_DESCRIPTION = PROMPT_DESCRIPTION_V2
    pipeline.make_example = make_example_v2

    world_manager = pipeline.BaseWorldManager()
    print("Processing multi_text with LangExtract + Agent pipeline [CausalCoT edition]")
    print(f"output dir: {args.output_dir}")
    print(f"merge threshold={args.merge_threshold}  merge={'on' if not args.no_merge else 'off'}  "
          f"action-filter={'on' if not args.no_filter else 'off'}")

    for i, path in enumerate(docs, 1):
        print(f"\n{'=' * 60}\nDocument {i}/{len(docs)}: {path.name}\n{'=' * 60}")
        source_text, _ = pipeline.load_source_document(path)

        lang_document = pipeline.run_langextract(
            source_text=source_text,
            api_key=args.api_key,
            model_id=args.model,
            base_url=args.base_url,
            extraction_passes=args.extraction_passes,
            max_char_buffer=args.max_char_buffer,
        )
        grounded = pipeline.grounded_extractions(lang_document)
        pre_context = pipeline.format_langextract_context(grounded)
        pre_graph = pipeline.build_pre_graph(grounded)

        annotated = attach_relation_meta(pre_graph, grounded)
        signs, rungs = edge_meta_stats(pre_graph)
        print(f"[meta] attached effect_sign/rung/mechanism to {annotated} pre_graph edge(s); "
              f"signs={dict(signs)} rungs={dict(rungs)}")

        llm_graph = pipeline.run_original_discovery(
            source_text=source_text,
            pre_graph=pre_graph,
            pre_context=pre_context,
            api_key=args.api_key,
            model_id=args.model,
            base_url=args.base_url,
            temperature=args.temperature,
        )

        world_manager.merge(pre_graph)
        world_manager.merge(llm_graph)
        print(
            f"[doc {i}] pre_graph={pre_graph.number_of_nodes()}n/"
            f"{pre_graph.number_of_edges()}e  llm_graph="
            f"{llm_graph.number_of_nodes()}n/{llm_graph.number_of_edges()}e"
        )

    final_graph = world_manager.get_complete_graph()
    n0, e0 = final_graph.number_of_nodes(), final_graph.number_of_edges()
    if not args.no_filter:
        filtered = filter_action_nodes(final_graph)
        removed = n0 - filtered.number_of_nodes()
        if removed:
            print(f"[filter] removed {removed} maintenance-action node(s)")
        final_graph = filtered
    if not args.no_merge:
        merged_graph = merge_duplicate_nodes(final_graph, args.merge_threshold)
        merged_aliases = merged_graph.graph.get("merged_duplicate_nodes", 0)
        if merged_aliases:
            print(f"[merge] merged {merged_aliases} near-duplicate variable(s) into canonical nodes")
        final_graph = merged_graph

    signs, rungs = edge_meta_stats(final_graph)
    print(f"\nFinal merged graph: {final_graph.number_of_nodes()} nodes, "
          f"{final_graph.number_of_edges()} edges, "
          f"DAG={nx.is_directed_acyclic_graph(final_graph)} "
          f"(raw: {n0} nodes / {e0} edges)")
    print(f"[meta] final graph effect_sign={dict(signs)} rung={dict(rungs)}")

    gml_path, png_path = pipeline.save_outputs(final_graph, args.output_dir)
    print(f"GML -> {gml_path}")
    print(f"PNG -> {png_path}")

    if args.evaluate:
        import evaluate_graph
        combined = combined_document(args.output_dir)
        result = evaluate_graph.evaluate(
            graph_paths=[gml_path],
            document_path=combined,
        )
        evaluate_graph.print_report(result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())