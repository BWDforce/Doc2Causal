# D2C_13_2 Validation Report

## Scope

This validation uses the 18 saved exp17 runs (9 water + 9 win95pts). It recompiles the
D2C_13_2 artifact, remaps the original verifier verdicts to the corresponding new
edges, and scores every view without any extraction or verifier API call.

Replay root:

```text
nova_exp/exp17_d2c13_2_verified_replay_9x_contractsplit
```

## Changes validated

1. Evidence packaging v3 records an explicit contract over source, target,
   predicate/direction, required condition, temporal consistency and fraction
   consistency. A complete path is accepted for composed path-only evidence.
2. Strict audit views are separated from legacy F1 views:
   `evidence_contract_core`, `contract_verified_core` and
   `contract_support_aware_core`.
3. Verifier acceptance is decoupled from evidence-contract strictness. The default
   `promotion.require_complete_evidence_contract_for_acceptance=false` preserves
   the exp17 extraction-F1 behavior. Setting it to `true` enables an audit-strict
   run.
4. Saved-verdict replay uses a case-preserving exact surface signature when alias
   normalization collapses two historical edges that differ only by case.

## Automated tests

```cmd
python -m pytest -q D2C_13_2\tests --basetemp nova_exp\_pytest_tmp_d2c13_2_final3
```

Result: `125 passed`. The editable environment package resolves to
`D2C_13_2` version `0.12.1` from `D2C_13_2/d2c13_2`, and `python -m d2c13_2 --help`
loads successfully.

## F1 preservation against exp17 D2C_12

| Corpus | View | D2C_12 F1 | D2C_13_2 F1 | Delta |
| --- | --- | ---: | ---: | ---: |
| water | raw | 0.9080 | 0.9080 | 0.0000 |
| water | soft_core_balanced | 0.9206 | 0.9206 | 0.0000 |
| water | support_aware_core | 0.8811 | 0.8811 | 0.0000 |
| water | verified_core | 0.8276 | 0.8276 | 0.0000 |
| win95pts | raw | 0.8657 | 0.8657 | 0.0000 |
| win95pts | soft_core_balanced | 0.8622 | 0.8622 | 0.0000 |
| win95pts | support_aware_core | 0.8563 | 0.8563 | 0.0000 |
| win95pts | verified_core | 0.7661 | 0.7661 | 0.0000 |

## Contract-strict views

These views are intentionally stricter and are not used as replacements for the
legacy extraction-F1 views.

| Corpus | View | P | R | F1 | TP/FP/FN |
| --- | --- | ---: | ---: | ---: | --- |
| water | contract_support_aware_core | 0.9388 | 0.6970 | 0.8000 | 414/27/180 |
| water | contract_verified_core | 0.9571 | 0.6768 | 0.7929 | 402/18/192 |
| water | evidence_contract_core | 0.9435 | 0.7593 | 0.8414 | 451/27/143 |
| win95pts | contract_support_aware_core | 0.9352 | 0.6875 | 0.7925 | 693/48/315 |
| win95pts | contract_verified_core | 0.9575 | 0.6042 | 0.7409 | 609/27/399 |
| win95pts | evidence_contract_core | 0.9332 | 0.6935 | 0.7957 | 699/50/309 |

## Contract coverage

| Corpus | Complete / retained edges | Coverage | Accepted-incomplete under legacy mode |
| --- | ---: | ---: | ---: |
| water | 856 / 1407 | 0.6084 | 136 |
| win95pts | 1115 / 1992 | 0.5597 | 198 |

The accepted-incomplete count is reported explicitly rather than hidden. Under
the default legacy-compatible policy these edges may retain verifier acceptance,
but they are excluded from the `contract_*` audit views. For a strict run, set
`promotion.require_complete_evidence_contract_for_acceptance=true`.

## Replay and interoperability

- Verdict mapping: water 1409/1409 and win95pts 2000/2000 requests mapped; 0 left
  unverified.
- Validation: 18/18 runs valid.
- SHACL: 18/18 runs conform.
- JSON/RDF round-trip, edge semantics, entity identity and query round-trip: 18/18.
- Independent consumer equality: 18/18.
- Query failures: 0.
- E5 contract sample export: 20/20 samples have a complete contract and a
  non-empty full evidence window.

## Main comparison artifacts

```text
comparison_vs_exp17_contractsplit.md
comparison_vs_exp17_contractsplit.json
replay_comparison.md
replay_comparison.json
e5_contract_samples.json
```

The first report compares D2C_13_2 with exp17 D2C_12. The second aggregates all
18 replay runs. The JSON files preserve the underlying per-view TP/FP/FN values.

## Reproduce

```cmd
python D2C_13_2\tools\replay_saved_verdicts.py ^
  --source-exp-root nova_exp\exp17 ^
  --out-root nova_exp\exp17_d2c13_2_verified_replay_9x_contractsplit ^
  --config D2C_13_2\config.deepseek-official.json ^
  --corpus both
```

Then aggregate:

```cmd
python D2C_13_2\tools\compare_exp17.py ^
  --replay-root nova_exp\exp17_d2c13_2_verified_replay_9x_contractsplit ^
  --baseline-analysis nova_exp\exp17\analysis_vs_d2c11_1.json ^
  --out nova_exp\exp17_d2c13_2_verified_replay_9x_contractsplit\comparison_vs_exp17_contractsplit.json

python D2C_13_2\tools\replay_report.py ^
  --replay-root nova_exp\exp17_d2c13_2_verified_replay_9x_contractsplit ^
  --source-exp-root nova_exp\exp17 ^
  --out nova_exp\exp17_d2c13_2_verified_replay_9x_contractsplit\replay_comparison.json
```


## E1-E7 rerun on D2C_13_2

- E1: 18/18 valid artifacts, 23/23 invalid contract/structural mutations detected.
- E2: JSON, RDF/Turtle, entity identity, views, edge semantics and query round-trip all 18/18.
- E3: independent consumer equality 18/18; all recovery means 1.0.
- E4 final exp18 remap: 71/71 exact; 39/39 Q1/Q6 contract-field checks passed.
- E5: the span-fixed 20-sample human review is complete: supported=16, partial=2, unsupported=2. The separate LLM advisory comparison has agreement=0.6 and Cohen kappa=-0.0667.
- E6: E6.1 7/7, E6.2 6/6, E6.3 5/5, E6.4 false-auto=0, E6.5 18-run F1-neutral, E6.6 within-document P=1.0/R=0.3340/F1=0.5007 with false-auto=0.
- E7: main F1 views are compared against the real D2C_11_1 aggregate baseline; contract-strict views are reported separately.

Summary file: `nova_exp/D2C_13_2_E1_E7_SUMMARY.md`.


## Span offset repair

- Root cause: LangExtract char intervals were relative to `Title: <title>\n - \nContent:\n<summary>`, while artifact spans were sliced from `<summary>` without subtracting the title-prefix offset.
- Fixed both evidence-ledger spans and GML `span_ids_json` spans, with boundary-aware correction and a `document_summary` coordinate marker for future runs.
- Span-fixed replay: `nova_exp/exp17_d2c13_2_spanfix2_replay_9x`.
- Validation: 2335 spans, 0 text mismatches, 0 empty spans, 2 residual mid-word boundaries outside the selected E5 samples.
- The 20 rebuilt E5 samples have 0 text mismatches and 0 mid-word span boundaries in their referenced evidence.
- Saved-verdict coverage after repair is water 1356/1431 and win95pts 1819/2054; verifier-dependent metrics require a verifier rerun for final reporting.

## Final exp18 verifier rerun

- 18/18 runs completed with full verdict coverage (3486/3486).
- Validation, SHACL, JSON/RDF round-trip, semantic/identity/query round-trip and independent consumer equality: 18/18 each.
- Corrected spans: 2468 checked, 0 text mismatches, 0 empty spans, 2 residual mid-word boundaries outside the reviewed E5 samples.
- E1: 18/18 valid artifacts, 23/23 mutations detected.
- E2: JSON and query round-trip 18/18.
- E3: independent consumer equality 18/18.
- E4: 71/71 exact after remapping Q1/span IDs to exp18; gold status human_reviewed.
- E5: 20 human labels completed: supported=16, partial=2, unsupported=2; LLM-human agreement=0.6, kappa=-0.0667.
- E6: E6.5 replay on exp18 has zero view deltas; E6.6 P=1.0, R=0.3340, F1=0.5007, false-auto=0.
- E7: exp18 F1 is water soft-core 0.9206 and win95pts soft-core 0.8615; contract-strict views are reported separately.

Detailed report: `nova_exp/exp18/FINAL_EXPERIMENT_REPORT.md`.
