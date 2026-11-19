# D2C_13_2 Baseline: exp18

`exp18` is treated as the completed D2C_12_1 F1 baseline for D2C_13_2. All 18 artifacts are present: water run_01-run_09 and win95pts run_01-run_09.

The F1 values and artifact hashes are frozen in:

```text
D2C_13_2/baseline_exp18.json
```

## F1 baseline

| Corpus | View | Precision | Recall | F1 |
|---|---|---:|---:|---:|
| water | raw | 0.9103 | 0.9057 | 0.9080 |
| water | pre_verification | 0.9450 | 0.8973 | 0.9206 |
| water | soft_core_balanced | 0.9237 | 0.9175 | 0.9206 |
| water | direct_core | 0.9467 | 0.7778 | 0.8540 |
| water | verified_core | 0.9487 | 0.7778 | 0.8548 |
| win95pts | raw | 0.8817 | 0.8502 | 0.8657 |
| win95pts | pre_verification | 0.8715 | 0.8075 | 0.8383 |
| win95pts | soft_core_balanced | 0.8816 | 0.8423 | 0.8615 |
| win95pts | direct_core | 0.9390 | 0.6875 | 0.7938 |
| win95pts | verified_core | 0.9385 | 0.6815 | 0.7897 |

## D2C_13_2 audit-only replay

The D2C_13_2 canonical audit was replayed on all 18 exp18 artifacts without API calls. The original artifact SHA-256 values were unchanged.

- water: Q3/Q4/Q7-ready in 9/9 runs.
- win95pts: Q3/Q4-ready in 9/9 runs, Q7-ready in 8/9 runs.
- Aggregate: `nova_exp/audit_d2c13_2_exp18_aggregate.json`
- Per-run replay summary: `nova_exp/audit_d2c13_2_exp18_summary.json`
- Per-run audit sidecars: `nova_exp/audit_d2c13_2_exp18/<corpus>/<run>/`

## Interpretation

The first D2C_13_2 milestone is F1-neutral. The exp18 F1 values remain the valid baseline for D2C_13_2 Part I. Any later change to canonical arbitration, alias-induced core merges, candidate promotion or cycle removal must be evaluated as a new F1-affecting version.
