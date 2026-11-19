#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lang6 node normalization stage (LLM-adjudicated variable canonicalization).

  1. BLOCKING (cheap, no LLM): candidate pairs from token overlap, extended
     with pairs that share a neighbourhood (>=1 common predecessor/successor)
     at moderate overlap. Pair count is capped; highest-overlap pairs win.
  2. HARD VETO (the only hard lexical rules left): a pair is never presented
     to the adjudicator if (a) the two names differ ONLY in numeric tokens
     (same sampling series, different index/time), or (b) they carry different
     clock times with >=2 shared content words. Everything else (slot-pattern
     modifiers like dissolved/total, grammatical variants, abbreviations) is
     SOFT: it is shown to the LLM as evidence, not pre-decided.
  3. ADJUDICATION (batched pairwise, LACR-1 style): one LLM call per batch of
     pairs. Each pair is judged from its signature (definition, type, values)
     plus its structural role (neighbourhood, mechanism text). Verdicts:
     same | different. "maybe" is treated as different (conservative).
  4. CLUSTERING + STRUCTURAL VETO: same-verdicts form connected components
     (no transitive chaining beyond explicit votes). A component is refused
     if two members already have a directed edge between them - a direct
     causal edge between two nodes is evidence they are distinct.
  5. AUDIT: merge_proposals.json records every candidate, verdict, veto and
     execution status, scoring merge precision/recall against gold aliases
     via eval_graph_quality.merge_metrics.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import networkx as nx

try:
    from ..alias_guard import semantic_alias_veto
except ImportError:  # pragma: no cover - run_extract imports this as top-level
    from d2c13_2.alias_guard import semantic_alias_veto

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "vendor" / "multi_extract"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_multi_langextract_2 as lang2  # noqa: E402
import api_runtime  # noqa: E402

MAX_CANDIDATES = 600
MAX_NEIGHBOURS = 6


def _clock_tokens(name) -> frozenset:
    return frozenset(re.findall(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", str(name)))


def _slot_words(name):
    stop = {"the", "a", "an", "of", "at", "in", "on", "for", "to", "with", "and"}
    return [w for w in re.findall(r"[a-z]+", str(name).lower())
            if w not in stop and len(w) > 1]


def _non_numeric_tokens(name: str) -> str:
    toks = re.findall(r"[A-Za-z0-9]+", str(name).lower())
    core = [ "".join(ch for ch in t if not ch.isdigit()) for t in toks]
    core = [t for t in core if len(t) > 1]
    return " ".join(sorted(set(core)))


def _tokset(x):
    return set(re.findall(r"[a-z0-9]+", str(x).lower()))


def _char_ngrams(x, n=3):
    t = re.sub(r"[^a-z0-9]", "", str(x).lower())
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def _dice_sets(a, b):
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _desc_text(graph, node):
    d = dict(graph.nodes[node])
    return " ".join(str(x) for x in (d.get("description"),
                                     d.get("contextual_information")) if x)


def hard_veto(a, b):
    """Hard lexical veto (return reason) or None."""
    if str(a).lower() != str(b).lower() and _non_numeric_tokens(a) == _non_numeric_tokens(b):
        return "numeric"
    ta, tb = _clock_tokens(a), _clock_tokens(b)
    if ta and tb and ta != tb:
        wa, wb = set(_slot_words(a)), set(_slot_words(b))
        if len(wa & wb) >= 2:
            return "time"
    return None


def block_pairs(graph, threshold=0.7, neighbour_floor=0.35, dump_path=None):
    """Candidate channels (any one may admit a pair):
      jac        name Jaccard >= threshold (lang6 channel)
      neigh1     moderate Jaccard + >=1 common neighbour (lang6 channel)
      contain    one name's token set is a subset of the other's
      ngram      character 3-gram Dice >= 0.5 (morphological near-matches)
      desc       description/context token Dice >= 0.5
      neigh2     >=2 common neighbours regardless of name overlap
    Hard veto (numeric-only difference / different clock times) is applied
    AFTER scoring; vetoed pairs are logged but never adjudicated."""
    nodes = sorted(graph.nodes(), key=lambda n: (-len(str(n)), str(n)))
    scored = []
    vetoed = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            ta, tb = _tokset(a), _tokset(b)
            jac = lang2.name_overlap(str(a), str(b))
            ng = _dice_sets(_char_ngrams(a), _char_ngrams(b))
            desc_dice = _dice_sets(_tokset(_desc_text(graph, a)),
                                   _tokset(_desc_text(graph, b)))
            neigh = (set(graph.predecessors(a)) | set(graph.successors(a))) & \
                    (set(graph.predecessors(b)) | set(graph.successors(b)))
            channels = []
            if jac >= threshold:
                channels.append(("jac", round(jac, 3)))
            elif jac >= neighbour_floor and len(neigh) >= 1:
                channels.append(("neigh1", round(jac, 3)))
            if ta and tb and (ta <= tb or tb <= ta) and jac < 1.0:
                channels.append(("contain", round(jac, 3)))
            if ng >= 0.5:
                channels.append(("ngram", round(ng, 3)))
            if desc_dice >= 0.5:
                channels.append(("desc", round(desc_dice, 3)))
            if len(neigh) >= 2:
                channels.append(("neigh2", len(neigh)))
            if not channels:
                continue
            semantic_veto = semantic_alias_veto(
                a, b,
                graph.nodes[a].get("type"),
                graph.nodes[b].get("type"),
                channels,
            )
            if semantic_veto:
                vetoed.append({
                    "u": str(a), "v": str(b),
                    "veto": semantic_veto,
                    "channels": [c[0] for c in channels],
                })
                continue
            if any(c[0] in ("jac", "neigh1") for c in channels):
                prio = jac
            elif any(c[0] in ("contain", "neigh2") for c in channels):
                prio = 0.8
            else:
                prio = max(ng, desc_dice)
            scored.append((prio, len(neigh), a, b, channels))
    scored.sort(key=lambda x: (-x[0], -x[1], str(x[2]).lower()))
    pairs = []
    for _prio, _n, a, b, channels in scored[:MAX_CANDIDATES]:
        veto = hard_veto(a, b)
        if veto:
            vetoed.append({"u": str(a), "v": str(b), "veto": veto,
                           "channels": [c[0] for c in channels]})
        else:
            pairs.append((a, b, channels))
    if dump_path is not None:
        # P2-5: full blocker-candidate ledger (ER-style pair completeness /
        # reduction-ratio bookkeeping). All scored pairs are dumped, with the
        # hard-veto flag and whether the pair entered LLM adjudication.
        n_nodes = graph.number_of_nodes()
        possible = n_nodes * (n_nodes - 1) // 2
        adjudicated = {(str(p[0]), str(p[1])) for p in pairs}
        dumped = []
        for _prio, _n, a, b, channels in scored:
            vet = hard_veto(a, b)
            entry = {"u": str(a), "v": str(b),
                     "priority": round(float(_prio), 3),
                     "common_neighbours": int(_n),
                     "channels": [c[0] for c in channels],
                     "hard_veto": vet or "",
                     "adjudicated": (str(a), str(b)) in adjudicated}
            dumped.append(entry)
        payload = {"generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "scored_total": len(scored),
                   "possible_pairs": possible,
                   "reduction_ratio": round(len(scored) / possible, 6) if possible else 0.0,
                   "candidates": dumped}
        from pathlib import Path as _P
        _P(dump_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return pairs, vetoed, len(scored)


def _cut(text, n):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:n] + ("..." if len(text) > n else "")


def node_signature(graph, node):
    d = dict(graph.nodes[node])
    lines = [
        f"name: {node}",
        f"definition: {_cut(d.get('description') or d.get('contextual_information'), 140)}",
        f"type: {d.get('type', '?')}",
    ]
    vals = d.get("values")
    if isinstance(vals, list) and vals:
        lines.append(f"values: {', '.join(str(v) for v in vals[:3])}")
    ins = [f"{_cut(p, 60)} --[{_cut(e.get('mechanism') or e.get('description'), 50)}]-->"
           for p, _, e in list(graph.in_edges(node, data=True))[:MAX_NEIGHBOURS]]
    outs = [f"--[{_cut(e.get('mechanism') or e.get('description'), 50)}]--> {_cut(q, 60)}"
            for _, q, e in list(graph.out_edges(node, data=True))[:MAX_NEIGHBOURS]]
    if ins:
        lines.append("causes (in-edges): " + " ; ".join(ins))
    if outs:
        lines.append("effects (out-edges): " + " ; ".join(outs))
    return "\n".join(lines)


def build_prompt(pairs, graph, offset=0):
    lines = []
    for i, (a, b, channels) in enumerate(pairs, offset + 1):
        wa, wb = set(_slot_words(a)), set(_slot_words(b))
        slot = ""
        if len(wa) == len(wb) and len(wa ^ wb) == 2 and wa & wb:
            slot = ("\n  SLOT NOTE: same word pattern differing only in one content word "
                    f"({sorted(wa ^ wb)}). 'different' unless the neighbourhood shows they "
                    "name the same quantity in the same role.")
        ch = ",".join(c[0] for c in channels)
        lines.append(f"--- pair {i} channels={ch} ---\n"
                     f"VARIABLE A:\n{node_signature(graph, a)}\n"
                     f"VARIABLE B:\n{node_signature(graph, b)}{slot}\n")
    return f"""You canonicalize variables in a causal graph built from technical documents.
For each candidate pair decide whether A and B are the SAME causal variable
(same quantity/state, same scope and role - paraphrases, abbreviations,
pluralization and light reorderings are the SAME variable).

GUIDELINES:
- different sampling times, batches, instances, tank numbers or locations that the
  text distinguishes => different;
- one term is a class and the other a specific instance/symptom of it => different;
- a modifier that changes the measured quantity (dissolved vs total, inlet vs
  outlet, before vs after) => different, unless the neighbourhood shows they
  really name the same quantity;
- overlap of causes/effects is strong but not decisive evidence.

Return ONLY JSON (no prose):
{{"pairs": [{{"i": <pair index>, "verdict": "same" | "different" | "maybe",
 "canonical": "best single name for the merged variable, or empty string",
 "reason": "<3-8 words>"}}]}}

CANDIDATES:
{chr(10).join(lines)}"""


def extract_json(raw):
    raw = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if m:
        raw = m.group(1)
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return json.loads(m.group(0))
        raise


def chat_json(api_key, base_url, model, temperature, prompt, retries=3):
    last_err = None
    for attempt in range(1, retries + 1):
        payload = {"model": model, "temperature": temperature, "max_tokens": 120000,
                   "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=1800) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            content = (data["choices"][0]["message"] or {}).get("content")
            if not content:
                raise ValueError("empty content")
            return extract_json(content)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            print(f"[merge-llm] attempt {attempt}/{retries} failed: {last_err}")
            time.sleep(5)
    raise RuntimeError(f"merge adjudication failed after {retries} attempts: {last_err}")


def apply_clusters(graph, same_pairs, members):
    """Merge same-pairs by connected components with a structural veto.
    Returns (merged_graph, rep_members: {rep: [all members]}, stats)."""
    parent = {n: n for n in members}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in same_pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    comps = {}
    for n in members:
        comps.setdefault(find(n), []).append(n)

    rep_members = {}
    veto_pairs = []
    for comp in comps.values():
        comp = sorted(set(comp), key=lambda n: (-len(str(n)), str(n)))
        rep = comp[0]
        accepted = [rep]
        for m in comp[1:]:
            bad = any(graph.has_edge(x, m) or graph.has_edge(m, x) for x in accepted)
            if bad:
                veto_pairs.append([str(rep), str(m)])
                continue
            accepted.append(m)
        if len(accepted) > 1:
            rep_members[rep] = accepted

    def conf_level(d):
        return {"high": 3, "medium": 2, "low": 1}.get(
            str(d.get("confidence") or "medium").lower(), 2)

    merged = nx.DiGraph()
    mapping = {}
    for rep, mems in rep_members.items():
        for m in mems:
            mapping[m] = rep
    for node, data in graph.nodes(data=True):
        target = mapping.get(node, node)
        if target in merged:
            continue
        attrs = dict(graph.nodes[target])
        aliases = [m for m, r in mapping.items() if r == target]
        if len(aliases) > 1:
            attrs["merged_aliases"] = [str(a) for a in aliases]
        merged.add_node(target, **attrs)
    self_loops = 0
    for u, v, data in graph.edges(data=True):
        a, b = mapping.get(u, u), mapping.get(v, v)
        if a == b:
            self_loops += 1
            continue
        if merged.has_edge(a, b):
            old = merged[a][b]
            if conf_level(data) > conf_level(old):
                merged[a][b].update(data)
        else:
            merged.add_edge(a, b, **data)
    stats = {"merged": sum(len(m) - 1 for m in rep_members.values()),
             "self_loops_removed": self_loops,
             "veto_edge_pairs": veto_pairs}
    return merged, rep_members, stats


def build_reconfirm_prompt(pairs, graph, offset=0):
    """Stricter second-pass prompt for pairs that the first pass left
    uncertain (maybe / corroboration-gated)."""
    lines = []
    for i, (a, b, channels) in enumerate(pairs, offset + 1):
        ch = ",".join(c[0] for c in channels)
        lines.append(f"--- pair {i} channels={ch} ---\n"
                     f"VARIABLE A:\n{node_signature(graph, a)}\n"
                     f"VARIABLE B:\n{node_signature(graph, b)}\n")
    return f"""STRICT RECONSIDERATION. You canonicalize variables in a causal graph.
These pairs were left UNCERTAIN in a first pass. Decide whether A and B are the SAME
causal variable. You may answer same if the definitions / neighbourhood / evidence
support it; otherwise answer different. MANDATORY: give a one-clause reason that
names the shared quantity or the distinguishing difference.
Same rules apply as before: paraphrase/abbreviation/light reorder = same; different
sampling times, class-vs-instance, modifiers that change the measured quantity =
different.

Return ONLY JSON (no prose):
{{"pairs": [{{"i": <pair index>, "verdict": "same" | "different",
 "canonical": "best single name for the merged variable, or empty string",
 "reason": "<3-8 words>"}}]}}

CANDIDATES:
{chr(10).join(lines)}"""


def _execute_verdict_queue(pairs, graph, chat, api_key, base_url, model,
                            temperature, batch, offset0, strict, verbose):
    """Run one batched adjudication round over (a,b,channels) pairs.
    Returns {global_index: item}. chat() is chat_json."""
    n = len(pairs)
    n_batches = (n + batch - 1) // batch
    out = {}
    for ci in range(n_batches):
        chunk = pairs[ci * batch:(ci + 1) * batch]
        if strict:
            prompt = build_reconfirm_prompt(chunk, graph, offset=offset0 + ci * batch)
        else:
            prompt = build_prompt(chunk, graph, offset=offset0 + ci * batch)
        if verbose:
            kind = "reconfirm" if strict else "adjudicating"
            print(f"[merge-llm] {kind} chunk {ci + 1}/{n_batches} "
                  f"(global ids {offset0 + ci * batch + 1}-{offset0 + ci * batch + len(chunk)} of regular run) "
                  f"n={len(chunk)} prompt_chars={len(prompt)} model={model_safe!r}" if False else
                  f"[merge-llm] {kind} chunk {ci + 1}/{n_batches} (n={len(chunk)}) "
                  f"prompt_chars={len(prompt)}")
        with api_runtime.api_slot():
            obj = chat(api_key, base_url, model, temperature, prompt)
        for item in obj.get("pairs") or []:
            try:
                idx = int(item.get("i"))
            except (TypeError, ValueError):
                continue
            out[idx] = item
    return out


def normalize_nodes(graph, api_key, base_url, model, temperature,
                    threshold=0.7, batch=20, out_dir=None, verbose=True,
                    corroborate=True, reconfirm=True, desc_strong=0.95):
    log = {
        "strategy": "llm_adjudicated_canonicalization; hard_veto=numeric,time; "
                    "structural_veto=direct_edge_between_members; maybe=conservative_different; "
                    "desc-only pairs require a corroborating channel; maybe/uncertain pairs "
                    "get a strict second pass (lang6_evo2)",
        "pairs": [], "merged_clusters": [],
        "stats": {"candidates_total": 0, "pairs_adjudicated": 0, "llm_same": 0,
                  "llm_different": 0, "llm_maybe": 0, "veto_numeric": 0,
                  "veto_time": 0, "veto_edge": 0, "merged": 0,
                  "self_loops_removed": 0, "corroboration_gated": 0,
                  "reconfirmed_pairs": 0, "r2_same": 0, "r2_different": 0,
                  "r2_maybe": 0},
    }
    if graph.number_of_nodes() < 2:
        return graph, log
    dump_path = out_dir / "blocker_candidates.json" if out_dir else None
    pairs, vetoed, n_total = block_pairs(graph, threshold=threshold, dump_path=dump_path)
    log["stats"]["candidates_total"] = len(pairs) + len(vetoed)
    log["stats"]["candidates_scored_total"] = n_total
    if graph.number_of_nodes() >= 2:
        poss = graph.number_of_nodes() * (graph.number_of_nodes() - 1) // 2
        log["stats"]["reduction_ratio"] = round(n_total / poss, 6)
    for v in vetoed:
        log["pairs"].append({"u": v["u"], "v": v["v"], "verdict": "veto",
                             "veto": v["veto"], "executed_merge": False,
                             "channels": v.get("channels") or [], "reason": ""})
    log["stats"]["veto_numeric"] = sum(1 for v in vetoed if v["veto"] == "numeric")
    log["stats"]["veto_time"] = sum(1 for v in vetoed if v["veto"] == "time")
    log["stats"]["veto_semantic"] = sum(
        1 for v in vetoed if v["veto"] in {"semantic_alias_guard", "type_conflict_guard"}
    )
    if verbose:
        print(f"[merge-llm] blocking: {n_total} scored pairs, {len(pairs)} to adjudicate, "
              f"{len(vetoed)} hard-vetoed (numeric {log['stats']['veto_numeric']}, "
              f"time {log['stats']['veto_time']}, semantic {log['stats']['veto_semantic']})")
    if not pairs:
        return graph, log

    # ---------------- round 1 ----------------
    verdicts1 = _execute_verdict_queue(pairs, graph,
                                       chat=chat_json, api_key=api_key,
                                       base_url=base_url, model=model,
                                       temperature=temperature,
                                       batch=batch, offset0=0,
                                       strict=False, verbose=verbose)
    entries = []
    for i, (a, b, channels) in enumerate(pairs, 1):
        item = verdicts1.get(i) or {}
        v1 = str(item.get("verdict") or "maybe").lower()
        if v1 not in ("same", "different"):
            v1 = "maybe"
        entries.append({
            "i": i, "u": str(a), "v": str(b),
            "channels": [[c[0], c[1]] for c in channels],
            "r1_verdict": v1,
            "r1_reason": _cut(item.get("reason"), 80),
            "r1_canonical": _cut(item.get("canonical"), 100),
            "corroboration_gated": False,
        })
    log["stats"]["pairs_adjudicated"] = len(entries)

    def _names(e):
        return [c[0] for c in e["channels"]]

    def _desc_val(e):
        vals = [c[1] for c in e["channels"] if c[0] == "desc"]
        return max(vals) if vals else 0.0

    def _should_gate(e):
        names = _names(e)
        return (names and "desc" in names and not any(n != "desc" for n in names)
                and _desc_val(e) < desc_strong)

    # corroboration gate (soft): desc-only pairs whose overlap is merely
    # moderate may not be merged on 'same'; strong desc overlap passes.
    if corroborate:
        for e in entries:
            if e["r1_verdict"] == "same" and _should_gate(e):
                e["r1_verdict"] = "maybe"
                e["corroboration_gated"] = True
                log["stats"]["corroboration_gated"] += 1

    # ---------------- round 2: reconfirm uncertain pairs ----------------
    uncertain = [e for e in entries if e["r1_verdict"] == "maybe"]
    if reconfirm and uncertain:
        sub_pairs = []
        for e in uncertain:
            a, b = e["u"], e["v"]
            chans = tuple((c[0], float(c[1]) if len(c) > 1 and isinstance(c[1], (int, float)) else 0.0)
                          for c in e["channels"])
            sub_pairs.append((a, b, chans))
        r2 = _execute_verdict_queue(sub_pairs, graph,
                                    chat=chat_json, api_key=api_key,
                                    base_url=base_url, model=model,
                                    temperature=temperature,
                                    batch=batch, offset0=0,
                                    strict=True, verbose=verbose)
        for k, e in enumerate(uncertain, 1):
            item = r2.get(k) or {}
            v2 = str(item.get("verdict") or "different").lower()
            if v2 not in ("same", "different"):
                v2 = "different"
            e["r2_verdict"] = v2
            e["r2_reason"] = _cut(item.get("reason"), 80)
            e["r2_canonical"] = _cut(item.get("canonical"), 100)
            log["stats"]["reconfirmed_pairs"] += 1
            if v2 == "same":
                log["stats"]["r2_same"] += 1
            elif v2 == "different":
                log["stats"]["r2_different"] += 1
            else:
                log["stats"]["r2_maybe"] += 1

    # ---------------- finalize ----------------
    for e in entries:
        if e["r1_verdict"] == "veto":
            continue
        if "r2_verdict" in e:
            e["verdict"] = e["r2_verdict"]
            e["reason"] = e.get("r2_reason", "")
            e["canonical_choice"] = e.get("r2_canonical", "")
        else:
            e["verdict"] = e["r1_verdict"]
            e["reason"] = e.get("r1_reason", "")
            e["canonical_choice"] = e.get("r1_canonical", "")
        e["executed_merge"] = (e["verdict"] == "same")
        log["pairs"].append(e)
        if e["verdict"] == "same":
            log["stats"]["llm_same"] += 1
        elif e["verdict"] == "different":
            log["stats"]["llm_different"] += 1
        else:
            log["stats"]["llm_maybe"] += 1

    same_pairs = [(e["u"], e["v"]) for e in entries if e.get("verdict") == "same"]
    if not same_pairs:
        if verbose:
            print("[merge-llm] no same-variable pairs after adjudication; graph unchanged")
        return graph, log

    members = set()
    for a, b in same_pairs:
        members.update([a, b])
    merged, rep_members, mstats = apply_clusters(graph, same_pairs, members)
    clusters = [[rep] + [m for m in mems if m != rep]
                for rep, mems in rep_members.items()]
    log["merged_clusters"] = clusters
    log["stats"]["merged"] = mstats["merged"]
    log["stats"]["self_loops_removed"] = mstats["self_loops_removed"]
    log["stats"]["veto_edge"] = len(mstats["veto_edge_pairs"])
    member_of = {}
    for rep, mems in rep_members.items():
        for m in mems:
            member_of[m] = rep
    for e in log["pairs"]:
        if e.get("verdict") == "same":
            ra, rb = member_of.get(e["u"]), member_of.get(e["v"])
            e["executed_merge"] = (ra is not None and ra == rb)
    if verbose:
        print(f"[merge-llm] merged {mstats['merged']} node(s) into "
              f"{len(clusters)} cluster(s); edge-veto {log['stats']['veto_edge']} pair(s); "
              f"gated {log['stats']['corroboration_gated']} desc-only pair(s); "
              f"reconfirmed {log['stats']['reconfirmed_pairs']} pair(s); "
              f"self-loops removed {mstats['self_loops_removed']}")
    return merged, log
