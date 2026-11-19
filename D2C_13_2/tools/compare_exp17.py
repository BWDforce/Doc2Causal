# -*- coding: utf-8 -*-
"""Compare D2C_13_2 replay on exp17 with the original exp17 D2C_12 analysis."""

from __future__ import annotations
import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEWS = (
    'raw', 'soft_core_balanced', 'support_aware_core', 'verified_core',
    'contract_support_aware_core', 'contract_verified_core',
    'exact_support_core', 'context_assisted_core', 'evidence_contract_core',
)


def _aggregate(rows, view):
    vals = [r[view] for r in rows if view in r]
    if not vals:
        return None
    tp = sum(int(v['tp']) for v in vals)
    fp = sum(int(v['fp']) for v in vals)
    fn = sum(int(v['fn']) for v in vals)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {
        'n': len(vals),
        'precision_mean': round(statistics.mean(float(v['precision']) for v in vals), 4),
        'recall_mean': round(statistics.mean(float(v['recall']) for v in vals), 4),
        'f1_mean': round(statistics.mean(float(v['f1']) for v in vals), 4),
        'f1_sd': round(statistics.stdev(float(v['f1']) for v in vals), 4) if len(vals) > 1 else 0.0,
        'micro_precision': round(p, 4), 'micro_recall': round(r, 4), 'micro_f1': round(f1, 4),
        'tp': tp, 'fp': fp, 'fn': fn,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Compare D2C_13_2 replay with exp17 D2C_12')
    parser.add_argument('--replay-root', default=str(ROOT / 'nova_exp' / 'exp17_d2c13_2_verified_replay_9x'))
    parser.add_argument('--baseline-analysis', default=str(ROOT / 'nova_exp' / 'exp17' / 'analysis_vs_d2c11_1.json'))
    parser.add_argument('--out', default=None)
    args = parser.parse_args()
    root = Path(args.replay_root)
    baseline = json.loads(Path(args.baseline_analysis).read_text(encoding='utf-8-sig'))
    data = {'protocol': 'same exp17 lang7 extraction; D2C_13_2 recompilation with exp17 saved verifier verdicts; no API', 'corpora': {}}
    for corpus in ('water', 'win95pts'):
        rows = []
        for run in sorted((root / corpus).glob('run_*')):
            score = run / 'score.json'
            if not score.exists():
                continue
            views = json.loads(score.read_text(encoding='utf-8-sig'))['views']
            rows.append(views)
        old = baseline.get('aggregates', {}).get(corpus, {})
        old_views = {v: old.get('D2C_12/' + v) for v in VIEWS if old.get('D2C_12/' + v)}
        new_views = {v: _aggregate(rows, v) for v in VIEWS if _aggregate(rows, v)}
        contracts = []
        for run in sorted((root / corpus).glob('run_*')):
            art_path = run / 'artifact.json'
            if not art_path.exists():
                continue
            art = json.loads(art_path.read_text(encoding='utf-8-sig'))
            edges = [e for e in art.get('edges', []) if e.get('kind') not in {'rejected', 'unsupported_hypothesis'}]
            contracts.append({
                'edges': len(edges),
                'contract_complete': sum(1 for e in edges if e.get('evidence_contract_complete')),
                'accepted_incomplete': sum(1 for e in edges if e.get('verification_status') == 'accepted' and not e.get('evidence_contract_complete')),
            })
        data['corpora'][corpus] = {
            'n_replay': len(rows),
            'baseline_exp17_d2c12': old_views,
            'd2c13_2_replay': new_views,
            'contract': {
                'runs': len(contracts),
                'edges': sum(x['edges'] for x in contracts),
                'contract_complete': sum(x['contract_complete'] for x in contracts),
                'contract_coverage': round(sum(x['contract_complete'] for x in contracts) / max(1, sum(x['edges'] for x in contracts)), 4),
                'accepted_incomplete': sum(x['accepted_incomplete'] for x in contracts),
            },
        }
    out = Path(args.out) if args.out else root / 'comparison_vs_exp17.json'
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# D2C_13_2 vs exp17 D2C_12', '', '| Corpus | View | D2C_12 F1 | D2C_13_2 F1 | Delta | D2C_13_2 P/R/F1 |', '| --- | --- | ---: | ---: | ---: | --- |']
    for corpus, block in data['corpora'].items():
        for view in VIEWS:
            a = block['baseline_exp17_d2c12'].get(view)
            b = block['d2c13_2_replay'].get(view)
            if not a or not b:
                continue
            lines.append(f"| {corpus} | {view} | {a.get('micro_f1')} | {b.get('micro_f1')} | {round(b.get('micro_f1',0)-a.get('micro_f1',0),4)} | {b.get('micro_precision')}/{b.get('micro_recall')}/{b.get('micro_f1')} |")
    lines += ['', '## Contract-strict views (D2C_13_2 only)', '', '| Corpus | View | P | R | F1 | TP/FP/FN |', '| --- | --- | ---: | ---: | ---: | --- |']
    for corpus, block in data['corpora'].items():
        for view in ('contract_support_aware_core', 'contract_verified_core', 'evidence_contract_core'):
            b = block['d2c13_2_replay'].get(view)
            if b:
                lines.append(
                    f"| {corpus} | {view} | {b.get('micro_precision')} | "
                    f"{b.get('micro_recall')} | {b.get('micro_f1')} | "
                    f"{b.get('tp')}/{b.get('fp')}/{b.get('fn')} |"
                )
    lines += ['', '## Evidence contract gate', '']
    for corpus, block in data['corpora'].items():
        c = block['contract']; lines.append(f"- {corpus}: {c['contract_complete']}/{c['edges']} edges complete ({c['contract_coverage']}); accepted-incomplete={c['accepted_incomplete']}.")
    report_md = Path(str(out).replace('.json', '.md')); report_md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'out': str(out), 'markdown': str(report_md)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
