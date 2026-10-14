#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate multi-document corpora from gold DAGs via LLM (NO fixed templates).

Pipeline per gold DAG:
  1. decompose the DAG into topology-preserving chains (every edge covered);
  2. pick a THEME for the DAG and auto-select a mix of document styles for it
     (a theme may span several styles, one style per document);
  3. build a CONTROLLED VOCABULARY (alias dictionary) for every variable via
     one LLM call -- documents may only use these pre-registered surface phrases,
     so every mention in the text maps back to a canonical variable:
     no unaligned "invisible" surface forms at evaluation time;
  4. deterministically pick SPURIOUS bait pairs (with guardrails: never the
     reverse of a gold edge, never a pair connected by a causal path), then ask
     the LLM to weave exactly those false claims into the document unmarked;
  5. write each document in its assigned style (<=400 words enforced, one
     automatic shortening pass if the draft is too long);
  6. save lang4-format documents plus a manifest recording planted gold edges,
     spurious edges (canonical), aliases, per-document style, and the THEME.

gen2 changes vs the first generation:
  * deterministic, guard-railed spurious pairs (fixes the "reverse-of-a-true-edge
    bait" trap that punished extraction precision);
  * alias dictionary = closed vocabulary (fixes alias-coverage gaps that made
    many extracted edges invisible to gold-aware evaluation);
  * hard 400-word cap with one auto-shorten retry;
  * `context_window` sent to the API (default 1048576);
  * optional separate input parent / metadata root so a second-generation corpus
    can live next to the first one (e.g. under an input/goldgen_gen2 folder).

API key comes from GLM_API_KEY env var or --api-key (a separate provider key).

Usage (first-generation layout, unchanged):
    python gold_gen_text/generate_documents.py --dag water --n-docs 4 --seed 1

Usage (second-generation corpus):
    python gold_gen_text/generate_documents.py --dag water --n-docs 5 --seed 1 ^
        --input-parent data/Test-text/input/goldgen_gen2 --subdir "{dag}" ^
        --out-dir gold_gen_text\output_gen2 --max-words 400 --context-window 1048576
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import networkx as nx

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
GOLD_DAG_DIR = PROJECT_ROOT / "gold_DAG"
OUT_DIR = HERE / "output"
DESC_DIR = HERE / "descriptions"
INPUT_ROOT = PROJECT_ROOT / "data" / "Test-text" / "input"

sys.path.insert(0, str(HERE))
import chain_utils  # noqa: E402

BUILTIN_DESCRIPTIONS = {
    "asia": {
        "asia": "recent visit to Asia", "tub": "tuberculosis",
        "smoke": "smoking habit", "lung": "lung cancer",
        "bronc": "bronchitis", "either": "tuberculosis or lung cancer",
        "xray": "positive chest X-ray result", "dysp": "dyspnea (shortness of breath)",
    },
    "sachs": {
        "Raf": "Raf kinase", "Mek": "Mek kinase",
        "Plcg": "phospholipase C gamma",
        "PIP2": "phosphatidylinositol 4,5-bisphosphate",
        "PIP3": "phosphatidylinositol 3,4,5-trisphosphate",
        "Erk": "Erk kinase", "Akt": "Akt / protein kinase B",
        "PKA": "protein kinase A", "PKC": "protein kinase C",
        "P38": "p38 MAP kinase", "Jnk": "c-Jun N-terminal kinase",
    },
}

STYLES = {
    "manual": {
        "label": "industrial equipment usage manual",
        "desc": (
            "Structure like a section of an industrial equipment usage manual: "
            "system composition, operating principle, key parameters, interlocks "
            "and alarms, and operating precautions. Objective, declarative, "
            "technical tone. Present how variables relate through the equipment's "
            "physics and operation."
        ),
    },
    "test_report": {
        "label": "equipment test / operation report",
        "desc": (
            "Structure like an equipment test or operation report: records "
            "observations, parameter readings, test procedures and findings in a "
            "chronological, factual tone. Present causal links as observed "
            "behaviour or recorded results rather than as definitions."
        ),
    },
    "fault_report": {
        "label": "equipment fault / troubleshooting report",
        "desc": (
            "Structure like an equipment fault or troubleshooting report: symptom, "
            "diagnosis of the root cause, and the causal chain from fault to "
            "observable effect. Factual, diagnostic tone."
        ),
    },
    "maintenance_log": {
        "label": "equipment maintenance log",
        "desc": (
            "Structure like a maintenance log entry: condition observed, actions "
            "taken, parts/parameters involved, and the rationale linking the "
            "action to the expected effect. Concise, chronological, technical."
        ),
    },
    "csb": {
        "label": "US Chemical Safety Board investigation report",
        "desc": (
            "Structure like a US Chemical Safety Board (CSB) incident investigation "
            "report: background, incident description, analysis of the causal "
            "chain, and key findings. Factual, formal, explicit cause-and-effect."
        ),
    },
    "asrs": {
        "label": "ASRS aviation safety report",
        "desc": (
            "Structure like an ASRS (Aviation Safety Reporting System) voluntary "
            "safety report: a narrative of an event or observation describing the "
            "sequence of events, contributing factors, and human/system factors in "
            "a confidential safety-reporting tone (first or third person)."
        ),
    },
}

# Theme per gold DAG: the KNOWN common topic of that corpus + the style mix.
# ``extract_theme`` is what the extraction stage receives as --theme.
THEMES = {
    "water": {
        "label": "industrial water treatment plant",
        "extract_theme": "industrial water treatment plant process",
        "styles": ["manual", "test_report", "fault_report", "maintenance_log"],
    },
    "win95pts": {
        "label": "printer hardware/software troubleshooting",
        "extract_theme": "printer hardware and software troubleshooting",
        "styles": ["manual", "fault_report", "maintenance_log", "test_report"],
    },
    "alarm": {
        "label": "ventilator and patient monitoring alarm system",
        "extract_theme": "ventilator machine and patient monitoring alarm system",
        "styles": ["manual", "test_report", "fault_report", "maintenance_log"],
    },
}

DEFAULT_THEME = {
    "label": "industrial equipment system",
    "extract_theme": "industrial equipment system operation and failure",
    "styles": ["manual", "test_report", "fault_report", "maintenance_log"],
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="LLM document generator from gold DAGs.")
    p.add_argument("--dag", default="water",
                   help="DAG name in gold_DAG/, or comma-separated list e.g. 'water,win95pts,alarm', or 'all'.")
    p.add_argument("--n-docs", type=int, default=4)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--max-tokens", type=int, default=100000,
                   help="Max completion tokens per attempt (auto-doubles on length-truncation up to "
                        "the context window).")
    p.add_argument("--context-window", type=int, default=131072,
                   help="Requested model context window (passed to the API as context_window).")
    p.add_argument("--retries", type=int, default=3,
                   help="Retry a failed/empty LLM response this many times.")
    p.add_argument("--timeout", type=int, default=600,
                   help="Seconds to wait for an API response before treating it as a "
                        "retryable timeout error.")
    p.add_argument("--model", default="deepseek-v4-pro")
    p.add_argument("--base-url", default="https://api.deepseek.com")
    p.add_argument("--api-key", default=os.environ.get("DEEPSEEK_API_KEY"))
    p.add_argument("--fake-per-doc", type=int, default=1)
    p.add_argument("--bait-mode", default="contradiction", choices=("contradiction", "novel"),
                   help="contradiction = plant REVERSED-direction claims of true gold edges in a "
                        "later document (detectable by cross-document conflict arbitration); "
                        "novel = plant relations between unrelated variables (legacy).")
    p.add_argument("--irrelevant-ratio", type=float, default=0.25)
    p.add_argument("--max-words", type=int, default=400,
                   help="Hard cap on document body length in words (one auto-shorten retry).")
    p.add_argument("--cross-doc-ratio", type=float, default=0.2)
    p.add_argument("--out-dir", type=Path, default=OUT_DIR,
                   help="Metadata root (manifest/prompts/theme/logs) per corpus.")
    p.add_argument("--input-parent", type=Path, default=INPUT_ROOT,
                   help="Parent directory that receives the generated documents.")
    p.add_argument("--subdir", default="goldgen_{dag}_seed{seed}",
                   help="Sub-folder name under --input-parent; {dag} and {seed} are substituted.")
    p.add_argument("--alias-dict", action=argparse.BooleanOptionalAction, default=True,
                   help="Build a closed alias vocabulary; documents may only use it (gen2).")
    p.add_argument("--variant-level", type=int, default=0, choices=(0, 1, 2),
                   help="0 = gen2 closed vocabulary (exact phrases only); "
                        "1 = gen3 moderate synonymy (light grammatical variants/anaphora); "
                        "2 = gen4 richer synonymy (paraphrase classes, nominal/anaphoric "
                        "chains, abbreviation-expansion mix). Every written surface form "
                        "is reported in the aliases output either way.")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def log_line(log_path, text):
    if log_path is None:
        return
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")
    except OSError:
        pass


def chat_text(args, messages, temperature=None, log_path=None):
    last_err = None
    max_tokens = args.max_tokens
    for attempt in range(1, args.retries + 1):
        payload = {"model": args.model, "messages": messages,
                   "temperature": args.temperature if temperature is None else temperature,
                   "max_tokens": max_tokens,
                   "context_window": args.context_window}
        log_line(log_path, f"attempt {attempt}/{args.retries} POST "
                           f"{args.base_url.rstrip('/')}/chat/completions "
                           f"model={args.model} temp={payload['temperature']} "
                           f"max_tokens={payload['max_tokens']} "
                           f"context_window={payload['context_window']}")
        log_line(log_path, "request: " + json.dumps(messages, ensure_ascii=False)[:2000])
        req = urllib.request.Request(
            args.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {args.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            log_line(log_path, f"HTTP {e.code}: {body[:2000]}")
            last_err = f"API HTTP {e.code}: {body[:300]!r}"
            time.sleep(2 * attempt)
            continue
        except (TimeoutError, urllib.error.URLError) as e:
            last_err = f"API timeout/connection error: {type(e).__name__}: {e}"
            log_line(log_path, f"attempt {attempt} timed out after {args.timeout}s; retrying")
            time.sleep(2 * attempt)
            continue
        log_line(log_path, "response: " + raw[:4000])
        try:
            data = json.loads(raw)
        except Exception as e:
            last_err = f"API returned non-JSON: {e}. raw[:300]={raw[:300]!r}"
            time.sleep(2 * attempt)
            continue
        choices = data.get("choices")
        if not choices:
            last_err = f"API returned no choices: {raw[:300]!r}"
            time.sleep(2 * attempt)
            continue
        msg = choices[0].get("message", {})
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(p["text"] for p in content if isinstance(p, dict) and p.get("text"))
        finish = choices[0].get("finish_reason")
        if finish == "length":
            if content:
                last_err = f"finish_reason=length: output truncated after {len(content)} chars"
            else:
                last_err = ("empty content with finish_reason=length (reasoning consumed "
                            "the whole completion budget)")
            log_line(log_path, f"attempt {attempt} truncated: {last_err}")
            if max_tokens < args.context_window:
                max_tokens = min(max_tokens * 2, args.context_window)
                log_line(log_path, f"retry with max_tokens={max_tokens}")
            time.sleep(2 * attempt)
            continue
        if not content:
            if msg.get("reasoning_content"):
                last_err = ("empty content but reasoning_content present "
                            f"(finish_reason={finish!r}) - likely truncated by max_tokens")
            else:
                last_err = f"API returned empty content (finish_reason={finish!r}): {raw[:200]!r}"
            log_line(log_path, f"attempt {attempt} failed: {last_err}")
            time.sleep(2 * attempt)
            continue
        log_line(log_path, f"content chars={len(content)} finish_reason={finish!r}")
        return content
    raise RuntimeError(f"chat failed after {args.retries} attempts: {last_err} "
                       f"(see log; consider a larger --max-tokens)")

def extract_json(text, log_path=None):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        log_line(log_path, f"no JSON object found in content: {text[:1500]!r}")
        raise RuntimeError(f"LLM output has no JSON object: {text[:300]!r} (see log)")
    text = text[s:e + 1]
    try:
        return json.loads(text)
    except Exception as exc:
        log_line(log_path, f"JSON parse failed: {exc}\n{text[:3000]}")
        raise RuntimeError(f"LLM JSON parse failed: {exc} (see log)")


def load_descriptions(args, dag_name, nodes, log_path=None):
    desc_file = DESC_DIR / f"{dag_name}.json"
    if dag_name in BUILTIN_DESCRIPTIONS:
        desc = dict(BUILTIN_DESCRIPTIONS[dag_name])
        desc.update({n: n for n in nodes if n not in desc})
        return desc
    if desc_file.exists():
        return json.loads(desc_file.read_text(encoding="utf-8"))
    if args.dry_run:
        return {n: n for n in nodes}
    sys_msg = ("You are given variable names from a domain causal network. Return a "
               "JSON object mapping each name to a SHORT, NEUTRAL one-line description "
               "of what the variable represents. Do NOT state any causal relation.")
    raw = chat_text(args, [{"role": "system", "content": sys_msg},
                           {"role": "user", "content": f"Variables: {json.dumps(nodes)}\nReturn JSON only."}],
                    temperature=0.3, log_path=log_path)
    desc = extract_json(raw, log_path=log_path)
    DESC_DIR.mkdir(parents=True, exist_ok=True)
    desc_file.write_text(json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8")
    return desc


def build_alias_dict(args, dag_name, nodes, node_desc, log_path=None):
    """One LLM call: canonical variable -> list of 3-4 allowed surface phrases.

    Returns {} on failure so the caller can fall back to free paraphrasing.
    """
    if not args.alias_dict or args.dry_run:
        return {}
    sys_msg = (
        "You build a CONTROLLED VOCABULARY for paraphrasing technical variables in "
        "plain English documents. For every canonical variable name return a JSON "
        "object mapping name -> array of 3 to 4 natural surface phrases that a "
        "technical writer may use in prose. Rules: phrases must be self-contained, "
        "unambiguous and distinct from every other variable's phrases; they may "
        "include brief context words (times, locations, units, subsystems) to stay "
        "distinct; never use the raw canonical code itself as a surface phrase; "
        "never include causal wording in a phrase. Return JSON only."
    )
    user_p = (f"Variables (name: description):\n"
              + "\n".join(f"{n}: {node_desc.get(n, n)}" for n in nodes)
              + "\nReturn the mapping as a single JSON object.")
    try:
        raw = chat_text(args, [{"role": "system", "content": sys_msg},
                               {"role": "user", "content": user_p}],
                        temperature=0.3, log_path=log_path)
        d = extract_json(raw, log_path=log_path)
        cleaned = {}
        for n in nodes:
            surfs = d.get(n) or []
            if isinstance(surfs, str):
                surfs = [surfs]
            cleaned[n] = [str(s) for s in surfs if str(s).strip()][:4]
        if all(not v for v in cleaned.values()):
            log_line(log_path, "[alias-dict] empty mapping returned; falling back to free paraphrasing")
            return {}
        log_line(log_path, f"[alias-dict] built vocabulary for {len(cleaned)} variables")
        return cleaned
    except Exception as exc:
        log_line(log_path, f"[alias-dict] failed ({exc}); falling back to free paraphrasing")
        return {}


def pick_spurious_pairs(g, gold_edges, doc_vars, n, rng, used_pairs):
    """Deterministically choose `n` false causal pairs for one document.

    Guardrails (these were the traps of the previous generation):
      * never the reverse of a gold edge;
      * never a pair whose two variables are connected by a causal path in the
        gold DAG (in either direction) -- the false claim must concern genuinely
        unrelated variables, not a subtly-reversed real relation.
    """
    nodes = list(g.nodes())
    candidates = []
    relaxed = []
    for u in nodes:
        for v in nodes:
            if u == v or (u, v) in used_pairs:
                continue
            if (u, v) in gold_edges or (v, u) in gold_edges:
                continue
            try:
                related = nx.has_path(g, u, v) or nx.has_path(g, v, u)
            except nx.NodeNotFound:
                related = False
            if related:
                relaxed.append((u, v))
            else:
                candidates.append((u, v))
    rng.shuffle(candidates)
    if len(candidates) >= n:
        return list(candidates[:n])
    rng.shuffle(relaxed)
    return list(candidates) + list(relaxed[:max(0, n - len(candidates))])


def pick_contradiction_bait(gold_edges, doc_index, planted_in_doc, true_doc_of,
                           used_baits, rng, n):
    """Pick up to `n` gold edges whose REVERSED direction will be planted as a
    false claim in THIS document. The true direction must have been stated in an
    EARLIER document (so on a 2:2 vote tie the earliest-evidence rule keeps the
    TRUE direction), neither direction may already be asserted in this document,
    and each gold edge is used for at most one contradiction bait."""
    cands = []
    for (u, v) in gold_edges:
        td = true_doc_of.get((u, v))
        if td is None or td >= doc_index:
            continue
        if (u, v) in planted_in_doc or (v, u) in planted_in_doc:
            continue
        if (u, v) in used_baits or (v, u) in used_baits:
            continue
        cands.append((u, v, td))
    rng.shuffle(cands)
    return cands[:n]


def alias_entries_for(varset, alias_dict):
    entries = []
    for v in sorted(varset):
        for surf in alias_dict.get(v) or []:
            entries.append({"canonical": v, "surface": surf})
    return entries


def build_doc_prompt(style_key, chains, extra_edges, node_desc, alias_dict,
                     spurious_pairs, irr_ratio, max_words, used_in_doc,
                     variant_level=0, bait_mode="contradiction"):
    style = STYLES[style_key]
    chain_lines = [f"{i+1}. " + " -> ".join(f"{n} ({node_desc.get(n, n)})" for n in path)
                   for i, path in enumerate(chains)]
    extra_lines = [f"- {u} ({node_desc.get(u, u)}) -> {v} ({node_desc.get(v, v)})"
                   for u, v in extra_edges] or ["- <none>"]
    irr_pct = int(irr_ratio * 100)

    if alias_dict:
        vocab_vars = sorted(set(used_in_doc))
        vocab_lines = []
        for v in vocab_vars:
            surfs = alias_dict.get(v) or []
            if not surfs:
                surfs = [node_desc.get(v, v) + " (natural phrase)"]
            vocab_lines.append(f"- {v} ({node_desc.get(v, v)}): " + " | ".join(surfs))
        vocab_block = ("\n".join(vocab_lines) if vocab_lines else "- (none)")
        if variant_level == 0:
            vocab_rule = (
                "CLOSED VOCABULARY (very important): the table below is the ONLY wording "
                "you are allowed to use for each variable. Every mention of a variable "
                "in the body MUST be one of its listed phrases verbatim. Do NOT invent "
                "any other paraphrase, do NOT shorten or extend a listed phrase, and do "
                "NOT write the raw canonical code anywhere in the document.\n" + vocab_block
            )
        elif variant_level == 1:
            vocab_rule = (
                "BASE VOCABULARY (very important): the table below gives the base wording "
                "for each variable. Most mentions should use a listed phrase VERBATIM. "
                "Moderate NATURAL VARIATION IS ALLOWED, e.g. reordering ('the 12:15 BOD "
                "reading'), a light anaphoric form ('this reading', 'that value'), or a "
                "grammatical variant ('quarter-past sample'). When you vary: keep the "
                "distinctive content words of the listed phrase; never replace the "
                "parameter/chemical term with a DIFFERENT technical term; never drop or "
                "change the sampling time; and for EVERY distinct surface form you "
                "actually write, add one pair to the aliases array of the JSON output. "
                "Never write the raw canonical code anywhere in the document.\n" + vocab_block
            )
        else:
            vocab_rule = (
                "BASE VOCABULARY (very important): the table below gives the base wording "
                "for each variable. RICHER SYNONYMY: do NOT repeat one listed phrase "
                "verbatim throughout; alternate between (a) the listed phrases, (b) "
                "word-level synonyms for the descriptive terms only ('noon' <-> 'midday', "
                "'quarter past noon' <-> '12:15 hours', 'concentration' <-> 'titre' <-> "
                "'reading'), (c) clause reorderings ('the BOD level recorded at 12:15'), "
                "and (d) nominal or anaphoric chains ('the former value', 'that earlier "
                "reading', 'both mid-cycle samples'). Use on average at least two distinct "
                "surface forms per variable per document. Hard limits: keep the sampling "
                "time identifiable; never swap the parameter class for a different "
                "chemical/physical quantity; never weaken or reverse any causal nuance. "
                "For EVERY distinct surface form you actually write, add one pair to the "
                "aliases array of the JSON output. Never write the raw canonical code.\n"
                + vocab_block
            )
    else:
        vocab_rule = (
            "ANTI LEAKAGE: do NOT use the exact variable names above. Refer to each "
            "variable with natural synonyms/phrases (e.g. 'shortness of breath' "
            "instead of 'dysp'). In the JSON output, list the surface phrase you "
            "used for each canonical variable."
        )

    if bait_mode == "contradiction":
        spurious_lines = [
            f"{i+1}. ASSERT THE OPPOSITE of the true link below: write in the text "
            f"that {v} ({node_desc.get(v, v)}) causes {u} ({node_desc.get(u, u)}). "
            f"(The true direction {u}->{v} is stated in another document.)"
            for i, (u, v) in enumerate(spurious_pairs)
        ] or ["- <none>"]
    else:
        spurious_lines = [
            f"{i+1}. {u} ({node_desc.get(u, u)}) --X--> {v} ({node_desc.get(v, v)})"
            for i, (u, v) in enumerate(spurious_pairs)
        ] or ["- <none>"]
    spurious_block = ("\n".join(spurious_lines))

    alias_schema = (', "aliases": [{"canonical": "A", "surface": "phrase written"}]'
                     if variant_level >= 1 else '')
    return f"""You are a technical writer producing ONE document in this style: {style['label']}.

STYLE GUIDANCE:
{style['desc']}

The document must describe a single system whose underlying causal graph is given
to you as directed chains below. Read each chain left-to-right as "X causes Y
causes Z". Variable descriptions are in parentheses.

CHAINS TO COVER IN THIS DOCUMENT:
{chr(10).join(chain_lines)}

ADDITIONAL CAUSAL LINKS TO ALSO MENTION (restate naturally, may bridge chains):
{chr(10).join(extra_lines)}

{vocab_rule}

FALSE CLAIMS TO PLANT (unmarked; for precision testing): weave EXACTLY these
statements into the text as if they were true. Each connects two variables that
are in reality UNRELATED -- never reverse a real link, never connect two
variables that belong to the same given chain:
{spurious_block}

RULES (very important):
1. Write in ENGLISH, in the requested style, coherent and realistic (a full
   document, not a bullet list of the chains).
2. Describe every true causal link as a plausible mechanism; do NOT drop
   intermediate variables (no skipping).
3. REALISM / NOISE: (a) include about {irr_pct}% non-causal background sentences
   (context, measurements, procedures) that carry NO causal claim; (b) plant the
   false statements above in a natural, unmarked way.
4. LENGTH (target): about {max_words} WORDS -- keep the body between {max_words-100} and {max_words+100} words.
   Focused section, not an exhaustive manual. Be brief in reasoning.
5. Output ONLY a JSON object (no prose around it) with this exact schema:
{{"title": "string", "body": "full document text"{alias_schema}}}
"""


WORD_RE = re.compile(r"\S+")


def count_words(text):
    return len(WORD_RE.findall(text or ""))


def main(argv=None):
    args = parse_args(argv)
    if not args.dry_run and not args.api_key:
        raise SystemExit("Set DEEPSEEK_API_KEY or pass --api-key (or use --dry-run).")

    if args.dag == "all":
        dag_names = sorted(p.stem for p in GOLD_DAG_DIR.glob("*.json"))
    elif "," in args.dag:
        dag_names = [x.strip() for x in args.dag.split(",") if x.strip()]
    else:
        dag_names = [args.dag]

    for dag_name in dag_names:
        gold_file = GOLD_DAG_DIR / f"{dag_name}.json"
        if not gold_file.exists():
            print(f"[skip] {dag_name}: gold_DAG/{dag_name}.json not found")
            continue
        gold = json.loads(gold_file.read_text(encoding="utf-8"))
        g = nx.DiGraph()
        g.add_nodes_from(gold["nodes"])
        g.add_edges_from(tuple(e) for e in gold["edges"])
        gold_edges = set(tuple(e) for e in gold["edges"])

        chains = chain_utils.decompose_into_chains(g)
        chain_utils.verify_cover(g, chains)

        meta_out = args.out_dir / dag_name / f"seed{args.seed}"
        meta_out.mkdir(parents=True, exist_ok=True)
        log_path = meta_out / "generate.log"
        log_line(log_path, f"[run] dag={dag_name} seed={args.seed} n_docs={args.n_docs} "
                           f"model={args.model} base_url={args.base_url} "
                           f"temperature={args.temperature} max_words={args.max_words} "
                           f"context_window={args.context_window} alias_dict={args.alias_dict}")
        log_line(log_path, f"[chains] {len(chains)} chains covering {len(gold['edges'])} gold edges")

        node_desc = load_descriptions(args, dag_name, gold["nodes"], log_path)
        if args.dry_run:
            # deterministic preview so prompts still show the closed-vocabulary block
            alias_dict = {n: [node_desc.get(n, n), f"{node_desc.get(n, n)} value"] for n in gold["nodes"]}
            alias_mode = "dict(dry-run preview)"
        else:
            alias_dict = build_alias_dict(args, dag_name, gold["nodes"], node_desc, log_path)
            alias_mode = "dict" if alias_dict else "free"
        if alias_dict:
            (meta_out / "alias_dict.json").write_text(
                json.dumps(alias_dict, ensure_ascii=False, indent=2), encoding="utf-8")

        theme = THEMES.get(dag_name, DEFAULT_THEME)
        styles = theme["styles"]

        rng = random.Random(args.seed)
        assignments = [[] for _ in range(args.n_docs)]
        for cid, path in enumerate(chains):
            assignments[cid % args.n_docs].append(cid)

        extra_by_doc = [[] for _ in range(args.n_docs)]
        for u, v in g.edges():
            if rng.random() < args.cross_doc_ratio:
                primary = None
                for cid, path in enumerate(chains):
                    if [u, v] in chain_utils.chain_edges(path):
                        primary = cid % args.n_docs
                        break
                candidates = [d for d in range(args.n_docs) if d != primary]
                if candidates:
                    extra_by_doc[rng.choice(candidates)].append((u, v))

        input_dir = args.input_parent / args.subdir.format(dag=dag_name, seed=args.seed)
        input_dir.mkdir(parents=True, exist_ok=True)
        (meta_out / "prompts").mkdir(parents=True, exist_ok=True)

        true_doc_of = {}
        for e in g.edges():
            for did in range(args.n_docs):
                plist = []
                for cid in assignments[did]:
                    plist.extend(chain_utils.chain_edges(chains[cid]))
                plist.extend([tuple(x) for x in extra_by_doc[did]])
                if tuple(e) in set(tuple(x) for x in plist):
                    true_doc_of.setdefault(tuple(e), did)
                    break

        used_spurious = set()
        manifest_docs = []
        for d in range(args.n_docs):
            style_key = styles[d % len(styles)]
            doc_cids = assignments[d]
            doc_chains = [(cid, chains[cid]) for cid in doc_cids]
            doc_paths = [chains[cid] for cid in doc_cids]
            doc_extra = extra_by_doc[d]
            planted = []
            for cid, path in doc_chains:
                planted.extend(chain_utils.chain_edges(path))
            planted.extend([list(e) for e in doc_extra])
            planted = [list(x) for x in {tuple(e) for e in planted}]

            doc_vars = set()
            for u, v in planted:
                doc_vars.update([u, v])
            doc_planted = set(tuple(e) for e in planted)
            if args.bait_mode == "contradiction":
                spurious_edges_meta = pick_contradiction_bait(
                    gold_edges, d, doc_planted, true_doc_of, used_spurious,
                    rng, args.fake_per_doc)
                spurious_pairs = [(u, v) for (u, v, _td) in spurious_edges_meta]
                for (u, v, _td) in spurious_edges_meta:
                    used_spurious.add((u, v))
                for u, v in spurious_pairs:
                    doc_vars.update([u, v])
            else:
                spurious_edges_meta = []
                spurious_pairs = pick_spurious_pairs(g, gold_edges, doc_vars,
                                                     args.fake_per_doc, rng, used_spurious)
                for pp in spurious_pairs:
                    used_spurious.add(pp)
                for u, v in spurious_pairs:
                    doc_vars.update([u, v])

            prompt = build_doc_prompt(style_key, doc_paths, doc_extra, node_desc,
                                      alias_dict, spurious_pairs, args.irrelevant_ratio,
                                      args.max_words, doc_vars,
                                      variant_level=args.variant_level,
                                      bait_mode=args.bait_mode)
            (meta_out / "prompts" / f"doc_{d+1:02d}_{style_key}.txt").write_text(
                prompt, encoding="utf-8")

            shorten_tries = 0
            _lo = max(50, args.max_words - 100)
            _hi = args.max_words + 100
            if args.dry_run:
                llm = {"title": f"{dag_name} {style_key} document {d+1}",
                       "body": "(dry-run: LLM not called)"}
            else:
                log_line(log_path, f"[doc {d+1}/{args.n_docs}] style={style_key} "
                                   f"prompt_chars={len(prompt)} spurious={spurious_pairs} alias_mode={alias_mode}")
                raw = chat_text(args, [{"role": "user", "content": prompt}], log_path=log_path)
                llm = extract_json(raw, log_path=log_path)
                body = llm.get("body", "")
                if count_words(body) > _hi:
                    shorten_tries = 1
                    log_line(log_path, f"[doc {d+1}] {count_words(body)} words > {_hi}; "
                                       f"one auto-shorten pass")
                    follow = (f"Your previous draft had {count_words(body)} words. Rewrite the SAME "
                              f"content (title, every chain link, every planted false claim) so the "
                              f"body has between {_lo} and {_hi} words, keeping identical terminology. "
                              f"Return the same JSON schema, JSON only.")
                    raw2 = chat_text(args, [{"role": "user", "content": prompt},
                                            {"role": "assistant", "content": raw},
                                            {"role": "user", "content": follow}],
                                     log_path=log_path)
                    llm2 = extract_json(raw2, log_path=log_path)
                    b2 = llm2.get("body", "")
                    if count_words(b2) <= args.max_words or (llm2 and b2 and count_words(b2) < count_words(body)):
                        llm = llm2
                    else:
                        log_line(log_path, f"[doc {d+1}] shorten pass not better; keeping first draft")
                log_line(log_path, f"[doc {d+1}] parsed OK title={llm.get('title', '')[:80]!r} "
                                   f"words={count_words(llm.get('body', ''))} "
                                   f"(target {_lo}-{_hi}) shorten_tries={shorten_tries}")
                time.sleep(0.5)

            doc_path = input_dir / f"{d+1:02d}_{style_key}.json"
            doc_path.write_text(json.dumps(
                {"title": {"eng": llm["title"]}, "summary": llm["body"]},
                ensure_ascii=False, indent=2), encoding="utf-8")

            alias_entries = alias_entries_for(doc_vars, alias_dict)
            if args.variant_level >= 1:
                seen_surfaces = {(a["canonical"], a["surface"]) for a in alias_entries}
                for a in llm.get("aliases", []) or []:
                    if isinstance(a, dict) and a.get("canonical") in doc_vars and a.get("surface"):
                        key = (a["canonical"], a["surface"])
                        if key not in seen_surfaces:
                            alias_entries.append({"canonical": a["canonical"], "surface": a["surface"]})
                            seen_surfaces.add(key)
            contra_meta = [{"gold_ref": [u, v],
                             "wrong_direction": [v, u],
                             "true_doc": td}
                            for (u, v, td) in spurious_edges_meta]
            manifest_docs.append({
                "file": doc_path.name, "style": style_key,
                "assigned_chain_ids": [cid for cid, _ in doc_chains],
                "extra_edges": [list(e) for e in doc_extra],
                "planted_edges": planted,
                "spurious_edges": [] if args.bait_mode == "contradiction"
                                  else [list(e) for e in spurious_pairs],
                "contradiction_bait_edges": contra_meta,
                "aliases": alias_entries,
                "words": count_words(llm.get("body", "")),
                "title": llm["title"],
            })

        manifest = {
            "dag": dag_name, "seed": args.seed, "n_docs": args.n_docs,
            "temperature": args.temperature, "model": args.model,
            "base_url": args.base_url, "input_dir": str(input_dir),
            "generator_version": "gen2",
            "alias_mode": alias_mode if not args.dry_run else "dry-run",
            "context_window": args.context_window,
            "max_words": args.max_words,
            "variant_level": args.variant_level,
            "spurious_policy": ("contradiction bait: reversed-direction claims of true gold edges, "
                                "planted in a later document so cross-document conflict arbitration "
                                "can resolve them (majority vote, earliest tie-break)") if args.bait_mode == "contradiction"
                                else ("novel bait: deterministic; excludes reverse gold edges and "
                                      "path-related pairs; no wrong-direction bait"),
            "theme": {"id": dag_name, "label": theme["label"],
                      "extract_theme": theme["extract_theme"]},
            "gold_nodes": gold["nodes"], "gold_edges": gold["edges"],
            "chains": [{"id": cid, "path": path, "edges": chain_utils.chain_edges(path)}
                       for cid, path in enumerate(chains)],
            "docs": manifest_docs, "dry_run": args.dry_run,
        }
        (meta_out / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (meta_out / "extract_theme.txt").write_text(
            theme["extract_theme"], encoding="utf-8")
        print(f"[done] {dag_name} theme={theme['label']!r} "
              f"styles={styles} chains={len(chains)} docs={args.n_docs} "
              f"alias_mode={manifest['alias_mode']}")
        print(f"       input -> {input_dir}")
        print(f"       meta  -> {meta_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
