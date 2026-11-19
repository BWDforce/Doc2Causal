# D2C Content-Addressed Extraction Cache

This cache avoids repeating the expensive Lang7 extraction stage when the same corpus and semantics-affecting settings are used again.

## What is cached

Only the `lang7_extract` stage output is cached. It includes the extraction checkpoints, evidence ledger, merge proposals, conflict/decycle sidecars and extraction graph products.

Compilation, verification and audit views are not skipped by this first version.

## Cache key

The key is a SHA-256 over:

- the sorted JSON document files and their content hashes;
- extractor model, endpoint and max-output-token setting;
- theme and extractor temperature;
- entity-first semantics: enabled flag, inventory cap, chunk cap, inventory temperature, selection policy, relation batch policy and inventory-node attachment;
- fixed D2C Lang7 options: two agent steps, one extraction pass, document vote mode and evidence union;
- source hashes for `d2c13_2/lang7_flow`, `vendor/multi_extract` and `vendor/langextract/langextract`;
- a stable run namespace.

Parallel worker counts and verifier settings are deliberately excluded: they change scheduling, not the semantic extraction contract.

Changing a document byte, model, theme, temperature, namespace or extraction implementation produces a different key.

## Stochastic run namespaces

Intentional repeated runs must not share one cached extraction. A stable cache namespace keeps them distinct:

```text
run_01
run_02
...
```

`d2c13_2.experiment` automatically passes the run id as the namespace. `CSB_exp/scripts/run_csb.py` passes `<corpus>/<run_id>`. The direct CLI exposes:

```text
--cache-namespace run_01
```

Re-running the same namespace with unchanged input and config is a cache hit. Running a different namespace creates an independent extraction and preserves n-run variability.

## Default location

```text
D2C_13_2/.cache/content-addressed/lang7_extract/<sha256>/
```

The cache directory is configurable in `runtime.content_cache_dir`. It can be disabled with:

```json
{"runtime": {"content_cache_enabled": false}}
```

## Restore behavior

On a hit, D2C copies the cached Lang7 output into the requested run directory and writes:

```text
content_cache_hit.json
```

The normal compile/enhance stage then runs from that cached extraction output.

On a miss, a completed extraction is copied into the content-addressed entry and `cache_manifest.json` is written last as the completion marker. Entries without this marker are treated as incomplete and are never restored.

To force a fresh extraction:

```text
--refresh-content-cache
```

or, in the CSB runner:

```text
--force-extract
```

The new successful output is then committed to the content-addressed cache.

## CLI

Inspect a key without running extraction:

```cmd
"python" D2C_13_2\tools\content_cache_cli.py key ^
  --input-dir data\Test-text\input\csb_macondo_q7 ^
  --config CSB_exp\config.json ^
  --theme "CSB Macondo blowout, well control, human factors and regulatory safety management" ^
  --temperature 0.3 ^
  --cache-namespace "macondo/run_01"
```

Seed the cache from an already completed Lang7 run:

```cmd
"python" D2C_13_2\tools\content_cache_cli.py seed ^
  --input-dir data\Test-text\input\csb_macondo_q7 ^
  --config CSB_exp\config.json ^
  --theme "CSB Macondo blowout, well control, human factors and regulatory safety management" ^
  --temperature 0.3 ^
  --cache-namespace "macondo/run_01" ^
  --source-dir CSB_exp\results\macondo_q7\artifacts\macondo\run_01\lang7
```

List cache entries:

```cmd
"python" D2C_13_2\tools\content_cache_cli.py list --config CSB_exp\config.json
```

## Current seeded entry

The completed Macondo Lang7 extraction has been imported with namespace `macondo/run_01`. Its key begins with:

```text
12413fbf6e43c9f9
```

All 18 completed `exp18` Lang7 extractions for water/win95pts have also been seeded with namespaces `run_01` through `run_09`. A machine-readable seed report is stored at:

```text
D2C_13_2/tools/seeded_exp18_cache.json
```

## Scope limitation

This is a corpus-level extraction cache. If one document changes, the corpus key changes and extraction is rerun. Per-document extraction caching is a later optimization because Lang7 currently processes documents as one corpus-level graph-building flow.
