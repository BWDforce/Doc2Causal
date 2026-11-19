# D2C_13_2 F1-Neutral Canonical Audit

D2C_13_2 is the first milestone after D2C_12_1. It adds a read-only audit layer for Q3/Q4/Q7 and intentionally does not change the core extraction, edge set, promotion policy or F1 views.

## Part I: implemented and F1-neutral

- canonical evidence pair reconstruction from the Lang7 evidence ledger;
- artifact alias mapping used to canonicalize raw evidence endpoints;
- Q3 document-level forward/reverse votes, including same-direction multi-document support;
- Q4 audit taxonomy:
  - `direction_conflict`
  - `sign_conflict`
  - `mechanism_conflict`
  - `condition_conflict`
  - `candidate_conflict`
  - `entity_identity_conflict`
- Q7 cycle audit:
  - `evidence_cycle`
  - `candidate_cycle`
  - `hypothesis_cycle`
  - `mixed_cycle`
  - `regime_candidate_cycle`
- candidate reciprocal pairs recorded without promoting or deleting them;
- artifact hash recorded before/after the audit to verify the core artifact is unchanged.

The audit does not modify:

- `edges`
- edge direction
- `edge.kind`
- `promotion_status`
- `soft_core_balanced`
- `verified_core`
- any F1 calculation view

## Part II: not implemented in D2C_13_2

These changes can alter F1 and are intentionally deferred:

- switching to `canonical_document` vote mode before conflict arbitration;
- relation-aware automatic alias merging that changes canonical pairs in the core graph;
- using candidate conflicts to demote core edges;
- using candidate cycles to remove edges;
- changing promotion thresholds or soft/verified-core construction.

They require a separate version and full n=9 F1 comparison.

## Outputs

For a normal D2C_13_2 run:

```text
<run_dir>/audit/audit_report.json
<run_dir>/audit/canonical_evidence.json
<run_dir>/audit/canonical_votes.json
<run_dir>/audit/canonical_conflicts.json
<run_dir>/audit/canonical_cycles.json
```

The normal pipeline summary contains:

```text
canonical_audit
canonical_audit_error
```

## Replay an existing D2C_12_1 or D2C_13_2 run

No API call and no re-extraction:

```cmd
cd /d "<repo-root>\D2C_13_2"
"python" -m d2c13_2 audit-run ^
  --run-dir "..\nova_exp\exp18\water\run_01" ^
  --out-dir "..\nova_exp\exp13_audit\water\run_01"
```

Or use the tool:

```cmd
"python" D2C_13_2\tools\run_canonical_audit.py ^
  --exp-root "nova_exp\exp18" ^
  --out-dir "nova_exp\audit_d2c13_2"
```

## Small validation completed

- synthetic audit contract test: passed;
- `exp18/water/run_01` replay:
  - canonical pairs: 136
  - multi-document pairs: 11
  - direction conflicts: 4
  - candidate conflicts: 2
  - candidate/mixed cycles: 9
- `exp18/win95pts/run_01` replay:
  - canonical pairs: 176
  - multi-document pairs: 6
  - direction conflicts: 2
  - candidate conflicts: 1
  - cycles: 3
- Macondo replay:
  - canonical pairs: 215
  - multi-document pairs: 0
  - candidate conflicts: 4
  - candidate cycles: 4
  - regime candidate cycles: 1
  - F1-neutral artifact hash unchanged: true

## Audit replay across all exp18 runs

The audit-only layer was replayed on all 18 exp18 artifacts without API calls. The artifact hash was unchanged for every run.

| Corpus | Runs | Q3-ready | Q4-ready | Q7-ready | Multi-doc pairs mean | Conflicts mean | Cycles mean |
|---|---:|---:|---:|---:|---:|---:|---:|
| water | 9 | 9 | 9 | 9 | 10.11 | 26.56 | 6.89 |
| win95pts | 9 | 9 | 9 | 8 | 6.56 | 72.78 | 4.22 |

Aggregate files:

```text
nova_exp/audit_d2c13_2_exp18_summary.json
nova_exp/audit_d2c13_2_exp18_aggregate.json
```

These results validate audit coverage; they are not new extraction F1 results.
