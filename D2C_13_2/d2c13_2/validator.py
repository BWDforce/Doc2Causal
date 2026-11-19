# -*- coding: utf-8 -*-
"""D2C_13_2 structural and SHACL validation."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import networkx as nx

from .constants import CANDIDATE_TIERS, CLAIM_SCOPES, CONSISTENCY_STATUSES, EDGE_KINDS, EVIDENCE_COMPLETENESS, EVIDENCE_GRADES, GROUNDING_STATUSES, SUPPORT_SCOPES, VERIFICATION_STATUSES, VERSION, WINDOW_KINDS
from .semantics import entity_similarity_candidates
from .util import as_text, truthy


def _err(report: dict, code: str, message: str, subject: str | None = None) -> None:
    report["errors"].append({"code": code, "message": message, "subject": subject})


def _warn(report: dict, code: str, message: str, subject: str | None = None) -> None:
    report["warnings"].append({"code": code, "message": message, "subject": subject})


def validate_artifact(artifact: dict, run_shacl: bool = True) -> dict:
    report: dict[str, Any] = {
        "version": VERSION,
        "artifact_id": artifact.get("artifact_id"),
        "valid": True,
        "errors": [],
        "warnings": [],
        "counts": {},
        "shacl": None,
    }
    if artifact.get("version") != VERSION:
        _err(report, "VERSION_MISMATCH", f"expected {VERSION}, got {artifact.get('version')}")
    entities = {e.get("id") for e in artifact.get("entities", [])}
    assertions = {a.get("id") for a in artifact.get("assertions", [])}
    spans = {s.get("id") for s in artifact.get("spans", [])}
    conditions = {c.get("id") for c in artifact.get("conditions", [])}
    edges = artifact.get("edges", [])
    edge_ids = [e.get("id") for e in edges]
    duplicates = {eid for eid, count in defaultdict(int, [(x, edge_ids.count(x)) for x in set(edge_ids)]).items() if count > 1}
    for eid in sorted(duplicates):
        _err(report, "DUPLICATE_EDGE_ID", "edge id appears more than once", eid)

    core_graph = nx.DiGraph()
    for edge in edges:
        eid = edge.get("id")
        kind = edge.get("kind")
        if kind not in EDGE_KINDS:
            _err(report, "EDGE_KIND_INVALID", f"unknown edge kind: {kind}", eid)
        if edge.get("claim_scope") is not None and edge.get("claim_scope") not in CLAIM_SCOPES:
            _err(report, "CLAIM_SCOPE_INVALID", f"unknown claim scope: {edge.get('claim_scope')}", eid)
        if edge.get("evidence_grade") is not None and edge.get("evidence_grade") not in EVIDENCE_GRADES:
            _err(report, "EVIDENCE_GRADE_INVALID", f"unknown evidence grade: {edge.get('evidence_grade')}", eid)
        if edge.get("verification_status") is not None and edge.get("verification_status") not in VERIFICATION_STATUSES:
            _err(report, "VERIFICATION_STATUS_INVALID", f"unknown verification status: {edge.get('verification_status')}", eid)
        if edge.get("candidate_tier") is not None and edge.get("candidate_tier") not in CANDIDATE_TIERS:
            _err(report, "CANDIDATE_TIER_INVALID", f"unknown candidate tier: {edge.get('candidate_tier')}", eid)
        if edge.get("condition_grounding") is not None and edge.get("condition_grounding") not in GROUNDING_STATUSES:
            _err(report, "CONDITION_GROUNDING_INVALID", f"unknown condition grounding: {edge.get('condition_grounding')}", eid)

        for field_name in ("support_scope", "verifier_support_scope", "pre_verification_support_scope"):
            value = edge.get(field_name)
            if value is not None and value not in SUPPORT_SCOPES:
                _err(report, "SUPPORT_SCOPE_INVALID", f"unknown {field_name}: {value}", eid)
        for field_name in ("evidence_completeness", "pre_verification_evidence_completeness"):
            value = edge.get(field_name)
            if value is not None and value not in EVIDENCE_COMPLETENESS:
                _err(report, "EVIDENCE_COMPLETENESS_INVALID", f"unknown {field_name}: {value}", eid)
        for field_name in ("best_window_kind", "pre_verification_best_window_kind"):
            value = edge.get(field_name)
            if value is not None and value not in WINDOW_KINDS:
                _err(report, "WINDOW_KIND_INVALID", f"unknown {field_name}: {value}", eid)
        for field_name in ("temporal_consistency", "fraction_consistency"):
            value = edge.get(field_name)
            if value is not None and value not in CONSISTENCY_STATUSES:
                _err(report, "CONSISTENCY_STATUS_INVALID", f"unknown {field_name}: {value}", eid)

        contract_complete = edge.get("evidence_contract_complete")
        contract = edge.get("evidence_contract")
        if contract_complete is not None and not isinstance(contract_complete, bool):
            _err(report, "EVIDENCE_CONTRACT_TYPE_INVALID", "evidence_contract_complete must be boolean", eid)
        if contract is not None and not isinstance(contract, dict):
            _err(report, "EVIDENCE_CONTRACT_INVALID", "evidence_contract must be an object", eid)
        elif isinstance(contract, dict) and contract:
            if contract_complete is None:
                _err(report, "EVIDENCE_CONTRACT_COMPLETE_MISSING", "evidence_contract_complete is required when evidence_contract exists", eid)
            nested_complete = contract.get("complete")
            if not isinstance(nested_complete, bool):
                _err(report, "EVIDENCE_CONTRACT_TYPE_INVALID", "evidence_contract.complete must be boolean", eid)
            elif isinstance(contract_complete, bool) and contract_complete != nested_complete:
                _err(report, "EVIDENCE_CONTRACT_BOOLEAN_MISMATCH", "edge and nested contract completeness disagree", eid)
            components = contract.get("components")
            if components is not None and not isinstance(components, dict):
                _err(report, "EVIDENCE_CONTRACT_INVALID", "evidence_contract.components must be an object", eid)
            for field_name in ("temporal_consistency", "fraction_consistency"):
                value = contract.get(field_name)
                if value is not None and value not in CONSISTENCY_STATUSES:
                    _err(report, "EVIDENCE_CONTRACT_CONSISTENCY_INVALID", f"unknown contract {field_name}: {value}", eid)
            text_complete = contract.get("text_complete")
            path_complete = contract.get("path_complete")
            if text_complete is not None and not isinstance(text_complete, bool):
                _err(report, "EVIDENCE_CONTRACT_TYPE_INVALID", "evidence_contract.text_complete must be boolean", eid)
            if path_complete is not None and not isinstance(path_complete, bool):
                _err(report, "EVIDENCE_CONTRACT_TYPE_INVALID", "evidence_contract.path_complete must be boolean", eid)
            if nested_complete is True and text_complete is not True and path_complete is not True:
                _err(report, "EVIDENCE_CONTRACT_INCONSISTENT", "complete contract requires complete text or complete path", eid)
            if text_complete is True and isinstance(components, dict):
                missing = [name for name in ("source", "target", "predicate", "condition") if components.get(name) is not True]
                if missing:
                    _err(report, "EVIDENCE_CONTRACT_COMPONENTS_MISSING", "complete text contract is missing: " + ",".join(missing), eid)
        if edge.get("source") not in entities or edge.get("target") not in entities:
            _err(report, "DANGLING_ENTITY", "edge references an unknown entity", eid)
        for aid in edge.get("support_assertion_ids", []):
            if aid not in assertions:
                _err(report, "DANGLING_ASSERTION", "edge references an unknown assertion", eid)
        for sid in edge.get("span_ids", []):
            if sid not in spans:
                _err(report, "DANGLING_SPAN", "edge references an unknown span", eid)
        if edge.get("condition_id") and edge["condition_id"] not in conditions:
            _err(report, "DANGLING_CONDITION", "edge references an unknown condition", eid)
        if kind == "direct_core":
            if not edge.get("support_assertion_ids"):
                _err(report, "CORE_NO_ASSERTION", "direct_core requires at least one assertion", eid)
            if not edge.get("span_ids"):
                _err(report, "CORE_NO_SPAN", "direct_core requires at least one span", eid)
            if edge.get("source") == edge.get("target"):
                _err(report, "CORE_SELF_LOOP", "direct_core cannot be a self-loop", eid)
            if edge.get("source") in entities and edge.get("target") in entities:
                core_graph.add_edge(edge["source"], edge["target"])
        elif kind == "conditional":
            if not edge.get("condition_id"):
                _err(report, "CONDITIONAL_NO_CONDITION", "conditional edge requires a condition", eid)
        elif kind == "regime":
            if not edge.get("condition_id"):
                _err(report, "REGIME_NO_CONDITION", "regime edge requires a condition", eid)
        elif kind == "transitive_derived":
            if not edge.get("derivation_edge_ids"):
                _err(report, "TRANSITIVE_NO_DERIVATION", "transitive_derived requires a derivation path", eid)
        elif kind == "candidate":
            candidate_ids = (
                edge.get("candidate_assertion_ids")
                or edge.get("candidate_span_ids")
                or edge.get("candidate_doc_ids")
                or edge.get("candidate_derivation_ids")
            )
            if not candidate_ids:
                _err(report, "CANDIDATE_NO_EVIDENCE", "candidate requires at least one candidate_* evidence field", eid)
            if edge.get("support_assertion_ids") or edge.get("support_doc_ids") or edge.get("span_ids"):
                _err(report, "CANDIDATE_HAS_AUTHORITY", "candidate must not carry documentary authority", eid)
        elif kind == "unsupported_hypothesis":
            if (edge.get("support_assertion_ids") or edge.get("support_doc_ids") or edge.get("span_ids")
                    or edge.get("candidate_assertion_ids") or edge.get("candidate_span_ids") or edge.get("candidate_doc_ids")
                    or edge.get("candidate_derivation_ids")):
                _err(report, "UNSUPPORTED_HYPOTHESIS_HAS_EVIDENCE", "unsupported_hypothesis must not carry candidate authority", eid)
        elif kind == "hypothesis":
            # Legacy import compatibility. New D2C_13_2 output uses candidate or
            # unsupported_hypothesis.
            if edge.get("support_doc_ids") or edge.get("span_ids"):
                _err(report, "HYPOTHESIS_HAS_DOCUMENT_AUTHORITY", "legacy hypothesis must not carry document support/span authority", eid)
        elif kind == "rejected":
            pass
    unverified = sum(1 for e in edges if e.get("kind") == "direct_core" and e.get("semantic_support") == "untested")
    if unverified:
        _warn(report, "CORE_UNVERIFIED_SUPPORT", f"{unverified} direct_core edge(s) have not passed semantic verification")
    if not nx.is_directed_acyclic_graph(core_graph):
        _err(report, "CORE_CYCLE", "direct_core graph contains a directed cycle")
    warnings = entity_similarity_candidates(artifact)
    if warnings:
        report["warnings"].append({
            "code": "ENTITY_SIMILARITY_CANDIDATES",
            "message": f"{len(warnings)} near-duplicate entity pair(s) need adjudication",
            "subjects": warnings[:20],
        })
    report["counts"] = {
        "documents": len(artifact.get("documents", [])),
        "entities": len(entities),
        "spans": len(spans),
        "assertions": len(assertions),
        "edges": len(edges),
        "edge_kinds": {kind: sum(1 for e in edges if e.get("kind") == kind) for kind in EDGE_KINDS},
        "evidence_grades": {
            grade: sum(1 for e in edges if e.get("evidence_grade") == grade)
            for grade in EVIDENCE_GRADES
        },
        "verification_statuses": {
            status: sum(1 for e in edges if e.get("verification_status") == status)
            for status in VERIFICATION_STATUSES
        },
        "evidence_completeness": {
            value: sum(1 for e in edges if e.get("evidence_completeness") == value)
            for value in EVIDENCE_COMPLETENESS
        },
    }
    if run_shacl:
        try:
            from .rdfio import validate_shacl
            shacl = validate_shacl(artifact)
            report["shacl"] = shacl
            if not shacl.get("conforms", False):
                _err(report, "SHACL_NONCONFORMANT", "D2C_13_2 SHACL validation reported violations")
        except Exception as exc:  # noqa: BLE001 - report missing optional interoperability stack
            report["shacl"] = {"conforms": None, "error": f"{type(exc).__name__}: {exc}"}
            _warn(report, "SHACL_UNAVAILABLE", f"SHACL validation could not run: {type(exc).__name__}: {exc}")
    report["valid"] = not report["errors"]
    return report


