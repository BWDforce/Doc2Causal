# -*- coding: utf-8 -*-
"""Constants for D2C_13_2."""

from __future__ import annotations

from pathlib import Path

VERSION = "D2C_13_2"
ACC_NS = "https://w3id.org/d2c/acc#"
PROV_NS = "http://www.w3.org/ns/prov#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
XSD_NS = "http://www.w3.org/2001/XMLSchema#"
D2C_URN = "urn:d2c:"

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = PACKAGE_ROOT / "schema"
SHACL_PATH = SCHEMA_DIR / "acc.shacl.ttl"
ONTOLOGY_PATH = SCHEMA_DIR / "acc.ontology.ttl"
CONTEXT_PATH = SCHEMA_DIR / "acc.context.jsonld"

EDGE_KINDS = (
    "direct_core",
    "conditional",
    "regime",
    "transitive_derived",
    "candidate",
    "unsupported_hypothesis",
    # Legacy D2C_1/D2C_2 import only; D2C_13_2 does not emit this kind.
    "hypothesis",
    "rejected",
)

CLAIM_SCOPES = ("direct", "composed", "conditional", "regime", "hypothesis", "unknown")
EVIDENCE_GRADES = (
    "span_grounded",
    "assertion_grounded",
    "span_only",
    "derived_path",
    "context_only",
    "none",
)
VERIFICATION_STATUSES = (
    "unverified",
    "accepted",
    "conditional",
    "transitive",
    "needs_review",
    "unsupported",
    "rejected",
    "advisory",
)
CANDIDATE_TIERS = ("high", "medium", "low", "none")
CANDIDATE_SUPPORT_MODES = ("direct_span", "assertion_only", "span_only", "weak_candidate", "candidate", "none")
EVIDENCE_COMPLETENESS = ("exact", "context_assisted", "partial", "assertion_only", "candidate_only", "path_only", "absent")
SUPPORT_SCOPES = ("exact_span", "exact_sentence_window", "context_assisted", "context_window", "partial_window", "assertion_only", "candidate", "path_derived", "none")
WINDOW_KINDS = ("exact_span", "exact_window", "context", "context_partial", "partial", "assertion_only", "original", "absent", "none")
GROUNDING_STATUSES = ("grounded", "context_grounded", "ungrounded", "not_applicable")
CONSISTENCY_STATUSES = ("consistent", "partial", "inconsistent", "not_applicable")

CORE_EDGE_KINDS = ("direct_core", "conditional", "regime")
RETAINED_EDGE_KINDS = CORE_EDGE_KINDS + ("transitive_derived",)

VIEW_NAMES = (
    "direct_core",
    "conditional",
    "regime",
    "transitive_derived",
    "candidate",
    "unsupported_hypothesis",
    "hypothesis",
    "rejected",
    "grounded",
    "verified_core",
    "contract_verified_core",
    "span_core",
    "assertion_core",
    "derived_core",
    "evidence_core",
    "evidence_contract_core",
    "soft_core",
    "soft_core_lenient",
    "soft_core_balanced",
    "soft_core_precision",
    "support_aware_core",
    "contract_support_aware_core",
    "assertion_recovery",
    "identity_review",
    "condition_review",
    "directness_review",
    "shortcut_review",
    "partial_forward_core",
    "path_core",
    "path_review",
    "candidate_high",
    "candidate_medium",
    "candidate_low",
    "needs_review",
    "direction_review",
    "exact_support_core",
    "context_assisted_core",
    "partial_evidence_review",
    "path_support_core",
    "no_evidence_queue",
    "canonical_soft_core",
    "canonical_verified_core",
    "canonical_multi_document_core",
    "candidate_safe_core",
    "candidate_risk_review",
    "arbitration_review",
)

EDGE_CLASS = {
    "direct_core": "DirectCoreEdge",
    "conditional": "ConditionalEdge",
    "regime": "RegimeEdge",
    "transitive_derived": "TransitiveDerivedEdge",
    "candidate": "CandidateEdge",
    "unsupported_hypothesis": "UnsupportedHypothesisEdge",
    "hypothesis": "HypothesisEdge",
    "rejected": "RejectedEdge",
}

HYPOTHESIS_KINDS = ("candidate", "unsupported_hypothesis", "hypothesis")

DIRECT_CUES = (
    "cause", "causes", "caused", "lead", "leads", "led", "result",
    "results", "resulted", "produce", "produces", "produced", "generate",
    "generates", "generated", "raise", "raises", "raised", "reduce",
    "reduces", "reduced", "increase", "increases", "increased",
    "decrease", "decreases", "decreased", "trigger", "triggers",
    "triggered", "enable", "enables", "enabled", "feed", "feeds",
    "fed", "determine", "determines", "determined", "influence",
    "influences", "influenced", "make", "makes", "made",
)

CONDITIONAL_CUES = (
    "conditional", "moderator", "moderating", "depends", "depending",
    "whether", "jointly", "interaction", "interacts", "if ", "when ",
)

