# -*- coding: utf-8 -*-
"""D2C_13_2 as a lang7-derived enhancement pipeline.

The primary D2C_13_2 pipeline is:

    lang7 extraction/build
      -> preserve the original lang7 run
      -> compile a typed ACC artifact
      -> chain/conditional/transitive views
      -> self-loop/cycle repair
      -> validation, queries, JSON-LD/Turtle, HTML viewer

The direct-LLM extractor in ``standalone.py`` is retained only as a separate
experimental baseline and is not used by this module.
"""

from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import Callable
from pathlib import Path
import subprocess
import sys
from typing import Any

from .audit import artifact_audit
from .compiler import compile_run
from .canonical_arbitration import apply_canonical_arbitration
from .canonical_audit import build_canonical_audit
from .canonical_votes import project_canonical_votes
from .config import DEFAULT_CONFIG_PATH, load_config, role_config
from .content_cache import cache_enabled_from_config, cache_root_from_config, commit as commit_content_cache, extraction_key, lookup as lookup_content_cache, restore as restore_content_cache
from .constants import VERSION
from .evidence_completeness import package_evidence, restore_pre_verification_evidence, snapshot_pre_verification_evidence
from .lang7_flow import run_extract as lang7_flow
from .policy import policy_from_config
from .queries import execute_query_cases, generate_query_cases
from .rdfio import roundtrip, write_jsonld, write_turtle
from .semantics import (
    apply_verdicts,
    build_verification_requests,
    classify_edge_directness,
    classify_edge_kinds,
    discover_span_chain_candidates,
    entity_similarity_candidates,
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
from .util import now_iso, read_json, write_json
from .validator import validate_artifact
from .verifier import verify_artifact
from .viewer import write_viewer_from_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GOLD_ROOT = PROJECT_ROOT / "gold_gen_text" / "output_gen5"


def infer_corpus(input_dir: str | Path) -> str | None:
    path = Path(input_dir).resolve()
    if path.parent.name == "goldgen_gen5":
        return path.name
    if path.name in {"water", "win95pts", "alarm", "chiller"}:
        return path.name
    return None


def infer_theme(input_dir: str | Path) -> str:
    corpus = infer_corpus(input_dir)
    if corpus:
        manifest = GOLD_ROOT / corpus / "seed1" / "manifest.json"
        if manifest.exists():
            payload = read_json(manifest, {}) or {}
            theme = (payload.get("theme") or {}).get("extract_theme")
            if theme:
                return str(theme)
    return "engineering system reliability"


def run_lang7(
    input_dir: str | Path,
    output_dir: str | Path,
    config_path: str | Path,
    theme: str | None = None,
    temperature: float = 0.3,
    timeout: int = 3600,
    use_content_cache: bool | None = None,
    refresh_content_cache: bool = False,
    cache_namespace: str | None = None,
) -> dict:
    """Run the original lang7 pipeline into ``output_dir``.

    The expensive extraction stage is content-addressed by document bytes and
    semantics-affecting extractor/entity-first settings.  Cache reads can be
    bypassed with ``refresh_content_cache=True``; successful runs still seed the
    cache for later reuse.
    """
    input_dir = Path(input_dir).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    extractor = role_config("extractor", config_path)
    full_config = load_config(config_path)
    entity_config = full_config.get("entity_first", {}) if isinstance(full_config, dict) else {}
    runtime_config = full_config.get("runtime", {}) if isinstance(full_config, dict) else {}
    arbitration_config = full_config.get("canonical_arbitration", {}) if isinstance(full_config, dict) else {}
    vote_mode = str(arbitration_config.get("vote_mode", "document") or "document").strip().lower()
    if vote_mode not in {"assertion", "document", "canonical_document"}:
        raise ValueError(f"canonical_arbitration.vote_mode must be assertion, document, or canonical_document: {vote_mode}")
    os.environ["D2C11_API_MAX_CONCURRENCY"] = str(int(runtime_config.get("api_max_concurrency", 0)))
    os.environ["D2C11_VERIFIER_WORKERS"] = str(int(runtime_config.get("verifier_parallel_workers", 4)))
    theme = theme or infer_theme(input_dir)
    cache_enabled = cache_enabled_from_config(config_path) if use_content_cache is None else bool(use_content_cache)
    cache_key = None
    cache_fingerprint = None
    cache_root = None
    cache_hit = False
    cache_skip_reason = None
    if cache_enabled and not Path(config_path).exists():
        cache_enabled = False
        cache_skip_reason = f"config not found: {config_path}"
    if cache_enabled:
        try:
            cache_key, cache_fingerprint = extraction_key(input_dir, config_path, theme, temperature, cache_namespace)
            cache_root = cache_root_from_config(config_path)
        except Exception as exc:
            cache_enabled = False
            cache_skip_reason = f"{type(exc).__name__}: {exc}"
        if cache_enabled and not refresh_content_cache:
            cached = lookup_content_cache(cache_root, cache_key)
            if cached is not None:
                restore_content_cache(cached, output_dir, cache_key)
                cache_hit = True
                log_path = output_dir / "d2c13_2_lang7_launch.log"
                log_path.write_text(
                    f"D2C_13_2 embedded flow: CONTENT CACHE HIT\nkey={cache_key}\ncache={cached}\n\n",
                    encoding="utf-8",
                )
                return {
                    "argv": [], "returncode": 0, "theme": theme,
                    "model": extractor["model"], "base_url": extractor["base_url"],
                    "output_dir": str(output_dir), "log": str(log_path),
                    "implementation": "D2C_13_2/d2c13_2/lang7_flow",
                    "content_cache": {"enabled": True, "hit": True, "key": cache_key, "cache_dir": str(cached), "namespace": cache_namespace},
                }
    argv = [
        "--input-dir", str(input_dir),
        "--output-dir", str(output_dir),
        "--theme", theme,
        "--model", extractor["model"],
        "--base-url", extractor["base_url"],
        "--temperature", str(temperature),
        "--max-steps", "2",
        "--extraction-passes", "1",
        "--max-output-tokens", str(int(extractor.get("max_tokens", 200000))),
        "--vote-mode", vote_mode,
        "--entity-first" if entity_config.get("enabled", True) else "--no-entity-first",
        "--entity-inventory-max-items", str(int(entity_config.get("max_items", 200))),
        "--entity-inventory-max-chunks", str(int(entity_config.get("max_chunks", 6))),
        "--entity-inventory-temperature", str(float(entity_config.get("temperature", 0.0))),
        "--relation-parallel-workers", str(int(entity_config.get("relation_parallel_workers", 4))),
    ]
    log_path = output_dir / "d2c13_2_lang7_launch.log"
    log_path.write_text("D2C_13_2 embedded flow: " + " ".join(argv) + "\n\n", encoding="utf-8")
    # This is the D2C_13_2-owned copy of the lang7 flow, executed in-process.
    # It does not import or invoke the archived lang5_evo1 extraction driver.
    returncode = lang7_flow.main(argv)
    if returncode != 0:
        raise RuntimeError(f"D2C_13_2 embedded lang7 flow exited with {returncode}; see {log_path}")
    cache_report = {"enabled": bool(cache_enabled), "hit": False, "key": cache_key, "cache_dir": None, "skip_reason": cache_skip_reason}
    if cache_enabled and cache_key and cache_fingerprint and cache_root is not None:
        entry = commit_content_cache(cache_root, cache_key, cache_fingerprint, output_dir)
        cache_report.update({"cache_dir": str(entry)})
    return {
        "argv": argv,
        "returncode": returncode,
        "theme": theme,
        "model": extractor["model"],
        "base_url": extractor["base_url"],
        "output_dir": str(output_dir),
        "log": str(log_path),
        "implementation": "D2C_13_2/d2c13_2/lang7_flow",
        "content_cache": cache_report,
    }


def direction_guard(artifact: dict) -> dict:
    """Reject the weaker direction when both direct-core directions exist."""
    index: dict[tuple[str, str], list[dict]] = {}
    for edge in artifact.get("edges", []):
        if edge.get("kind") != "direct_core":
            continue
        pair = tuple(sorted((edge["source"], edge["target"])))
        index.setdefault(pair, []).append(edge)
    removed = 0
    decisions = artifact.setdefault("conflict_decisions", [])
    for pair, edges in index.items():
        directions = {(e["source"], e["target"]): e for e in edges}
        if len(directions) < 2:
            continue
        def rank(e):
            return (
                len(e.get("support_doc_ids") or []),
                len(e.get("support_assertion_ids") or []),
                len(e.get("span_ids") or []),
                e.get("confidence", ""),
                e.get("id", ""),
            )
        ordered = sorted(edges, key=rank, reverse=True)
        keep = ordered[0]
        for edge in ordered[1:]:
            edge["kind"] = "rejected"
            edge["edge_kind"] = "rejected"
            edge["semantic_support"] = "contradicted"
            edge["verification_status"] = "rejected"
            edge["provenance"] = "direction_guard"
            removed += 1
        decisions.append({
            "id": __import__("hashlib").sha1((keep["id"] + str(pair)).encode("utf-8")).hexdigest()[:16],
            "kind": "direction",
            "pair": list(pair),
            "selected": keep["id"],
            "dropped": ordered[1]["id"],
            "rule": "support_doc_then_assertion_then_span_then_confidence",
            "sources": {},
        })
    materialize_views(artifact)
    return {"direction_edges_removed": removed}


def _refresh_alias_guard_notes(artifact: dict) -> int:
    """Replace stale pre-split verifier notes after a deterministic replay."""
    count = 0
    for edge in artifact.get("edges", []) or []:
        attrs = edge.get("attributes") or {}
        if not attrs.get("alias_guard_rebound"):
            continue
        note = str(edge.get("verification_note") or "").strip()
        if not note:
            continue
        edge["verification_note_original"] = note
        edge["verification_note"] = (
            "Alias guard rebound the evidence endpoint; the saved verifier "
            "verdict was replayed from the pre-fix edge."
        )
        count += 1
    guard = artifact.setdefault("diagnostics", {}).setdefault("alias_guard", {})
    guard["verification_notes_refreshed"] = count
    return count


def enhance_lang7_run(
    lang7_run_dir: str | Path,
    out_dir: str | Path,
    docs_dir: str | Path | None = None,
    corpus: str | None = None,
    config_path: str | Path = str(DEFAULT_CONFIG_PATH),
    chain_mode: str = "safe",
    verdicts_path: str | Path | None = None,
    verify: bool = False,
    verification_policy: str = "balanced",
    evidence_dirs: list[str | Path] | None = None,
    verdicts_adapter: Callable[[dict], list[dict]] | None = None,
) -> dict:
    """Compile and enhance an existing lang7 run into a D2C_13_2 artifact bundle."""
    lang7_run_dir = Path(lang7_run_dir).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    full_config = load_config(config_path)
    alias_config = (full_config.get("entity_resolution") or {}).get("document_alias") or {}
    arbitration_config = full_config.get("canonical_arbitration") or {}
    artifact = compile_run(
        lang7_run_dir, docs_dir=docs_dir, corpus=corpus,
        method="lang7", chain_mode=chain_mode,
        repair_chain=True, strict_core=True, evidence_dirs=evidence_dirs,
        document_alias_config=alias_config,
        canonical_arbitration_config=arbitration_config,
    )
    promotion_policy = policy_from_config(full_config)
    artifact["promotion_policy"] = promotion_policy
    artifact["source"]["pipeline"] = "lang7->D2C_13_2"
    artifact["source"]["lang7_run_dir"] = str(lang7_run_dir)
    artifact["provenance"]["producer"] = "D2C_13_2-lang7-enhanced"
    artifact["provenance"]["artifact_version"] = VERSION
    artifact["provenance"]["lang7_run_dir"] = str(lang7_run_dir)
    artifact["provenance"]["generated_at"] = now_iso()
    artifact["provenance"]["verification_policy"] = verification_policy
    union_report_path = lang7_run_dir / "evidence_union_report.json"
    entity_first_report_path = lang7_run_dir / "entity_first_report.json"
    if entity_first_report_path.exists():
        artifact["provenance"]["entity_first"] = read_json(entity_first_report_path, {}) or {}
    if union_report_path.exists():
        artifact["provenance"]["evidence_union"] = read_json(union_report_path, {}) or {}
    classify_edge_kinds(artifact, mode=chain_mode)
    discover_span_chain_candidates(artifact)
    repair_chain_edges(artifact)
    transitive_before = normalize_transitive_derivation(artifact)
    entity_coverage = repair_entity_coverage(artifact)
    assertion_recovery = recover_assertion_edges(artifact)
    textual_direction = textual_direction_guard(artifact)
    relation_role_direction = relation_role_direction_guard(artifact)
    temporal_direction = temporal_direction_guard(artifact)
    directness = classify_edge_directness(artifact)
    shortcut = shortcut_guard(artifact)
    condition_repair = repair_condition_edges(artifact)
    identity_repair = repair_identity_edges(artifact)
    evidence_packaging = package_evidence(artifact)
    evidence_snapshot = snapshot_pre_verification_evidence(artifact)
    materialize_views(artifact)

    # Preserve the exact pre-verifier input.  The final artifact is useful for
    # scoring/consumption; the pre-verifier artifact is the reproducible audit
    # record of what the verifier was asked to judge.
    pre_verification_artifact = copy.deepcopy(artifact)
    write_json(out_dir / "artifact.pre_verification.json", pre_verification_artifact)
    input_requests = build_verification_requests(pre_verification_artifact)
    write_json(out_dir / "verification_requests.input.json", input_requests)

    applied_verdicts = None
    if verdicts_path:
        applied_verdicts = read_json(verdicts_path, []) or []
        apply_verdicts(
            artifact,
            applied_verdicts,
            direction_policy=verification_policy,
        )
    elif verdicts_adapter is not None and not verify:
        applied_verdicts = verdicts_adapter(artifact) or []
        apply_verdicts(
            artifact,
            applied_verdicts,
            direction_policy=verification_policy,
        )
    verification = None
    if verify:
        verification = verify_artifact(
            artifact,
            config_path=config_path,
            verification_policy=verification_policy,
            checkpoint_path=out_dir / "verification_checkpoint.json",
        )
        artifact["diagnostics"]["verification"] = {
            key: value for key, value in verification.items() if key != "records"
        }
        write_json(out_dir / "verification_results.json", verification.get("records", []))
    else:
        # A deterministic replay may inject saved verdicts rather than call
        # the verifier.  Persist the exact verdict list that was applied so the
        # run remains auditable and reproducible.
        write_json(out_dir / "verification_results.json", applied_verdicts or [])
        _refresh_alias_guard_notes(artifact)

    normalize_transitive_derivation(artifact)
    core_repair = repair_core_graph(artifact)
    direction_repair = direction_guard(artifact)
    identity_repair_after_verifier = repair_identity_edges(artifact)
    directness_after_verifier = classify_edge_directness(artifact)
    shortcut_after_verifier = shortcut_guard(artifact)
    evidence_packaging_after_verifier = package_evidence(artifact)
    evidence_restore = restore_pre_verification_evidence(artifact)
    canonical_arbitration_after_verifier = apply_canonical_arbitration(artifact, arbitration_config)
    materialize_views(artifact)
    artifact["diagnostics"]["core_repair"] = core_repair
    artifact["diagnostics"]["direction_guard"] = direction_repair
    artifact["diagnostics"]["transitive_normalization"] = {
        "before_verifier": transitive_before,
    }
    artifact["diagnostics"]["entity_coverage_repair"] = entity_coverage
    artifact["diagnostics"]["assertion_recovery"] = assertion_recovery
    artifact["diagnostics"]["textual_direction"] = textual_direction
    artifact["diagnostics"]["relation_role_direction"] = relation_role_direction
    artifact["diagnostics"]["temporal_direction"] = temporal_direction
    artifact["diagnostics"]["directness"] = directness
    artifact["diagnostics"]["shortcut_guard"] = shortcut
    artifact["diagnostics"]["condition_repair"] = condition_repair
    artifact["diagnostics"]["evidence_packaging"] = evidence_packaging
    artifact["diagnostics"]["evidence_snapshot"] = evidence_snapshot
    artifact["diagnostics"]["evidence_packaging_after_verifier"] = evidence_packaging_after_verifier
    artifact["diagnostics"]["evidence_restore"] = evidence_restore
    artifact["diagnostics"]["canonical_arbitration_after_verifier"] = canonical_arbitration_after_verifier
    artifact["diagnostics"]["identity_repair"] = identity_repair
    artifact["diagnostics"]["identity_repair_after_verifier"] = identity_repair_after_verifier
    artifact["diagnostics"]["directness_after_verifier"] = directness_after_verifier
    artifact["diagnostics"]["shortcut_after_verifier"] = shortcut_after_verifier
    audit_report = artifact_audit(artifact)
    artifact["diagnostics"]["audit"] = audit_report
    artifact["diagnostics"]["edge_counts"] = {
        kind: sum(1 for e in artifact["edges"] if e.get("kind") == kind)
        for kind in sorted({e.get("kind") for e in artifact["edges"]})
    }
    artifact["diagnostics"]["evidence_grade_counts"] = {
        grade: sum(1 for e in artifact["edges"] if e.get("evidence_grade") == grade)
        for grade in sorted({e.get("evidence_grade", "none") for e in artifact["edges"]})
    }
    artifact["diagnostics"]["verification_status_counts"] = {
        status: sum(1 for e in artifact["edges"] if e.get("verification_status") == status)
        for status in sorted({e.get("verification_status", "unverified") for e in artifact["edges"]})
    }
    write_json(out_dir / "artifact.json", artifact)
    write_json(out_dir / "evidence_audit.json", audit_report)
    canonical_audit_summary = None
    canonical_audit_error = None
    try:
        canonical_audit_summary = build_canonical_audit(
            out_dir / "artifact.json",
            (lang7_run_dir / "evidence.json") if (lang7_run_dir / "evidence.json").exists() else None,
            out_dir / "audit",
        )
        canonical_votes_path = out_dir / "audit" / "canonical_votes.json"
        if canonical_votes_path.exists():
            canonical_vote_payload = read_json(canonical_votes_path, {}) or {}
            before_hash = str(canonical_audit_summary.get("artifact_sha256_after") or "")
            projection = project_canonical_votes(artifact, canonical_vote_payload)
            write_json(out_dir / "artifact.json", artifact)
            after_hash = hashlib.sha256((out_dir / "artifact.json").read_bytes()).hexdigest()
            canonical_vote_payload["projection"] = {
                **projection,
                "artifact_sha256_before_projection": before_hash,
                "artifact_sha256_after_projection": after_hash,
            }
            write_json(canonical_votes_path, canonical_vote_payload)
            canonical_audit_summary["artifact_sha256_before"] = before_hash
            canonical_audit_summary["artifact_sha256_after"] = after_hash
            canonical_audit_summary["canonical_vote_projection"] = canonical_vote_payload["projection"]
            canonical_audit_summary["f1_neutral"] = True
            write_json(out_dir / "audit" / "audit_report.json", canonical_audit_summary)
    except Exception as exc:  # noqa: BLE001 - audit sidecar must not alter core artifact
        canonical_audit_error = f"{type(exc).__name__}: {exc}"
        write_json(out_dir / "audit" / "audit_error.json", {"error": canonical_audit_error})
    write_jsonld(artifact, out_dir / "artifact.jsonld")
    write_turtle(artifact, out_dir / "artifact.ttl")
    validation = validate_artifact(artifact, run_shacl=True)
    write_json(out_dir / "validation_report.json", validation)
    cases = generate_query_cases(artifact, limit=30)
    query_report = execute_query_cases(artifact, cases)
    write_json(out_dir / "query_report.json", query_report)
    write_json(out_dir / "verification_requests.json", build_verification_requests(artifact))
    write_json(out_dir / "entity_candidates.json", entity_similarity_candidates(artifact))
    rt = roundtrip(artifact, out_dir / "roundtrip")
    consumer_report = None
    consumer_script = PROJECT_ROOT / "D2C_13_2" / "tools" / "independent_consumer.py"
    if consumer_script.exists():
        try:
            consumer_output = out_dir / "consumer_report.json"
            subprocess.run(
                [sys.executable, str(consumer_script), "--input", str(out_dir / "artifact.ttl"),
                 "--artifact-json", str(out_dir / "artifact.json"), "--output", str(consumer_output)],
                check=False, capture_output=True, text=True, timeout=180,
            )
            consumer_report = read_json(consumer_output, {}) or {}
        except Exception as exc:  # noqa: BLE001 - optional interoperability diagnostic
            consumer_report = {"consumer": "rdflib-independent", "error": f"{type(exc).__name__}: {exc}"}
    write_viewer_from_file(out_dir / "artifact.json", out_dir / "visualization.html", validation_path=out_dir / "validation_report.json")

    verification_summary = None if verification is None else {
        key: value for key, value in verification.items() if key != "records"
    }
    consumer_summary = None if consumer_report is None else {
        key: value for key, value in consumer_report.items()
        if key not in {"direct_core", "candidate", "unsupported_hypothesis"}
    }
    summary = {
        "version": VERSION,
        "pipeline": "lang7->D2C_13_2",
        "lang7_run_dir": str(lang7_run_dir),
        "artifact": str((out_dir / "artifact.json").resolve()),
        "artifact_pre_verification": str((out_dir / "artifact.pre_verification.json").resolve()),
        "validation_valid": validation["valid"],
        "shacl_conforms": (validation.get("shacl") or {}).get("conforms"),
        "query_failed": query_report["failed"],
        "query_total": query_report["total"],
        "roundtrip_json": rt.get("json_roundtrip"),
        "roundtrip_rdf": rt.get("rdf_roundtrip"),
        "roundtrip_edge_semantics": rt.get("edge_semantics_roundtrip"),
        "roundtrip_entity_identity": rt.get("entity_identity_roundtrip"),
        "roundtrip_queries": rt.get("query_roundtrip"),
        "edge_counts": artifact["diagnostics"]["edge_counts"],
        "view_counts": artifact["diagnostics"].get("view_counts", {}),
        "alias_resolution": artifact["diagnostics"].get("alias_resolution", {}),
        "document_alias": artifact["diagnostics"].get("document_alias", {}),
        "canonical_arbitration": artifact["diagnostics"].get("canonical_arbitration", {}),
        "canonical_pair_replay": artifact.get("canonical_pair_replay", {}).get("counts", {}),
        "path_aware": artifact["diagnostics"].get("path_aware", {}),
        "support_aware_core": artifact["diagnostics"].get("view_counts", {}).get("support_aware_core", 0),
        "audit": audit_report,
        "evidence_grade_counts": artifact["diagnostics"].get("evidence_grade_counts", {}),
        "evidence_completeness_counts": artifact["diagnostics"].get("evidence_completeness_counts", {}),
        "evidence_packaging": artifact["diagnostics"].get("evidence_packaging", {}),
        "verification_status_counts": artifact["diagnostics"].get("verification_status_counts", {}),
        "assertion_support_counts": artifact["diagnostics"].get("assertion_support_counts", {}),
        "core_repair": core_repair,
        "direction_guard": direction_repair,
        "temporal_direction": {
            "corrected": artifact["diagnostics"].get("temporal_direction", {}).get("corrected", 0)
        },
        "verification_policy": verification_policy,
        "verification_input_requests": len(input_requests),
        "evidence_union": artifact.get("provenance", {}).get("evidence_union", {}),
        "entity_first": artifact.get("provenance", {}).get("entity_first", {}),
        "consumer": consumer_summary,
        "cross_tool_equal": None if consumer_report is None else consumer_report.get("cross_tool_equal"),
        "verification": verification_summary,
        "verification_source": "live_verifier" if verification is not None else ("saved_verdicts" if applied_verdicts else "none"),
        "verification_telemetry": None if verification is None else verification.get("telemetry"),
        "canonical_audit": canonical_audit_summary,
        "canonical_audit_error": canonical_audit_error,
        "viewer": str((out_dir / "visualization.html").resolve()),
    }
    write_json(out_dir / "pipeline_summary.json", summary)
    return summary

def run_lang7_enhanced(
    input_dir: str | Path,
    out_dir: str | Path,
    config_path: str | Path = str(DEFAULT_CONFIG_PATH),
    theme: str | None = None,
    temperature: float = 0.3,
    chain_mode: str = "safe",
    verdicts_path: str | Path | None = None,
    verify: bool = True,
    verification_policy: str = "balanced",
    evidence_dirs: list[str | Path] | None = None,
    use_content_cache: bool = True,
    refresh_content_cache: bool = False,
    cache_namespace: str | None = None,
) -> dict:
    """Run D2C_13_2's embedded lang7 flow and then compile/enhance it."""
    out_dir = Path(out_dir).resolve()
    lang7_dir = out_dir / "lang7"
    launch = run_lang7(input_dir, lang7_dir, config_path, theme=theme, temperature=temperature, use_content_cache=use_content_cache, refresh_content_cache=refresh_content_cache, cache_namespace=cache_namespace)
    corpus = infer_corpus(input_dir)
    summary = enhance_lang7_run(
        lang7_dir, out_dir, docs_dir=input_dir, corpus=corpus,
        config_path=config_path, chain_mode=chain_mode, verdicts_path=verdicts_path,
        verify=verify, verification_policy=verification_policy,
        evidence_dirs=evidence_dirs,
    )
    summary["lang7"] = launch
    write_json(out_dir / "pipeline_summary.json", summary)
    return summary






