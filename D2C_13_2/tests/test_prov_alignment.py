# -*- coding: utf-8 -*-
import json

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF

from d2c13_2.constants import ONTOLOGY_PATH
from d2c13_2.rdfio import artifact_to_jsonld


ACC = Namespace("https://w3id.org/d2c/acc#")
PROV = Namespace("http://www.w3.org/ns/prov#")


def _minimal_artifact():
    artifact_id = "urn:d2c:artifact:test"
    document_id = "urn:d2c:document:test"
    entity_a = "urn:d2c:entity:a"
    entity_b = "urn:d2c:entity:b"
    span_id = "urn:d2c:span:test"
    assertion_id = "urn:d2c:assertion:test"
    edge_id = "urn:d2c:edge:test"
    activity_id = "urn:d2c:activity:test"
    return {
        "artifact_id": artifact_id,
        "created_at": "2026-11-20T00:00:00+08:00",
        "source": {"run_dir": "test"},
        "documents": [{"id": document_id, "doc_id": "doc1", "title": "test", "source_file": "test.txt"}],
        "spans": [{"id": span_id, "doc_id": "doc1", "start": 0, "end": 3, "text": "A B", "context": "A causes B", "document_id": document_id}],
        "entities": [{"id": entity_a, "label": "A"}, {"id": entity_b, "label": "B"}],
        "conditions": [],
        "assertions": [{"id": assertion_id, "source": entity_a, "target": entity_b, "doc_id": "doc1", "span_id": span_id, "mechanism": "A causes B"}],
        "edges": [{
            "id": edge_id, "source": entity_a, "target": entity_b,
            "source_name": "A", "target_name": "B", "kind": "direct_core",
            "support_assertion_ids": [assertion_id], "span_ids": [span_id],
            "support_doc_ids": ["doc1"], "candidate_assertion_ids": [],
            "candidate_span_ids": [], "candidate_doc_ids": [], "candidate_derivation_ids": [],
            "derivation_edge_ids": [], "verification_status": "accepted",
            "semantic_support": "entailed", "attributes": {},
        }],
        "votes": [], "conflict_decisions": [], "cycle_decisions": [],
        "views": {},
    }


def test_ontology_imports_prov_o():
    graph = Graph()
    graph.parse(ONTOLOGY_PATH, format="turtle")
    assert (ACC.ACCOntology, OWL.imports, URIRef("http://www.w3.org/ns/prov-o")) in graph


def test_export_contains_prov_alignment():
    artifact = _minimal_artifact()
    graph = Graph()
    graph.parse(data=json.dumps(artifact_to_jsonld(artifact), ensure_ascii=False), format="json-ld")
    edge = URIRef("urn:d2c:edge:test")
    assertion = URIRef("urn:d2c:assertion:test")
    artifact_iri = URIRef("urn:d2c:artifact:test")
    assert (edge, RDF.type, PROV.Entity) in graph
    assert (artifact_iri, RDF.type, PROV.Entity) in graph
    assert (edge, PROV.wasDerivedFrom, assertion) in graph
    activity = graph.value(artifact_iri, PROV.wasGeneratedBy)
    assert activity is not None
    assert (activity, RDF.type, PROV.Activity) in graph
