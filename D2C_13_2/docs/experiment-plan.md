# D2C_13_2 Experiment Plan

## Hypothesis

The D2C_11 pilot showed that increasing a global candidate-pair count does not reliably
improve extraction and can increase prompt length, latency and endpoint confusion.
D2C_13_2 replaces that global cap with source-structure batching:

- candidate pairs are deduplicated by unordered entity pair;
- pairs are ranked by causal cue, same-sentence locality, mention distance and window size;
- evidence text is written once per evidence window;
- relation completion runs one prompt per source paragraph, retaining all candidates for
  that paragraph rather than applying a corpus-specific global pair limit.

## Pilot

```bat
D2C_13_2\run_exp15_d2c13_2_pilot.cmd
```

- 3 runs per corpus
- 6 parallel workers
- temperature 0.3
- balanced verifier
- output `nova_exp/exp15_d2c13_2_pilot`

## Analysis

```bat
D2C_13_2\run_exp15_d2c13_2_analysis.cmd
```

The analysis should compare against D2C_11 pilot 1 and D2C_9:

- raw GML P/R/F1;
- `soft_core_balanced` P/R/F1;
- `verified_core` P/R/F1;
- exact/context-assisted evidence coverage;
- prompt batch count, candidate count and run latency.

The key check is whether paragraph batching improves stability and F1 without using a
fixed candidate-pair count.

## D2C_13_2 arbitration decision run

Use `run_exp19_d2c13_2_clean.cmd` for the first clean extraction run with `canonical_document` voting. Compare `raw`, `pre_verification`, `soft_core_balanced`, `support_aware_core`, `canonical_soft_core`, `canonical_verified_core`, and `candidate_safe_core`. The legacy views must remain available; the arbitration views are the decision layer.
