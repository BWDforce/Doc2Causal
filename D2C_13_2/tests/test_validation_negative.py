# -*- coding: utf-8 -*-
from d2c13_2.validator import validate_artifact


def test_invalid_core_self_loop_detected():
    artifact = {
        "version": "D2C_13_2", "artifact_id": "urn:d2c:artifact:invalid",
        "entities": [{"id": "urn:d2c:entity:a", "label": "A", "aliases": []}],
        "documents": [], "spans": [], "assertions": [], "conditions": [],
        "edges": [{
            "id": "urn:d2c:edge:self", "source": "urn:d2c:entity:a", "target": "urn:d2c:entity:a",
            "kind": "direct_core", "support_assertion_ids": [], "span_ids": [], "support_doc_ids": [],
            "condition_id": None, "derivation_edge_ids": [], "semantic_support": "untested",
        }],
        "votes": [], "conflict_decisions": [], "cycle_decisions": [], "hypotheses": [], "views": {}, "diagnostics": {},
    }
    report = validate_artifact(artifact, run_shacl=False)
    codes = {e["code"] for e in report["errors"]}
    assert "CORE_SELF_LOOP" in codes
    assert "CORE_NO_ASSERTION" in codes
    assert "CORE_NO_SPAN" in codes



def _contract_artifact(contract=None, complete=True):
    return {
        "version": "D2C_13_2", "artifact_id": "urn:d2c:artifact:contract",
        "entities": [
            {"id": "urn:d2c:entity:a", "label": "A", "aliases": []},
            {"id": "urn:d2c:entity:b", "label": "B", "aliases": []},
        ],
        "documents": [], "spans": [], "assertions": [], "conditions": [],
        "edges": [{
            "id": "urn:d2c:edge:a-b", "source": "urn:d2c:entity:a", "target": "urn:d2c:entity:b",
            "kind": "candidate", "support_assertion_ids": [], "span_ids": [], "support_doc_ids": [],
            "candidate_assertion_ids": ["urn:d2c:assertion:x"], "condition_id": None,
            "derivation_edge_ids": [], "semantic_support": "candidate",
            "evidence_contract_complete": complete,
            "evidence_contract": contract if contract is not None else {
                "complete": True,
                "text_complete": True,
                "path_complete": False,
                "components": {"source": True, "target": True, "predicate": True, "condition": True},
                "temporal_consistency": "consistent",
                "fraction_consistency": "consistent",
            },
        }],
        "votes": [], "conflict_decisions": [], "cycle_decisions": [], "hypotheses": [], "views": {}, "diagnostics": {},
    }


def test_valid_evidence_contract_has_no_contract_errors():
    report = validate_artifact(_contract_artifact(), run_shacl=False)
    codes = {e["code"] for e in report["errors"]}
    assert not {code for code in codes if code.startswith("EVIDENCE_CONTRACT")}


def test_evidence_contract_boolean_mismatch_detected():
    artifact = _contract_artifact()
    artifact["edges"][0]["evidence_contract"]["complete"] = False
    codes = {e["code"] for e in validate_artifact(artifact, run_shacl=False)["errors"]}
    assert "EVIDENCE_CONTRACT_BOOLEAN_MISMATCH" in codes


def test_complete_text_contract_requires_all_components():
    artifact = _contract_artifact()
    artifact["edges"][0]["evidence_contract"]["components"]["target"] = False
    codes = {e["code"] for e in validate_artifact(artifact, run_shacl=False)["errors"]}
    assert "EVIDENCE_CONTRACT_COMPONENTS_MISSING" in codes


def test_complete_contract_requires_text_or_path():
    artifact = _contract_artifact()
    contract = artifact["edges"][0]["evidence_contract"]
    contract["text_complete"] = False
    contract["path_complete"] = False
    codes = {e["code"] for e in validate_artifact(artifact, run_shacl=False)["errors"]}
    assert "EVIDENCE_CONTRACT_INCONSISTENT" in codes
