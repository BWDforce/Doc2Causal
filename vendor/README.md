# Runtime Compatibility Stack

This directory contains the LangExtract-based compatibility code required by the
current D2C_13_2 extraction pipeline:

- `multi_extract/run_multi_langextract_2.py`: extraction helper used by the
  embedded lang7 flow;
- `pipeline.py`: legacy engine wiring;
- `src/`: engine modules;
- `langextract/`: vendored Google LangExtract 1.7.0 (Apache-2.0).

The complete earlier `lang5_evo1` project is preserved under
`legacy/lang5_evo1/`. The files here are the subset required to run the latest
implementation without keeping the old project tree at the repository root.
