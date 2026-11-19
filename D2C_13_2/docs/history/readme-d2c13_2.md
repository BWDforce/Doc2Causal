# D2C_13_2: Evidence-Packaged Causal Artifact Method

## F1-affecting arbitration layer

D2C_13_2 adds config-controlled canonical-document voting, relation-aware alias guarding, canonical-pair replay, candidate conflict/cycle risk flags and separate arbitration views. See `ARBITRATION.md`. Small offline evidence is recorded in `SMALL_VALIDATION.md`. The legacy baseline views remain available.

## exp18 baseline

`exp18` is the completed D2C_12_1 F1 baseline for D2C_13_2. Exact F1 values and artifact hashes are recorded in `BASELINE_EXP18.md` and `baseline_exp18.json`.

## F1-neutral canonical audit

D2C_13_2 adds a read-only Q3/Q4/Q7 canonical audit layer. It records document votes, conflict taxonomy, candidate conflicts and candidate/mixed/evidence cycles without modifying the core artifact or F1 views. See `F1_NEUTRAL_AUDIT.md`.

## Content-addressed extraction cache

The Lang7 extraction stage is content-addressed and reused when document bytes, extraction settings, implementation hashes and a stable run namespace match. See `CONTENT_CACHE.md`. Intentional n-run stochastic experiments use per-run namespaces (`run_01`, `run_02`, ...), so different runs remain independent.

## Document-level semantic alias normalization

D2C_13_2 adds a conservative document-level alias pass before entity resolution. It uses actual document context, local causal neighbors, normalized surface forms, time/number/state compatibility and qualifier/scope guards. The benchmark alias dictionary is not used by the runtime pass. Auto-merge is precision-first; ambiguous candidates are placed in a capped review queue.

## D2C_13_2 entity-first recall layer

D2C_13_2 keeps the D2C_10 evidence, verifier and interoperability stack and adds an entity inventory before the relation-matrix pass:

- `entity_inventory.json` caches a high-recall inventory of variables, states, measurements, conditions and aliases per document.
- The second LangExtract pass is no longer an identical repeat. It receives the inventory and evidence-local relation candidate pairs, and is instructed to recover missing endpoints and explicit relations.
- Inventory nodes are attached to the pre-graph even when no relation is found, so node recall is observable and aliases can be normalized later.
- `entity_first_report.json` records inventory and relation-candidate counts.
- `tools/diagnose_raw_recall.py` splits FN into missing endpoint, missing relation and missing direct-edge categories.

The pilot is `run_exp15_d2c13_2_pilot.cmd`; the clean 18-run main run is `run_exp16_d2c13_2_clean.cmd`.



## D2C_13_2 relation prompt

D2C_13_2 does not use a global candidate-pair cap. Candidate pairs are deduplicated by entity pair, ranked by evidence locality, grouped by source paragraph and evidence window, and extracted in paragraph-level batches. Evidence text is stored once per window in the prompt, and all candidate pairs for that window are retained.

D2C_13_2 extends D2C_9 with a deterministic evidence ledger, calibrated verifier
support scopes and an interoperability layer. The LLM extraction flow remains
the embedded lang7 flow.

## Core design

### 1. Evidence packaging v3

`d2c13_2/evidence_completeness.py` selects the shortest sufficient window for each
edge:

1. exact source span;
2. minimal one/two-sentence exact window;
3. complete non-exact context window;
4. partial window;
5. assertion/candidate/path/no-evidence fallback.

It distinguishes:

- `exact_span` and `exact_sentence_window`;
- `context_assisted`;
- `partial_window`, `assertion_only`, `candidate`, `path_derived` and `none`.

A complete context window is recorded as `support_scope=context_assisted`; it is
not mislabeled as an exact span and is not discarded merely because no single
direct span exists. Evidence packaging v3 also records an explicit
`evidence_contract` covering source, target, predicate/direction, required
condition, temporal consistency and fraction consistency. A derivation path is
marked complete only when the full path is available.

### 2. Verifier calibration

The verifier may return `support=entailed` for a complete context window. In
that case it records `verifier_support_scope=context_assisted`, while
`direct_support` can remain `partial`, `uncertain` or `not_applicable`.
Candidate promotion and evidence audit are separate: demotion changes the final
edge kind, not the frozen canonical evidence package.

### 3. Promotion-independent evidence views

D2C_9 views remain available. D2C_13_2 adds:

- `exact_support_core`;
- `context_assisted_core`;
- `partial_evidence_review`;
- `path_support_core`;
- `no_evidence_queue`;
- `evidence_contract_core`;
- `contract_verified_core`;
- `contract_support_aware_core`.

`exact_support_core` and `context_assisted_core` are evidence-audit views. They
retain pre-verifier evidence even when the verifier later moves an edge to
`candidate` or `unsupported_hypothesis`. The `contract_*` views add the stricter
condition that the evidence contract is complete. They are reported separately
from the legacy extraction-F1 views so that an audit requirement does not
silently rewrite verifier outcomes and collapse recall.

Verifier acceptance remains legacy-compatible by default. Set
`promotion.require_complete_evidence_contract_for_acceptance=true` only for an
audit-strict run; this setting is independent of
`promotion.require_complete_evidence_contract`, which controls the support-aware
promotion gate.

### 4. Pre-verifier checkpoint

After the first evidence packaging, D2C_13_2 freezes:

- `pre_verification_evidence_completeness`;
- `pre_verification_support_scope`;
- `pre_verification_evidence_coverage`;
- `pre_verification_evidence_windows`;
- best-window and condition/time/fraction metadata.

The complete pre-verifier artifact is also written as
`artifact.pre_verification.json`. After verifier application, canonical evidence
fields are restored from this snapshot.

### 5. Interoperability

New scope, best-window and pre-verification fields are serialized in
JSON-LD/Turtle, validated by SHACL and recovered by the RDF parser. Query Q1 and
Q6 expose the evidence scope and verifier scope.

## Independent SPARQL Q1-Q7 consumer

`sparql/` contains the independent SPARQL implementation of Q1-Q7. The consumer
`tools/sparql_consumer.py` imports only `rdflib` and the Python standard library,
reads Turtle/JSON-LD, and never imports `d2c13_2`.

Single artifact:

```bat
python D2C_13_2\tools\sparql_consumer.py ^
  --input CSB_exp\results\csb_d2c13_2_aliasguard_latest\hotwork\run_01\artifact.ttl ^
  --output nova_exp\hotwork_sparql_answers.json
```

Full parity over the 18 controlled artifacts and six CSB artifacts:

```bat
D2C_13_2\run_sparql_parity.cmd
```

The parity command writes `sparql_parity_report.json` and
`sparql_parity_mismatches.csv` under `nova_exp\exp_sparql_parity_d2c13_2`.
The public query contract is documented in `sparql\README.md`.

## Model configuration

`D2C_13_2/config.json` controls extractor and verifier models, URLs and API-key
environment variables. The clean run uses the model and endpoint in
`config.deepseek-official.json` and reads the key from `DEEPSEEK_API_KEY`.

## Clean exp16 run

```bat
set "DEEPSEEK_API_KEY=<key>"
D2C_13_2\run_exp16_d2c13_2_clean.cmd
```

This writes a fresh 18-run experiment to `nova_exp/exp16_d2c13_2_clean` and
refuses to overwrite an existing directory. During extraction, each run bar is
subdivided into document, pass and agent stages.

## Verifier and query repair utilities

- `tools/resume_missing_verdicts.py` resumes only missing verifier verdicts from cached pre-verification artifacts and checkpoints. It does not rerun LLM extraction.
- `tools/refresh_query_reports.py` regenerates Q1-Q7 reports from cached
  artifacts after a query-semantics fix.
## Validation

The latest exp17 replay and contract-split results are summarized in
`VALIDATION_REPORT.md`.

```bat
python -m pytest D2C_13_2\tests -q --basetemp nova_exp\_pytest_tmp_full
```

Offline repack of cached artifacts:

```bat
python D2C_13_2\tools\repack_evidence.py --input-root nova_exp\exp12\water\run_01 --output-root nova_exp\_d2c13_2_smoke\repack_water_01
```

Full clean run after setting `DEEPSEEK_API_KEY`:

```bat
D2C_13_2\run_exp16_d2c13_2_clean.cmd
```

The launcher writes 18 runs under `nova_exp\exp16_d2c13_2_clean` and shows one
progress bar per run. Logs remain under each run's `runner_logs` directory.

## Verified saved-verdict replay

The deterministic replay driver compiles the existing exp17 lang7 runs, remaps saved verifier verdicts to D2C_13_2 edges, and scores all views without any extraction or verifier API call.

```cmd
python D2C_13_2\tools\replay_saved_verdicts.py ^
  --source-exp-root nova_exp\exp17 ^
  --out-root nova_exp\exp17_d2c13_2_verified_replay_9x_contractsplit ^
  --config D2C_13_2\config.deepseek-official.json ^
  --corpus both
```

Aggregate the result:

```cmd
python D2C_13_2\tools\replay_report.py ^
  --replay-root nova_exp\exp17_d2c13_2_verified_replay_9x_contractsplit ^
  --source-exp-root nova_exp\exp17
```

The final comparison is written to `replay_comparison.json` and `replay_comparison.md` under the replay root.

## Full arbitration run (not started)

```cmd
set "DEEPSEEK_API_KEY=<key>"
D2C_13_2\run_exp19_d2c13_2_clean.cmd
```

This writes 18 runs to `nova_exp\exp19_d2c13_2_clean` with the canonical-document vote mode and independent arbitration views.

## Single-run rerun with progress

```cmd
set "DEEPSEEK_API_KEY=<key>"
D2C_13_2\run_exp19_water_run05.cmd
```

This runs only `water/run_05`, forces fresh extraction, writes child logs to `water/run_05/runner_logs/`, and shows one progress bar in the terminal. The run summary is written separately as `experiment_summary_water_run05.json`.
