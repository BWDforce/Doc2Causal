# -*- coding: utf-8 -*-
"""Create the exp14 D2C_9 vs D2C_13_2 comparison report from analysis_summary.json."""

from __future__ import annotations

import json
import statistics as sp
from pathlib import Path

try:
    from scipy.stats import ttest_ind, ttest_rel
except ImportError:
    ttest_ind = None

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "nova_exp" / "exp14_d2c13_2_clean" / "analysis_summary.json"
OUT = ROOT / "nova_exp" / "exp14_d2c13_2_clean" / "comparison_d2c9.md"


def table(headers, rows):
    lines = ["| " + " | ".join(map(str, headers)) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def main():
    data = json.loads(SUMMARY.read_text(encoding="utf-8-sig"))
    agg = data["aggregates"]
    main_rows = []
    for corpus in ("water", "win95pts"):
        for view, label in (
            ("raw", "Raw GML"),
            ("soft_core_balanced", "Soft core balanced"),
            ("support_aware_core", "Support-aware core"),
            ("verified_core", "Verified core"),
        ):
            a = agg[corpus].get(f"D2C_9/{view}")
            b = agg[corpus].get(f"D2C_13_2/{view}")
            if a and b:
                main_rows.append([corpus, label, f"{a['precision_mean']:.4f}", f"{a['recall_mean']:.4f}", f"{a['f1_mean']:.4f} ± {a['f1_sd']:.4f}", f"{b['precision_mean']:.4f}", f"{b['recall_mean']:.4f}", f"{b['f1_mean']:.4f} ± {b['f1_sd']:.4f}", f"{b['f1_mean'] - a['f1_mean']:+.4f}"])
    evidence_rows = []
    for corpus in ("water", "win95pts"):
        for view, label in (
            ("exact_support_core", "Exact support core"),
            ("context_assisted_core", "Context-assisted core"),
            ("partial_evidence_review", "Partial evidence review"),
            ("no_evidence_queue", "No-evidence queue"),
        ):
            x = agg[corpus].get(f"D2C_13_2/{view}")
            if x:
                evidence_rows.append([corpus, label, f"{x['micro_precision']:.4f}", f"{x['micro_recall']:.4f}", f"{x['micro_f1']:.4f}", f"{x['f1_mean']:.4f} ± {x['f1_sd']:.4f}", f"{x['tp']}/{x['fp']}/{x['fn']}"])
    stat_rows = []
    if ttest_ind is not None:
        for view, label in (("raw", "Raw GML"), ("soft_core_balanced", "Soft core balanced"), ("support_aware_core", "Support-aware core"), ("verified_core", "Verified core")):
            for corpus in ("water", "win95pts"):
                a = [r["metrics"]["f1"] for r in data["records"] if r["method"] == "D2C_9" and r["corpus"] == corpus and r["view"] == view]
                b = [r["metrics"]["f1"] for r in data["records"] if r["method"] == "D2C_13_2" and r["corpus"] == corpus and r["view"] == view]
                if a and b:
                    _, pvalue = ttest_ind(b, a, equal_var=False)
                    stat_rows.append([corpus, label, len(a), len(b), f"{pvalue:.4f}"])
    lang7_rows = []
    if ttest_rel is not None:
        for view, label in (("pre_soft_core", "Pre soft core"), ("soft_core_balanced", "Soft core balanced"), ("support_aware_core", "Support-aware core"), ("verified_core", "Verified core"), ("exact_support_core", "Exact support core"), ("context_assisted_core", "Context-assisted core")):
            for corpus in ("water", "win95pts"):
                by = {}
                for r in data["records"]:
                    if r["method"] == "D2C_13_2" and r["corpus"] == corpus and r["view"] in ("raw", view):
                        by.setdefault(r["run"], {})[r["view"]] = r["metrics"]["f1"]
                pairs = [(x["raw"], x[view]) for x in by.values() if "raw" in x and view in x]
                if not pairs:
                    continue
                deltas = [new - raw for raw, new in pairs]
                pvalue = ttest_rel([new for _, new in pairs], [raw for raw, _ in pairs]).pvalue
                lang7_rows.append([corpus, label, f"{sp.mean([raw for raw, _ in pairs]):.4f}", f"{sp.mean([new for _, new in pairs]):.4f}", f"{sp.mean(deltas):+.4f}", f"{pvalue:.4f}", f"{sum(v > 0 for v in deltas)}/{sum(v == 0 for v in deltas)}/{sum(v < 0 for v in deltas)}"])
    ev = data["d2c13_2_evidence_core_edges"]
    evidence_meta = data.get("d2c13_2_evidence_metadata", {})
    run = data["run_summary"]
    def f1_value(corpus: str, method: str, view: str) -> float | None:
        value = agg[corpus].get(f"{method}/{view}")
        return None if value is None else float(value["f1_mean"])

    def summary_line(view: str) -> str:
        parts = []
        for corpus in ("water", "win95pts"):
            old = f1_value(corpus, "D2C_9", view)
            new = f1_value(corpus, "D2C_13_2", view)
            if old is not None and new is not None:
                parts.append(f"{corpus}: {old:.4f} -> {new:.4f} (delta {new - old:+.4f})")
        return "; ".join(parts)

    soft_summary = summary_line("soft_core_balanced")
    raw_summary = summary_line("raw")
    verified_summary = summary_line("verified_core")
    exact_summary = []
    context_summary = []
    for corpus in ("water", "win95pts"):
        exact = agg[corpus].get("D2C_13_2/exact_support_core")
        context = agg[corpus].get("D2C_13_2/context_assisted_core")
        if exact:
            exact_summary.append(f"{corpus}: P={exact['micro_precision']:.4f}, R={exact['micro_recall']:.4f}, F1={exact['micro_f1']:.4f}")
        if context:
            context_summary.append(f"{corpus}: P={context['micro_precision']:.4f}, R={context['micro_recall']:.4f}, F1={context['micro_f1']:.4f}")
    exact_summary = "; ".join(exact_summary)
    context_summary = "; ".join(context_summary)
    text = f"""# exp14: D2C_13_2 vs D2C_9

> Generated from the completed exp14 end-to-end run. Regenerate after any later D2C_13_2 code change.

## Protocol

- D2C_9: `nova_exp/exp12`, 9 runs per corpus, D2C_9 config.
- D2C_13_2: `nova_exp/exp14_d2c13_2_clean`, 9 runs per corpus, D2C_13_2 config.
- Both use `deepseek-v4.1-flash-1017`, `temperature=0.3`, `verification_policy=balanced`, and the same gold manifests.
- The runs are independent stochastic runs; differences are descriptive, not a paired significance test.

## Main comparison

{table(["Corpus", "View", "D2C_9 P", "D2C_9 R", "D2C_9 F1", "D2C_13_2 P", "D2C_13_2 R", "D2C_13_2 F1", "ΔF1"], main_rows)}

## D2C_13_2 evidence-aware views

Micro P/R/F1 aggregate all TP/FP/FN across runs. Mean F1 ± SD treats each run separately.

{table(["Corpus", "View", "Micro P", "Micro R", "Micro F1", "Mean F1 ± SD", "TP/FP/FN"], evidence_rows)}

## D2C_13_2 evidence completeness

Core edge counts across 18 runs:

```json
{json.dumps(ev, ensure_ascii=False, indent=2)}
```

Packaging metadata:

```json
{json.dumps(evidence_meta, ensure_ascii=False, indent=2)}
```

The `soft_core_balanced` view remains the promotion view. Its membership is decoupled from canonical exact/context evidence, so demoted candidates can remain visible in evidence-audit views without being counted as promoted core edges.

## D2C_13_2 vs same-run lang7 baseline

The raw GML and D2C_13_2 views come from the same exp14 runs, so this is a paired comparison. Delta = D2C_13_2 view F1 - raw lang7 F1. Wins/ties/losses count paired runs.

{table(["Corpus", "D2C_13_2 View", "Lang7 Raw F1", "View F1", "Delta", "Paired p", "W/T/L"], lang7_rows)}

## Exploratory Welch tests

These are unpaired Welch tests over 9 independent runs per corpus. No multiple-comparison correction is applied; use them as descriptive guidance, not definitive significance claims.

{table(["Corpus", "View", "n D2C_9", "n D2C_13_2", "Welch p"], stat_rows)}

## Interpretation

- Primary promotion view (`soft_core_balanced`) F1, D2C_9 -> D2C_13_2: {soft_summary}.
- Raw upstream GML F1, D2C_9 -> D2C_13_2: {raw_summary}.
- Verified core F1: {verified_summary}.
- Exact support core micro metrics: {exact_summary}.
- Context-assisted core micro metrics: {context_summary}.
- The verifier can now accept `entailed` support backed by a complete context window while recording `support_scope=context_assisted`; exact/context audit views use the frozen pre-verifier evidence package and survive later candidate demotion.

## Conclusion

D2C_13_2 keeps promotion decisions and evidence-audit decisions as separate layers. The verifier may demote a claim without deleting its exact/context evidence record, so the paper can report raw extraction, pre-verification evidence, grounded core, direct core and verified core without conflating them. The main `soft_core_balanced` F1 should still be interpreted as a promotion-oriented view, while exact/context and verified-core results characterize evidence retention and auditability.
## Run reliability

- Planned: {run['planned']}
- Completed: {run['total']}
- Failed: {run['failed']}
- Mean run duration: {run['mean_elapsed_seconds']} s
- Maximum run duration: {run['max_elapsed_seconds']} s
"""
    OUT.write_text(text, encoding="utf-8")
    print({"output": str(OUT)})


if __name__ == "__main__":
    main()
