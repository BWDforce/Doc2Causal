# -*- coding: utf-8 -*-
"""Command-line interface for D2C_13_2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .compiler import compile_run
from .config import DEFAULT_CONFIG_PATH, load_config, role_config
from .constants import VERSION
from .extractor import DeepSeekExtractor, load_documents, load_from_extractions
from .evidence_completeness import package_evidence, restore_pre_verification_evidence, snapshot_pre_verification_evidence
from .queries import answer, execute_query_cases, generate_query_cases
from .rdfio import roundtrip, write_jsonld, write_turtle
from .semantics import apply_verdicts, build_verification_requests, entity_similarity_candidates, materialize_views, repair_core_graph
from .lang7_pipeline import enhance_lang7_run, run_lang7_enhanced
from .canonical_audit import build_from_run_dir
from .standalone import build_artifact_from_extractions
from .util import read_json, write_json
from .validator import validate_artifact
from .viewer import write_viewer_from_file


def _load(path: str) -> dict:
    return read_json(Path(path), {}) or {}


def cmd_compile(args: argparse.Namespace) -> int:
    full_config = load_config(args.config)
    alias_config = (full_config.get("entity_resolution") or {}).get("document_alias") or {}
    artifact = compile_run(
        args.run_dir, docs_dir=args.docs_dir, corpus=args.corpus,
        method=args.method, run=args.run, chain_mode=args.chain_mode,
        repair_chain=not args.no_repair, strict_core=not args.allow_unsupported_core,
        document_alias_config=alias_config,
        canonical_arbitration_config=full_config.get("canonical_arbitration") or {},
    )
    write_json(Path(args.output), artifact)
    print(json.dumps({
        "version": VERSION,
        "artifact_id": artifact["artifact_id"],
        "edges": len(artifact["edges"]),
        "counts": artifact.get("diagnostics", {}).get("edge_counts", {}),
        "output": str(Path(args.output).resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_visualize(args: argparse.Namespace) -> int:
    path = write_viewer_from_file(
        args.artifact, args.output,
        validation_path=args.validation,
        metrics_path=args.metrics,
    )
    print(json.dumps({"version": VERSION, "output": str(Path(path).resolve())}, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    artifact = _load(args.artifact)
    report = validate_artifact(artifact, run_shacl=not args.no_shacl)
    if args.output:
        write_json(Path(args.output), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


def cmd_export(args: argparse.Namespace) -> int:
    artifact = _load(args.artifact)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonld(artifact, out / "artifact.jsonld")
    write_turtle(artifact, out / "artifact.ttl")
    print(json.dumps({"jsonld": str(out / "artifact.jsonld"), "turtle": str(out / "artifact.ttl")}, indent=2))
    return 0


def cmd_roundtrip(args: argparse.Namespace) -> int:
    artifact = _load(args.artifact)
    report = roundtrip(artifact, args.out_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("json_roundtrip") and report.get("rdf_roundtrip") else 1


def cmd_audit_run(args: argparse.Namespace) -> int:
    report = build_from_run_dir(args.run_dir, args.out_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    artifact = _load(args.artifact)
    payload = answer(artifact, args.query, args.edge_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_query_suite(args: argparse.Namespace) -> int:
    artifact = _load(args.artifact)
    cases = generate_query_cases(artifact, limit=args.limit)
    report = execute_query_cases(artifact, cases)
    if args.output:
        write_json(Path(args.output), report)
    print(json.dumps({k: report[k] for k in ("total", "passed", "failed")}, indent=2))
    return 0 if report["failed"] == 0 else 1


def cmd_requests(args: argparse.Namespace) -> int:
    artifact = _load(args.artifact)
    requests = build_verification_requests(artifact)
    write_json(Path(args.output), requests)
    print(json.dumps({"requests": len(requests), "output": str(Path(args.output).resolve())}, indent=2))
    return 0


def cmd_verdicts(args: argparse.Namespace) -> int:
    artifact = _load(args.artifact)
    verdicts = read_json(Path(args.verdicts), []) or []
    package_evidence(artifact)
    snapshot_pre_verification_evidence(artifact)
    result = apply_verdicts(artifact, verdicts)
    package_evidence(artifact)
    restore_pre_verification_evidence(artifact)
    materialize_views(artifact)
    write_json(Path(args.output), artifact)
    print(json.dumps(result, indent=2))
    return 0


def _write_artifact_bundle(artifact: dict, out: Path, no_shacl: bool = False, query_limit: int = 30) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "artifact.json", artifact)
    write_jsonld(artifact, out / "artifact.jsonld")
    write_turtle(artifact, out / "artifact.ttl")
    validation = validate_artifact(artifact, run_shacl=not no_shacl)
    write_json(out / "validation_report.json", validation)
    cases = generate_query_cases(artifact, limit=query_limit)
    query_report = execute_query_cases(artifact, cases)
    write_json(out / "query_report.json", query_report)
    requests = build_verification_requests(artifact)
    write_json(out / "verification_requests.json", requests)
    entity_candidates = entity_similarity_candidates(artifact)
    write_json(out / "entity_candidates.json", entity_candidates)
    rt = roundtrip(artifact, out / "roundtrip")
    summary = {
        "version": VERSION,
        "artifact": str((out / "artifact.json").resolve()),
        "validation_valid": validation["valid"],
        "validation_errors": len(validation["errors"]),
        "shacl_conforms": (validation.get("shacl") or {}).get("conforms"),
        "query_total": query_report["total"],
        "query_failed": query_report["failed"],
        "roundtrip_json": rt.get("json_roundtrip"),
        "roundtrip_rdf": rt.get("rdf_roundtrip"),
        "roundtrip_queries": rt.get("query_roundtrip"),
        "edge_counts": artifact.get("diagnostics", {}).get("edge_counts", {}),
        "entity_candidate_pairs": len(entity_candidates),
    }
    write_json(out / "pipeline_summary.json", summary)
    return summary


def cmd_lang7_run(args: argparse.Namespace) -> int:
    if args.dry_run:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        write_json(out / "lang7_dry_run.json", {
            "version": VERSION,
            "pipeline": "lang7->D2C_13_2",
            "input_dir": str(Path(args.input_dir).resolve()),
            "config": args.config,
            "theme": args.theme,
            "temperature": args.temperature,
        })
        print(json.dumps({"version": VERSION, "pipeline": "lang7->D2C_13_2", "dry_run": True, "out_dir": str(out.resolve())}, indent=2))
        return 0
    summary = run_lang7_enhanced(
        args.input_dir, args.out_dir, config_path=args.config,
        theme=args.theme, temperature=args.temperature,
        chain_mode=args.chain_mode, verdicts_path=args.verdicts,
        verify=not args.no_verify, verification_policy=args.verification_policy,
        use_content_cache=not args.no_content_cache,
        refresh_content_cache=args.refresh_content_cache,
        cache_namespace=args.cache_namespace,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_valid"] and summary["roundtrip_json"] and summary["roundtrip_rdf"] and summary["roundtrip_queries"] else 1


def cmd_lang7_enhance(args: argparse.Namespace) -> int:
    summary = enhance_lang7_run(
        args.lang7_run_dir, args.out_dir, docs_dir=args.docs_dir,
        corpus=args.corpus, config_path=args.config,
        chain_mode=args.chain_mode, verdicts_path=args.verdicts,
        verify=args.verify, verification_policy=args.verification_policy,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_valid"] and summary["roundtrip_json"] and summary["roundtrip_rdf"] and summary["roundtrip_queries"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    docs = load_documents(args.input_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    extractor_config = role_config(
        "extractor", args.config, model=args.model,
        base_url=args.base_url, api_key_env=args.api_key_env,
    )
    model_name = extractor_config["model"]
    base_url = extractor_config["base_url"]
    api_key_env = extractor_config["api_key_env"]
    max_tokens = args.max_tokens or extractor_config["max_tokens"]
    timeout = args.timeout or extractor_config["timeout"]
    if args.dry_run:
        prompt_dir = out / "prompts"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        extractor = DeepSeekExtractor(
            api_key="", base_url=base_url, model=model_name,
            temperature=args.temperature, max_tokens=max_tokens,
            timeout=timeout, max_chars=args.max_chars, dry_run=True,
        )
        manifest = []
        for doc in docs:
            prompt = extractor.prompt(doc.title, doc.text)
            prompt_path = prompt_dir / f"{doc.doc_id}_{doc.source_file}.prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            manifest.append({"doc_id": doc.doc_id, "source_file": doc.source_file, "title": doc.title, "prompt": str(prompt_path)})
        write_json(out / "dry_run.json", {
            "version": VERSION, "model": model_name, "base_url": base_url,
            "config_path": extractor_config["config_path"],
            "documents": manifest, "prompt_version": "D2C_13_2-chain-v1",
        })
        print(json.dumps({"version": VERSION, "dry_run": True, "documents": len(docs), "out_dir": str(out.resolve())}, indent=2))
        return 0
    if args.from_extractions:
        extractions = load_from_extractions(args.from_extractions)
        model = args.model or "mock"
    else:
        import os
        api_key = os.environ.get(api_key_env, "")
        extractor = DeepSeekExtractor(
            api_key=api_key, base_url=base_url, model=model_name,
            temperature=args.temperature, max_tokens=max_tokens,
            timeout=timeout, max_chars=args.max_chars, raw_dir=out / "api_raw",
        )
        model = model_name
        extractions = {}
        for doc in docs:
            extractions[doc.doc_id] = extractor.extract_document(doc)
    write_json(out / "extractions.json", {"version": VERSION, "model": model, "extractions": extractions})
    artifact = build_artifact_from_extractions(
        docs, extractions, input_dir=args.input_dir, model=model,
        chain_mode=args.chain_mode, repair_chain=not args.no_repair,
    )
    summary = _write_artifact_bundle(artifact, out, no_shacl=args.no_shacl, query_limit=args.query_limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["validation_valid"] and summary["roundtrip_json"] and summary["roundtrip_rdf"] and summary["roundtrip_queries"] else 1


def cmd_repair(args: argparse.Namespace) -> int:
    artifact = _load(args.artifact)
    stats = repair_core_graph(artifact)
    summary = _write_artifact_bundle(artifact, Path(args.out_dir), no_shacl=args.no_shacl, query_limit=args.query_limit)
    write_json(Path(args.out_dir) / "repair_summary.json", {"version": VERSION, "stats": stats, **summary})
    print(json.dumps({"version": VERSION, "stats": stats, **summary}, ensure_ascii=False, indent=2))
    return 0 if summary["validation_valid"] and summary["roundtrip_json"] and summary["roundtrip_rdf"] and summary["roundtrip_queries"] else 1


def cmd_pipeline(args: argparse.Namespace) -> int:
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    full_config = load_config(args.config)
    alias_config = (full_config.get("entity_resolution") or {}).get("document_alias") or {}
    artifact = compile_run(
        args.run_dir, docs_dir=args.docs_dir, corpus=args.corpus,
        method=args.method, run=args.run, chain_mode=args.chain_mode,
        repair_chain=not args.no_repair, strict_core=not args.allow_unsupported_core,
        document_alias_config=alias_config,
        canonical_arbitration_config=full_config.get("canonical_arbitration") or {},
    )
    write_json(out / "artifact.json", artifact)
    write_jsonld(artifact, out / "artifact.jsonld")
    write_turtle(artifact, out / "artifact.ttl")
    validation = validate_artifact(artifact, run_shacl=not args.no_shacl)
    write_json(out / "validation_report.json", validation)
    cases = generate_query_cases(artifact, limit=args.query_limit)
    query_report = execute_query_cases(artifact, cases)
    write_json(out / "query_report.json", query_report)
    requests = build_verification_requests(artifact)
    write_json(out / "verification_requests.json", requests)
    entity_candidates = entity_similarity_candidates(artifact)
    write_json(out / "entity_candidates.json", entity_candidates)
    rt = roundtrip(artifact, out / "roundtrip")
    summary = {
        "version": VERSION,
        "artifact": str((out / "artifact.json").resolve()),
        "validation_valid": validation["valid"],
        "validation_errors": len(validation["errors"]),
        "shacl_conforms": (validation.get("shacl") or {}).get("conforms"),
        "query_total": query_report["total"],
        "query_failed": query_report["failed"],
        "roundtrip_json": rt.get("json_roundtrip"),
        "roundtrip_rdf": rt.get("rdf_roundtrip"),
        "roundtrip_queries": rt.get("query_roundtrip"),
        "edge_counts": artifact.get("diagnostics", {}).get("edge_counts", {}),
        "entity_candidate_pairs": len(entity_candidates),
    }
    write_json(out / "pipeline_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if validation["valid"] and rt.get("json_roundtrip") and rt.get("rdf_roundtrip") and rt.get("query_roundtrip") else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="d2c13_2", description=f"D2C_13_2 typed ACC artifact tools ({VERSION})")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("compile")
    c.add_argument("--run-dir", required=True)
    c.add_argument("--docs-dir", default=None)
    c.add_argument("--output", required=True)
    c.add_argument("--corpus", default=None)
    c.add_argument("--method", default=None)
    c.add_argument("--run", type=int, default=None)
    c.add_argument("--chain-mode", choices=["safe", "aggressive", "off"], default="safe")
    c.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    c.add_argument("--no-repair", action="store_true")
    c.add_argument("--allow-unsupported-core", action="store_true")
    c.set_defaults(func=cmd_compile)

    vis = sub.add_parser("visualize", help="Write a self-contained HTML graph viewer for one artifact")
    vis.add_argument("--artifact", required=True)
    vis.add_argument("--output", required=True)
    vis.add_argument("--validation", default=None)
    vis.add_argument("--metrics", default=None)
    vis.set_defaults(func=cmd_visualize)

    v = sub.add_parser("validate")
    v.add_argument("--artifact", required=True)
    v.add_argument("--output", default=None)
    v.add_argument("--no-shacl", action="store_true")
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser("export")
    e.add_argument("--artifact", required=True)
    e.add_argument("--out-dir", required=True)
    e.set_defaults(func=cmd_export)

    r = sub.add_parser("roundtrip")
    r.add_argument("--artifact", required=True)
    r.add_argument("--out-dir", required=True)
    r.set_defaults(func=cmd_roundtrip)

    q = sub.add_parser("query")
    q.add_argument("--artifact", required=True)
    q.add_argument("--query", required=True, choices=["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"])
    q.add_argument("--edge-id", required=True)
    q.set_defaults(func=cmd_query)

    qs = sub.add_parser("query-suite")
    qs.add_argument("--artifact", required=True)
    qs.add_argument("--output", default=None)
    qs.add_argument("--limit", type=int, default=30)
    qs.set_defaults(func=cmd_query_suite)

    req = sub.add_parser("requests")
    req.add_argument("--artifact", required=True)
    req.add_argument("--output", required=True)
    req.set_defaults(func=cmd_requests)

    ar = sub.add_parser("audit-run", help="Replay the F1-neutral D2C_13_2 Q3/Q4/Q7 canonical audit on an existing run")
    ar.add_argument("--run-dir", required=True)
    ar.add_argument("--out-dir", default=None)
    ar.set_defaults(func=cmd_audit_run)

    vd = sub.add_parser("apply-verdicts")
    vd.add_argument("--artifact", required=True)
    vd.add_argument("--verdicts", required=True)
    vd.add_argument("--output", required=True)
    vd.set_defaults(func=cmd_verdicts)

    lr = sub.add_parser("lang7-run", help="Run D2C_13_2's embedded lang7-derived flow, then enhance it")
    lr.add_argument("--input-dir", required=True)
    lr.add_argument("--out-dir", required=True)
    lr.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    lr.add_argument("--theme", default=None)
    lr.add_argument("--temperature", type=float, default=0.3)
    lr.add_argument("--chain-mode", choices=["safe", "aggressive", "off"], default="safe")
    lr.add_argument("--verdicts", default=None)
    lr.add_argument("--no-verify", action="store_true", help="Disable the D2C_13_2 semantic/direction verifier")
    lr.add_argument("--verification-policy", choices=["advisory", "balanced", "strict"], default="balanced", help="How ambiguous direction is handled")
    lr.add_argument("--dry-run", action="store_true")
    lr.add_argument("--no-content-cache", action="store_true", help="Disable content-addressed Lang7 extraction cache")
    lr.add_argument("--refresh-content-cache", action="store_true", help="Ignore a cache hit, re-extract, and replace the cache entry")
    lr.add_argument("--cache-namespace", default=None, help="Stable run namespace for intentional stochastic runs (for example run_01)")
    lr.set_defaults(func=cmd_lang7_run)

    le = sub.add_parser("lang7-enhance", help="Compile and enhance an existing lang7 run without invoking extraction")
    le.add_argument("--lang7-run-dir", required=True)
    le.add_argument("--out-dir", required=True)
    le.add_argument("--docs-dir", default=None)
    le.add_argument("--corpus", default=None)
    le.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    le.add_argument("--chain-mode", choices=["safe", "aggressive", "off"], default="safe")
    le.add_argument("--verdicts", default=None)
    le.add_argument("--verify", action="store_true", help="Run the D2C_13_2 semantic/direction verifier")
    le.add_argument("--verification-policy", choices=["advisory", "balanced", "strict"], default="balanced", help="How ambiguous direction is handled")
    le.set_defaults(func=cmd_lang7_enhance)

    run = sub.add_parser("run", help="Legacy direct-LLM baseline; not the primary D2C_13_2 path")
    run.add_argument("--input-dir", required=True)
    run.add_argument("--out-dir", required=True)
    run.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    run.add_argument("--model", default=None, help="Optional one-off override; default comes from config.json")
    run.add_argument("--base-url", default=None, help="Optional one-off override; default comes from config.json")
    run.add_argument("--api-key-env", default=None, help="Optional one-off override; default comes from config.json")
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--max-tokens", type=int, default=None, help="Optional override; default comes from config.json")
    run.add_argument("--timeout", type=int, default=None, help="Optional request timeout seconds; default comes from config.json")
    run.add_argument("--max-chars", type=int, default=6000)
    run.add_argument("--from-extractions", default=None)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--chain-mode", choices=["safe", "aggressive", "off"], default="safe")
    run.add_argument("--no-repair", action="store_true")
    run.add_argument("--no-shacl", action="store_true")
    run.add_argument("--query-limit", type=int, default=30)
    run.set_defaults(func=cmd_run)

    rp = sub.add_parser("repair", help="Remove core self-loops and cycles from an existing artifact")
    rp.add_argument("--artifact", required=True)
    rp.add_argument("--out-dir", required=True)
    rp.add_argument("--no-shacl", action="store_true")
    rp.add_argument("--query-limit", type=int, default=30)
    rp.set_defaults(func=cmd_repair)

    pp = sub.add_parser("pipeline")
    pp.add_argument("--run-dir", required=True)
    pp.add_argument("--docs-dir", default=None)
    pp.add_argument("--out-dir", required=True)
    pp.add_argument("--corpus", default=None)
    pp.add_argument("--method", default=None)
    pp.add_argument("--run", type=int, default=None)
    pp.add_argument("--chain-mode", choices=["safe", "aggressive", "off"], default="safe")
    pp.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    pp.add_argument("--no-repair", action="store_true")
    pp.add_argument("--allow-unsupported-core", action="store_true")
    pp.add_argument("--no-shacl", action="store_true")
    pp.add_argument("--query-limit", type=int, default=30)
    pp.set_defaults(func=cmd_pipeline)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

