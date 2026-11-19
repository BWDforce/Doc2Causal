# Doc2Causal

Doc2Causal is a research implementation for converting technical and industrial
documents into evidence-bearing, RDF-portable causal artifacts. The latest
release in this repository is **D2C_13_2**, which combines evidence packaging,
scope-aware verification, canonical-document voting, alias guarding,
conflict/cycle arbitration, SHACL validation, and independent SPARQL access to
the seven competency questions Q1--Q7.

The repository is organized around the current research artifact rather than the
earlier `lang5_evo1` extraction pipeline. The prior implementation is retained
under [`legacy/lang5_evo1`](legacy/lang5_evo1/) for provenance.

## What is included

- Entity-first relation completion over document bundles.
- Evidence-window packaging and evidence-contract checks.
- Verifier support, direction, scope, and promotion state.
- Document-level alias normalization and canonical voting.
- Conflict and cycle decisions that remain queryable.
- PROV-O-aligned provenance for artifacts, evidence, votes, and decisions.
- JSON Schema, JSON-LD, Turtle, and SHACL assets.
- Independent SPARQL Q1--Q7 consumer and producer/consumer parity harness.
- Unit tests for arbitration, semantics, validation, caching, and query access.
- The previous `lang5_evo1` pipeline archived under `legacy/`.

## Repository layout

```text
.
??? D2C_13_2/                 latest implementation
?   ??? d2c13_2/              Python package
?   ??? schema/               JSON Schema, JSON-LD context, OWL, SHACL
?   ??? sparql/               Q1--Q7 query contract and files
?   ??? scripts/              current experiment and parity launchers
?   ??? tests/                regression tests
?   ??? tools/                runtime, replay, analysis, and parity tools
?   ??? docs/                 method, experiment, and validation notes
??? vendor/                   LangExtract compatibility stack used by D2C_13_2
??? legacy/lang5_evo1/        earlier extraction pipeline
??? CITATION.cff
??? LICENSE
??? NOTICE.md
```

## Requirements

Python 3.10 or newer is required. The reference environment is Linux, macOS, or
Windows with an OpenAI-compatible chat-completions endpoint.

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e D2C_13_2
```

On Linux/macOS, activate the environment with `source .venv/bin/activate`.

## Quick start

Set the API key through the environment; never put a key in a configuration file
or commit it to Git.

```bat
set "DEEPSEEK_API_KEY=<your-api-key>"
```

Run the package tests:

```bat
python -m pytest D2C_13_2\tests -q
```

The test suite uses synthetic checks by default. Tests that require the
third-party corpora or generated artifacts are skipped when those data are not
present.

Run the current full experiment:

```bat
D2C_13_2\scripts\run_exp20_d2c13_2_aliasguard_clean.cmd
```

The launcher uses nine fresh runs on each controlled corpus with balanced
verification and promotion. Full runs make live API calls and require the local
document corpora described by the experiment scripts.

## Independent SPARQL Q1--Q7

The independent consumer is deliberately outside the `d2c13_2` package. It
imports only `rdflib` and the standard library and reads Turtle or JSON-LD:

```bat
python D2C_13_2\tools\sparql_consumer.py ^
  --input path\to\artifact.ttl ^
  --output path\to\answers.json
```

The parity harness compares the independent answers with the frozen producer
reports. Paths to those artifacts are supplied explicitly:

```bat
python D2C_13_2\tools\compare_sparql_parity.py ^
  --controlled-report path\to\e4_report.json ^
  --csb-root path\to\csb_results ^
  --output-dir path\to\parity_output
```

See [`D2C_13_2/sparql/README.md`](D2C_13_2/sparql/README.md) for the public query
contract and [`D2C_13_2/docs/prov-o-alignment.md`](D2C_13_2/docs/prov-o-alignment.md)
for the PROV-O interoperability check.

## Data and artifacts

This repository does **not** redistribute the third-party CSB reports or the
generated experiment outputs. The scripts expect those files to be prepared
locally under paths such as `data/` and `nova_exp/`. Generated artifacts,
caches, logs, and API credentials are excluded by `.gitignore`.

## Configuration

The default configuration is `D2C_13_2/config.json`. Alternative endpoint
configurations are stored alongside it. All configurations read the API key from
`DEEPSEEK_API_KEY` or another explicitly configured environment variable.

## Legacy implementation

The runtime subset of the LangExtract stack used by the current pipeline is
under [`vendor/`](vendor/). The complete previous `lang5_evo1` tree, including
its corpus generator, vendored LangExtract copy, and experiment drivers, is under
[`legacy/lang5_evo1`](legacy/lang5_evo1/). It is preserved for transparency and
is not the primary implementation of the current release.

## Citation

Use the metadata in [`CITATION.cff`](CITATION.cff). If you use the code, please
cite the associated Doc2Causal paper when it becomes available.

## License

The repository is distributed under the MIT License. Third-party components and
vendored code are acknowledged in [`NOTICE.md`](NOTICE.md) and in the legacy
tree.
