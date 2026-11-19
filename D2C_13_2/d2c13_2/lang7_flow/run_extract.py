#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lang7: lang6_evo3 + P2 contract fixes (reviewer-driven).

Changes vs lang6_evo3 (see review-exp.md P2):
  P2-1 vote-by-document: ``--vote-mode {assertion, document,
      canonical_document}``. `document` counts one vote per asserting source
      document with doc-internal majority (abstain on internal tie);
      `canonical_document` additionally coalesces the evidence ledger through
      the normalization merge mapping before arbitration, so synonyms of one
      variable vote together. Tie-break = earliest document, then earliest
      assertion. The raw assertion ledger is dumped to `evidence.json`.
  P2-2 weighted cycle score: ``_edge_quality`` uses
      ``q = w_conf*conf + w_support*|A_e| + w_schema*(sign+mech)``
      (defaults 3.0 / 1.0 / 1.0; overridable via ``--w-conf/--w-support/--w-schema``),
      and `decycle.json` records the score decomposition.
  P2-3 completion = hypothesis: neighbor-completion edges are tagged
      ``edge_type="hypothesis", provenance="derived", A_e=derived``, are
      excluded from the DAG guarantee (de-cycling works on evidence edges
      only) and carry no document provenance. Optional
      ``--completion-require-span`` rejects proposals not locatable in the
      corpus (option-3 sub-constraint).
  P2-4 span_ids: final edges export ``span_ids_json`` (doc + char interval +
      channel) aggregated from the evidence ledger through the merge mapping;
      `span_report.json` reports the share of span-backed edges.
  P2-6 cycle taxonomy: cycles whose opposing directions come from distinct
      documents (or from explicit differing `condition` fields) are classified
      as regime-dependent and preserved (``--keep-conditional-cycles``,
      default on); only contradiction cycles are broken. The DAG guarantee is
      stated over the evidence subgraph.

Everything else is identical to lang6_evo3. Defaults reproduce the legacy
behavior via ``--vote-mode assertion`` for differential re-analysis.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MULTI_EXTRACT_DIR = PROJECT_ROOT / "vendor" / "multi_extract"
sys.path.insert(0, str(MULTI_EXTRACT_DIR))

import networkx as nx  # noqa: E402

import run_multi_langextract_2 as lang2  # noqa: E402  (reuses CausalCoT prompt/helpers)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize  # noqa: E402  (LLM-adjudicated canonicalization stage)
import extract_boost  # noqa: E402  (evidence union / name alignment / completion)
import entity_inventory as entity_first  # noqa: E402  (D2C_13_2 entity-first recall)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "Test-text" / "input" / "multi_text_4"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "Test-text" / "output" / "multi_text_4"
DEFAULT_THEME = "combustible dust explosions"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="lang7: lang6_evo3 + P2 contract fixes (vote-by-document, weighted evidence score, hypothesis-tagged completion, span_ids, cycle taxonomy).")
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
    p.add_argument("--max-output-tokens", type=int, default=None,
                   help="Maximum output tokens for each LangExtract request.")
    p.add_argument("--merge-threshold", type=float, default=0.7)
    p.add_argument("--propagate-threshold", type=float, default=0.7)
    p.add_argument("--reverse", action="store_true",
                   help="Process documents in reverse order (order-sensitivity check).")
    p.add_argument("--no-merge", action="store_true")
    p.add_argument("--legacy-merge", action="store_true",
                   help="Use the old chain-closure merge instead of semantic merge rules.")
    p.add_argument("--no-llm-merge", action="store_true",
                    help="Fall back to the lang5_evo1 semantic merge (ablation).")
    p.add_argument("--merge-model", default=None,
                    help="Model for the canonicalization stage (default: same as --model).")
    p.add_argument("--merge-batch", type=int, default=20,
                    help="Candidate pairs per adjudication call.")
    p.add_argument("--merge-temperature", type=float, default=0.2)
    p.add_argument("--entity-first", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Use a high-recall entity inventory and relation-matrix second pass.")
    p.add_argument("--relation-parallel-workers", type=int, default=4,
                   help="Maximum paragraph relation batches executed concurrently.")
    p.add_argument("--entity-inventory-max-items", type=int, default=200)
    p.add_argument("--entity-inventory-max-chunks", type=int, default=6)
    p.add_argument("--entity-inventory-model", default=None)
    p.add_argument("--entity-inventory-temperature", type=float, default=0.0)
    p.add_argument("--evidence-union", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Run a second LangExtract pass and union the grounded evidence.")
    p.add_argument("--no-neighbor-completion", action="store_true",
                   help="Disable the post-merge neighbor-completion pass.")
    p.add_argument("--completion-max-edges", type=int, default=40,
                   help="Max edges the neighbor-completion pass may add.")
    p.add_argument("--merge-desc-strong", type=float, default=0.95,
                   help="desc overlap above which a desc-only, type/time-compatible pair may merge "
                        "without a corroborating channel.")
    p.add_argument("--merge-corroborate", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Require a corroborating lexical/structural channel for "
                        "desc-only candidate pairs before merging.")
    p.add_argument("--merge-reconfirm", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Give maybe/uncertain pairs a strict second adjudication pass.")
    p.add_argument("--keep-cycles", action="store_true",
                   help="Skip de-cycling (keeps cycles in the final graph; ablation only).")
    p.add_argument("--no-filter", action="store_true")
    p.add_argument("--vote-mode", default="document",
                   choices=("assertion", "document", "canonical_document"),
                   help="Vote unit for direction/attribute arbitration: per assertion "
                        "(legacy), per asserting document (doc-internal majority, abstain "
                        "on internal tie), or per document after canonical-name coalescing.")
    p.add_argument("--w-conf", type=float, default=3.0,
                   help="Cycle-edge quality weight on confidence.")
    p.add_argument("--w-support", type=float, default=1.0,
                   help="Cycle-edge quality weight on document support count |A_e|.")
    p.add_argument("--w-schema", type=float, default=1.0,
                   help="Cycle-edge quality weight on sign/mechanism schema completeness.")
    p.add_argument("--keep-conditional-cycles", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Preserve cycles whose opposing evidence comes from distinct documents "
                        "or explicit differing conditions (regime-dependent); only contradiction "
                        "cycles are de-cycled.")
    p.add_argument("--completion-require-span", action="store_true",
                   help="Reject neighbor-completion proposals that cannot be located in the corpus text.")
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


def build_compact_task(source_text, pre_graph, world_graph, theme, aligned_names_str,
                       keep_conditional: bool = True) -> str:
    edges = candidate_edge_lines(pre_graph)
    edge_block = "\n".join(edges) if edges else "- <none>"
    aligned = aligned_names_str or "<none yet>"
    if keep_conditional:
        cond_line = '    description="<mechanism>", type="direct", condition="<regime>",'
        cond_rules = ('- condition is REQUIRED: if the text ties the effect to a regime ("when", '
                       '  "whenever", "only while", "during"), copy that regime as a short phrase; '
                       '  otherwise write condition="".')
        cond_extra = ', and condition'
    else:
        cond_line = '    description="<mechanism>", type="direct",'
        cond_rules = 'condition attributes are NOT allowed in this run.'
        cond_extra = ''
    return f"""CENTRE THEME: {theme}. Extract only causal content relevant to this theme.
Ignore illustrative examples of unrelated incident types cited in the text.

SOURCE TEXT:
{source_text}

VALIDATED CANDIDATE EDGES (high priority; keep unless contradicted by the source):
{edge_block}

ALREADY-ALIGNED VARIABLE NAMES (reuse the closest existing name; do NOT create a
near-duplicate variable):
{aligned}

TIME-SENSITIVE RULE: names indexed by clock times or sampling points (e.g.
12:15 vs 12:45) are DIFFERENT variables. Never merge two labels that differ
only in their time/index token, and never reuse a timed label for another time.

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
{cond_line}
    contextual_information="<short context>", strength="high",
    confidence="high", function=None,
)
# allowed `type`: direct | indirect

final_answer(G)
```

STRICT RULES:
- Every node MUST carry exactly: name, description, type, values, and the
  optional supporting_text_snippets / current_value / contextual_information.
- Every edge MUST carry exactly: cause, effect, description, type{cond_extra};
  optional extras: contextual_information / strength / confidence / function.
{cond_rules}
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
    task = build_compact_task(source_text, pre_graph, world_graph, args.theme, aligned,
                               keep_conditional=args.keep_conditional_cycles)
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


def _normalize_graph_evidence_spans(graph, text: str, prefix_offset: int) -> int:
    """Convert source_text offsets to summary coordinates without double-shifting."""
    if not prefix_offset or not text:
        return 0
    corrected = 0
    for _, _, data in graph.edges(data=True):
        raw = str(data.get("evidence_span") or "")
        if ":" not in raw:
            continue
        left, right = raw.split(":", 1)
        try:
            start, end = int(left), int(right)
        except ValueError:
            continue
        new_start, new_end = start - prefix_offset, end - prefix_offset
        if not (0 <= new_start < new_end <= len(text)):
            continue
        original_score = _span_boundary_score(text, start, end)
        shifted_score = _span_boundary_score(text, new_start, new_end)
        if shifted_score > original_score:
            data["evidence_span"] = f"{new_start}:{new_end}"
            corrected += 1
    return corrected


def collect_edge_evidence(evidence, graph, source, order):
    """Accumulate per-edge evidence (direction/sign/rung/mechanism) from one doc.

    P2-1: every assertion is recorded individually (doc / channel / span /
    order) while the legacy `sources` list is kept for compatibility. The
    arbitration layer then aggregates these assertions into document votes."""
    source = str(source)
    doc_id = source.split("_", 1)[0]
    channel = source.split("_", 1)[1] if "_" in source else source
    for u, v, d in graph.edges(data=True):
        ev = evidence.setdefault((u, v), {
            "sources": [], "first_order": None, "first_doc_order": None,
            "assertions": [],
            "sign": [], "rung": [], "mechanism": [],
        })
        order[0] += 1
        if ev["first_order"] is None:
            ev["first_order"] = order[0]
        didx = _doc_index(source)
        if ev["first_doc_order"] is None or didx < ev["first_doc_order"]:
            ev["first_doc_order"] = didx
        span = d.get("evidence_span")
        ev["assertions"].append({
            "source": source, "doc_id": doc_id, "channel": channel,
            "order": order[0], "span": str(span) if span else "",
        })
        ev["sources"].append(source)
        if d.get("effect_sign") is not None:
            ev["sign"].append((str(d["effect_sign"]), source))
        if d.get("rung") is not None:
            ev["rung"].append((str(d["rung"]), source))
        if d.get("mechanism"):
            ev["mechanism"].append((str(d["mechanism"]), source))


def _doc_index(source):
    """Document arrival index from a source label like doc3_llm."""
    m = re.search(r"doc(\d+)", str(source).lower())
    return int(m.group(1)) if m else 10 ** 6


def _first_doc(assertions):
    idxs = [_doc_index(a.get("doc_id", a.get("source", ""))) for a in assertions]
    return min(idxs) if idxs else 10 ** 6


def _doc_votes(u_assertions, v_assertions):
    """Per-direction document votes. Each asserting doc votes for the side it
    asserted more often; an internal tie makes the doc abstain."""
    du = Counter(a.get("doc_id", a.get("source", "")) for a in u_assertions)
    dv = Counter(a.get("doc_id", a.get("source", "")) for a in v_assertions)
    d1 = d2 = 0
    detail = {}
    for doc in sorted(set(du) | set(dv)):
        nu, nv = du.get(doc, 0), dv.get(doc, 0)
        if nu > nv:
            d1 += 1
            detail[doc] = "u->v"
        elif nv > nu:
            d2 += 1
            detail[doc] = "v->u"
        else:
            detail[doc] = "abstain"
    return d1, d2, detail


def _majority(values, vote_mode="document"):
    """values: list[(value, source)] -> (chosen, votes_dict, rule, doc_detail).

    vote_mode essentials:
      assertion - one vote per grounded assertion (legacy behavior)
      document  - one vote per asserting doc; doc-internal majority, abstain
                  on internal tie; ties between values broken by earliest doc
                  then earliest assertion."""
    if not values:
        return "", {}, "none", {}
    if vote_mode == "assertion":
        cnt = Counter(v for v, _ in values)
        if len(cnt) == 1:
            return values[0][0], dict(cnt), "unanimous", {}
        top = cnt.most_common()[0][1]
        tied = [v for v, c in cnt.items() if c == top]
        if len(tied) == 1:
            return tied[0], dict(cnt), "majority_vote", {}
        for v, _ in values:
            if v in tied:
                return v, dict(cnt), "tie_break_earliest", {}
    # document-level aggregation
    doc_vals = {}
    first_doc = {}
    first_order = {}
    for v, src in values:
        doc = str(src).split("_", 1)[0]
        doc_vals.setdefault(doc, Counter())[v] += 1
        first_doc.setdefault(v, _doc_index(src))
        first_order.setdefault(v, 10 ** 6)
    votes = Counter()
    detail = {}
    value_first_order = {}
    for doc, cnt in doc_vals.items():
        top = cnt.most_common()[0][1]
        doc_tied = [v for v, c in cnt.items() if c == top]
        if len(doc_tied) == 1:
            v = doc_tied[0]
            votes[v] += 1
            value_first_order.setdefault(v, 10 ** 6)
            od = None
            for vi, (vv, src) in enumerate(values):
                if vv == v and str(src).split("_", 1)[0] == doc:
                    od = vi
                    break
            value_first_order[v] = min(value_first_order[v], od)
            detail[doc] = v
        else:
            detail[doc] = "abstain"
    if not votes:
        # every doc abstained on an internal tie: fall back to assertion-level
        # counts so arbitration still terminates deterministically.
        cnt = Counter(v for v, _ in values)
        top = cnt.most_common()[0][1]
        tied = [v for v, c in cnt.items() if c == top]
        for v, _ in values:
            if v in tied:
                return v, dict(cnt), "all_docs_abstained_fallback_assertion", detail
    if len(votes) == 1:
        v = next(iter(votes))
        return v, dict(votes), "unanimous", detail
    top = votes.most_common()[0][1]
    tied = [v for v, c in votes.items() if c == top]
    if len(tied) == 1:
        return tied[0], dict(votes), "majority_vote", detail
    tied.sort(key=lambda v: (first_doc.get(v, 10 ** 6), value_first_order.get(v, 10 ** 6)))
    rule = "tie_break_earliest_doc"
    return tied[0], dict(votes), rule, detail



def resolve_conflicts(graph, evidence, vote_mode="document", member_of=None, keep_conditional=True):
    """Resolve direction / sign / rung / mechanism conflicts in place.

    P2-1 vote units:
      assertion           legacy: one vote per grounded assertion
      document            one vote per asserting source document
      canonical_document  document votes after coalescing the ledger through
                          the merge mapping (member_of: raw name -> rep) so
                          synonyms of one variable vote together.

    Records carry both count layers plus a per-doc vote detail for audit.
    Returns (graph, records)."""
    coalesced = None
    if vote_mode == "canonical_document" and member_of:
        coalesced = coalesce_evidence(evidence, member_of)
    records = []
    edge_set = set(graph.edges())
    seen_pairs = set()
    for u, v in list(graph.edges()):
        if (v, u) not in edge_set or frozenset((u, v)) in seen_pairs:
            continue
        seen_pairs.add(frozenset((u, v)))
        ev1, ev2 = evidence.get((u, v), {}), evidence.get((v, u), {})
        a1 = ev1.get("assertions", [])
        a2 = ev2.get("assertions", [])
        if coalesced is not None:
            e1c = coalesced.get((u, v), {})
            e2c = coalesced.get((v, u), {})
            if e1c or e2c:
                a1 = e1c.get("assertions", a1)
                a2 = e2c.get("assertions", a2)
        c1, c2 = len(a1), len(a2)
        d1, d2, doc_detail = _doc_votes(a1, a2)
        use1, use2 = (c1, c2) if vote_mode == "assertion" else (d1, d2)
        f1 = ev1.get("first_order", float("inf"))
        f2 = ev2.get("first_order", float("inf"))
        fd1, fd2 = _first_doc(a1), _first_doc(a2)
        cond_a = str(graph[u][v].get("condition") or "").strip()
        cond_b = str(graph[v][u].get("condition") or "").strip()
        if keep_conditional and cond_a and cond_b and cond_a != cond_b:
            graph[u][v]["regime_dependent"] = True
            graph[v][u]["regime_dependent"] = True
            records.append({
                "kind": "condition", "pair": [u, v],
                "classification": "conditional_preserved",
                "conditions": {f"{u}->{v}": cond_a, f"{v}->{u}": cond_b},
                "decision": "keep_both",
                "rule": "regime_dependent_distinct_conditions",
            })
            continue
        if vote_mode == "assertion":
            if c1 > c2 or (c1 == c2 and f1 <= f2):
                keep, drop = (u, v), (v, u)
            else:
                keep, drop = (v, u), (u, v)
            rule = ("majority_vote" if c1 != c2 else "tie_break_earliest_assertion")
        else:
            if d1 != d2:
                keep, drop = ((u, v) if d1 > d2 else (v, u)), ((v, u) if d1 > d2 else (u, v))
                rule = "majority_vote"
            elif fd1 < fd2:
                keep, drop = (u, v), (v, u)
                rule = "tie_break_earliest_doc"
            elif fd2 < fd1:
                keep, drop = (v, u), (u, v)
                rule = "tie_break_earliest_doc"
            elif f1 <= f2:
                keep, drop = (u, v), (v, u)
                rule = "tie_break_earliest_assertion"
            else:
                keep, drop = (v, u), (u, v)
                rule = "tie_break_earliest_assertion"
        if graph.has_edge(*drop):
            graph.remove_edge(*drop)
            edge_set.discard(drop)
        records.append({
            "kind": "direction", "pair": [u, v],
            "vote_mode": vote_mode,
            "votes": {f"{u}->{v}": {"assertions": c1, "documents": d1},
                      f"{v}->{u}": {"assertions": c2, "documents": d2}},
            "doc_vote_detail": doc_detail or {},
            "decision": f"{keep[0]}->{keep[1]}",
            "dropped": f"{drop[0]}->{drop[1]}",
            "rule": rule,
            "sources": {f"{u}->{v}": ev1.get("sources", []),
                        f"{v}->{u}": ev2.get("sources", [])},
        })
    for u, v in list(graph.edges()):
        ev = evidence.get((u, v))
        if coalesced is not None:
            evc = coalesced.get((u, v))
            if evc:
                ev = evc
        if not ev:
            continue
        for attr, out_key in (("sign", "effect_sign"), ("rung", "rung"),
                              ("mechanism", "mechanism")):
            values = ev[attr]
            if len({x for x, _ in values}) <= 1:
                continue
            chosen, votes, rule, doc_detail = _majority(values, vote_mode)
            previous = graph.edges[u, v].get(out_key)
            graph.edges[u, v][out_key] = chosen
            records.append({
                "kind": attr, "edge": [u, v], "candidates": votes,
                "vote_mode": vote_mode,
                "doc_vote_detail": doc_detail or {},
                "decision": chosen, "previous": previous, "rule": rule,
                "sources": [src for _, src in values],
            })
    return graph, records


def coalesce_evidence(evidence, member_of):
    """Aggregate the assertion ledger through the merge mapping (raw -> rep)
    so synonyms of one variable share a vote pool (P2-1 canonical-document
    mode; also used for support/span aggregation after normalization)."""
    out = {}
    for (u, v), ev in evidence.items():
        ku = member_of.get(u, u)
        kv = member_of.get(v, v)
        base = out.setdefault((ku, kv), {
            "sources": [], "assertions": [], "first_order": 10 ** 9,
            "first_doc_order": 10 ** 9, "sign": [], "rung": [], "mechanism": [],
        })
        base["sources"].extend(ev.get("sources", []))
        base["assertions"].extend(ev.get("assertions", []))
        base["first_order"] = min(base["first_order"], ev.get("first_order", 10 ** 9))
        base["first_doc_order"] = min(base["first_doc_order"], ev.get("first_doc_order", 10 ** 9))
        for key in ("sign", "rung", "mechanism"):
            base[key].extend(ev.get(key, []))
    return out


def build_edge_support(graph, evidence, member_of=None):
    """{(u, v): (doc_id_set, n_assertions)} backing each final graph edge."""
    ledger = coalesce_evidence(evidence, member_of) if member_of else evidence
    lookup = {}
    for (u, v), ev in ledger.items():
        if graph.has_edge(u, v):
            docs = {a.get("doc_id", a.get("source", "")) for a in ev.get("assertions", [])}
            lookup[(u, v)] = (docs, len(ev.get("assertions", [])))
    return lookup


def attach_span_ids(graph, evidence, member_of=None):
    """P2-4: final edges export span_ids_json = [(doc, char interval, channel)]
    aggregated through the merge mapping. Returns (n_with_span, n_edges)."""
    ledger = coalesce_evidence(evidence, member_of) if member_of else evidence
    n_with = 0
    n_total = 0
    for u, v, data in graph.edges(data=True):
        n_total += 1
        spans = []
        ev = ledger.get((u, v))
        if ev:
            for a in ev.get("assertions", []):
                span = str(a.get("span") or "").strip()
                if span and span not in ("None", "ungrounded"):
                    spans.append({"doc": a.get("doc_id", ""), "span": span,
                                  "channel": a.get("channel", "")})
        if not spans and data.get("evidence_span"):
            spans.append({"doc": "?", "span": str(data["evidence_span"]), "channel": "attached"})
        if spans:
            data["span_ids_json"] = json.dumps(spans, ensure_ascii=False)
            n_with += 1
        else:
            data.setdefault("span_ids_json", "")
    return n_with, n_total


def dump_evidence(evidence, path, meta):
    """P2-1: raw assertion ledger snapshot (pre-arbitration), machine readable."""
    entries = {}
    for (u, v), ev in evidence.items():
        key = f"{u} || {v}"
        entries[key] = {
            "sources": ev.get("sources", []),
            "first_order": ev.get("first_order"),
            "first_doc_order": ev.get("first_doc_order"),
            "assertions": ev.get("assertions", []),
            "sign": [list(x) for x in ev.get("sign", [])],
            "rung": [list(x) for x in ev.get("rung", [])],
            "mechanism": [list(x) for x in ev.get("mechanism", [])],
        }
    payload = {"meta": meta,
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "n_entries": len(entries),
               "entries": entries}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_conflicts(records, path,
                    strategy="majority_vote_across_documents; tie_break=earliest; "
                            "cycles=dag_constraint_weakest_evidence"):
    payload = {"strategy": strategy,
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "total_conflicts": len(records),
               "records": records}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _edge_quality(data, support=0, weights=(3.0, 1.0, 1.0)):
    """P2-2 weighted evidence score used to pick the weakest edge on a cycle:

        q = w_conf * conf(high=3/medium=2/low=1)
            + w_support * |A_e|      (distinct supporting documents)
            + w_schema * (has_sign + has_mechanism)

    Defaults (3.0 / 1.0 / 1.0): confidence dominates, document support is the
    second axis, schema completeness only breaks near-ties -- so "weakest
    evidence" now includes, rather than excludes, the |A_e| support that the
    rest of the paper treats as the decision basis."""
    w_conf, w_support, w_schema = weights
    conf = str(data.get("confidence") or "medium").lower()
    q = w_conf * {"high": 3, "medium": 2, "low": 1}.get(conf, 2)
    supp = support
    if supp in (None, 0):
        supp = data.get("support_docs", 0)
    q += w_support * int(supp or 0)
    if data.get("effect_sign") is not None and str(data.get("effect_sign")) != "":
        q += w_schema
    if data.get("mechanism"):
        q += w_schema
    return q



_SLOT_STOP = {"the", "a", "an", "of", "at", "in", "on", "for", "to", "with", "and"}


def _slot_words(name: str):
    return [w for w in re.findall(r"[a-z]+", str(name).lower())
            if w not in _SLOT_STOP and len(w) > 1]


def _clock_tokens(name) -> frozenset:
    import re as _re
    return frozenset(_re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", str(name)))


def _time_protected(a, b) -> bool:
    """Names carrying DIFFERENT clock times that still share >=2 content words
    describe measurements at different sampling points -- never merge them."""
    ta, tb = _clock_tokens(a), _clock_tokens(b)
    if not ta or not tb or ta == tb:
        return False
    wa, wb = set(_slot_words(a)), set(_slot_words(b))
    return len(wa & wb) >= 2


def _slot_pattern_protected(a, b) -> bool:
    """Protect pairs that are the same word pattern differing by exactly one
    content word (systematic modifier difference, e.g. dissolved vs total)."""
    wa, wb = _slot_words(a), _slot_words(b)
    if not wa or not wb:
        return False
    i = 0
    while i < min(len(wa), len(wb)) and wa[i] == wb[i]:
        i += 1
    j = 0
    while j < min(len(wa) - i, len(wb) - i) and wa[-1 - j] == wb[-1 - j]:
        j += 1
    ra = wa[i:len(wa) - j] if j else wa[i:]
    rb = wb[i:len(wb) - j] if j else wb[i:]
    return len(ra) == 1 and len(rb) == 1 and ra[0] != rb[0]


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
    stats = {"merged": 0, "protected_numeric": 0, "protected_pattern": 0,
             "protected_time": 0, "self_loops_removed": 0}
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
            if _time_protected(n, str(c["rep"])):
                stats["protected_time"] += 1
                continue
            if _slot_pattern_protected(n, str(c["rep"])):
                stats["protected_pattern"] += 1
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
    return merged, stats, mapping


def cleanup_degenerate_edges(graph) -> int:
    """Drop self-loops that survived (defensive; merge already avoids most)."""
    loops = [(u, v) for u, v in graph.edges() if u == v]
    for u, v in loops:
        graph.remove_edge(u, v)
    if loops:
        print(f"[cleanup] removed {len(loops)} self-loop(s)")
    return len(loops)


def decycle_graph(graph, weights=(3.0, 1.0, 1.0), support_lookup=None,
                    keep_conditional_cycles=True):
    """Break every directed CONTRADICTION cycle by removing weakest-evidence
    edges; preserve regime-dependent cycles (P2-6).

    Cycle taxonomy on evidence:
      contradiction - opposing directions asserted by the same document, or
                      single-side evidence cycles
      conditional   - a pair carries opposing directions from DISJOINT
                      document sets (different regimes/timestamps), or the two
                      edges carry explicit differing `condition` fields
    Hypothesis edges (completion, P2-3) are outside the DAG guarantee and are
    excluded from cycle detection entirely.

    Returns (n_removed, records, conditional_report)."""
    records = []
    removed = 0
    cond_records = []
    core_evidence_edges = set()
    while True:
        # core = evidence edges only (skip hypothesis + preserved regime edges)
        core = nx.DiGraph()
        for u, v, d in graph.edges(data=True):
            if str(d.get("edge_type")) == "hypothesis":
                continue
            if d.get("regime_dependent"):
                continue
            core.add_edge(u, v, **d)
            core_evidence_edges.add((u, v))
        sccs = [sorted(s) for s in nx.strongly_connected_components(core) if len(s) > 1]
        if not sccs:
            break
        progressed = False
        for comp in sccs:
            sub = core.subgraph(comp)
            conditional_pairs = []
            seen_cond = set()
            for u, v, d in sub.edges(data=True):
                if not sub.has_edge(v, u):
                    continue
                if frozenset((u, v)) in seen_cond:
                    continue
                cond_a = str(d.get("condition") or "").strip()
                cond_b = str(sub[v][u].get("condition") or "").strip()
                if cond_a and cond_b and cond_a != cond_b:
                    conditional_pairs.append((u, v))
                    seen_cond.add(frozenset((u, v)))
                    continue
                sup = support_lookup or {}
                docs_a = sup.get((u, v), (set(), 0))[0]
                docs_b = sup.get((v, u), (set(), 0))[0]
                if docs_a and docs_b and not (docs_a & docs_b):
                    conditional_pairs.append((u, v))
                    seen_cond.add(frozenset((u, v)))
            if keep_conditional_cycles and conditional_pairs:
                for (u, v) in conditional_pairs:
                    if graph.has_edge(u, v):
                        graph[u][v]["regime_dependent"] = True
                    if graph.has_edge(v, u):
                        graph[v][u]["regime_dependent"] = True
                    cond_records.append({
                        "classification": "conditional_preserved",
                        "pair": [str(u), str(v)],
                        "regime_dependent": True,
                        "reason": ("opposing assertions from disjoint document sets "
                                   "or explicit differing condition fields"),
                    })
                progressed = True
                continue
            cand = []
            for u, v, d in sub.edges(data=True):
                docs, n_assert = (support_lookup or {}).get((u, v), (set(), 0))
                cand.append((_edge_quality(d, support=len(docs), weights=weights),
                             len(comp), len(docs), n_assert,
                             str(u).lower(), str(v).lower(), u, v, dict(d)))
            qmin = min(c[0] for c in cand)
            pool = [c for c in cand if c[0] == qmin]
            pool.sort(key=lambda c: (c[1], c[4], c[5]))  # SCC size, then name order
            _, scc_size, n_docs, n_assert, _, _, u, v, d = pool[0]
            graph.remove_edge(u, v)
            core_evidence_edges.discard((u, v))
            removed += 1
            records.append({
                "classification": "contradiction_removed",
                "edge": [str(u), str(v)],
                "scc_size": scc_size,
                "quality_score": qmin,
                "confidence": d.get("confidence"),
                "effect_sign": d.get("effect_sign"),
                "support_docs": n_docs,
                "support_assertions": n_assert,
                "weights": list(weights),
                "mechanism": (str(d.get("mechanism") or ""))[:160],
                "description": (str(d.get("description") or ""))[:160],
            })
            progressed = True
            break
        if not progressed:
            break
    conditional_pairs_preserved = len(cond_records)
    conditional_report = {
        "classifications": {"contradiction_removed": removed,
                            "conditional_preserved": conditional_pairs_preserved},
        "weights": list(weights),
        "records": cond_records,
    }
    return removed, records, conditional_report



def write_json_sidecar(payload, path, kind):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{kind}] log -> {path}")
    return path

def write_extraction_reports(args, union_report):
    """Write extraction-side reports without coupling report calls."""
    entity_report = write_json_sidecar({
        "enabled": bool(getattr(args, "entity_first", True)),
        "documents": getattr(args, "entity_first_docs", {}),
    }, args.output_dir / "entity_first_report.json", "entity-first")
    union_path = write_json_sidecar(
        union_report,
        args.output_dir / "evidence_union_report.json",
        "evidence-union",
    )
    return {"entity_first": entity_report, "evidence_union": union_path}

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


def run_merge_stage(args, final_graph):
    """Normalization stage with merge-mapping export.

    Returns (graph, merge_stats, member_of). member_of maps raw names to the
    cluster representative so the evidence ledger can later be coalesced
    (P2-1 canonical-document votes, P2-4 span_ids)."""
    merge_stats = {"mode": "disabled", "merged": 0, "protected_numeric": 0,
                   "protected_pattern": 0, "protected_time": 0,
                   "self_loops_removed": 0, "vote_mode": args.vote_mode}
    member_of = {}
    if args.no_merge:
        return final_graph, merge_stats, member_of
    if args.legacy_merge:
        merged = lang2.merge_duplicate_nodes(final_graph, args.merge_threshold)
        n_alias = merged.graph.get("merged_duplicate_nodes", 0)
        if n_alias:
            print(f"[merge] merged {n_alias} near-duplicate variable(s)")
        merge_stats = {"mode": "legacy", "merged": n_alias,
                       "protected_numeric": 0, "protected_pattern": 0,
                       "self_loops_removed": 0, "vote_mode": args.vote_mode}
        merge_stats["self_loops_removed"] += cleanup_degenerate_edges(merged)
        return merged, merge_stats, member_of
    if args.no_llm_merge:
        merged, merge_stats, mapping = merge_duplicate_nodes_evo(final_graph, args.merge_threshold)
        merge_stats["mode"] = "semantic(evo1-fallback)"
        merge_stats["vote_mode"] = args.vote_mode
        if merge_stats["merged"]:
            print(f"[merge-evo] merged {merge_stats['merged']} near-duplicate variable(s), "
                  f"numeric-protected {merge_stats['protected_numeric']} pair(s), "
                  f"pattern-protected {merge_stats['protected_pattern']} pair(s)")
        merge_stats["self_loops_removed"] += cleanup_degenerate_edges(merged)
        return merged, merge_stats, dict(mapping)
    merge_model = args.merge_model or args.model
    final_graph, mlog = normalize.normalize_nodes(
        final_graph,
        api_key=args.api_key, base_url=args.base_url,
        model=merge_model, temperature=args.merge_temperature,
        threshold=args.merge_threshold, batch=args.merge_batch,
        out_dir=args.output_dir,
        corroborate=args.merge_corroborate,
        reconfirm=args.merge_reconfirm,
        desc_strong=args.merge_desc_strong)
    mpp = args.output_dir / "merge_proposals.json"
    mpp.write_text(json.dumps(mlog, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[merge-llm] proposals log -> {mpp}")
    for cluster in mlog.get("merged_clusters", []):
        if not cluster:
            continue
        rep = cluster[0]
        for mem in cluster[1:]:
            member_of[mem] = rep
    st = mlog["stats"]
    merge_stats = {
        "mode": "llm-normalize", "model": merge_model,
        "merge_batch": args.merge_batch,
        "merged": st["merged"],
        "protected_numeric": st["veto_numeric"],
        "protected_time": st["veto_time"],
        "protected_pattern": 0,
        "self_loops_removed": st["self_loops_removed"],
        "pairs_adjudicated": st["pairs_adjudicated"],
        "llm_same": st["llm_same"],
        "llm_different": st["llm_different"],
        "llm_maybe": st["llm_maybe"],
        "veto_edge": st["veto_edge"],
        "corroboration_gated": st.get("corroboration_gated", 0),
        "reconfirmed_pairs": st.get("reconfirmed_pairs", 0),
        "r2_same": st.get("r2_same", 0),
        "r2_different": st.get("r2_different", 0),
        "desc_strong": args.merge_desc_strong,
        "candidates_scored_total": st.get("candidates_scored_total", 0),
        "reduction_ratio": st.get("reduction_ratio", 0.0),
        "vote_mode": args.vote_mode,
    }
    merge_stats["self_loops_removed"] += cleanup_degenerate_edges(final_graph)
    return final_graph, merge_stats, member_of


def main(argv: list[str] | None = None) -> int:
    old_argv = sys.argv
    if argv is not None:
        sys.argv = [old_argv[0], *argv]
    try:
        args = parse_args()
    finally:
        sys.argv = old_argv
    if not args.api_key:
        raise SystemExit("Set DEEPSEEK_API_KEY or pass --api-key.")
    weights = (args.w_conf, args.w_support, args.w_schema)

    docs = sorted(p2 for p2 in args.input_dir.glob("*.json") if p2.name != "provenance.json")
    if not docs:
        raise SystemExit(f"No JSON documents found in {args.input_dir}")
    if args.reverse:
        docs.reverse()

    lang2.pipeline.make_example = lang2.make_example_v2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.checkpoint_dir = args.output_dir / "_checkpoints"
    args.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    args.evidence_union_docs = {}
    args.entity_first_docs = {}
    args.current_entity_inventory = []
    args.current_relation_pairs = []
    if args.init_graph:
        init_graph = nx.read_gml(str(args.init_graph))
        print(f"[lang7] init-graph: {init_graph.number_of_nodes()} nodes / "
              f"{init_graph.number_of_edges()} edges loaded from {args.init_graph}")
        world_manager = lang2.pipeline.BaseWorldManager(initial_graph=init_graph)
    else:
        world_manager = lang2.pipeline.BaseWorldManager()
    meta_edges = []
    evidence = {}
    order = [0]
    embedding_model = lang2.pipeline.HuggingFaceEmbeddings(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    print(f"[lang7] docs={len(docs)} reverse={args.reverse} theme={args.theme!r} "
          f"max_steps={args.max_steps} vote_mode={args.vote_mode} "
          f"q_weights={list(weights)}")
    print(f"[lang7] in={args.input_dir}  out={args.output_dir}")

    for i, path in enumerate(docs, 1):
        print(chr(10) + '=' * 60 + chr(10) + f'Document {i}/{len(docs)}: {path.name}' + chr(10) + '=' * 60)
        args.current_doc_key = path.stem
        args.current_doc_index = i
        args.current_doc_total = len(docs)
        print(f"[D2C11-progress] stage=extract_document phase=start doc={i}/{len(docs)}", flush=True)
        source_text, summary = lang2.pipeline.load_source_document(path)
        span_prefix_offset = max(0, len(source_text) - len(summary))

        aligned_now = aligned_names(world_manager.get_complete_graph())
        lang2.pipeline.PROMPT_DESCRIPTION = extract_boost.build_aligned_lang_prompt(
            build_lang_prompt(args.theme), args.theme, aligned_now)
        print(f"[lang7] LangExtract prompt aligned to {len(aligned_now.split(',')) if aligned_now else 0} "
              f"known name(s)")
        grounded = extract_boost.run_langextract_union(args, source_text, lang2.pipeline)
        if span_prefix_offset:
            grounded = extract_boost._shift_char_intervals(grounded, -span_prefix_offset)
        pre_context = lang2.pipeline.format_langextract_context(grounded)
        pre_graph = lang2.pipeline.build_pre_graph(grounded)
        inventory_added = entity_first.attach_inventory_nodes(pre_graph, getattr(args, "current_entity_inventory", []))
        if inventory_added:
            print(f"[entity-first] attached {inventory_added} inventory node(s) for document {i}/{len(docs)}", flush=True)
        if pre_graph.number_of_nodes() == 0:
            print('[lang7] WARNING: 0 grounded extractions for this document - '
                  'span_ids will be empty for its llm-built edges; check LangExtract fuzzy alignment.')
        annotated = lang2.attach_relation_meta(pre_graph, grounded)
        meta_edges.extend(collect_meta_edges(pre_graph, grounded))
        print(f"[meta] attached fields to {annotated} pre_graph edge(s)")

        print(f"[D2C11-progress] stage=extract_agent phase=start doc={i}/{len(docs)}", flush=True)
        llm_graph = run_compact_discovery(args, source_text, pre_graph, pre_context,
                                          world_manager.get_complete_graph(),
                                          embedding_model)
        _normalize_graph_evidence_spans(llm_graph, summary, span_prefix_offset)
        print(f"[D2C11-progress] stage=extract_agent phase=done doc={i}/{len(docs)}", flush=True)
        collect_edge_evidence(evidence, pre_graph, f"doc{i}_pre", order)
        collect_edge_evidence(evidence, llm_graph, f"doc{i}_llm", order)
        world_manager.merge(pre_graph)
        world_manager.merge(llm_graph)
        print(f"[D2C11-progress] stage=extract_document phase=done doc={i}/{len(docs)}", flush=True)
        print(f"[doc {i}] pre_graph={pre_graph.number_of_nodes()}n/"
              f"{pre_graph.number_of_edges()}e  llm_graph="
              f"{llm_graph.number_of_nodes()}n/{llm_graph.number_of_edges()}e")

    print(f"[D2C11-progress] stage=extract_complete phase=done doc={len(docs)}/{len(docs)}", flush=True)

    union_report = {
        "enabled": bool(getattr(args, "evidence_union", True)),
        "requested_passes": max(2 if getattr(args, "evidence_union", True) else 1, int(getattr(args, "extraction_passes", 1))),
        "degraded": bool(getattr(args, "evidence_union_degraded", False)),
        "fallback_used": bool(getattr(args, "evidence_union_fallback_used", False)),
        "documents": getattr(args, "evidence_union_docs", {}),
        "entity_first": {
            "enabled": bool(getattr(args, "entity_first", True)),
            "documents": getattr(args, "entity_first_docs", {}),
        },
        "relation_selection_policy": "evidence_window_compaction_v1",
    }
    write_extraction_reports(args, union_report)
    evidence_log = dump_evidence(
        evidence, args.output_dir / "evidence.json",
        {"vote_mode": args.vote_mode, "weights": list(weights),
         "n_docs": len(docs), "stage": "pre_arbitration_snapshot",
         "span_coordinate_system": "document_summary",
         "evidence_union": union_report})
    print(f"[ledger] {evidence_log}")

    final_graph = world_manager.get_complete_graph().copy()
    n0, e0 = final_graph.number_of_nodes(), final_graph.number_of_edges()
    member_of = {}
    merged_early = False
    if args.vote_mode == "canonical_document":
        print("[D2C11-progress] stage=merge_llm phase=start", flush=True)
        final_graph, merge_stats, member_of = run_merge_stage(args, final_graph)
        print("[D2C11-progress] stage=merge_llm phase=done", flush=True)
        merged_early = True
    final_graph, conflict_records = resolve_conflicts(
        final_graph, evidence, vote_mode=args.vote_mode, member_of=member_of,
        keep_conditional=args.keep_conditional_cycles)
    if conflict_records:
        print(f"[conflict] resolved {len(conflict_records)} conflict(s) "
              f"(vote_mode={args.vote_mode})")
    filled = propagate_meta(final_graph, meta_edges, args.propagate_threshold)
    print(f"[meta] propagated lang meta to {filled} additional edge(s)")

    if not args.no_filter:
        filtered = lang2.filter_action_nodes(final_graph)
        if filtered.number_of_nodes() != final_graph.number_of_nodes():
            print(f"[filter] removed {final_graph.number_of_nodes()-filtered.number_of_nodes()} action node(s)")
        final_graph = filtered
    if not merged_early:
        print("[D2C11-progress] stage=merge_llm phase=start", flush=True)
        final_graph, merge_stats, member_of = run_merge_stage(args, final_graph)
        print("[D2C11-progress] stage=merge_llm phase=done", flush=True)
    else:
        loops2 = cleanup_degenerate_edges(final_graph)
        merge_stats["self_loops_removed"] += loops2

    support_lookup = build_edge_support(final_graph, evidence, member_of)
    for u, v, data in final_graph.edges(data=True):
        docs_s, n_assert = support_lookup.get((u, v), (set(), 0))
        data["support_docs"] = len(docs_s)
        data["support_assertions"] = n_assert

    if not args.no_neighbor_completion and final_graph.number_of_nodes() >= 5:
        digest = ""
        try:
            parts = []
            for docp in sorted(pp for pp in args.input_dir.glob("*.json")
                               if not pp.name.startswith("_")):
                with docp.open("r", encoding="utf-8-sig") as f:
                    dd = json.load(f)
                parts.append(f"{dd.get('title', {}).get('eng', '')}. {dd.get('summary', '')}")
            digest = chr(10).join(parts)[:20000]
        except Exception:
            digest = ""
        final_graph, completion_log = extract_boost.neighbor_completion(
            final_graph, digest, api_key=args.api_key, base_url=args.base_url,
            model=args.model, temperature=args.temperature,
            max_edges=args.completion_max_edges, out_dir=args.output_dir,
            require_span=args.completion_require_span)
        add = completion_log.get("stats", completion_log).get("added", 0)
        print(f"[neighbor-completion] added {add} hypothesis edge(s) "
              f"(require_span={args.completion_require_span})")

    cycle_removed, cycle_records, cond_report = 0, [], {}
    if not args.keep_cycles:
        cycle_removed, cycle_records, cond_report = decycle_graph(
            final_graph, weights=weights, support_lookup=support_lookup,
            keep_conditional_cycles=args.keep_conditional_cycles)
        if cycle_removed:
            print(f"[decycle] removed {cycle_removed} weakest-evidence edge(s) "
                  f"(contradiction cycles removed={cycle_removed})")
            for rec in cycle_records:
                conflict_records.append({
                    "kind": "cycle",
                    "classification": rec.get("classification", "contradiction_removed"),
                    "pair": rec["edge"],
                    "scc_size": rec.get("scc_size"),
                    "quality_score": rec.get("quality_score"),
                    "confidence": rec.get("confidence"),
                    "support_docs": rec.get("support_docs"),
                    "support_assertions": rec.get("support_assertions"),
                    "weights": list(weights),
                    "effect_sign": rec.get("effect_sign"),
                    "decision": "drop_edge",
                    "rule": "dag_constraint_weighted_evidence",
                    "rationale": ("a contradiction cycle among evidence edges is broken "
                                  "by dropping the weakest-evidence edge (q = "
                                  "w_conf*conf + w_support*|A_e| + w_schema*(sign+mech))"),
                })
        if cond_report.get("classifications", {}).get("conditional_preserved"):
            print(f"[decycle] preserved {cond_report['classifications']['conditional_preserved']} "
                  f"regime-dependent pair(s) inside cycles")
    else:
        print("[decycle] skipped (--keep-cycles)")

    n_span, n_edges = attach_span_ids(final_graph, evidence, member_of)
    signs, rungs = lang2.edge_meta_stats(final_graph)
    core = final_graph.edge_subgraph(
        [(u, v) for u, v, d in final_graph.edges(data=True)
         if str(d.get("edge_type")) != "hypothesis" and not d.get("regime_dependent")])
    core_dag = nx.is_directed_acyclic_graph(core)
    print(chr(10) + f'Final graph: {final_graph.number_of_nodes()} nodes, '
          f'{final_graph.number_of_edges()} edges (raw: {n0}n/{e0}e)')
    print(f"[DAG] core evidence graph acyclic={core_dag} "
          f"(hypothesis_edges_excluded={sum(1 for _, _, d in final_graph.edges(data=True) if str(d.get('edge_type')) == 'hypothesis')}, "
          f"regime_dependent_edges={sum(1 for _, _, d in final_graph.edges(data=True) if d.get('regime_dependent'))})")
    print(f"[span] edges with span_ids: {n_span}/{n_edges} ({n_span/max(1,n_edges):.3f})")
    print(f"[meta] final effect_sign={dict(signs)} rung={dict(rungs)}")

    gml_path, png_path = lang2.pipeline.save_outputs(final_graph, args.output_dir)
    print(f"GML -> {gml_path}")
    print(f"PNG -> {png_path}")
    conflicts_path = write_conflicts(
        conflict_records, args.output_dir / "conflicts.json",
        strategy=(f"vote_unit={args.vote_mode}; direction=document-majority_vs_earliest; "
                  f"cycle_score=w_conf*conf+w_support*|A_e|+w_schema*(sign+mech) "
                  f"with weights={list(weights)}; "
                  f"hypothesis_edges_excluded_from_dag_guarantee"))
    print(f"[conflict] log -> {conflicts_path}")
    write_json_sidecar(
        {"strategy": ("dag_constraint_weighted_evidence; "
                      "conditional_regime_cycles_preserved") if args.keep_conditional_cycles
         else "dag_constraint_weighted_evidence",
         "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "total_removed": cycle_removed,
         "weights": list(weights),
         "classifications": cond_report.get("classifications", {}),
         "core_evidence_dag": core_dag,
         "keep_cycles_disabled_by_flag": bool(args.keep_cycles),
         "keep_conditional_cycles": bool(args.keep_conditional_cycles),
         "records": cycle_records,
         "conditional_records": cond_report.get("records", [])},
        args.output_dir / "decycle.json", "decycle")
    write_json_sidecar(
        {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "merge_threshold": args.merge_threshold,
         "vote_mode": args.vote_mode,
         "stats": merge_stats},
        args.output_dir / "merge_stats.json", "merge-stats")
    write_json_sidecar(
        {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "edges_total": n_edges,
         "edges_with_span": n_span,
         "edges_without_span": n_edges - n_span,
         "span_backed_share": round(n_span / max(1, n_edges), 4),
         "note": "hypothesis edges carry A_e='derived' and no span by construction"},
        args.output_dir / "span_report.json", "span-report")

    if args.evaluate:
        import evaluate_graph
        combined = combined_document(args.input_dir, args.output_dir)
        result = evaluate_graph.evaluate(graph_paths=[gml_path], document_path=combined)
        evaluate_graph.print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())