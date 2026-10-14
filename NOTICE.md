# Third-party notices

## Causal Cartographer (base engine)
The engine modules under `src/` derive from
https://github.com/ggendro/causal-cartographer
Copyright (c) 2025 Gaël Gendron, MIT License — see `LICENSE`.

Our modifications center on the document-extraction flow
(`multi_extract/`, `pipeline.py`): theme anchoring, compact schema-constrained
graph building, cross-document conflict arbitration, semantic merge rules,
evidence-based de-cycling and the gold-by-construction corpus generator
(`gold_gen_text/`). The vanilla discovery/inference entry scripts of the
original project are intentionally NOT included.

## LangExtract (grounded extraction library)
Directory `langextract/` is a vendored copy of
https://github.com/google/langextract
Copyright 2025 Google LLC, Apache License 2.0 — see `langextract/LICENSE`.

## Dependencies installed via requirements.txt
`smolagents`, `langchain-community`, `langchain-core`, `networkx`, `flask`,
`matplotlib`, `numpy`, `pandas`, `more-itertools` — each distributed under
their own license.
