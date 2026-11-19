# D2C_13_2 small offline validation

This checkpoint validates the post-compilation arbitration layer against the
existing exp18 lang7 runs. No extraction or verifier API call was made.

## Setup

- Source outputs: `nova_exp/exp18/{water,win95pts}/run_01/lang7`
- Replay outputs: `nova_exp/exp_d2c13_2_offline_smoke`
- Config: `D2C_13_2/config.deepseek-official.json`
- Gold manifests: `gold_gen_text/output_gen5/*/seed1/manifest.json`

## Edge-F1 checkpoint

| Corpus | View | TP | FP | FN | F1 |
|---|---|---:|---:|---:|---:|
| water | soft_core | 62 | 4 | 4 | 0.9394 |
| water | soft_core_balanced | 62 | 4 | 4 | 0.9394 |
| water | canonical_soft_core | 62 | 3 | 4 | 0.9466 |
| win95pts | soft_core | 93 | 9 | 19 | 0.8692 |
| win95pts | soft_core_balanced | 95 | 10 | 17 | 0.8756 |
| win95pts | canonical_soft_core | 95 | 10 | 17 | 0.8756 |

`candidate_risk_review` contained only false candidate edges in these two
small checks:

- water: 0 TP / 8 FP;
- win95pts: 0 TP / 3 FP.

This is the intended behavior: candidate conflicts/cycles are routed to review
instead of deleting edges or changing the legacy F1 view.

## Macondo structural replay

Replay of the existing Macondo run produced:

```text
canonical pairs:              215
multi-document pairs:           0
candidate conflicts:            4
candidate cycles:               5
risk-marked edges:             12
```

## Gates already checked

- JSON serialization and RDF/Turtle round-trip: pass on the water replay.
- SHACL validation: pass on the water and win95pts smoke runs.
- Independent consumer equality: pass on the water smoke run.
- Full unit suite: `139 passed` after the final arbitration change.

## Not yet claimed

The `canonical_document` extraction-stage vote mode is implemented and unit
tested, but its downstream F1 effect requires a fresh extraction run because the
saved exp18 lang7 graphs were produced with the previous vote mode.