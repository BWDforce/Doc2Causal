# -*- coding: utf-8 -*-
"""JSON-LD, RDF/Turtle, SHACL and semantic round-trip helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, Namespace, URIRef

from .constants import ACC_NS, CONTEXT_PATH, EDGE_CLASS, SHACL_PATH, VERSION
from .util import canonical_json, read_json, stable_urn, write_json

ACC = Namespace(ACC_NS)


def _ref(value: str | None) -> dict | None:
    return {"id": value} if value else None


def _refs(values: list[str]) -> list[dict]:
    return [{"id": value} for value in values if value]


def _bool_value(value) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _json_object(value, default):
    if value is None or str(value) == "":
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _pre_verification_evidence_json(edge: dict) -> str:
    payload = {
        key: value
        for key, value in edge.items()
        if key.startswith("pre_verification_")
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload else ""

def artifact_to_jsonld(artifact: dict) -> dict:
    context = read_json(CONTEXT_PATH, {})
    graph: list[dict] = []
    activity_id = stable_urn("activity", artifact["artifact_id"])
    graph.append({
        "id": artifact["artifact_id"],
        "type": ["Artifact", "prov:Entity"],
        "version": VERSION,
        "createdAt": artifact.get("created_at", ""),
        "sourceRun": artifact.get("source", {}).get("run_dir", ""),
        "hasEntity": _refs([e["id"] for e in artifact.get("entities", [])]),
        "hasEdge": _refs([e["id"] for e in artifact.get("edges", [])]),
        "hasAssertion": _refs([a["id"] for a in artifact.get("assertions", [])]),
        "hasSpan": _refs([s["id"] for s in artifact.get("spans", [])]),
        "prov:wasGeneratedBy": _ref(activity_id),
        "prov:generatedAtTime": artifact.get("created_at", ""),
    })
    graph.append({
        "id": activity_id,
        "type": ["prov:Activity", "ArtifactCompilationActivity"],
        "prov:startedAtTime": artifact.get("created_at", ""),
        "prov:endedAtTime": artifact.get("created_at", ""),
        "rdfs:label": "D2C_13_2 artifact compilation and verification",
    })
    for document in artifact.get("documents", []):
        graph.append({
            "id": document["id"], "type": ["Document", "prov:Entity"],
            "docId": document.get("doc_id", ""), "title": document.get("title", ""),
            "sourceFile": document.get("source_file", ""),
            "prov:wasGeneratedBy": _ref(activity_id),
        })
    for span in artifact.get("spans", []):
        graph.append({
            "id": span["id"], "type": ["Span", "prov:Entity"], "docId": span.get("doc_id", ""),
            "start": int(span.get("start", 0)), "end": int(span.get("end", 0)),
            "text": span.get("text", ""),
            "textHash": span.get("text_hash", ""),
            "contextText": span.get("context", ""),
            "ofDocument": _ref(span.get("document_id")),
            "prov:wasGeneratedBy": _ref(activity_id),
        })
    for entity in artifact.get("entities", []):
        graph.append({
            "id": entity["id"], "type": ["Entity", "prov:Entity"], "label": entity.get("label", ""),
            "description": entity.get("description", ""), "entityType": entity.get("entity_type", ""),
            "alias": list(entity.get("aliases") or []),
            "contextSignature": entity.get("context_signature", ""),
            "timeIndex": list(entity.get("time_indices") or []),
            "stateNegation": bool((entity.get("state_signature") or {}).get("negation", False)),
            "stateTerm": list((entity.get("state_signature") or {}).get("state_terms") or []),
            "identityKey": entity.get("identity_key", ""),
            "prov:wasGeneratedBy": _ref(activity_id),
        })
    for condition in artifact.get("conditions", []):
        graph.append({
            "id": condition["id"], "type": ["Condition", "prov:Entity"], "raw": condition.get("raw", ""),
            "normalized": condition.get("normalized", ""),
            "equivalenceKey": condition.get("equivalence_key", ""),
            "conditionKind": condition.get("condition_kind", ""),
            "prov:wasGeneratedBy": _ref(activity_id),
        })
    for assertion in artifact.get("assertions", []):
        graph.append({
            "id": assertion["id"], "type": ["Assertion", "prov:Entity"],
            "source": _ref(assertion.get("source")), "target": _ref(assertion.get("target")),
            "docId": assertion.get("doc_id", ""), "channel": assertion.get("channel", ""),
            "order": assertion.get("order"),
            "ofSpan": _ref(assertion.get("span_id")),
            "sign": assertion.get("sign", ""), "rung": assertion.get("rung", ""),
            "mechanism": assertion.get("mechanism", ""),
            "prov:wasGeneratedBy": _ref(activity_id),
        })
    doc_uri_by_id = {d.get("doc_id"): d.get("id") for d in artifact.get("documents", [])}
    for edge in artifact.get("edges", []):
        kind = edge.get("kind", "direct_core")
        graph.append({
            "id": edge["id"], "type": ["RetainedEdge", EDGE_CLASS.get(kind, "RetainedEdge"), "prov:Entity"],
            "edgeKind": kind,
            "prov:wasGeneratedBy": _ref(activity_id),
            "source": _ref(edge.get("source")), "target": _ref(edge.get("target")),
            "hasAssertion": _refs(edge.get("support_assertion_ids") or []),
            "hasSpan": _refs(edge.get("span_ids") or []),
            "hasDocumentSupport": _refs([doc_uri_by_id.get(d, stable_urn("document", d)) for d in edge.get("support_doc_ids", [])]),
            "candidateAssertion": _refs(edge.get("candidate_assertion_ids") or []),
            "candidateSpan": _refs(edge.get("candidate_span_ids") or []),
            "candidateDocument": _refs([doc_uri_by_id.get(d, stable_urn("document", d)) for d in edge.get("candidate_doc_ids", [])]),
            "candidateDerivation": _refs(edge.get("candidate_derivation_ids") or []),
            "condition": _ref(edge.get("condition_id")),
            "derivedFrom": _refs(edge.get("derivation_edge_ids") or []),
            "prov:wasDerivedFrom": _refs(edge.get("support_assertion_ids") or edge.get("candidate_assertion_ids") or []),
            "verificationStatus": edge.get("semantic_support", "untested"),
            "directionStatus": edge.get("direction_status", "unverified"),
            "directionAudit": edge.get("direction_audit", ""),
            "directionSupportForward": edge.get("direction_support_forward", 0),
            "directionSupportReverse": edge.get("direction_support_reverse", 0),
            "relationRoleSource": edge.get("relation_role_source", "unknown"),
            "relationRoleTarget": edge.get("relation_role_target", "unknown"),
            "roleDirection": edge.get("role_direction", "abstain"),
            "roleConflict": bool(edge.get("role_conflict", False)),
            "roleOverride": bool(edge.get("role_override", False)),
            "textualVoteSource": edge.get("textual_vote_source", ""),
            "textualSpanForwardVotes": int(edge.get("textual_span_forward_votes", 0) or 0),
            "textualSpanReverseVotes": int(edge.get("textual_span_reverse_votes", 0) or 0),
            "textualAttributeForwardVotes": int(edge.get("textual_attribute_forward_votes", 0) or 0),
            "textualAttributeReverseVotes": int(edge.get("textual_attribute_reverse_votes", 0) or 0),
            "temporalMode": edge.get("temporal_mode", "unknown"),
            "temporalAbstained": bool(edge.get("temporal_abstained", False)),
            "directnessClass": edge.get("directness_class", ""),
            "directnessReason": edge.get("directness_reason", ""),
            "shortcutRisk": edge.get("shortcut_risk", "low"),
            "shortcutPathEdgeId": list(edge.get("shortcut_path_edge_ids") or []),
            "polarityStatus": edge.get("polarity_status", ""),
            "identityStatus": edge.get("identity_status", "ok"),
            "conditionStatus": edge.get("condition_status", ""),
            "provisionalRecovery": bool(edge.get("provisional_recovery", False)),
            "assertionRecovery": bool(edge.get("assertion_recovery", False)),
            "supportAwareEligible": bool(edge.get("support_aware_eligible", False)),
            "supportAwareStatus": edge.get("support_aware_status", "excluded"),
            "supportAwareReason": edge.get("support_aware_reason", ""),
            "pathSummary": bool(edge.get("path_summary", False)),
            "pathSummaryExplicitDirect": bool(edge.get("path_summary_explicit_direct", False)),
            "supportMode": edge.get("support_mode", ""),
            "supportStrength": edge.get("support_strength", ""),
            "claimScope": edge.get("claim_scope", "unknown"),
            "evidenceGrade": edge.get("evidence_grade", "none"),
            "evidenceCompleteness": edge.get("evidence_completeness", ""),
            "supportScope": edge.get("support_scope", ""),
            "conditionGrounding": edge.get("condition_grounding", ""),
            "temporalConsistency": edge.get("temporal_consistency", ""),
            "fractionConsistency": edge.get("fraction_consistency", ""),
            "evidenceCoverageJson": json.dumps(edge.get("evidence_coverage") or {}, ensure_ascii=False, sort_keys=True),
            "missingEvidenceComponent": list(edge.get("missing_evidence_components") or []),
            "evidenceWindowsJson": json.dumps(edge.get("evidence_windows") or [], ensure_ascii=False, sort_keys=True),
            "evidenceWindowCount": int(edge.get("evidence_window_count", 0) or 0),
            "bestEvidenceWindowId": edge.get("best_evidence_window_id") or "",
            "evidencePackagingVersion": edge.get("evidence_packaging_version") or "",
            "bestWindowKind": edge.get("best_window_kind", "none"),
            "bestWindowSource": edge.get("best_window_source", "none"),
            "bestWindowWidth": int(edge.get("best_window_width", 0) or 0),
            "evidenceExactMatch": bool(edge.get("evidence_exact_match", False)),
            "contextWindowComplete": bool(edge.get("context_window_complete", False)),
            "evidenceContractComplete": bool(edge.get("evidence_contract_complete", False)),
            "evidenceContractJson": json.dumps(edge.get("evidence_contract") or {}, ensure_ascii=False, sort_keys=True),
            "evidencePackagingStage": edge.get("evidence_packaging_stage", ""),
            "verifierSupportScope": edge.get("verifier_support_scope", "none"),
            "verifierWindowKind": edge.get("verifier_window_kind", "none"),
            "verifierWindowId": edge.get("verifier_window_id") or "",
            "preVerificationEvidenceJson": _pre_verification_evidence_json(edge),
            "canonicalArbitrationJson": json.dumps({
                "canonical_pair_id": edge.get("canonical_pair_id"),
                "canonical_support_tier": edge.get("canonical_support_tier", "none"),
                "canonical_support_doc_count": int(edge.get("canonical_support_doc_count", 0) or 0),
                "canonical_direction_doc_count": int(edge.get("canonical_direction_doc_count", 0) or 0),
                "canonical_multi_document_support": bool(edge.get("canonical_multi_document_support")),
                "candidate_risk_flags": list(edge.get("candidate_risk_flags") or []),
                "candidate_risk_status": edge.get("candidate_risk_status", "clear"),
                "arbitration_enabled": bool(edge.get("arbitration_enabled", True)),
                "arbitration_eligible": bool(edge.get("arbitration_eligible", True)),
                "arbitration_status": edge.get("arbitration_status", "canonical_safe"),
                "arbitration_reason": edge.get("arbitration_reason", ""),
            }, ensure_ascii=False, sort_keys=True),
            "candidateSupportMode": edge.get("candidate_support_mode", ""),
            "candidateSupportStrength": edge.get("candidate_support_strength", ""),
            "candidateOrigin": edge.get("candidate_origin", ""),
            "candidateTier": edge.get("candidate_tier", "none"),
            "candidateEvidence": _refs(edge.get("candidate_evidence_ids") or []),
            "evidenceUnion": _refs(edge.get("evidence_union_ids") or []),
            "directSupport": edge.get("direct_support", "untested"),
            "assertionSupport": edge.get("assertion_support", "untested"),
            "promotionStatus": edge.get("promotion_status", "none"),
            "softCoreEligible": bool(edge.get("soft_core_eligible", False)),
            "partialForwardPromotion": bool(edge.get("partial_forward_promotion", False)),
            "pathSupport": edge.get("path_support", "untested"),
            "conditionSupport": edge.get("condition_support", "untested"),
            "verificationCoverage": edge.get("verification_coverage", "unknown"),
            "verifierSupport": edge.get("verifier_support", ""),
            "verifierDirection": edge.get("verifier_direction", ""),
            "verifierRecommendedKind": edge.get("verifier_recommended_kind", ""),
            "verificationState": edge.get("verification_status", ""),
            "confidence": edge.get("confidence", "unknown"),
            "provenance": edge.get("provenance", ""),
            "mechanism": edge.get("attributes", {}).get("mechanism", ""),
        })
    for vote in artifact.get("votes", []):
        graph.append({
            "id": vote["id"], "type": ["Vote", "prov:Entity"], "direction": vote.get("direction", ""),
            "assertions": int(vote.get("assertions", 0)), "documents": int(vote.get("documents", 0)),
            "rule": vote.get("rule", ""), "pair": _refs(vote.get("pair") or []),
            "supportDocument": list(vote.get("support_doc_ids") or []),
            "oppositionDocument": list(vote.get("opposition_doc_ids") or []),
            "uncertainDocument": list(vote.get("uncertain_doc_ids") or []),
            "forwardDocument": list(vote.get("forward_doc_ids") or []),
            "reverseDocument": list(vote.get("reverse_doc_ids") or []),
            "supportVoteCount": int(vote.get("support_votes", 0) or 0),
            "oppositionVoteCount": int(vote.get("opposition_votes", 0) or 0),
            "directionRule": vote.get("direction_rule") or vote.get("rule", ""),
            "referenceOrientation": vote.get("reference_orientation", "forward"),
            "voteEdgeId": _refs(vote.get("edge_ids") or []),
            "voteSourceLabel": vote.get("source_label", ""),
            "voteTargetLabel": vote.get("target_label", ""),
            "docVoteDetailJson": json.dumps(vote.get("doc_vote_detail") or {}, ensure_ascii=False, sort_keys=True),
            "provenance": vote.get("provenance", ""),
            "prov:wasGeneratedBy": _ref(activity_id),
        })
    for decision in artifact.get("conflict_decisions", []):
        graph.append({
            "id": decision["id"], "type": ["ConflictDecision", "prov:Entity"],
            "conflictKind": decision.get("kind", ""), "selected": _ref(decision.get("selected")),
            "dropped": _ref(decision.get("dropped")), "rule": decision.get("rule", ""),
            "pair": _refs(decision.get("pair") or []),
            "decisionEdgeId": _refs(decision.get("edge_ids") or []),
            "prov:wasGeneratedBy": _ref(activity_id),
        })
    for decision in artifact.get("cycle_decisions", []):
        graph.append({
            "id": decision["id"], "type": ["CycleDecision", "prov:Entity"],
            "classification": decision.get("classification", ""),
            "regimeDependent": bool(decision.get("regime_dependent")),
            "reason": decision.get("reason", ""),
            "pair": _refs(decision.get("pair") or []),
            "decisionEdgeId": _refs(decision.get("edge_ids") or []),
            "removedEdge": decision.get("removed_edge") or "",
            "prov:wasGeneratedBy": _ref(activity_id),
        })
    return {"@context": context.get("@context", context), "@graph": graph}


def _graph_from_jsonld(artifact: dict) -> Graph:
    graph = Graph()
    graph.parse(data=json.dumps(artifact_to_jsonld(artifact), ensure_ascii=False), format="json-ld")
    return graph


def validate_shacl(artifact: dict) -> dict:
    from pyshacl import validate
    graph = _graph_from_jsonld(artifact)
    conforms, results_graph, results_text = validate(
        graph,
        shacl_graph=str(SHACL_PATH),
        inference="none",
        abort_on_first=False,
        allow_warnings=False,
        serialize_report_graph=False,
    )
    return {
        "conforms": bool(conforms),
        "results_text": results_text,
        "violations": 0 if conforms else len(results_text.splitlines()),
    }


def write_jsonld(artifact: dict, path: str | Path) -> None:
    payload = artifact_to_jsonld(artifact)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_turtle(artifact: dict, path: str | Path) -> None:
    graph = _graph_from_jsonld(artifact)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(path), format="turtle")


def artifact_from_rdf(path: str | Path) -> dict:
    graph = Graph()
    graph.parse(str(path), format=None)
    entities = []
    for subject in graph.subjects(URIRef(f"{ACC_NS}Entity"), None):
        pass
    # rdflib requires rdf:type triples; use explicit RDF namespace.
    from rdflib.namespace import RDF, RDFS
    for subject in graph.subjects(RDF.type, ACC.Entity):
        entities.append({
            "id": str(subject), "label": str(graph.value(subject, ACC.label) or ""),
            "description": str(graph.value(subject, ACC.description) or ""),
            "entity_type": str(graph.value(subject, ACC.entityType) or ""),
            "aliases": [str(x) for x in graph.objects(subject, ACC.alias)],
            "context_signature": str(graph.value(subject, ACC.contextSignature) or ""),
            "time_indices": [str(x) for x in graph.objects(subject, ACC.timeIndex)],
            "state_signature": {
                "negation": bool(graph.value(subject, ACC.stateNegation) or False),
                "state_terms": sorted(str(x) for x in graph.objects(subject, ACC.stateTerm)),
            },
            "identity_key": str(graph.value(subject, ACC.identityKey) or ""),
        })
    doc_id_by_uri = {}
    for subject in graph.subjects(RDF.type, ACC.Document):
        doc_id_by_uri[str(subject)] = str(graph.value(subject, ACC.docId) or "")
    spans = []
    for subject in graph.subjects(RDF.type, ACC.Span):
        spans.append({
            "id": str(subject), "doc_id": str(graph.value(subject, ACC.docId) or ""),
            "start": int(graph.value(subject, ACC.start) or 0), "end": int(graph.value(subject, ACC.end) or 0),
            "text": str(graph.value(subject, ACC.text) or ""),
            "text_hash": str(graph.value(subject, ACC.textHash) or ""),
            "context": str(graph.value(subject, ACC.contextText) or ""),
            "document_id": str(graph.value(subject, ACC.ofDocument) or ""),
        })
    conditions = []
    for subject in graph.subjects(RDF.type, ACC.Condition):
        conditions.append({
            "id": str(subject), "raw": str(graph.value(subject, ACC.raw) or ""),
            "normalized": str(graph.value(subject, ACC.normalized) or ""),
            "equivalence_key": str(graph.value(subject, ACC.equivalenceKey) or ""),
            "condition_kind": str(graph.value(subject, ACC.conditionKind) or ""),
        })
    assertions = []
    for subject in graph.subjects(RDF.type, ACC.Assertion):
        assertions.append({
            "id": str(subject), "source": str(graph.value(subject, ACC.source) or ""),
            "target": str(graph.value(subject, ACC.target) or ""),
            "doc_id": str(graph.value(subject, ACC.docId) or ""),
            "channel": str(graph.value(subject, ACC.channel) or ""),
            "span_id": str(graph.value(subject, ACC.ofSpan) or "") or None,
            "sign": str(graph.value(subject, ACC.sign) or ""),
            "rung": str(graph.value(subject, ACC.rung) or ""),
            "mechanism": str(graph.value(subject, ACC.mechanism) or ""),
        })
    edges = []
    for subject in graph.subjects(RDF.type, ACC.RetainedEdge):
        kind = str(graph.value(subject, ACC.edgeKind) or "direct_core")
        edges.append({
            "id": str(subject), "source": str(graph.value(subject, ACC.source) or ""),
            "target": str(graph.value(subject, ACC.target) or ""), "kind": kind,
            "edge_kind": kind,
            "support_assertion_ids": [str(x) for x in graph.objects(subject, ACC.hasAssertion)],
            "span_ids": [str(x) for x in graph.objects(subject, ACC.hasSpan)],
            "support_doc_ids": [doc_id_by_uri.get(str(x), str(x)) for x in graph.objects(subject, ACC.hasDocumentSupport)],
            "candidate_assertion_ids": [str(x) for x in graph.objects(subject, ACC.candidateAssertion)],
            "candidate_span_ids": [str(x) for x in graph.objects(subject, ACC.candidateSpan)],
            "candidate_doc_ids": [doc_id_by_uri.get(str(x), str(x)) for x in graph.objects(subject, ACC.candidateDocument)],
            "candidate_derivation_ids": [str(x) for x in graph.objects(subject, ACC.candidateDerivation)],
            "condition_id": str(graph.value(subject, ACC.condition) or ""),
            "derivation_edge_ids": [str(x) for x in graph.objects(subject, ACC.derivedFrom)],
            "semantic_support": str(graph.value(subject, ACC.verificationStatus) or "untested"),
            "direction_status": str(graph.value(subject, ACC.directionStatus) or "unverified"),
            "direction_audit": str(graph.value(subject, ACC.directionAudit) or ""),
            "direction_support_forward": str(graph.value(subject, ACC.directionSupportForward) or "0"),
            "direction_support_reverse": str(graph.value(subject, ACC.directionSupportReverse) or "0"),
            "relation_role_source": str(graph.value(subject, ACC.relationRoleSource) or "unknown"),
            "relation_role_target": str(graph.value(subject, ACC.relationRoleTarget) or "unknown"),
            "role_direction": str(graph.value(subject, ACC.roleDirection) or "abstain"),
            "role_conflict": bool(graph.value(subject, ACC.roleConflict) or False),
            "role_override": bool(graph.value(subject, ACC.roleOverride) or False),
            "textual_vote_source": str(graph.value(subject, ACC.textualVoteSource) or ""),
            "textual_span_forward_votes": int(graph.value(subject, ACC.textualSpanForwardVotes) or 0),
            "textual_span_reverse_votes": int(graph.value(subject, ACC.textualSpanReverseVotes) or 0),
            "textual_attribute_forward_votes": int(graph.value(subject, ACC.textualAttributeForwardVotes) or 0),
            "textual_attribute_reverse_votes": int(graph.value(subject, ACC.textualAttributeReverseVotes) or 0),
            "temporal_mode": str(graph.value(subject, ACC.temporalMode) or "unknown"),
            "temporal_abstained": bool(graph.value(subject, ACC.temporalAbstained) or False),
            "directness_class": str(graph.value(subject, ACC.directnessClass) or ""),
            "directness_reason": str(graph.value(subject, ACC.directnessReason) or ""),
            "shortcut_risk": str(graph.value(subject, ACC.shortcutRisk) or "low"),
            "shortcut_path_edge_ids": [str(x) for x in graph.objects(subject, ACC.shortcutPathEdgeId)],
            "polarity_status": str(graph.value(subject, ACC.polarityStatus) or ""),
            "identity_status": str(graph.value(subject, ACC.identityStatus) or "ok"),
            "condition_status": str(graph.value(subject, ACC.conditionStatus) or ""),
            "provisional_recovery": bool(graph.value(subject, ACC.provisionalRecovery) or False),
            "assertion_recovery": bool(graph.value(subject, ACC.assertionRecovery) or False),
            "support_aware_eligible": bool(graph.value(subject, ACC.supportAwareEligible) or False),
            "support_aware_status": str(graph.value(subject, ACC.supportAwareStatus) or "excluded"),
            "support_aware_reason": str(graph.value(subject, ACC.supportAwareReason) or ""),
            "path_summary": bool(graph.value(subject, ACC.pathSummary) or False),
            "path_summary_explicit_direct": bool(graph.value(subject, ACC.pathSummaryExplicitDirect) or False),
            "support_mode": str(graph.value(subject, ACC.supportMode) or ""),
            "support_strength": str(graph.value(subject, ACC.supportStrength) or ""),
            "claim_scope": str(graph.value(subject, ACC.claimScope) or "unknown"),
            "evidence_grade": str(graph.value(subject, ACC.evidenceGrade) or "none"),
            "evidence_completeness": str(graph.value(subject, ACC.evidenceCompleteness) or ""),
            "support_scope": str(graph.value(subject, ACC.supportScope) or ""),
            "condition_grounding": str(graph.value(subject, ACC.conditionGrounding) or ""),
            "temporal_consistency": str(graph.value(subject, ACC.temporalConsistency) or ""),
            "fraction_consistency": str(graph.value(subject, ACC.fractionConsistency) or ""),
            "evidence_coverage": _json_object(graph.value(subject, ACC.evidenceCoverageJson), {}),
            "missing_evidence_components": [str(x) for x in graph.objects(subject, ACC.missingEvidenceComponent)],
            "evidence_windows": _json_object(graph.value(subject, ACC.evidenceWindowsJson), []),
            "evidence_window_count": int(graph.value(subject, ACC.evidenceWindowCount) or 0),
            "best_evidence_window_id": str(graph.value(subject, ACC.bestEvidenceWindowId) or "") or None,
            "evidence_packaging_version": str(graph.value(subject, ACC.evidencePackagingVersion) or ""),
            "best_window_kind": str(graph.value(subject, ACC.bestWindowKind) or "none"),
            "best_window_source": str(graph.value(subject, ACC.bestWindowSource) or "none"),
            "best_window_width": int(graph.value(subject, ACC.bestWindowWidth) or 0),
            "evidence_exact_match": bool(graph.value(subject, ACC.evidenceExactMatch) or False),
            "context_window_complete": bool(graph.value(subject, ACC.contextWindowComplete) or False),
            "evidence_contract_complete": _bool_value(graph.value(subject, ACC.evidenceContractComplete)),
            "evidence_contract": _json_object(graph.value(subject, ACC.evidenceContractJson), {}),
            "evidence_packaging_stage": str(graph.value(subject, ACC.evidencePackagingStage) or ""),
            "verifier_support_scope": str(graph.value(subject, ACC.verifierSupportScope) or "none"),
            "verifier_window_kind": str(graph.value(subject, ACC.verifierWindowKind) or "none"),
            "verifier_window_id": str(graph.value(subject, ACC.verifierWindowId) or "") or None,
            **(_json_object(graph.value(subject, ACC.preVerificationEvidenceJson), {}) or {}),
            **(_json_object(graph.value(subject, ACC.canonicalArbitrationJson), {}) or {}),
            "candidate_support_mode": str(graph.value(subject, ACC.candidateSupportMode) or ""),
            "candidate_support_strength": str(graph.value(subject, ACC.candidateSupportStrength) or ""),
            "candidate_origin": str(graph.value(subject, ACC.candidateOrigin) or ""),
            "candidate_tier": str(graph.value(subject, ACC.candidateTier) or "none"),
            "candidate_evidence_ids": [str(x) for x in graph.objects(subject, ACC.candidateEvidence)],
            "evidence_union_ids": [str(x) for x in graph.objects(subject, ACC.evidenceUnion)],
            "direct_support": str(graph.value(subject, ACC.directSupport) or "untested"),
            "assertion_support": str(graph.value(subject, ACC.assertionSupport) or "untested"),
            "promotion_status": str(graph.value(subject, ACC.promotionStatus) or "none"),
            "soft_core_eligible": bool(graph.value(subject, ACC.softCoreEligible) or False),
            "partial_forward_promotion": bool(graph.value(subject, ACC.partialForwardPromotion) or False),
            "path_support": str(graph.value(subject, ACC.pathSupport) or "untested"),
            "condition_support": str(graph.value(subject, ACC.conditionSupport) or "untested"),
            "verification_coverage": str(graph.value(subject, ACC.verificationCoverage) or "unknown"),
            "verifier_support": None if graph.value(subject, ACC.verifierSupport) is None else str(graph.value(subject, ACC.verifierSupport)),
            "verifier_direction": str(graph.value(subject, ACC.verifierDirection) or ""),
            "verifier_recommended_kind": str(graph.value(subject, ACC.verifierRecommendedKind) or ""),
            "verification_status": str(graph.value(subject, ACC.verificationState) or ""),
            "confidence": str(graph.value(subject, ACC.confidence) or "unknown"),
            "provenance": str(graph.value(subject, ACC.provenance) or ""),
            "attributes": {"mechanism": str(graph.value(subject, ACC.mechanism) or "")},
        })
    documents = []
    for subject in graph.subjects(RDF.type, ACC.Document):
        documents.append({
            "id": str(subject), "doc_id": str(graph.value(subject, ACC.docId) or ""),
            "title": str(graph.value(subject, ACC.title) or ""),
            "source_file": str(graph.value(subject, ACC.sourceFile) or ""),
        })
    votes = []
    for subject in graph.subjects(RDF.type, ACC.Vote):
        detail_raw = str(graph.value(subject, ACC.docVoteDetailJson) or "")
        try:
            detail = json.loads(detail_raw) if detail_raw else {}
        except json.JSONDecodeError:
            detail = {}
        votes.append({
            "id": str(subject), "direction": str(graph.value(subject, ACC.direction) or ""),
            "assertions": int(graph.value(subject, ACC.assertions) or 0),
            "documents": int(graph.value(subject, ACC.documents) or 0),
            "rule": str(graph.value(subject, ACC.rule) or ""),
            "pair": [str(x) for x in graph.objects(subject, ACC.pair)],
            "support_doc_ids": [str(x) for x in graph.objects(subject, ACC.supportDocument)],
            "opposition_doc_ids": [str(x) for x in graph.objects(subject, ACC.oppositionDocument)],
            "uncertain_doc_ids": [str(x) for x in graph.objects(subject, ACC.uncertainDocument)],
            "forward_doc_ids": [str(x) for x in graph.objects(subject, ACC.forwardDocument)],
            "reverse_doc_ids": [str(x) for x in graph.objects(subject, ACC.reverseDocument)],
            "support_votes": int(graph.value(subject, ACC.supportVoteCount) or 0),
            "opposition_votes": int(graph.value(subject, ACC.oppositionVoteCount) or 0),
            "direction_rule": str(graph.value(subject, ACC.directionRule) or ""),
            "reference_orientation": str(graph.value(subject, ACC.referenceOrientation) or "forward"),
            "edge_ids": [str(x) for x in graph.objects(subject, ACC.voteEdgeId)],
            "source_label": str(graph.value(subject, ACC.voteSourceLabel) or ""),
            "target_label": str(graph.value(subject, ACC.voteTargetLabel) or ""),
            "doc_vote_detail": detail if isinstance(detail, dict) else {},
            "provenance": str(graph.value(subject, ACC.provenance) or ""),
        })
    conflict_decisions = []
    for subject in graph.subjects(RDF.type, ACC.ConflictDecision):
        conflict_decisions.append({
            "id": str(subject), "kind": str(graph.value(subject, ACC.conflictKind) or ""),
            "selected": str(graph.value(subject, ACC.selected) or ""),
            "dropped": str(graph.value(subject, ACC.dropped) or ""),
            "rule": str(graph.value(subject, ACC.rule) or ""),
            "pair": [str(x) for x in graph.objects(subject, ACC.pair)],
            "edge_ids": [str(x) for x in graph.objects(subject, ACC.decisionEdgeId)],
        })
    cycle_decisions = []
    for subject in graph.subjects(RDF.type, ACC.CycleDecision):
        cycle_decisions.append({
            "id": str(subject), "classification": str(graph.value(subject, ACC.classification) or ""),
            "regime_dependent": bool(graph.value(subject, ACC.regimeDependent) or False),
            "reason": str(graph.value(subject, ACC.reason) or ""),
            "pair": [str(x) for x in graph.objects(subject, ACC.pair)],
            "edge_ids": [str(x) for x in graph.objects(subject, ACC.decisionEdgeId)],
            "removed_edge": str(graph.value(subject, ACC.removedEdge) or ""),
        })
    has_arbitration_fields = any("arbitration_enabled" in edge for edge in edges)
    artifact = {
        "artifact_id": stable_urn("artifact", "rdf-roundtrip"),
        "canonical_arbitration_config": {
            "enabled": any(bool(edge.get("arbitration_enabled")) for edge in edges) if has_arbitration_fields else False,
        },
        "version": VERSION,
        "entities": entities,
        "spans": spans,
        "conditions": conditions,
        "assertions": assertions,
        "edges": edges,
        "documents": documents,
        "votes": votes, "conflict_decisions": conflict_decisions,
        "cycle_decisions": cycle_decisions, "hypotheses": [],
    }
    from .semantics import materialize_views
    materialize_views(artifact)
    return artifact


def semantic_snapshot(artifact: dict) -> dict:
    return {
        "views": {k: sorted((x["source"], x["target"]) for x in v) for k, v in artifact.get("views", {}).items()},
        "entity_labels": sorted((e.get("id"), e.get("label")) for e in artifact.get("entities", [])),
        "entity_identity": sorted(
            (
                e.get("id"),
                tuple(e.get("time_indices") or []),
                bool((e.get("state_signature") or {}).get("negation", False)),
                tuple((e.get("state_signature") or {}).get("state_terms") or []),
                e.get("identity_key", ""),
            )
            for e in artifact.get("entities", [])
        ),
        "assertion_ids": sorted(a.get("id") for a in artifact.get("assertions", [])),
        "span_ids": sorted(s.get("id") for s in artifact.get("spans", [])),
        "condition_keys": sorted((c.get("id"), c.get("equivalence_key")) for c in artifact.get("conditions", [])),
        "edge_semantics": sorted(
            (
                e.get("id"),
                e.get("claim_scope"),
                e.get("evidence_grade"),
                e.get("evidence_completeness"),
                e.get("support_scope"),
                e.get("condition_grounding"),
                e.get("temporal_consistency"),
                e.get("fraction_consistency"),
                json.dumps(e.get("evidence_coverage") or {}, ensure_ascii=False, sort_keys=True),
                tuple(sorted(str(x) for x in (e.get("missing_evidence_components") or []))),
                json.dumps(e.get("evidence_windows") or [], ensure_ascii=False, sort_keys=True),
                int(e.get("evidence_window_count", 0) or 0),
                e.get("best_evidence_window_id") or "",
                e.get("evidence_packaging_version") or "",
                e.get("best_window_kind") or "none",
                e.get("best_window_source") or "none",
                int(e.get("best_window_width", 0) or 0),
                bool(e.get("evidence_exact_match", False)),
                bool(e.get("context_window_complete", False)),
                bool(e.get("evidence_contract_complete", False)),
                json.dumps(e.get("evidence_contract") or {}, ensure_ascii=False, sort_keys=True),
                e.get("evidence_packaging_stage") or "",
                e.get("verifier_support_scope") or "none",
                e.get("verifier_window_kind") or "none",
                e.get("verifier_window_id") or "",
                json.dumps({k: v for k, v in e.items() if k.startswith("pre_verification_")}, ensure_ascii=False, sort_keys=True),
                e.get("candidate_support_mode"),
                e.get("candidate_support_strength"),
                e.get("candidate_origin"),
                e.get("candidate_tier"),
                e.get("verification_status"),
                e.get("direction_audit"),
                e.get("path_summary"),
                e.get("path_summary_explicit_direct"),
                e.get("direct_support"),
                e.get("assertion_support"),
                e.get("promotion_status"),
                e.get("path_support"),
                e.get("condition_support"),
                str(e.get("direction_support_forward") or "0"),
                str(e.get("direction_support_reverse") or "0"),
                e.get("relation_role_source"),
                e.get("relation_role_target"),
                e.get("role_direction"),
                bool(e.get("role_conflict", False)),
                e.get("textual_vote_source"),
                e.get("temporal_mode"),
                bool(e.get("temporal_abstained", False)),
                e.get("directness_class") or "",
                e.get("shortcut_risk") or "low",
                tuple(sorted(e.get("shortcut_path_edge_ids") or [])),
                e.get("polarity_status") or "",
                e.get("identity_status") or "ok",
                bool(e.get("provisional_recovery", False)),
                bool(e.get("assertion_recovery", False)),
                bool(e.get("support_aware_eligible", False)),
                e.get("support_aware_status") or "excluded",
                e.get("canonical_support_tier") or "none",
                int(e.get("canonical_support_doc_count", 0) or 0),
                int(e.get("canonical_direction_doc_count", 0) or 0),
                bool(e.get("canonical_multi_document_support", False)),
                tuple(sorted(e.get("candidate_risk_flags") or [])),
                bool(e.get("arbitration_enabled", True)),
                bool(e.get("arbitration_eligible", True)),
                e.get("arbitration_status") or "canonical_safe",
                e.get("arbitration_reason") or "",
            )
            for e in artifact.get("edges", [])
        ),
    }


def _query_signature(artifact: dict, cases: list[dict]) -> list[dict]:
    from .queries import answer
    signatures = []
    for case in cases:
        q, eid = case["query"], case["edge_id"]
        payload = answer(artifact, q, eid)
        if q == "Q1":
            sig = sorted((x.get("assertion_id"), x.get("span_id") or None, x.get("doc_id")) for x in payload.get("evidence", []))
        elif q == "Q2":
            sig = sorted(x.get("doc_id") for x in payload.get("documents", []))
        elif q == "Q3":
            sig = sorted((x.get("vote_id") or x.get("id"), x.get("direction"), x.get("relative_direction"), x.get("assertions"), x.get("documents")) for x in payload.get("votes", []))
        elif q in {"Q4", "Q7"}:
            sig = sorted((x.get("decision_id") or x.get("id"), x.get("decision") or x.get("decision_value")) for x in payload.get("decisions", []))
        elif q == "Q5":
            c = payload.get("condition") or {}
            sig = (c.get("id"), c.get("equivalence_key"), payload.get("grounding_status"), tuple(payload.get("grounding_span_ids") or []))
        else:
            sig = (
                payload.get("kind"), payload.get("semantic_support"),
                payload.get("claim_scope"), payload.get("evidence_grade"),
                payload.get("verification_status"), payload.get("candidate_support_mode"),
                payload.get("evidence_completeness"), payload.get("support_scope"), payload.get("condition_grounding"),
                payload.get("candidate_tier"), payload.get("provenance"), (payload.get("verifier_support") or None), payload.get("promotion_status"),
            )
        signatures.append({"query": q, "edge_id": eid, "signature": sig})
    return signatures


def roundtrip(artifact: dict, out_dir: str | Path) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "artifact.json"
    jsonld_path = out_dir / "artifact.jsonld"
    ttl_path = out_dir / "artifact.ttl"
    write_json(json_path, artifact)
    write_jsonld(artifact, jsonld_path)
    write_turtle(artifact, ttl_path)
    recovered_json = json.loads(json_path.read_text(encoding="utf-8"))
    recovered_rdf = artifact_from_rdf(ttl_path)
    before = semantic_snapshot(artifact)
    after_json = semantic_snapshot(recovered_json)
    after_rdf = semantic_snapshot(recovered_rdf)
    from .queries import generate_query_cases
    cases = generate_query_cases(artifact, limit=30)
    query_before = _query_signature(artifact, cases)
    query_after_rdf = _query_signature(recovered_rdf, cases)
    query_roundtrip = query_before == query_after_rdf
    report = {
        "version": VERSION,
        "json_roundtrip": before == after_json,
        "rdf_roundtrip": before["views"] == after_rdf["views"],
        "edge_semantics_roundtrip": before["edge_semantics"] == after_rdf["edge_semantics"],
        "entity_identity_roundtrip": before["entity_identity"] == after_rdf["entity_identity"],
        "query_roundtrip": query_roundtrip,
        "query_cases": len(cases),
        "query_failures": [
            {"before": b, "after": a}
            for b, a in zip(query_before, query_after_rdf) if b != a
        ][:20],
        "views_before": before["views"],
        "views_after_rdf": after_rdf["views"],
        "entity_count_before": len(before["entity_labels"]),
        "entity_count_after_rdf": len(after_rdf["entity_labels"]),
        "assertion_count_before": len(before["assertion_ids"]),
        "assertion_count_after_rdf": len(after_rdf["assertion_ids"]),
        "span_count_before": len(before["span_ids"]),
        "span_count_after_rdf": len(after_rdf["span_ids"]),
        "condition_count_before": len(before["condition_keys"]),
        "condition_count_after_rdf": len(after_rdf["condition_keys"]),
        "canonical_json_equal": canonical_json(before) == canonical_json(after_json),
        "edge_semantics_before": before["edge_semantics"],
        "edge_semantics_after_rdf": after_rdf["edge_semantics"],
        "paths": {"json": str(json_path), "jsonld": str(jsonld_path), "turtle": str(ttl_path)},
    }
    write_json(out_dir / "roundtrip_report.json", report)
    return report








