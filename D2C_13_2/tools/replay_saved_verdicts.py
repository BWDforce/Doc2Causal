import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from d2c13_2.evaluation import evaluate_unified_views
from d2c13_2.lang7_pipeline import enhance_lang7_run
from d2c13_2.semantics import build_verification_requests
from d2c13_2.util import read_json, write_json

ROOT = Path(__file__).resolve().parents[2]


def _norm(value) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("_", " ").replace(":", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _entity_labels(artifact: dict) -> dict[str, str]:
    return {
        str(entity.get("id")): str(entity.get("label") or entity.get("id") or "")
        for entity in artifact.get("entities", [])
        if entity.get("id")
    }


def _endpoint(row: dict, side: str, labels: dict[str, str]) -> str:
    value = row.get(f"{side}_name") or row.get(side) or ""
    return labels.get(str(value), str(value))


def _surface_signature(row: dict, labels: dict[str, str], reverse: bool = False) -> tuple[str, str]:
    """Return a case-preserving endpoint signature.

    Normalized semantic signatures deliberately case-fold names. When alias
    normalization collapses two historical edges that differ only by casing,
    this secondary index disambiguates them without weakening the general
    semantic matcher.
    """
    source = unicodedata.normalize("NFKC", str(_endpoint(row, "source", labels) or "")).strip()
    target = unicodedata.normalize("NFKC", str(_endpoint(row, "target", labels) or "")).strip()
    if reverse:
        source, target = target, source
    return (source, target)


def _signature(row: dict, labels: dict[str, str], reverse: bool = False) -> tuple[str, str, str]:
    source = _endpoint(row, "source", labels)
    target = _endpoint(row, "target", labels)
    if reverse:
        source, target = target, source
    claim = row.get("claim_text") or row.get("description") or ""
    return (_norm(source), _norm(target), _norm(claim))


def _pair_signature(row: dict, labels: dict[str, str], unordered: bool = True) -> tuple[str, str]:
    source = _norm(_endpoint(row, "source", labels))
    target = _norm(_endpoint(row, "target", labels))
    return tuple(sorted((source, target))) if unordered else (source, target)


def _claim(row: dict) -> str:
    return _norm(row.get("claim_text") or row.get("description") or "")


def _flip_direction(value) -> str:
    text = str(value or "").lower()
    if text == "forward":
        return "reverse"
    if text == "reverse":
        return "forward"
    return text


def _remap_one(verdict: dict, new_id: str, old_id: str, reverse: bool) -> dict:
    row = dict(verdict)
    row["edge_id"] = new_id
    row["replay_source_edge_id"] = old_id
    if reverse:
        row["direction"] = _flip_direction(row.get("direction"))
        forward = row.get("direction_support_forward", 0)
        reverse_score = row.get("direction_support_reverse", 0)
        row["direction_support_forward"] = reverse_score
        row["direction_support_reverse"] = forward
    return row


def remap_verdicts(
    old_requests: list[dict],
    old_verdicts: list[dict],
    new_requests: list[dict],
    old_artifact: dict,
    new_artifact: dict,
) -> tuple[list[dict], dict]:
    old_labels = _entity_labels(old_artifact)
    new_labels = _entity_labels(new_artifact)
    verdict_by_id = {str(row.get("edge_id")): row for row in old_verdicts if row.get("edge_id")}
    old_request_by_id = {str(row.get("edge_id")): row for row in old_requests if row.get("edge_id")}

    direct_index = defaultdict(list)
    reverse_index = defaultdict(list)
    surface_direct_index = defaultdict(list)
    surface_reverse_index = defaultdict(list)
    claim_index = defaultdict(list)
    surface_claim_index = defaultdict(list)
    pair_index = defaultdict(list)
    for old_id, request in old_request_by_id.items():
        if old_id not in verdict_by_id:
            continue
        direct_index[_signature(request, old_labels, False)].append(old_id)
        reverse_index[_signature(request, old_labels, True)].append(old_id)
        surface_direct_index[_surface_signature(request, old_labels, False)].append(old_id)
        surface_reverse_index[_surface_signature(request, old_labels, True)].append(old_id)
        claim = _claim(request)
        if claim:
            claim_index[claim].append(old_id)
            windows = list(request.get("evidence_windows") or [])
            best = request.get("best_evidence_window")
            if isinstance(best, dict):
                windows.append(best)
            for window in windows:
                if not isinstance(window, dict):
                    continue
                source_surface = _norm(window.get("source_surface"))
                target_surface = _norm(window.get("target_surface"))
                if source_surface and target_surface:
                    surface_claim_index[(claim, source_surface, target_surface)].append(old_id)
        pair_index[_pair_signature(request, old_labels)].append(old_id)

    mapped = []
    unmatched = []
    methods = defaultdict(int)
    for request in new_requests:
        new_id = str(request.get("edge_id") or "")
        if not new_id:
            continue
        if new_id in verdict_by_id:
            mapped.append(_remap_one(verdict_by_id[new_id], new_id, new_id, False))
            methods["exact_edge_id"] += 1
            continue

        # Prefer an exact, case-preserving surface match when normalization
        # collapses two otherwise distinct historical edges.
        surface_direct = _surface_signature(request, new_labels, False)
        surface_reverse = _surface_signature(request, new_labels, True)
        surface_candidates = list(dict.fromkeys(
            surface_direct_index.get(surface_direct, [])
            + surface_reverse_index.get(surface_reverse, [])
        ))
        if len(surface_candidates) == 1:
            old_id = surface_candidates[0]
            reverse = not surface_direct_index.get(surface_direct) and bool(surface_reverse_index.get(surface_reverse))
            mapped.append(_remap_one(verdict_by_id[old_id], new_id, old_id, reverse))
            methods["exact_surface_signature_reverse" if reverse else "exact_surface_signature"] += 1
            continue

        direct = _signature(request, new_labels, False)
        reverse = _signature(request, new_labels, True)
        candidates = []
        for old_id in direct_index.get(direct, []):
            candidates.append((old_id, False, "semantic_signature"))
        for old_id in reverse_index.get(reverse, []):
            candidates.append((old_id, True, "semantic_signature_reverse"))

        # Claim text is the most stable signal when alias merging changed the
        # endpoint labels or IDs. Keep it only when the candidate is unique.
        if not candidates:
            claim = _claim(request)
            if claim:
                claim_candidates = list(dict.fromkeys(claim_index.get(claim, [])))
                if len(claim_candidates) == 1:
                    old_id = claim_candidates[0]
                    old_request = old_request_by_id[old_id]
                    old_pair = _pair_signature(old_request, old_labels)
                    new_pair = _pair_signature(request, new_labels)
                    candidates.append((old_id, old_pair != new_pair and tuple(reversed(old_pair)) == new_pair, "claim_text"))

        if not candidates:
            claim = _claim(request)
            windows = list(request.get("evidence_windows") or [])
            best = request.get("best_evidence_window")
            if isinstance(best, dict):
                windows.append(best)
            surface_candidates = []
            for window in windows:
                if not isinstance(window, dict):
                    continue
                source_surface = _norm(window.get("source_surface"))
                target_surface = _norm(window.get("target_surface"))
                if not source_surface or not target_surface:
                    continue
                for old_id in surface_claim_index.get((claim, source_surface, target_surface), []):
                    surface_candidates.append((old_id, False, "claim_surface"))
                for old_id in surface_claim_index.get((claim, target_surface, source_surface), []):
                    surface_candidates.append((old_id, True, "claim_surface_reverse"))
            surface_candidates = list(dict.fromkeys(surface_candidates))
            if len({item[0] for item in surface_candidates}) == 1:
                candidates.extend(surface_candidates)

        if not candidates:
            new_pair = _pair_signature(request, new_labels)
            pair_candidates = list(dict.fromkeys(pair_index.get(new_pair, [])))
            if len(pair_candidates) == 1:
                candidates.append((pair_candidates[0], False, "endpoint_pair"))

        unique = {(old_id, reverse, method) for old_id, reverse, method in candidates}
        # If endpoint aliases collapse to the same normalized surface, the
        # forward and reverse signatures are identical.  This is still one
        # saved edge, not two competing verdicts; prefer the original forward
        # orientation unless an explicit reverse signature was the only match.
        by_old = defaultdict(list)
        for candidate in unique:
            by_old[candidate[0]].append(candidate)
        if len(by_old) == 1:
            same_old = next(iter(by_old.values()))
            selected = next((item for item in same_old if not item[1]), same_old[0])
            old_id, reverse, method = selected
            mapped.append(_remap_one(verdict_by_id[old_id], new_id, old_id, reverse))
            methods[method] += 1
        else:
            unmatched.append({
                "new_edge_id": new_id,
                "candidate_count": len(unique),
                "candidates": sorted(unique),
                "claim": request.get("claim_text") or request.get("description") or "",
            })

    report = {
        "old_requests": len(old_requests),
        "old_verdicts": len(old_verdicts),
        "new_requests": len(new_requests),
        "mapped_verdicts": len(mapped),
        "unmapped_new_requests": len(unmatched),
        "mapping_methods": dict(sorted(methods.items())),
        "unmapped": unmatched,
        "protocol": "saved verifier verdict replay by edge id, surface signature, semantic signature, claim text, claim+surface, or endpoint pair",
    }
    return mapped, report


def _load_manifest(corpus: str):
    path = ROOT / "gold_gen_text" / "output_gen5" / corpus / "seed1" / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    aliases = {}
    for document in data.get("docs", []):
        for item in document.get("aliases", []) or []:
            if item.get("surface") and item.get("canonical"):
                aliases[str(item["surface"]).lower().strip()] = str(item["canonical"])
    gold_edges = {tuple(edge) for edge in data.get("gold_edges", []) if len(edge) == 2}
    return list(data.get("gold_nodes", [])), aliases, gold_edges


def _score_run(run_dir: Path, corpus: str, lang7_dir: Path | None = None) -> dict:
    gold_nodes, aliases, gold_edges = _load_manifest(corpus)
    raw_candidates = sorted((Path(lang7_dir) if lang7_dir else run_dir / "lang7").glob("*.gml"))
    raw_gml = raw_candidates[-1] if raw_candidates else None
    views = evaluate_unified_views(
        run_dir / "artifact.json",
        run_dir / "artifact.pre_verification.json",
        gold_nodes,
        aliases,
        gold_edges,
        raw_gml_path=raw_gml,
    )
    return {
        "views": views,
        "raw_gml": str(raw_gml) if raw_gml else None,
        "gold_nodes": len(gold_nodes),
        "gold_edges": len(gold_edges),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Replay saved verifier verdicts into D2C_13_2 without API calls")
    parser.add_argument("--source-exp-root", default=str(ROOT / "nova_exp" / "exp17"))
    parser.add_argument("--out-root", default=str(ROOT / "nova_exp" / "exp17_d2c13_2_verified_replay"))
    parser.add_argument("--config", default=str(ROOT / "D2C_13_2" / "config.json"))
    parser.add_argument("--corpus", choices=["water", "win95pts", "both"], default="both")
    parser.add_argument("--only-run", default=None)
    parser.add_argument("--only-runs", default=None, help="comma-separated run names, e.g. run_01,run_02")
    parser.add_argument("--summary-name", default="replay_summary.json")
    parser.add_argument("--verification-policy", choices=["advisory", "balanced", "strict"], default="balanced")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    source_root = Path(args.source_exp_root).resolve()
    out_root = Path(args.out_root).resolve()
    selected_runs = {item.strip() for item in (args.only_runs or "").split(",") if item.strip()}
    corpora = ["water", "win95pts"] if args.corpus == "both" else [args.corpus]
    out_root.mkdir(parents=True, exist_ok=True)
    summary = {"runs": [], "corpora": corpora, "protocol": "saved verdict replay; no extraction or verifier API calls"}

    for corpus in corpora:
        corpus_root = source_root / corpus
        for source_run in sorted(corpus_root.glob("run_*")):
            if args.only_run and source_run.name != args.only_run:
                continue
            if selected_runs and source_run.name not in selected_runs:
                continue
            lang7_dir = source_run / "lang7"
            old_requests_path = source_run / "verification_requests.input.json"
            old_results_path = source_run / "verification_results.json"
            old_artifact_path = source_run / "artifact.json"
            if not lang7_dir.exists() or not old_requests_path.exists() or not old_results_path.exists():
                print(f"SKIP {corpus}/{source_run.name}: missing lang7 or saved verifier artifacts")
                continue
            out_run = out_root / corpus / source_run.name
            if out_run.exists() and not args.resume:
                print(f"SKIP {corpus}/{source_run.name}: output exists (use --resume)")
                continue

            old_requests = read_json(old_requests_path, []) or []
            old_results = read_json(old_results_path, []) or []
            old_artifact = read_json(old_artifact_path, {}) or {}

            def adapter(new_artifact: dict, *, _run=out_run) -> list[dict]:
                new_requests = build_verification_requests(new_artifact)
                mapped, report = remap_verdicts(
                    old_requests, old_results, new_requests, old_artifact, new_artifact,
                )
                write_json(_run / "verdict_replay.json", mapped)
                write_json(_run / "verdict_replay_report.json", report)
                return mapped

            summary_run = enhance_lang7_run(
                lang7_dir,
                out_run,
                docs_dir=ROOT / "data" / "Test-text" / "input" / "goldgen_gen5" / corpus,
                corpus=corpus,
                config_path=args.config,
                verify=False,
                verification_policy=args.verification_policy,
                verdicts_adapter=adapter,
            )
            score = _score_run(out_run, corpus, lang7_dir=lang7_dir)
            write_json(out_run / "score.json", score)
            report = read_json(out_run / "verdict_replay_report.json", {}) or {}
            summary["runs"].append({
                "corpus": corpus,
                "run": source_run.name,
                "mapped": report.get("mapped_verdicts"),
                "new_requests": report.get("new_requests"),
                "unmapped": report.get("unmapped_new_requests"),
                "validation_valid": summary_run.get("validation_valid"),
                "shacl_conforms": summary_run.get("shacl_conforms"),
                "roundtrip": {
                    "json": summary_run.get("roundtrip_json"),
                    "rdf": summary_run.get("roundtrip_rdf"),
                    "edge_semantics": summary_run.get("roundtrip_edge_semantics"),
                    "entity_identity": summary_run.get("roundtrip_entity_identity"),
                    "queries": summary_run.get("roundtrip_queries"),
                },
                "score": score["views"],
            })
            print(f"DONE {corpus}/{source_run.name}: mapped={report.get('mapped_verdicts')}/{report.get('new_requests')}")

    write_json(out_root / args.summary_name, summary)
    print(json.dumps({"out_root": str(out_root), "runs": len(summary["runs"])}, ensure_ascii=False, indent=2))
    return 0 if all(row.get("validation_valid") for row in summary["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
