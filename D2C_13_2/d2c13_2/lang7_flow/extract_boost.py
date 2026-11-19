#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lang6_evo3 extraction-side boosts.

  1. Evidence union: run LangExtract twice (independent requests) and UNION the
     grounded extraction lists so single-mention edges get a second chance and
     the pre-graph candidates fed to the compact agent are denser.
  2. Aligned-name injection: the LangExtract first-stage prompt carries the
     current aligned variable-name list so new documents reuse existing
     names instead of minting near-duplicates.
  3. Neighbor completion: one batched, document-grounded pass over the
     post-merge graph proposing MISSING direct causal edges among covered
     variables. Guardrails: both endpoints must exist in the graph, no
     self-loops, no existing/reverse edge; a max edge budget is enforced.
"""
from __future__ import annotations

import ast
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

try:
    from .normalize import chat_json, extract_json, _cut  # type: ignore
    from . import entity_inventory as entity_first  # type: ignore
    from .. import api_runtime  # type: ignore
except ImportError:  # script-mode import from run_extract.py
    from normalize import chat_json, extract_json, _cut  # type: ignore
    import entity_inventory as entity_first  # type: ignore
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import api_runtime  # type: ignore


# ---------------------------------------------------------------- (1) union -
def _checkpoint_path(args, pass_index: int):
    root = getattr(args, "checkpoint_dir", None)
    doc_key = getattr(args, "current_doc_key", None)
    if not root or not doc_key:
        return None
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(doc_key))
    return Path(root) / f"{safe_key}.pass{pass_index + 1}.json"


def _normalize_extraction_dict(row: dict) -> dict:
    interval = row.get("char_interval")
    if hasattr(interval, "start_pos"):
        interval = {"start_pos": interval.start_pos, "end_pos": interval.end_pos}
    status = row.get("alignment_status")
    if hasattr(status, "value"):
        status = status.value
    return {
        "extraction_class": str(row.get("extraction_class") or ""),
        "extraction_text": str(row.get("extraction_text") or ""),
        "char_interval": interval if isinstance(interval, dict) else None,
        "alignment_status": status,
        "extraction_index": row.get("extraction_index"),
        "group_index": row.get("group_index"),
        "description": row.get("description"),
        "attributes": dict(row.get("attributes") or {}),
    }


def _parse_legacy_extraction_string(value: str) -> dict | None:
    text = str(value or "").strip()
    if not text.startswith("Extraction("):
        return None
    text = re.sub(r"<AlignmentStatus\.[A-Z_]+:\s*'([^']+)'>", r"'\1'", text)
    def _constructor(**kwargs):
        return kwargs
    try:
        parsed = eval(text, {"__builtins__": {}}, {"Extraction": _constructor, "CharInterval": _constructor})
    except Exception:  # noqa: BLE001
        return None
    return _normalize_extraction_dict(parsed) if isinstance(parsed, dict) else None


def _serialize_extraction(item) -> dict:
    if isinstance(item, dict):
        return _normalize_extraction_dict(item)
    if isinstance(item, str):
        parsed = _parse_legacy_extraction_string(item)
        if parsed is not None:
            return parsed
    return _normalize_extraction_dict({
        "extraction_class": getattr(item, "extraction_class", ""),
        "extraction_text": getattr(item, "extraction_text", ""),
        "char_interval": getattr(item, "char_interval", None),
        "alignment_status": getattr(item, "alignment_status", None),
        "extraction_index": getattr(item, "extraction_index", None),
        "group_index": getattr(item, "group_index", None),
        "description": getattr(item, "description", None),
        "attributes": getattr(item, "attributes", None) or {},
    })


def _deserialize_extraction(row: dict):
    row = _normalize_extraction_dict(row)
    interval = row.get("char_interval")
    char_interval = SimpleNamespace(**interval) if isinstance(interval, dict) else None
    status = row.get("alignment_status")
    alignment_status = SimpleNamespace(value=status) if status else None
    return SimpleNamespace(
        extraction_class=row.get("extraction_class", ""),
        extraction_text=row.get("extraction_text", ""),
        char_interval=char_interval,
        alignment_status=alignment_status,
        extraction_index=row.get("extraction_index"),
        group_index=row.get("group_index"),
        description=row.get("description"),
        attributes=dict(row.get("attributes") or {}),
    )


def _deserialize_items(items) -> list:
    result = []
    for item in items or []:
        if isinstance(item, str):
            parsed = _parse_legacy_extraction_string(item)
            if parsed is not None:
                result.append(_deserialize_extraction(parsed))
        elif isinstance(item, dict):
            result.append(_deserialize_extraction(item))
        else:
            result.append(item)
    return result


def _read_checkpoint(path):
    if not path or not Path(path).exists():
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        items = payload.get("items") if isinstance(payload, dict) else None
        return _deserialize_items(items)
    except (OSError, json.JSONDecodeError):
        return None


def _write_checkpoint(path, pass_index: int, items):
    if not path:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pass": pass_index + 1,
        "format": "d2c13_2-extractions-v2",
        "items": [_serialize_extraction(item) for item in items],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _emit_progress(args, stage: str, phase: str, **fields) -> None:
    payload = {
        "stage": stage,
        "phase": phase,
        "doc": f"{getattr(args, 'current_doc_index', 0)}/{getattr(args, 'current_doc_total', 0)}",
    }
    payload.update({key: value for key, value in fields.items() if value is not None})
    print("[D2C11-progress] " + " ".join(f"{key}={value}" for key, value in payload.items()), flush=True)

def _shift_char_intervals(items: list, offset: int) -> list:
    """Shift grounded char intervals from a paragraph to document coordinates."""
    if not offset:
        return items
    for item in items:
        if isinstance(item, dict):
            interval = item.get("char_interval")
            if not isinstance(interval, dict):
                continue
            start = interval.get("start_pos")
            end = interval.get("end_pos")
            if start is not None:
                interval["start_pos"] = int(start) + int(offset)
            if end is not None:
                interval["end_pos"] = int(end) + int(offset)
            continue
        interval = getattr(item, "char_interval", None)
        if interval is None:
            continue
        start = getattr(interval, "start_pos", None)
        end = getattr(interval, "end_pos", None)
        if start is not None:
            interval.start_pos = int(start) + int(offset)
        if end is not None:
            interval.end_pos = int(end) + int(offset)
    return items


def _run_langextract_batch(args, pipeline, batch, pass_index, batch_index, use_prompt_argument):
    """Run one independent batch with retries and return a stable result record."""
    attempts = 0
    started = time.perf_counter()
    prompt = str(batch.get("prompt") or "")
    total_batches = int(batch.get("_total_batches") or 0)
    for attempt in (1, 2):
        attempts += 1
        try:
            kwargs = {
                "source_text": str(batch.get("source_text") or ""),
                "api_key": args.api_key,
                "model_id": args.model,
                "base_url": args.base_url,
                "extraction_passes": 1,
                "max_char_buffer": int(batch.get("max_char_buffer") or 1800),
                "max_output_tokens": getattr(args, "max_output_tokens", None),
            }
            if use_prompt_argument:
                kwargs["prompt_description"] = prompt
            with api_runtime.api_slot():
                lang_document = pipeline.run_langextract(**kwargs)
            grounded = pipeline.grounded_extractions(lang_document)
            grounded = _shift_char_intervals(grounded, int(batch.get("offset") or 0))
            return {
                "batch_index": batch_index,
                "paragraph_id": batch.get("paragraph_id"),
                "pair_count": int(batch.get("pair_count") or 0),
                "window_count": int(batch.get("window_count") or 0),
                "source_chars": len(str(batch.get("source_text") or "")),
                "prompt_chars": len(prompt),
                "item_count": len(grounded),
                "api_calls_est": attempts,
                "max_char_buffer": int(batch.get("max_char_buffer") or 1800),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "items": grounded,
            }
        except Exception as exc:  # noqa: BLE001
            print(
                f"[LangExtract-union] pass {pass_index + 1} batch {batch_index}/{total_batches or '?'} "
                f"attempt {attempt} failed: {type(exc).__name__}: {exc}",
                flush=True,
            )
            if attempt == 1:
                time.sleep(5)
                continue
            raise


def _run_relation_batches(args, pipeline, relation_batches, pass_index):
    workers = max(1, int(getattr(args, "relation_parallel_workers", api_runtime.relation_workers(4))))
    workers = min(workers, max(1, len(relation_batches)))
    if workers == 1:
        return [
            _run_langextract_batch(args, pipeline, batch, pass_index, i, True)
            for i, batch in enumerate(relation_batches, 1)
        ]
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="d2c-relation") as pool:
        futures = [
            pool.submit(_run_langextract_batch, args, pipeline, batch, pass_index, i, True)
            for i, batch in enumerate(relation_batches, 1)
        ]
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
    if errors:
        raise errors[0]
    return sorted(results, key=lambda row: row["batch_index"])


def run_langextract_union(args, source_text, pipeline):
    """Run the baseline pass and an entity-first relation-completion pass."""
    api_runtime.configure()
    grounded_lists = []
    n_passes = max(1, int(getattr(args, "extraction_passes", 1)))
    if getattr(args, "evidence_union", True):
        n_passes = max(n_passes, 2)
    doc_key = getattr(args, "current_doc_key", None)
    if not hasattr(args, "_evidence_union_any_degraded"):
        args._evidence_union_any_degraded = False
    args.evidence_union_degraded = False
    args.evidence_union_fallback_used = False
    args.evidence_union_completed_passes = 0
    args.current_relation_batch_count = 0
    args.current_relation_pairs = []
    if doc_key:
        reports = getattr(args, "evidence_union_docs", None)
        if reports is None:
            reports = {}
            args.evidence_union_docs = reports
        reports[str(doc_key)] = {"requested_passes": n_passes, "completed_passes": 0, "checkpoint_hits": 0, "degraded": False}
    inventory = []
    relation_pairs = []
    relation_batch_stats = []
    pass_timings = []
    inventory_elapsed = 0.0
    entity_enabled = bool(getattr(args, "entity_first", True))
    completed_passes = 0
    for pi in range(n_passes):
        if pi > 0 and entity_enabled and not inventory:
            _emit_progress(args, "extract_inventory", "start", pass_index=f"{pi + 1}/{n_passes}")
            inventory_start = time.perf_counter()
            inventory = entity_first.build_entity_inventory(args, source_text, grounded_lists)
            inventory_elapsed += time.perf_counter() - inventory_start
            args.current_entity_inventory = inventory
            _emit_progress(args, "extract_inventory", "done", pass_index=f"{pi + 1}/{n_passes}", items=len(inventory))
        if pi > 0 and entity_enabled and inventory:
            relation_pairs = entity_first.build_relation_pairs(source_text, inventory)
            args.current_relation_pairs = relation_pairs
        checkpoint = _checkpoint_path(args, pi)
        cached = _read_checkpoint(checkpoint)
        if cached is not None:
            grounded_lists.extend(cached)
            completed_passes = pi + 1
            if doc_key:
                args.evidence_union_docs[str(doc_key)]["checkpoint_hits"] += 1
            _emit_progress(args, "extract_pass", "checkpoint", pass_index=f"{pi + 1}/{n_passes}", items=len(cached))
            print(f"[LangExtract-union] pass {pi + 1} restored from checkpoint")
            continue
        original_prompt = getattr(pipeline, "PROMPT_DESCRIPTION", "")
        if pi > 0 and entity_enabled and inventory:
            relation_batches = entity_first.build_relation_completion_batches(
                original_prompt, inventory, relation_pairs, source_text
            )
            for index, batch in enumerate(relation_batches, 1):
                batch["_total_batches"] = len(relation_batches)
            _emit_progress(
                args, "extract_relation_matrix", "start",
                pass_index=f"{pi + 1}/{n_passes}", batches=len(relation_batches),
            )
        else:
            relation_batches = [{
                "paragraph_id": "document",
                "source_text": source_text,
                "offset": 0,
                "max_char_buffer": 1800,
                "pair_count": 0,
                "window_count": 0,
                "prompt": original_prompt,
                "_total_batches": 1,
            }]
            _emit_progress(args, "extract_pass", "start", pass_index=f"{pi + 1}/{n_passes}")
        args.current_relation_batch_count = len(relation_batches)
        batch_items = []
        relation_batch_stats = []
        failed = False
        pass_start = time.perf_counter()
        try:
            if pi > 0 and entity_enabled and inventory:
                try:
                    results = _run_relation_batches(args, pipeline, relation_batches, pi)
                except Exception:
                    if grounded_lists:
                        args.evidence_union_degraded = True
                        args._evidence_union_any_degraded = True
                        args.evidence_union_fallback_used = True
                        _emit_progress(args, "extract_pass", "degraded", pass_index=f"{pi + 1}/{n_passes}")
                        print(f"[LangExtract-union] pass {pi + 1} unavailable; "
                              f"using {completed_passes} completed pass(es) and continuing")
                        if doc_key:
                            args.evidence_union_docs[str(doc_key)]["degraded"] = True
                        failed = True
                        results = []
                    else:
                        raise
            else:
                pipeline.PROMPT_DESCRIPTION = original_prompt
                try:
                    result = _run_langextract_batch(args, pipeline, relation_batches[0], pi, 1, False)
                    results = [result]
                except Exception:
                    if pi > 0 and grounded_lists:
                        args.evidence_union_degraded = True
                        args._evidence_union_any_degraded = True
                        args.evidence_union_fallback_used = True
                        _emit_progress(args, "extract_pass", "degraded", pass_index=f"{pi + 1}/{n_passes}")
                        print(f"[LangExtract-union] pass {pi + 1} unavailable; "
                              f"using {completed_passes} completed pass(es) and continuing")
                        if doc_key:
                            args.evidence_union_docs[str(doc_key)]["degraded"] = True
                        failed = True
                        results = []
                    else:
                        raise
            if not failed:
                for result in results:
                    batch_items.extend(result["items"])
                    if pi > 0 and entity_enabled and inventory:
                        relation_batch_stats.append({
                            key: result[key] for key in (
                                "paragraph_id", "pair_count", "window_count", "source_chars",
                                "prompt_chars", "item_count", "api_calls_est",
                                "max_char_buffer", "elapsed_seconds",
                            )
                        })
        finally:
            pipeline.PROMPT_DESCRIPTION = original_prompt
        pass_timings.append({"pass_index": pi + 1, "elapsed_seconds": round(time.perf_counter() - pass_start, 3), "items": len(batch_items)})
        if failed or (not batch_items and relation_batches):
            break
        items = batch_items
        _emit_progress(args, "extract_pass", "items", pass_index=f"{pi + 1}/{n_passes}", items=len(items))
        grounded_lists.extend(items)
        _write_checkpoint(checkpoint, pi, items)
        _emit_progress(args, "extract_pass", "done", pass_index=f"{pi + 1}/{n_passes}", items=len(items))
        completed_passes = pi + 1
    args.evidence_union_completed_passes = completed_passes
    args.evidence_union_degraded = bool(getattr(args, "_evidence_union_any_degraded", False))
    if doc_key:
        args.evidence_union_docs[str(doc_key)]["completed_passes"] = completed_passes
        args.evidence_union_docs[str(doc_key)]["pass_timings"] = pass_timings
        entity_reports = getattr(args, "entity_first_docs", None)
        if entity_reports is None:
            entity_reports = {}
            args.entity_first_docs = entity_reports
        relation_prompt_chars = [int(row.get("prompt_chars") or 0) for row in relation_batch_stats]
        entity_reports[str(doc_key)] = {
            "inventory_count": len(inventory),
            "relation_candidate_count": len(relation_pairs),
            "relation_causal_cue_count": sum(1 for row in relation_pairs if row.get("causal_cue")),
            "relation_same_sentence_count": sum(1 for row in relation_pairs if row.get("same_sentence")),
            "relation_mean_pair_score": round(
                sum(float(row.get("pair_score") or 0.0) for row in relation_pairs) / len(relation_pairs), 4
            ) if relation_pairs else 0.0,
            "relation_window_count": len({
                (str(row.get("window_id") or ""), str(row.get("evidence_text") or ""))
                for row in relation_pairs
            }),
            "relation_selection_policy": "evidence_window_compaction_v1",
            "relation_batch_count": len(relation_batch_stats),
            "relation_parallel_workers": int(getattr(args, "relation_parallel_workers", api_runtime.relation_workers(4))),
            "relation_prompt_chars_total": sum(relation_prompt_chars),
            "relation_prompt_chars_max": max(relation_prompt_chars) if relation_prompt_chars else 0,
            "relation_source_chars_total": sum(int(row.get("source_chars") or 0) for row in relation_batch_stats),
            "relation_elapsed_seconds": round(sum(float(row.get("elapsed_seconds") or 0.0) for row in relation_batch_stats), 3),
            "inventory_elapsed_seconds": round(inventory_elapsed, 3),
            "relation_batches": relation_batch_stats,
            "inventory_source": "entity_first" if inventory else "disabled_or_empty",
        }
    print(f"[LangExtract-union] {completed_passes}/{n_passes} pass(es), "
          f"{len(grounded_lists)} grounded items, inventory={len(inventory)}, "
          f"relation_pairs={len(relation_pairs)}, relation_workers={getattr(args, 'relation_parallel_workers', api_runtime.relation_workers(4))}, "
          f"degraded={args.evidence_union_degraded}")
    return grounded_lists

# ---------------------------------------------------------------- (2) names -
def build_aligned_lang_prompt(base_prompt, theme, names_str, limit=60):
    """Append the alignment-reuse block to the LangExtract prompt."""
    names = [n.strip() for n in (names_str or "").split(",") if n.strip()]
    block = ""
    if names:
        shown = ", ".join(names[:limit])
        block = (f"\n\nVARIABLE-NAME ALIGNMENT (important): if a variable you extract "
                 f"is the SAME quantity/state as one of these already-known names, "
                 f"REUSE that exact name instead of inventing a new one. Known names: "
                 f"{shown}" + (f" (first {limit} of more)" if len(names) > limit else "") + ".")
    return (base_prompt or "").rstrip() + block


# ------------------------------------------------------- (3) completion pass -
def _span_hit(corpus_text, u, v, mechanism, max_sent=6000):
    """True if the evidence sentence(s) contain a locatable surface of both
    endpoints (or the proposed mechanism) - a weak grounding gate for the
    option-3 sub-constraint. Not an alignment guarantee."""
    text = (corpus_text or "").lower()
    if not text:
        return False
    mech = (mechanism or "").lower().strip()
    if mech and mech[:60] in text:
        return True
    for sent in re.split(r"[.!?;\n]+", text)[:max_sent]:
        us, vs = u.lower().strip(), v.lower().strip()
        if us and vs and us[:40] in sent and vs[:40] in sent:
            return True
    return False


def build_completion_prompt(graph, corpus_text):
    node_lines = []
    for n in sorted(graph.nodes()):
        d = dict(graph.nodes[n])
        desc = _cut(d.get("description") or d.get("contextual_information"), 100)
        node_lines.append(f'- {n}: {desc}')
    names = sorted(graph.nodes())
    return f"""You repair a causal graph extracted from the documents below by adding MISSING direct edges.

DOCUMENT EVIDENCE (abridged):
{corpus_text[:20000]}

VARIABLE ROSTER (nodes currently in the graph):
{chr(10).join(node_lines)}

TASK: propose up to 40 DIRECT causal edges that are clearly stated in the evidence
but are MISSING from the graph. Both endpoints MUST come from the roster above.
Do NOT reverse an existing edge, do NOT propose self-loops, do NOT add edges
between variables that are only indirectly related.

Return ONLY JSON:
{{"edges": [{{"cause": "<from roster>", "effect": "<from roster>",
 "effect_sign": "+"|"-"|"0", "mechanism": "<one grounded sentence>"}}]}}"""


def neighbor_completion(graph, corpus_text, api_key, base_url, model, temperature,
                        max_edges=40, out_dir=None, verbose=True, require_span=False):
    if graph.number_of_nodes() < 5:
        return graph, {"added": 0, "rejected": 0, "skipped": 0, "reasons": {}}
    prompt = build_completion_prompt(graph, corpus_text)
    try:
        obj = chat_json(api_key, base_url, model, 0.2, prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"[neighbor-completion] LLM call failed: {type(exc).__name__}: {exc}")
        return graph, {"added": 0, "rejected": 0, "skipped": 0,
                       "error": str(exc)[:200]}
    roster = set(graph.nodes())
    stats = {"added": 0, "rejected": 0, "skipped": 0, "reasons": {}}
    records = []
    for item in (obj.get("edges") or [])[:max_edges]:
        u, v = str(item.get("cause") or "").strip(), str(item.get("effect") or "").strip()
        if not u or not v or u not in roster or v not in roster:
            stats["rejected"] += 1
            stats["reasons"].setdefault("off-roster", 0)
            stats["reasons"]["off-roster"] += 1
            continue
        if u == v:
            stats["rejected"] += 1
            stats["reasons"].setdefault("self-loop", 0)
            stats["reasons"]["self-loop"] += 1
            continue
        if graph.has_edge(u, v) or graph.has_edge(v, u):
            stats["skipped"] += 1
            stats["reasons"].setdefault("exists_or_reverse", 0)
            stats["reasons"]["exists_or_reverse"] += 1
            continue
        sign = str(item.get("effect_sign") or "").strip()
        if sign not in ("+", "-", "0"):
            sign = ""
        mech = _cut(item.get("mechanism"), 200)
        span_hit = _span_hit(corpus_text, u, v, mech)
        if require_span and not span_hit:
            # P2-3 sub-constraint (option 3): a completion candidate must be
            # locatable in the corpus; otherwise it is rejected, never a
            # hypothesis edge.
            stats["rejected"] += 1
            stats["reasons"].setdefault("no_span_hit", 0)
            stats["reasons"]["no_span_hit"] += 1
            records.append({"cause": u, "effect": v, "effect_sign": sign,
                            "mechanism": mech, "span_hit": False,
                            "status": "rejected_no_span_hit"})
            continue
        graph.add_edge(u, v, effect_sign=sign or "", mechanism=mech,
                       confidence="medium", rung=2,
                       edge_type="hypothesis", provenance="derived",
                       support_docs=0, span_ids_json="", condition="",
                       span_hit=span_hit)
        # P2-3 (option 2, primary): hypothesis edges carry A_e="derived",
        # never a document set; strictly separated from evidence edges.
        stats["added"] += 1
        records.append({"cause": u, "effect": v, "effect_sign": sign,
                        "mechanism": mech, "span_hit": span_hit})
    log = {"generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "model": model, "max_edges": max_edges, "stats": stats,
           "require_span": bool(require_span),
           "added_edges": records}
    if out_dir is not None:
        (out_dir / "neighbor_completion.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print(f"[neighbor-completion] added {stats['added']} edge(s), "
              f"rejected {stats['rejected']}, skipped {stats['skipped']}")
    return graph, log
