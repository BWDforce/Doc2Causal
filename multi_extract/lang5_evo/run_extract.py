#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lang5_evo: lang5 + semantic merge rules + evidence-based decycling.

Evolved post-processing (this is the "latest version" pipeline used by exp12):

  1. SEMANTIC MERGE RULES (default; ``--legacy-merge`` restores the old merge):
     - no transitive chaining: a node may only merge with a CLUSTER
       REPRESENTATIVE, never through an intermediate member (kills the
       "merge-black-hole" cascades seen in exp11/gen1, e.g. one node swallowing
       15 distinct time-series variables);
     - numeric/timestamp protection: two names that differ ONLY in numeric
       tokens (e.g. "... at 12:15" vs "... at 12:30", "pump A1" vs "pump A2")
       are NEVER merged, no matter the token overlap;
     - representative = longest name; attributes/values merged; parallel edges
       deduplicated by confidence; self-loops produced by merging are removed.
     Merge statistics are written to ``merge_stats.json``.

  2. DE-CYCLING (default; ``--keep-cycles`` disables it for ablation):
     after conflict resolution / meta propagation / merging, any remaining
     directed cycle is broken by removing the WEAKEST-EVIDENCE edge inside the
     cycle (scored by confidence + presence of sign/mechanism, tie-broken by
     SCC size then name order). Every removal is logged to ``decycle.json``,
     so the output graph is a DAG with an auditable break record.

Everything else (compact task, theme anchoring, CausalCoT meta, conflict
resolution, fallback/retry, efficiency) is identical to lang5.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MULTI_EXTRACT_DIR = PROJECT_ROOT / "multi_extract"
sys.path.insert(0, str(MULTI_EXTRACT_DIR))

import networkx as nx  # noqa: E402

import run_multi_langextract_2 as lang2  # noqa: E402  (reuses CausalCoT prompt/helpers)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "Test-text" / "input" / "multi_text_4"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "Test-text" / "output" / "multi_text_4"
DEFAULT_THEME = "combustible dust explosions"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="lang4: theme-anchored compact extraction.")
    p.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY"))
    p.add_argument("--base-url", default="https://api.deepseek.com")
    p.add_argument("--model", default="deepseek-v4-flash")
    p.add_argument("--temperature", type=float, default=0.7)
    p.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--init-graph", type=Path, default=None,
                   help="Optional GML to seed the world graph (incremental continuation).")
    p.add_argument("--theme", default=DEFAULT_THEME,
                   help="Centre theme clamped onto both extraction stages.")
    p.add_argument("--max-steps", type=int, default=2,
                   help="Discovery agent max steps (2 = one observe-error->fix retry, still bounded).")
    p.add_argument("--extraction-passes", type=int, default=2,
                   help="LangExtract passes per document (1 = faster, ~half the LangExtract LLM calls).")
    p.add_argument("--merge-threshold", type=float, default=0.7)
    p.add_argument("--propagate-threshold", type=float, default=0.7)
    p.add_argument("--reverse", action="store_true",
                   help="Process documents in reverse order (order-sensitivity check).")
    p.add_argument("--no-merge", action="store_true")
    p.add_argument("--legacy-merge", action="store_true",
                   help="Use the old chain-closure merge instead of semantic merge rules.")
    p.add_argument("--keep-cycles", action="store_true",
                   help="Skip de-cycling (keeps cycles in the final graph; ablation only).")
    p.add_argument("--no-filter", action="store_true")
    p.add_argument("--evaluate", action="store_true")
    return p.parse_args()


# --- LangExtract prompt with theme anchor ---
def build_lang_prompt(theme: str) -> str:
    base = lang2.PROMPT_DESCRIPTION_V2
    return base + f"""

CENTRE THEME (corpus scope): {theme}.
- Restrict extraction to causal content relevant to this theme.
- If the text cites an illustrative example of a DIFFERENT incident / accident
  type (e.g. maritime, railway, aspects unrelated to the theme), do NOT extract
  it as a causal fact of the document; skip those sentences.
""".rstrip()


# --- meta helpers (same semantics as lang3) ---
def collect_meta_edges(graph, extractions):
    edges = []
    for cause, effect, attrs in lang2.iter_relation_meta(extractions):
        if not graph.has_edge(cause, effect):
            continue
        data = graph.edges[cause, effect]
        meta = {k: data[k] for k in ("effect_sign", "rung", "mechanism") if data.get(k) is not None}
        if meta:
            edges.append((cause, effect, meta))
    return edges


def propagate_meta(graph, meta_edges, threshold=0.7):
    filled = 0
    for a, b, data in graph.edges(data=True):
        if data.get("effect_sign") is not None:
            continue
        best, best_score = None, 0.0
        for u, v, meta in meta_edges:
            score = min(lang2.name_overlap(a, u), lang2.name_overlap(b, v))
            if score > best_score:
                best, best_score = (u, v, meta), score
        if best is not None and best_score >= threshold:
            for key, value in best[2].items():
                data[key] = value
            filled += 1
    return filled


def candidate_edge_lines(graph) -> list[str]:
    lines = []
    for u, v, d in graph.edges(data=True):
        sign = d.get("effect_sign", "?")
        rung = d.get("rung", "?")
        lines.append(f"- {u} -> {v}  (effect_sign={sign}, rung={rung})")
    return lines


def aligned_names(world_graph, limit=120) -> str:
    names = sorted(str(n) for n in world_graph.nodes())[:limit]
    return ", ".join(names)


def build_compact_task(source_text, pre_graph, world_graph, theme, aligned_names_str) -> str:
    edges = candidate_edge_lines(pre_graph)
    edge_block = "\n".join(edges) if edges else "- <none>"
    aligned = aligned_names_str or "<none yet>"
    return f"""CENTRE THEME: {theme}. Extract only causal content relevant to this theme.
Ignore illustrative examples of unrelated incident types cited in the text.

SOURCE TEXT:
{source_text}

VALIDATED CANDIDATE EDGES (high priority; keep unless contradicted by the source):
{edge_block}

ALREADY-ALIGNED VARIABLE NAMES (reuse the closest existing name; do NOT create a
near-duplicate variable):
{aligned}

TASK: refine and complete the causal graph from the source text only.
Write exactly ONE code block that builds a networkx.DiGraph and ends with
final_answer(G). Do NOT print the graph or long JSON. Do NOT echo code.
Use the MANDATORY skeleton below; the result is schema-validated, so attribute
names must match exactly (no **dict expansion, no extra keys).

MANDATORY CODE SKELETON:
```py
import networkx as nx
G = nx.DiGraph()

# NODE TEMPLATE (repeat for each node). `name` is BOTH the node id and a
# REQUIRED attribute.
G.add_node(
    "<name>", name="<name>", description="<one sentence>", type="<type>",
    values=["<value>", ...], supporting_text_snippets=["<verbatim quote>"],
    current_value="<observed value>", contextual_information="<short context>",
)
# allowed `type`: event | condition | measurement | action | string

# EDGE TEMPLATE (repeat for each edge). `cause`/`effect` are REQUIRED and must
# equal the endpoint node names.
G.add_edge(
    "<cause>", "<effect>", cause="<cause>", effect="<effect>",
    description="<mechanism>", type="direct",
    contextual_information="<short context>", strength="high",
    confidence="high", function=None,
)
# allowed `type`: direct | indirect

final_answer(G)
```

STRICT RULES:
- Every node MUST carry exactly: name, description, type, values, and the
  optional supporting_text_snippets / current_value / contextual_information.
- Every edge MUST carry exactly: cause, effect, description, type, and the
  optional contextual_information / strength / confidence / function.
- Write one explicit G.add_node(...) / G.add_edge(...) call per item; never use
  **dict expansion, add_nodes_from with dicts, or any attribute not listed above.
""".strip()


def _run_agent_once(factory, base_model, api_key, max_steps, task):
    """Run a fresh agent once; returns (graph | None, error string)."""
    agent = factory.createAgent(base_model, api_key=api_key, max_steps=max_steps)
    try:
        result = agent.run(task)
    except Exception as exc:  # noqa: BLE001 - network/model errors
        return None, f"agent.run raised {type(exc).__name__}: {exc}"
    if isinstance(result, nx.DiGraph):
        return result, None
    return None, f"agent returned {type(result).__name__}: {str(result)[:1500]}"


def run_compact_discovery(args, source_text, pre_graph, pre_context, world_graph,
                          embedding_model):
    """Compact discovery agent: bounded steps, one retry, then pre_graph fallback."""
    litellm_id = args.model if "/" in args.model else f"deepseek/{args.model}"
    print(f"[compact agent] model={litellm_id}  max_steps={args.max_steps}")

    base_model = lang2.pipeline.LiteLLMModel(
        model_id=litellm_id, api_key=args.api_key,
        api_base=args.base_url, temperature=args.temperature)
    factory = lang2.pipeline.AtomicRAGDiscoveryAgentFactory(
        embeddings=embedding_model,
        initial_graph=pre_graph if pre_graph.number_of_nodes() else None)

    aligned = aligned_names(world_graph)
    task = build_compact_task(source_text, pre_graph, world_graph, args.theme, aligned)
    if pre_context:
        task += f"\n\n--- LangExtract grounded candidate evidence (abridged) ---\n{pre_context}\n"
    print(f"[compact agent] task chars={len(task)}")
    for attempt in (1, 2):
        result, err = _run_agent_once(factory, base_model, args.api_key,
                                      args.max_steps, task)
        if result is not None:
            if attempt == 2:
                print("[compact agent] retry succeeded.")
            return result
        print(f"[compact agent] attempt {attempt} failed: {err}")
        if attempt == 1:
            print("[compact agent] retrying once with a fresh agent ...")

    print("[compact agent] WARNING: falling back to pre_graph (LangExtract result).")
    return pre_graph.copy() if pre_graph.number_of_nodes() else nx.DiGraph()


def collect_edge_evidence(evidence, graph, source, order):
    """Accumulate per-edge evidence (direction/sign/rung/mechanism) from one doc."""
    for u, v, d in graph.edges(data=True):
        ev = evidence.setdefault((u, v), {
            "sources": [], "first_order": None,
            "sign": [], "rung": [], "mechanism": [],
        })
        order[0] += 1
        if ev["first_order"] is None:
            ev["first_order"] = order[0]
        ev["sources"].append(source)
        if d.get("effect_sign") is not None:
            ev["sign"].append((str(d["effect_sign"]), source))
        if d.get("rung") is not None:
            ev["rung"].append((str(d["rung"]), source))
        if d.get("mechanism"):
            ev["mechanism"].append((str(d["mechanism"]), source))


def _majority(values):
    """values: list[(value, source)] -> (chosen, votes_dict, rule)."""
    cnt = Counter(v for v, _ in values)
    if len(cnt) == 1:
        return values[0][0], dict(cnt), "unanimous"
    top = cnt.most_common()[0][1]
    tied = [v for v, c in cnt.items() if c == top]
    if len(tied) == 1:
        return tied[0], dict(cnt), "majority_vote"
    chosen = None
    for v, _ in values:
        if v in tied:
            chosen = v
            break
    return chosen, dict(cnt), "tie_break_earliest"


def resolve_conflicts(graph, evidence):
    """Resolve direction / sign / rung / mechanism conflicts in place.
    Returns (graph, records)."""
    records = []
    edge_set = set(graph.edges())
    seen_pairs = set()
    for u, v in list(graph.edges()):
        if (v, u) not in edge_set or frozenset((u, v)) in seen_pairs:
            continue
        seen_pairs.add(frozenset((u, v)))
        ev1 = evidence.get((u, v), {})
        ev2 = evidence.get((v, u), {})
        c1, c2 = len(ev1.get("sources", [])), len(ev2.get("sources", []))
        f1 = ev1.get("first_order", float("inf"))
        f2 = ev2.get("first_order", float("inf"))
        if c1 > c2 or (c1 == c2 and f1 <= f2):
            keep, drop = (u, v), (v, u)
        else:
            keep, drop = (v, u), (u, v)
        rule = "majority_vote" if c1 != c2 else "tie_break_earliest"
        if graph.has_edge(*drop):
            graph.remove_edge(*drop)
            edge_set.discard(drop)
        records.append({
            "kind": "direction", "pair": [u, v],
            "votes": {f"{u}->{v}": c1, f"{v}->{u}": c2},
            "decision": f"{keep[0]}->{keep[1]}",
            "dropped": f"{drop[0]}->{drop[1]}",
            "rule": rule,
            "sources": {f"{u}->{v}": ev1.get("sources", []),
                        f"{v}->{u}": ev2.get("sources", [])},
        })
    for u, v in list(graph.edges()):
        ev = evidence.get((u, v))
        if not ev:
            continue
        for attr, out_key in (("sign", "effect_sign"), ("rung", "rung"),
                              ("mechanism", "mechanism")):
            values = ev[attr]
            if len({x for x, _ in values}) <= 1:
                continue
            chosen, votes, rule = _majority(values)
            previous = graph.edges[u, v].get(out_key)
            graph.edges[u, v][out_key] = chosen
            records.append({
                "kind": attr, "edge": [u, v], "candidates": votes,
                "decision": chosen, "previous": previous, "rule": rule,
                "sources": [src for _, src in values],
            })
    return graph, records


def write_conflicts(records, path,
                    strategy="majority_vote_across_documents; tie_break=earliest"):
    payload = {"strategy": strategy,
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "total_conflicts": len(records),
               "records": records}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _edge_quality(data) -> int:
    """Higher = stronger evidence (used to pick the weakest edge on a cycle)."""
    conf = str(data.get("confidence") or "medium").lower()
    q = {"high": 3, "medium": 2, "low": 1}.get(conf, 2)
    if (data.get("effect_sign") is not None and str(data.get("effect_sign")) != ""):
        q += 1
    if data.get("mechanism"):
        q += 1
    return q


def _non_numeric_tokens(name: str) -> str:
    """Fingerprint of the non-numeric content tokens of a name."""
    toks = re.findall(r"[A-Za-z0-9]+", str(name).lower())
    core = []
    for t in toks:
        t2 = "".join(ch for ch in t if not ch.isdigit())
        if len(t2) > 1:
            core.append(t2)
    return " ".join(sorted(set(core)))


def merge_duplicate_nodes_evo(graph, threshold=0.7):
    """Semantic merge: representative-only (no transitive chaining) +
    numeric/timestamp protection. Returns (graph, stats)."""
    stats = {"merged": 0, "protected_numeric": 0, "self_loops_removed": 0}
    if graph.number_of_nodes() < 2:
        return graph, stats
    nodelist = sorted(graph.nodes(), key=lambda n: (-len(str(n)), str(n)))
    clusters = []  # dicts: rep, members
    for n in nodelist:
        best_idx, best_score = None, threshold
        for ci, c in enumerate(clusters):
            sc = lang2.name_overlap(str(n), str(c["rep"]))
            if sc < threshold:
                continue
            if _non_numeric_tokens(n) == _non_numeric_tokens(str(c["rep"])):
                stats["protected_numeric"] += 1
                continue
            if sc > best_score:
                best_idx, best_score = ci, sc
        if best_idx is None:
            clusters.append({"rep": n, "members": [n]})
        else:
            clusters[best_idx]["members"].append(n)

    canonical_attrs, mapping = {}, {}
    for c in clusters:
        members = c["members"]
        rep = c["rep"]
        if len(members) == 1:
            continue
        stats["merged"] += len(members) - 1
        attrs = dict(graph.nodes[rep])
        attrs["merged_aliases"] = [str(m) for m in members]
        values = []
        for m in members:
            mv = graph.nodes[m].get("values")
            if isinstance(mv, list):
                values.extend(mv)
        if values:
            attrs["values"] = sorted(set(values))
        canonical_attrs[rep] = attrs
        for m in members:
            mapping[m] = rep

    def conf_level(d):
        conf = str(d.get("confidence") or "medium").lower()
        return {"high": 3, "medium": 2, "low": 1}.get(conf, 2)

    merged = nx.DiGraph()
    for node, data in graph.nodes(data=True):
        target = mapping.get(node, node)
        if target in merged:
            continue
        merged.add_node(target, **canonical_attrs.get(target, data))
    for u, v, data in graph.edges(data=True):
        a, b = mapping.get(u, u), mapping.get(v, v)
        if a == b:
            stats["self_loops_removed"] += 1
            continue
        if merged.has_edge(a, b):
            old = merged[a][b]
            if conf_level(data) > conf_level(old):
                merged[a][b].update(data)
        else:
            merged.add_edge(a, b, **data)
    merged.graph["merged_duplicate_nodes"] = stats["merged"]
    return merged, stats


def cleanup_degenerate_edges(graph) -> int:
    """Drop self-loops that survived (defensive; merge already avoids most)."""
    loops = [(u, v) for u, v in graph.edges() if u == v]
    for u, v in loops:
        graph.remove_edge(u, v)
    if loops:
        print(f"[cleanup] removed {len(loops)} self-loop(s)")
    return len(loops)


def decycle_graph(graph):
    """Break every directed cycle by removing weakest-evidence edges.
    Returns (n_removed, records)."""
    records = []
    removed = 0
    while True:
        sccs = [s for s in nx.strongly_connected_components(graph) if len(s) > 1]
        if not sccs:
            break
        cand = []
        for s in sccs:
            sub = graph.subgraph(s)
            for u, v, data in sub.edges(data=True):
                cand.append((_edge_quality(data), len(s), str(u).lower(),
                             str(v).lower(), u, v, dict(data)))
        qmin = min(c[0] for c in cand)
        pool = [c for c in cand if c[0] == qmin]
        pool.sort(key=lambda c: (c[1], c[2], c[3]))
        _, scc_size, _, _, u, v, data = pool[0]
        graph.remove_edge(u, v)
        removed += 1
        records.append({
            "edge": [str(u), str(v)],
            "scc_size": scc_size,
            "quality_score": qmin,
            "confidence": data.get("confidence"),
            "effect_sign": data.get("effect_sign"),
            "mechanism": (str(data.get("mechanism") or ""))[:160],
            "description": (str(data.get("description") or ""))[:160],
        })
    return removed, records


def write_json_sidecar(payload, path, kind):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{kind}] log -> {path}")
    return path

def combined_document(input_dir: Path, output_dir: Path) -> Path:
    summaries = []
    for path in sorted(p for p in input_dir.glob("*.json")):
        with path.open("r", encoding="utf-8-sig") as f:
            doc = json.load(f)
        summaries.append(f"{doc['title']['eng']}. {doc['summary']}")
    out = output_dir / "_combined_document.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump({"title": {"eng": "lang4 combined corpus"},
                   "summary": "\n\n".join(summaries)}, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    args = parse_args()
    if not args.api_key:
        raise SystemExit("Set DEEPSEEK_API_KEY or pass --api-key.")

    docs = sorted(p for p in args.input_dir.glob("*.json") if p.name != "provenance.json")
    if not docs:
        raise SystemExit(f"No JSON documents found in {args.input_dir}")
    if args.reverse:
        docs.reverse()

    # CausalCoT prompt + theme anchor for LangExtract stage.
    lang2.pipeline.PROMPT_DESCRIPTION = build_lang_prompt(args.theme)
    lang2.pipeline.make_example = lang2.make_example_v2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.init_graph:
        init_graph = nx.read_gml(str(args.init_graph))
        print(f"[lang5] init-graph: {init_graph.number_of_nodes()} nodes / "
              f"{init_graph.number_of_edges()} edges loaded from {args.init_graph}")
        world_manager = lang2.pipeline.BaseWorldManager(initial_graph=init_graph)
    else:
        world_manager = lang2.pipeline.BaseWorldManager()
    meta_edges = []
    evidence = {}
    order = [0]
    embedding_model = lang2.pipeline.HuggingFaceEmbeddings(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    print(f"[lang5_evo] docs={len(docs)} reverse={args.reverse} theme={args.theme!r} "
          f"max_steps={args.max_steps}")
    print(f"[lang5] in={args.input_dir}  out={args.output_dir}")

    for i, path in enumerate(docs, 1):
        print(f"\n{'='*60}\nDocument {i}/{len(docs)}: {path.name}\n{'='*60}")
        source_text, _ = lang2.pipeline.load_source_document(path)

        lang_document = lang2.pipeline.run_langextract(
            source_text=source_text, api_key=args.api_key, model_id=args.model,
            base_url=args.base_url, extraction_passes=args.extraction_passes, max_char_buffer=1800)
        grounded = lang2.pipeline.grounded_extractions(lang_document)
        pre_context = lang2.pipeline.format_langextract_context(grounded)
        pre_graph = lang2.pipeline.build_pre_graph(grounded)
        annotated = lang2.attach_relation_meta(pre_graph, grounded)
        meta_edges.extend(collect_meta_edges(pre_graph, grounded))
        print(f"[meta] attached fields to {annotated} pre_graph edge(s)")

        llm_graph = run_compact_discovery(args, source_text, pre_graph, pre_context,
                                          world_manager.get_complete_graph(),
                                          embedding_model)
        collect_edge_evidence(evidence, pre_graph, f"doc{i}_pre", order)
        collect_edge_evidence(evidence, llm_graph, f"doc{i}_llm", order)
        # incremental alignment: merge now so next doc sees this doc's variable names
        world_manager.merge(pre_graph)
        world_manager.merge(llm_graph)
        print(f"[doc {i}] pre_graph={pre_graph.number_of_nodes()}n/"
              f"{pre_graph.number_of_edges()}e  llm_graph="
              f"{llm_graph.number_of_nodes()}n/{llm_graph.number_of_edges()}e")

    final_graph = world_manager.get_complete_graph().copy()
    n0, e0 = final_graph.number_of_nodes(), final_graph.number_of_edges()
    final_graph, conflict_records = resolve_conflicts(final_graph, evidence)
    if conflict_records:
        print(f"[conflict] resolved {len(conflict_records)} conflict(s)")
    filled = propagate_meta(final_graph, meta_edges, args.propagate_threshold)
    print(f"[meta] propagated lang meta to {filled} additional edge(s)")

    if not args.no_filter:
        filtered = lang2.filter_action_nodes(final_graph)
        if filtered.number_of_nodes() != final_graph.number_of_nodes():
            print(f"[filter] removed {final_graph.number_of_nodes()-filtered.number_of_nodes()} action node(s)")
        final_graph = filtered
    merge_stats = {"mode": "disabled", "merged": 0, "protected_numeric": 0,
                   "self_loops_removed": 0}
    if not args.no_merge:
        if args.legacy_merge:
            merged = lang2.merge_duplicate_nodes(final_graph, args.merge_threshold)
            n_alias = merged.graph.get("merged_duplicate_nodes", 0)
            if n_alias:
                print(f"[merge] merged {n_alias} near-duplicate variable(s)")
            final_graph = merged
            merge_stats = {"mode": "legacy", "merged": n_alias,
                           "protected_numeric": 0, "self_loops_removed": 0}
        else:
            merged, merge_stats = merge_duplicate_nodes_evo(final_graph, args.merge_threshold)
            merge_stats["mode"] = "semantic"
            if merge_stats["merged"]:
                print(f"[merge-evo] merged {merge_stats['merged']} near-duplicate variable(s), "
                      f"numeric-protected {merge_stats['protected_numeric']} pair(s)")
            final_graph = merged
        loops = cleanup_degenerate_edges(final_graph)
        merge_stats["self_loops_removed"] += loops

    cycle_removed, cycle_records = 0, []
    if not args.keep_cycles:
        cycle_removed, cycle_records = decycle_graph(final_graph)
        if cycle_removed:
            print(f"[decycle] removed {cycle_removed} weakest-evidence edge(s); "
                  f"DAG={nx.is_directed_acyclic_graph(final_graph)}")
        else:
            print(f"[decycle] graph already acyclic; nothing removed")

    signs, rungs = lang2.edge_meta_stats(final_graph)
    print(f"\nFinal graph: {final_graph.number_of_nodes()} nodes, "
          f"{final_graph.number_of_edges()} edges, "
          f"DAG={nx.is_directed_acyclic_graph(final_graph)} (raw: {n0}n/{e0}e)")
    print(f"[meta] final effect_sign={dict(signs)} rung={dict(rungs)}")

    gml_path, png_path = lang2.pipeline.save_outputs(final_graph, args.output_dir)
    print(f"GML -> {gml_path}")
    print(f"PNG -> {png_path}")
    conflicts_path = write_conflicts(conflict_records, args.output_dir / "conflicts.json")
    print(f"[conflict] log -> {conflicts_path}")
    write_json_sidecar(
        {"strategy": "remove_weakest_evidence_edge_on_cycle; tie_break=smaller_scc_then_name_order",
         "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "total_removed": cycle_removed,
         "keep_cycles_disabled_by_flag": bool(args.keep_cycles),
         "records": cycle_records},
        args.output_dir / "decycle.json", "decycle")
    write_json_sidecar(
        {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "merge_threshold": args.merge_threshold,
         "stats": merge_stats},
        args.output_dir / "merge_stats.json", "merge-stats")

    if args.evaluate:
        import evaluate_graph
        combined = combined_document(args.input_dir, args.output_dir)
        result = evaluate_graph.evaluate(graph_paths=[gml_path], document_path=combined)
        evaluate_graph.print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())