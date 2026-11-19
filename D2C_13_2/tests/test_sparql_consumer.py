# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from sparql_consumer import query_artifact  # noqa: E402


ACC = Namespace("https://w3id.org/d2c/acc#")


def _build_graph(path: Path) -> None:
    g = Graph()
    edge = URIRef("urn:d2c:edge:test")
    source = URIRef("urn:d2c:entity:source")
    target = URIRef("urn:d2c:entity:target")
    document = URIRef("urn:d2c:document:test")
    span = URIRef("urn:d2c:span:test")
    assertion = URIRef("urn:d2c:assertion:test")
    condition = URIRef("urn:d2c:condition:test")
    vote = URIRef("urn:d2c:vote:test")
    conflict = URIRef("urn:d2c:conflict:test")
    cycle = URIRef("urn:d2c:cycle:test")

    g.add((edge, RDF.type, ACC.RetainedEdge))
    g.add((edge, ACC.source, source))
    g.add((edge, ACC.target, target))
    g.add((edge, ACC.edgeKind, Literal("direct_core")))
    g.add((edge, ACC.verificationState, Literal("accepted")))
    g.add((edge, ACC.verificationStatus, Literal("entailed")))
    g.add((edge, ACC.verifierSupport, Literal("entailed")))
    g.add((edge, ACC.evidenceGrade, Literal("span_grounded")))
    g.add((edge, ACC.promotionStatus, Literal("core")))
    g.add((edge, ACC.evidenceCompleteness, Literal("exact")))
    g.add((edge, ACC.supportScope, Literal("exact")))
    g.add((edge, ACC.verifierSupportScope, Literal("exact")))
    g.add((edge, ACC.evidenceContractComplete, Literal(True, datatype=XSD.boolean)))
    g.add((edge, ACC.evidenceContractJson, Literal(json.dumps({"complete": True}))))
    g.add((edge, ACC.evidenceCoverageJson, Literal(json.dumps({"score": 1.0}))))
    g.add((edge, ACC.bestEvidenceWindowId, Literal("urn:d2c:evidence_window:test")))
    g.add((edge, ACC.bestWindowKind, Literal("exact")))
    g.add((edge, ACC.bestWindowSource, Literal("sentence_window")))
    g.add((edge, ACC.bestWindowWidth, Literal(1, datatype=XSD.integer)))
    g.add((edge, ACC.evidenceExactMatch, Literal(True, datatype=XSD.boolean)))
    g.add((edge, ACC.contextWindowComplete, Literal(True, datatype=XSD.boolean)))
    g.add((edge, ACC.conditionGrounding, Literal("grounded")))
    g.add((edge, ACC.temporalConsistency, Literal("consistent")))
    g.add((edge, ACC.fractionConsistency, Literal("consistent")))
    g.add((edge, ACC.claimScope, Literal("direct")))
    g.add((edge, ACC.provenance, Literal("document_grounded")))
    g.add((edge, ACC.directSupport, Literal("exact")))
    g.add((edge, ACC.pathSupport, Literal("not_applicable")))
    g.add((edge, ACC.verifierDirection, Literal("forward")))
    g.add((edge, ACC.verifierRecommendedKind, Literal("direct_core")))
    g.add((edge, ACC.hasAssertion, assertion))
    g.add((edge, ACC.hasSpan, span))
    g.add((edge, ACC.hasDocumentSupport, document))
    g.add((edge, ACC.condition, condition))

    g.add((source, RDF.type, ACC.Entity))
    g.add((source, ACC.label, Literal("Smoke")))
    g.add((target, RDF.type, ACC.Entity))
    g.add((target, ACC.label, Literal("Alarm")))
    g.add((document, RDF.type, ACC.Document))
    g.add((document, ACC.docId, Literal("doc1")))
    g.add((document, ACC.title, Literal("Test report")))
    g.add((document, ACC.sourceFile, Literal("test.txt")))

    g.add((assertion, RDF.type, ACC.Assertion))
    g.add((assertion, ACC.docId, Literal("doc1")))
    g.add((assertion, ACC.channel, Literal("pre")))
    g.add((assertion, ACC.sign, Literal("+")))
    g.add((assertion, ACC.rung, Literal("2")))
    g.add((assertion, ACC.mechanism, Literal("Smoke -> Alarm")))
    g.add((assertion, ACC.ofSpan, span))
    g.add((span, RDF.type, ACC.Span))
    g.add((span, ACC.docId, Literal("doc1")))
    g.add((span, ACC.text, Literal("Smoke triggers the alarm when it is hot")))

    g.add((condition, RDF.type, ACC.Condition))
    g.add((condition, ACC.raw, Literal("when it is hot")))
    g.add((condition, ACC.normalized, Literal("when it is hot")))
    g.add((condition, ACC.equivalenceKey, Literal("test-condition")))
    g.add((condition, ACC.conditionKind, Literal("conditional")))

    g.add((vote, RDF.type, ACC.Vote))
    g.add((vote, ACC.voteEdgeId, edge))
    g.add((vote, ACC.direction, Literal("support")))
    g.add((vote, ACC.assertions, Literal(1, datatype=XSD.integer)))
    g.add((vote, ACC.documents, Literal(1, datatype=XSD.integer)))
    g.add((vote, ACC.rule, Literal("unanimous_forward")))
    g.add((vote, ACC.supportDocument, Literal("doc1")))
    g.add((vote, ACC.supportVoteCount, Literal(1, datatype=XSD.integer)))
    g.add((vote, ACC.oppositionVoteCount, Literal(0, datatype=XSD.integer)))
    g.add((vote, ACC.directionRule, Literal("unanimous_forward")))
    g.add((vote, ACC.referenceOrientation, Literal("forward")))
    g.add((vote, ACC.docVoteDetailJson, Literal(json.dumps({"doc1": "support"}))))
    g.add((vote, ACC.provenance, Literal("canonical_audit")))

    g.add((conflict, RDF.type, ACC.ConflictDecision))
    g.add((conflict, ACC.decisionEdgeId, edge))
    g.add((conflict, ACC.conflictKind, Literal("direction")))
    g.add((conflict, ACC.rule, Literal("selection_rule")))
    g.add((conflict, ACC.selected, edge))

    g.add((cycle, RDF.type, ACC.CycleDecision))
    g.add((cycle, ACC.decisionEdgeId, edge))
    g.add((cycle, ACC.classification, Literal("preserved")))
    g.add((cycle, ACC.reason, Literal("retained after review")))
    g.add((cycle, ACC.regimeDependent, Literal(False, datatype=XSD.boolean)))

    g.serialize(path, format="turtle")


def test_sparql_consumer_executes_q1_to_q7(tmp_path: Path) -> None:
    ttl = tmp_path / "artifact.ttl"
    _build_graph(ttl)
    payload = query_artifact(ttl)
    answers = payload["answers"]["urn:d2c:edge:test"]

    assert answers["Q1"]["evidence"][0]["span_id"] == "urn:d2c:span:test"
    assert answers["Q2"]["documents"][0]["doc_id"] == "doc1"
    assert answers["Q3"]["votes"][0]["relative_direction"] == "support"
    assert answers["Q4"]["decisions"][0]["decision_id"] == "urn:d2c:conflict:test"
    assert answers["Q5"]["grounding_status"] == "grounded"
    assert answers["Q6"]["kind"] == "direct_core"
    assert answers["Q7"]["decisions"][0]["decision"] == "preserve"
