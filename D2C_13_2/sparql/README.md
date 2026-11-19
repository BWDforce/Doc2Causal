# Independent SPARQL Q1-Q7 Contract

These query files execute the seven competency questions over a D2C_13_2 Turtle or
JSON-LD artifact. They do not import the `d2c13_2` package and do not read
`artifact.json`.

## Query files

| CQ | File | Required RDF path |
|---|---|---|
| Q1 | `q1_evidence.rq` | Edge -> Assertion -> Span / Document |
| Q2 | `q2_documents.rq` | Edge -> Document support |
| Q3 | `q3_votes.rq` | Vote -> Edge or unordered entity pair |
| Q4 | `q4_conflicts.rq` | ConflictDecision -> Edge or unordered entity pair |
| Q5 | `q5_conditions.rq` | Edge -> Condition + support spans |
| Q6 | `q6_status.rq` | Edge verification and promotion fields |
| Q7 | `q7_cycles.rq` | CycleDecision -> Edge / pair / removed edge |

`q3_entity_surfaces.rq` is an auxiliary query for surface-based vote orientation.

## Canonical result contract

The parity test compares the fields that define the public query answer:

- Q1: assertion, span, document, evidence text, support scope, evidence contract,
  and best-window identifiers.
- Q2: supporting document identifiers.
- Q3: vote identifier, direction, relative direction, document sets, vote counts,
  and vote provenance.
- Q4: decision identifier, kind, rule, selected/dropped value, and linked edges.
- Q5: condition fields, grounding status, grounding spans, and grounding documents.
- Q6: claim kind, verification state, semantic support, evidence grade, promotion,
  support scope, and evidence contract.
- Q7: cycle classification, decision, removed edge, regime flag, and linked edges.

Producer-only diagnostics are not part of this contract: Q1 diagnostics, Q2
document text length, Q4 `vote_ids` / `competing_directions` / `candidate_edge_ids`,
and Q6 `hypothesis_records`.

## Direct use

Run all seven queries for one artifact:

```bat
python D2C_13_2\tools\sparql_consumer.py ^
  --input CSB_exp\results\csb_d2c13_2_aliasguard_latest\hotwork\run_01\artifact.ttl ^
  --output nova_exp\hotwork_sparql_answers.json
```

Run one query for one edge:

```bat
python D2C_13_2\tools\sparql_consumer.py ^
  --input CSB_exp\results\csb_d2c13_2_aliasguard_latest\hotwork\run_01\artifact.ttl ^
  --query Q3 ^
  --edge-id urn:d2c:edge:03811f478da2de27
```


## PROV-O alignment check

- `prov_alignment.rq`: counts provenance-bearing records typed as
  `prov:Entity` and linked to a `prov:Activity`.
- `prov_edge_derivation.rq`: checks that retained edges expose
  `prov:wasDerivedFrom` links to supporting assertions.

Run the check with `tools/check_prov_alignment.py`.
