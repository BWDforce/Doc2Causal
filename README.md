# Doc2Causal

Schema-constrained, theme-anchored multi-document **causal graph extraction** from
technical/industrial documents (manuals, test records, fault reports, maintenance
logs), producing a **DAG**-formatted world model (GML + PNG) with auditable
evidence trails.

The repository ships the latest version of our extraction pipeline
(**lang5_evo1**) together with the benchmark-corpus generator used in our
experiments. The vanilla discovery agent of the original
[Causal Cartographer](https://github.com/ggendro/causal-cartographer) project is
**not** part of this repository; only the engine modules required by our pipeline
are vendored under `src/` (see NOTICE.md).

## Pipeline overview (lang5_evo1 — latest)

| Stage | Mechanism |
|---|---|
| Grounded pre-extraction | LangExtract span-level extraction -> candidate causal graph (`pre_graph`) |
| Theme anchoring | corpus theme injected into extraction + graph-building prompts to suppress cross-event noise |
| Compact schema-constrained graph building | bounded steps, mandatory code skeleton, schema validation, retry + pre_graph fallback |
| Cross-document conflict arbitration | direction / sign / rung / mechanism decided by majority vote across documents, ties -> earliest evidence; every decision logged (`conflicts.json`) |
| Meta propagation | fills missing effect_sign / rung / mechanism on final edges by name-similarity to labelled candidates |
| **Semantic merge rules** *(evo)* | representative-only merging (no transitive chain collapse), numeric/timestamp protection (names differing only in numbers never merge), slot-pattern protection, degenerate-edge cleanup (`merge_stats.json`) |
| **Time-aware identity** *(evo1)* | clock-time/ sampling-point protection — names carrying different times never merge or reuse, enforced both at prompt level and merge level |
| **Evidence-based de-cycling / cycle-as-conflict** *(evo1)* | remaining cycles treated as conflicts and broken by removing the weakest-evidence edge; every removal appears as a `kind:"cycle"` record in `conflicts.json` (plus `decycle.json`); output graph is always a DAG |

## Quick start

```bash
pip install -r requirements.txt              # includes -e ./langextract (vendored third-party lib)

# documents are JSON files: {"title": {"eng": ...}, "summary": "text"}
python multi_extract/lang5_evo1/run_extract.py \
    --input-dir path/to/docs \
    --output-dir path/to/out \
    --theme "your system theme" \
    --model deepseek-chat --base-url https://api.deepseek.com \
    --temperature 0.3 --max-steps 2 --extraction-passes 1 \
    --propagate-threshold 0.2 --merge-threshold 0.7
```

API key via `DEEPSEEK_API_KEY` or `--api-key` (any OpenAI-compatible endpoint
works through `--base-url` / `--model`).

Incremental continuation (4+1 protocol): pass `--init-graph <previous.gml>` to
seed the world graph and process only the new document set.

Ablation switches: `--legacy-merge` (old chain-closure merging),
`--keep-cycles` (skip de-cycling), `--no-merge`, `--no-filter`.

Outputs: `causal_graph_*.gml`, `causal_graph_*.png`, `run.log` (log not committed),
`conflicts.json`, `decycle.json`, `merge_stats.json`.

## Gold-by-construction benchmark corpus

`gold_gen_text/` generates multi-document corpora from a known gold DAG
(topology-preserving chain decomposition, closed paraphrasing vocabulary,
deterministic guard-railed false-cause bait, <=400-word docs). See
`gold_gen_text/README.md`.

## Experiments

`graph_exp/exp12/` reproduces the discovery-vs-lang5_evo comparison on the
generated corpora with a 4+1 incremental protocol (docs 1-4, then doc 5) and
gold-aware evaluation (edge P/R/F1, node recall, bait hit rate, DAG status,
conflict/decycle/merge statistics, phase0->phase1 edge deltas). See
`graph_exp/exp12/README.md`.

## Repository layout

```
multi_extract/lang5_evo1/run_extract.py  latest pipeline (see Stages above)
multi_extract/lang5_evo/run_extract.py   previous iteration (semantic merge + decycling)
multi_extract/run_multi_langextract_2.py shared extraction core (our CausalCoT edition)
pipeline.py                              engine wiring (vendored + modified)
src/causal_world_modelling_agent/        engine modules required by the pipeline
src/{visualize_graph.py,evaluate_graph.py}
langextract/                             vendored third-party library (Apache-2.0)
gold_gen_text/                           gold-DAG -> document corpus generator
graph_exp/exp12/                         benchmark experiment driver (4+1, n=3)
```

## Third-party credits

- Base engine derived from [Causal Cartographer](https://github.com/ggendro/causal-cartographer) (MIT, Gaël Gendron).
- Grounded extraction uses [LangExtract](https://github.com/google/langextract) (Apache-2.0, Google LLC), vendored under `langextract/`.
- LLM/agent plumbing: `smolagents`, `langchain` (installed via requirements).
See `NOTICE.md` for details.
