# D2C_13_2 Revision Notes

## Purpose

D2C_13_2 is a process and evidence-audit revision over D2C_13_1. It does not change extraction, edge construction, edge direction, promotion thresholds, or F1 views.

## Q3 process repair

- Canonical-audit vote rows are now projected into `artifact["votes"]`.
- Q3 case generation is restored.
- Q3 supports pair and edge-ID matching.
- Q3 reports document support, opposition, uncertainty, direction rule, and relative support/opposition.
- `q3_ready` now means that at least one canonical vote row exists; `q3_multi_document_ready` separately records the multi-document subset.
- JSON-LD, ontology, Turtle round-trip, and independent consumer handling now preserve vote direction, document groups, orientation, counts, and rules.

## Evidence metric redesign

The previous explicit-predicate evidence contract is no longer the primary quality metric because D2C targets implicit, multi-sentence, and cross-paragraph causal knowledge.

New audit metrics:

- `grounded_evidence_coverage`: assertion/span/path evidence with endpoint coverage, condition grounding, and time/fraction consistency; no explicit causal predicate requirement.
- `verified_evidence_coverage`: grounded evidence plus verifier-supported status.
- `cross_sentence_evidence_coverage`: verified evidence spanning multiple assertions, spans, evidence windows, or a multi-sentence context window.
- `implicit_verified_coverage`: verified evidence without a lexical direct-causal cue.
- `lexical_cue_coverage`: diagnostic only, never a gate.

Legacy evidence-contract fields remain serialized for backward compatibility, but `evidence_contract_coverage` is removed from the primary evidence-attribution report.

## F1 neutrality design

The revision changes only vote records, audit metrics, query availability, and RDF serialization. It does not alter edge kind, edge set, direction, promotion, or any F1 view. Saved-verdict replay is used to verify this property on exp19.


## Phase 1 alias guard

- Added `d2c13_2/alias_guard.py` with conservative hard vetoes for desc-only temporal/generic aliases and event/non-event type conflicts.
- Raised the default desc-only merge gate from 0.75 to 0.95.
- Added compiler-side GML alias sanitation so pre-existing unsafe aliases are removed and used aliases are split into separate graph nodes with edge rebinding.
- Added surface-aware saved-verdict remapping for split edges and refreshed stale verifier notes.
- Added `tests/test_alias_guard.py`.
- Full test suite: 152 passed.

Pilot replay (`nova_exp/exp19_d2c13_2_aliasfix_phase1` and `..._final2`):

- `win95pts/run_08`: split `Before Selecting a Job` and `Print Data Content`; rebound 3 edges; mapped 234/234 saved verdicts; `soft_core_balanced` F1 0.8257 -> 0.8311.
- `water/run_08`: removed `time point 12:15`, `time point 12:30`, and generic `level` aliases; F1 unchanged at 0.8636.
- Replayed water run_01/run_02/run_09 and win95pts run_04 with no F1 regression.


## Canonical arbitration recovery fix

- Fixed a crash when support-tier diagnostics contained an unset/None tier.
- Edges that cannot form a canonical pair now receive `canonical_support_tier="none"`.
- Missing support tiers are normalized to `none` in the replay count.
- Added a self-loop regression test.
- Verified `exp20/win95pts/run_06` can recompile from its completed Lang7 output.
- Test suite: 153 passed.
