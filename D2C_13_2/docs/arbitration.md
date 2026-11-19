# D2C_13_2 F1-affecting arbitration layer

D2C_13_2 keeps the D2C_13 F1-neutral audit intact and adds an independent,
config-controlled arbitration layer. The legacy `raw`, `pre_verification`,
`soft_core_balanced`, and `verified_core` views are not rewritten.

## Configuration

```json
{
  "canonical_arbitration": {
    "enabled": true,
    "vote_mode": "canonical_document",
    "relation_role_guard": true,
    "candidate_conflict_policy": "needs_review",
    "candidate_cycle_policy": "needs_review",
    "require_canonical_support": false,
    "canonical_support_min_documents": 1,
    "canonical_support_require_direction": true,
    "max_cycle_length": 12,
    "max_cycles": 200
  }
}
```

`vote_mode=canonical_document` is passed through to the embedded lang7 flow.
The other policies are applied after compilation.

## Added behavior

1. **Canonical-document arbitration.** The extraction-stage direction vote can
   coalesce aliases before document-level voting.
2. **Relation-aware alias guard.** A document-level alias candidate is not
   auto-merged when the two mentions are observed repeatedly in incompatible
   source/target causal roles. The pair remains in the review queue.
3. **Canonical pair replay.** The final alias mapping is used to recompute
   unordered canonical-pair support, direction-specific support, and
   multi-document support. These fields are emitted on every edge and in
   `artifact["canonical_pair_replay"]`.
4. **Candidate conflict/cycle risk.** Reciprocal hypothesis pairs are marked
   `candidate_conflict`; hypothesis edges in candidate, mixed or regime
   candidate cycles are marked `candidate_cycle`. Grounded evidence edges in
   mixed cycles are retained without being blamed for the hypothesis risk.
5. **Conservative promotion.** Risk-marked edges remain in the artifact, enter
   `candidate_risk_review`/`arbitration_review`, and are excluded only from the
   new arbitration views unless policy is explicitly changed to `keep`.
6. **Q4/Q7 decision projection.** Candidate conflicts and candidate/mixed cycles
   are projected into the artifact-level `conflict_decisions` and
   `cycle_decisions` arrays with `decision_source` provenance. Q4 and Q7 can
   therefore retrieve them directly; unrelated compiler and semantics decisions
   are preserved. Q3 is not synthesized from candidate risk because Q3 remains
   a document-direction vote query.
7. **Exact assertion rebinding before core promotion.** If alias normalization
   leaves a span-only candidate with canonical assertions, the verifier stage
   rebinds only same-direction assertions inside the edge document/span scope.
   Promotion to `direct_core` requires both assertion and span authority;
   otherwise the evidence remains a candidate under review.

## New views

| View | Meaning |
|---|---|
| `canonical_soft_core` | `soft_core_balanced` after candidate-risk arbitration |
| `canonical_verified_core` | `verified_core` after candidate-risk arbitration |
| `canonical_multi_document_core` | canonical soft-core edges with multi-document support |
| `candidate_safe_core` | support-aware high/medium candidates with no candidate risk |
| `candidate_risk_review` | edges carrying candidate conflict/cycle flags |
| `arbitration_review` | all edges not eligible for an arbitration core view |

## Offline validation

No extraction or verifier API calls are required:

```cmd
set "PYTHONPATH=%CD%\D2C_13_2"
python D2C_13_2\tools\replay_canonical_arbitration.py ^
  --artifact nova_exp\exp18\water\run_01\artifact.json ^
  --output nova_exp\_d2c13_2_replay_water_01.json ^
  --config D2C_13_2\config.deepseek-official.json
```

On water/run_01 and win95pts/run_01, the offline replay matched or improved the legacy balanced soft-core F1 while moving candidate false positives into the risk-review view.
The `canonical_document` extraction mode itself is covered by a unit test; a
full extraction rerun is still required before claiming its F1 effect.