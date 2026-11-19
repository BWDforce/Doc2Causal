# PROV-O Alignment Check

## Purpose

This small experiment tests the provenance layer of the D2C_13_2 RDF export
against PROV-O. It does not claim full OWL-DL reasoning or equivalence with a
general causal ontology.

## What is checked

1. `acc.ontology.ttl` imports `http://www.w3.org/ns/prov-o`.
2. Artifact, document, span, entity, assertion, condition, retained edge, vote,
   conflict decision, and cycle decision classes are subclasses of `prov:Entity`.
3. Exported records carry explicit `prov:Entity` types and
   `prov:wasGeneratedBy` links to a `prov:Activity`.
4. Retained edges with support or candidate assertions carry
   `prov:wasDerivedFrom` links to assertion records.

## Run

```bat
python D2C_13_2\tools\check_prov_alignment.py ^
  --artifact-root path\to\controlled_artifacts ^
  --artifact-root path\to\csb_artifacts ^
  --output-dir path\to\prov_report
```

## Result

Run over 18 controlled artifacts and six CSB artifacts:

- Artifacts passed: 24/24
- Provenance-bearing records checked: 32,982
- Records typed as `prov:Entity`: 32,982/32,982
- Records linked to `prov:Activity`: 32,982/32,982
- Retained edges with assertions: 3,725
- Edges with `prov:wasDerivedFrom`: 3,725/3,725
- Ontology import and subclass checks: pass

These results establish a PROV-O-aligned provenance layer and explicit
cross-vocabulary access to the exported records. The causal claim, vote, and
decision vocabulary remains a documented `acc:` domain extension; the check does
not assert equivalence with an external causal ontology.
