# Third-party notices

## Current implementation: D2C_13_2

D2C_13_2 is a research implementation derived from the Causal Cartographer
project and the authors' subsequent document-to-artifact work. The base
Causal Cartographer code is distributed under the MIT License; see `LICENSE`.

The current implementation depends on third-party Python packages including
`rdflib`, `pyshacl`, `networkx`, `sentence-transformers`, `requests`, `scipy`,
and `tqdm`. Each dependency is distributed under its own license.

## Legacy implementation: lang5_evo1

The `legacy/lang5_evo1/` tree preserves the earlier extraction pipeline. It
includes a vendored copy of Google LangExtract under
`legacy/lang5_evo1/langextract/` (Apache License 2.0; see the license and README
inside that directory), and engine modules derived from Causal Cartographer.

The legacy tree is retained for research provenance. It is not the primary
implementation of this repository.

## Data

The repository does not redistribute third-party CSB reports, generated corpora,
or experiment outputs. Users are responsible for obtaining those materials and
for complying with their source licenses.
