# D2C_13_2

This directory contains the current Doc2Causal implementation.

## Package layout

| Path | Purpose |
|---|---|
| `d2c13_2/` | Python package and experiment CLI |
| `schema/` | JSON Schema, JSON-LD context, OWL vocabulary, SHACL shapes |
| `sparql/` | Independent Q1--Q7 SPARQL query contract |
| `tools/` | Consumer, parity, replay, validation, and analysis utilities |
| `tests/` | Regression tests |
| `scripts/` | Current and historical experiment launchers |
| `docs/` | Method, experiment, and validation notes |
| `examples/` | Small example inputs |

## Install and test

From the repository root:

```bat
python -m pip install -e D2C_13_2
python -m pytest D2C_13_2\tests -q
```

The reference command uses `python` from an active environment with the
dependencies installed. No API key is required for the unit tests.


## PROV-O alignment

The ontology imports PROV-O and aligns provenance-bearing records to
`prov:Entity`; the construction activity is a `prov:Activity`. Retained edges
link to supporting assertions through `prov:wasDerivedFrom`. Causal claim,
vote, and decision classes remain an explicit `acc:` domain extension.

Check the alignment on exported JSON artifacts:

```bat
python D2C_13_2\tools\check_prov_alignment.py ^
  --artifact-root path\to\artifacts ^
  --output-dir path\to\prov_report
```

## Main experiment

Set `DEEPSEEK_API_KEY`, prepare the local corpora, and run:

```bat
D2C_13_2\scripts\run_exp20_d2c13_2_aliasguard_clean.cmd
```

The script writes outputs under `nova_exp/exp20_d2c13_2_aliasguard_clean` and
does not overwrite an existing run directory unless explicitly resumed.

## Independent consumer

```bat
python D2C_13_2\tools\sparql_consumer.py ^
  --input path\to\artifact.ttl ^
  --output path\to\answers.json
```

The consumer never imports `d2c13_2` and never reads `artifact.json`.

## Documentation

- [Arbitration](docs/arbitration.md)
- [Content-addressed cache](docs/content-cache.md)
- [F1-neutral audit](docs/f1-neutral-audit.md)
- [Validation report](docs/validation-report.md)
- [Experiment plan](docs/experiment-plan.md)
- [Experiment exp20](docs/experiment-exp20.md)
- [Revision notes](docs/revision-notes-13_2.md)
- [Small validation](docs/small-validation.md)
- [Archived notes](docs/history/README.md)
