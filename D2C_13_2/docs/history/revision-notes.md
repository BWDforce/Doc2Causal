# D2C_13_2 Revision Notes

## Scope

D2C_13_2 is an independently runnable successor to D2C_10. It retains the D2C_10 evidence packaging, verifier completeness, promotion-independent audit views and interoperability stack, and adds an entity-first raw-recall layer.

## Entity-first changes

1. A high-recall entity inventory is generated per document before the second extraction pass.
2. The inventory is cached under `_checkpoints/<doc>.entity_inventory.json`.
3. The second LangExtract pass is changed from a repeated generic extraction to an entity-first relation-completion pass.
4. Relation candidates are built from same-sentence and adjacent-sentence windows using inventory aliases.
5. Inventory entities are attached to the pre-graph even when no relation is extracted.
6. `entity_first_report.json` records inventory and relation-candidate counts.
7. Raw recall diagnostics separate endpoint-missing, relation-missing and path-without-direct FN.
8. `tools/simulate_recall_uplift.py` provides an oracle sensitivity check without API calls.

## Experiments

Pilot:

```bat
D2C_13_2\run_exp15_d2c13_2_pilot.cmd
```

- 3 runs per corpus
- 6 parallel workers
- output `nova_exp/exp15_d2c13_2_pilot`

Clean main run:

```bat
D2C_13_2\run_exp16_d2c13_2_clean.cmd
```

- 9 runs per corpus
- 18 parallel workers
- output `nova_exp/exp16_d2c13_2_clean`

## Validation

```bat
python -m pytest D2C_13_2\tests -q --basetemp nova_exp\_pytest_tmp_d2c13_2
```

## Interpretation

D2C_13_2 is designed to increase endpoint and relation recall at extraction time while preserving the D2C_10 evidence and verifier gates. A higher raw candidate count is not treated as a final promoted edge until it passes the same evidence and verification stack.
## Checkpoint extraction rehydration

Exp15 exposed that pass checkpoints serialized LangExtract `Extraction` objects with `default=str`. Resume restored strings, so relation and span evidence disappeared from the pre-graph. D2C_13_2 now writes structured extraction records and rehydrates both new and legacy checkpoints before graph construction.## D2C_13_2 adaptive relation-prompt revision

D2C_13_2 removes the corpus-specific global relation-pair cap. Candidate pairs are
still deduplicated by unordered entity pair and ranked by causal cue, same-sentence
locality, mention distance and window size, but no fixed number of pairs is retained.

Instead, candidate pairs are organized by their source paragraph and evidence window.
Each prompt contains the evidence text once per window and lists all candidate pairs
for that window. Relation extraction runs one prompt per source paragraph, so the
number of pairs per request is emergent from the source structure rather than tuned to
a corpus-specific count. This also avoids repeating the same evidence sentence for every
pair and keeps the prompt focused on a single paragraph.

The prompt explicitly forbids mixing endpoints across evidence blocks and requires
preserving the exact source/target surfaces and time indices. Checkpoint storage remains
per extraction pass and stores the union of all paragraph-batch results.


## D2C_13_2 document-level semantic alias normalization

D2C_13_2 adds a document-level alias discovery pass before entity resolution.

- Mentions are collected from document-specific edges, assertions, spans and entity descriptions.
- Pair scoring uses normalized surfaces, document context, local causal neighbors, time/number/state compatibility and qualifier/scope conflict guards.
- Auto-merge is precision-first; weak or ambiguous document-level candidates enter a review queue.
- Review candidates are capped by a generic per-entity rule rather than corpus-specific absolute counts.
- The benchmark alias dictionary is not read by the normalization pass; it is used only for E6 evaluation.
- Entity merges are followed by a core-cycle repair guard before downstream packaging.

Configuration is under `entity_resolution.document_alias`.
## Final D2C_13_2 normalization hardening

- Selected embedding auto-merge threshold `0.93`; E6.6 false auto-merge count is zero.
- Added shared clock/ordinal normalization, including `midday`/`noon` and word ordinals.
- Compatibility checks now inspect all aliases, not only the primary label.
- Added state-modifier/polarity conflicts and a state-only auto-merge block.
- Added path-aware alias identity checks and incremental cycle-safe merge contraction.
- Added saved-verdict replay and aggregate reporting tools:
  - `D2C_13_2/tools/replay_saved_verdicts.py`
  - `D2C_13_2/tools/replay_report.py`
- Added Q1 query expectation handling for candidate-only records.

Completed replay:

```text
nova_exp/exp16_d2c13_2_verified_replay_9x_final
```

Validation command:

```cmd
python -m pytest -q D2C_13_2\tests --basetemp nova_exp\_pytest_tmp_d2c13_2_final
```

Result: `111 passed` in `146.64s`.


## Colon alias-definition fix

- Preserve and match `Entity: definition` before replacing colons during alias-link normalization.
- E6.3 now passes 5/5; the `AppOK: application operates without errors` case auto-merges as an explicit definition.
- exp17 E6.5 resolver replay remains F1-neutral and validation-clean.

## Contract/F1 separation and exp17 replay validation

- Added evidence contract v3 and contract-strict views without making contract completeness a mandatory verifier-acceptance condition.
- Added `promotion.require_complete_evidence_contract_for_acceptance` as an independent opt-in strict mode.
- Added `contract_verified_core` and `contract_support_aware_core` alongside `evidence_contract_core`.
- Hardened saved-verdict replay with a case-preserving exact surface signature; the two prior win95pts/run_01 ambiguous mappings now resolve deterministically.
- exp17 18-run replay: all main F1 views match D2C_12 exactly; 100% verdict mapping; all validation/SHACL/round-trip/consumer checks pass. E7 uses the real D2C_11_1 aggregate baseline rather than the source D2C_12 artifact.
- Full test result: `119 passed`.
- Full evidence: `VALIDATION_REPORT.md` and `nova_exp/exp17_d2c13_2_verified_replay_9x_contractsplit/`.

## E1?E7 D2C_13_2 rerun

- Added structural evidence-contract checks to the validator and corresponding negative tests.
- Reran E1?E4/E6/E7 against D2C_13_2 replay artifacts without new extraction or verifier calls.
- E1 detected all 23 invalid mutations; E2/E3 passed 18/18; E4 passed 69/69 with 39/39 contract-field checks.
- E6.5 remained F1-neutral across 18 runs. E6.6 was corrected to use within-document mention pairs only: P=1.0, R=0.3340, F1=0.5007, false auto-merges=0.
- E5 final advisory: DeepSeek produced 13 supported, 7 partial and 0 unsupported; Codex produced 14 supported, 5 partial and 1 unsupported; observed agreement=0.60, quadratic-weighted kappa=0.2793 (LLM-to-LLM advisory only). Human validation remains required.
- Test suite: `123 passed`.

## Span coordinate repair

- Fixed the document-title-prefix offset bug in legacy evidence-ledger spans and GML `span_ids_json` spans.
- New runs mark evidence spans as `document_summary` coordinates and avoid double-shifting.
- Span-fixed replay: 2335 spans, 0 text mismatches, 0 empty spans, 2 residual mid-word boundaries outside selected E5 samples.
- Rebuilt E5 under `nova_exp/exp_e5_d2c13_2_spanfix`; previous E5 results are invalidated. Valid span-fixed DeepSeek advisory: 15 supported, 5 partial, 0 unsupported; human validation remains required.
- Full test suite: `125 passed`.

## D2C_13_2 F1-affecting arbitration

- Added `canonical_arbitration` config with `canonical_document` vote mode.
- Added generic relation-role alias guard; ambiguous pairs remain in review rather than auto-merging.
- Added canonical-pair replay and multi-document support fields.
- Added candidate conflict/cycle flags without deleting edges; evidence edges in mixed cycles are preserved.
- Added `canonical_soft_core`, `canonical_verified_core`, `canonical_multi_document_core`, `candidate_safe_core`, `candidate_risk_review`, and `arbitration_review`.
- Added RDF/Turtle serialization and round-trip recovery for arbitration fields.
- Small offline replay: water/run_01 canonical_soft_core F1 0.9466; win95pts/run_01 0.8756, matching the balanced legacy view.
- Full tests: 139 passed.
