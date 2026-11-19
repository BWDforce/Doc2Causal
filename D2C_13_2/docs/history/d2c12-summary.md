# D2C_13_2 Summary

## Status

D2C_13_2 is implemented as an independently runnable `d2c13_2` package. It is a lang7-derived method with embedded extraction flow, not a wrapper that calls external lang7 code.

## Final changes

- Document-level semantic alias normalization uses only mentions, sentence-window context, local causal neighbors, time/number/state compatibility and surface embeddings from `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. The benchmark alias dictionary is not read at runtime.
- The selected embedding auto-merge threshold is `0.93`, selected as the lowest tested threshold with zero false auto-merges in E6.6. Review threshold remains `0.80`.
- Temporal normalization covers clock times plus `noon`, `midday` and `midnight`; numeric normalization covers cardinal and ordinal words.
- Context checks inspect every surface/alias of an entity, not only the alphabetically selected primary label.
- Explicit `Entity: definition` colon definitions are recognized before colon normalization and treated as `context_definition` evidence.
- Explicit polarity, state-modifier and qualifier/scope conflicts are routed away from auto-merge.
- Alias pairs that are causally ordered are demoted to review. Accepted aliases are contracted incrementally; any merge combination that would make the core graph cyclic is demoted to review before `repair_core_graph`.
- Q1 query expectations now distinguish span-backed evidence from candidate-only assertion records without fabricating evidence.
- Saved verifier verdicts can be replayed without extraction or verifier API calls, with semantic edge remapping for alias-induced ID changes.

## E6.6 document-level alias test

Inputs are actual `goldgen_gen5` mentions and sentence-window contexts. Manifest canonical labels are evaluation-only.

| Corpus | Positive | Negative | Auto P | Auto R | Auto F1 | Review coverage | False auto |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| water | 703 | 1058 | 1.0000 | 0.1863 | 0.3141 | 0.0370 | 0.0000 |
| win95pts | 1228 | 2085 | 1.0000 | 0.0293 | 0.0570 | 0.2085 | 0.0000 |
| overall | 1931 | 3143 | **1.0000** | **0.0865** | **0.1592** | **0.1460** | **0.0000** |

Combined positive coverage (auto + review) is `0.2325`, up from `0.2237` before the final guards, while false auto-merges remain zero. The strict auto-merge F1 is `0.1592`; the threshold-selection rule prioritizes zero false auto-merges over a lower threshold that produced one false merge in the development set.

## Verified saved-verdict replay (9 runs per corpus)

Experiment root:

```text
nova_exp/exp16_d2c13_2_verified_replay_9x_final
```

The replay uses the same upstream exp16 lang7 extraction and the saved exp16 verifier verdicts. It makes no extraction or verifier API calls. Unmapped new alias-induced edges are left unverified rather than assigned a fabricated verdict.

| Corpus | Method | Raw F1 | soft_core_balanced F1 | support_aware_core F1 | verified_core F1 | exact_support F1 | context_assisted F1 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| water | D2C_11_1 | 0.9059 | 0.9114 | 0.8721 | 0.8388 | 0.1287 | 0.8351 |
| water | D2C_13_2 | 0.9059 | 0.9103 | 0.8658 | 0.8366 | 0.0743 | 0.8329 |
| win95pts | D2C_11_1 | 0.8586 | 0.8580 | 0.8583 | 0.7585 | 0.3863 | 0.7674 |
| win95pts | D2C_13_2 | 0.8586 | 0.8576 | 0.8574 | 0.7576 | 0.3975 | 0.7680 |

The primary `soft_core_balanced` and `verified_core` views remain within `0.0022` F1 of the matched D2C_11_1 extraction.

Across both corpora, micro F1 changes from `0.8778` to `0.8771` for `soft_core_balanced` and from `0.7894` to `0.7880` for `verified_core`. The small decrease is consistent with the stricter alias identity guards; the primary goal was to retain the lang7-level F1 while adding auditable normalization and interoperability. The result is therefore a conservative normalization/interoperability upgrade rather than a claim of a significant F1 gain.

## Interoperability

Across all 18 replay runs:

- validation: 18/18
- SHACL: 18/18
- JSON/RDF/edge-semantics/entity-identity/query round-trips: 18/18
- independent consumer cross-tool equality: 18/18
- query failures: 0

Verdict mapping coverage: water `1392/1428`; win95pts `2057/2081`. Remaining requests correspond to new alias-induced edges and are intentionally left unverified.

## Validation

```text
python -m pytest -q D2C_13_2\tests --basetemp nova_exp\_pytest_tmp_d2c13_2_final
111 passed
```

## Full live run

The clean live-API run remains:

```cmd
cd /d "<repo-root>"
set "DEEPSEEK_API_KEY=<key>"
D2C_13_2\run_exp16_d2c13_2_clean.cmd
```

The saved-verdict replay is the completed verification experiment in this workspace. A fresh live extraction run is still required before claiming a new stochastic full-run F1 gain.
