# -*- coding: utf-8 -*-
"""Validate artifact span offsets and word boundaries against source summaries."""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'D2C_13_2'))
from d2c13_2.util import read_json, write_json


def _word(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch == '_')


def _summary_map(docs_dir: Path) -> dict[str, str]:
    return {
        p.name: str(read_json(p, {}).get('summary') or '')
        for p in sorted(docs_dir.glob('*.json'))
        if not p.name.startswith('_')
    }


def validate_run(run_dir: Path, docs_dir: Path) -> dict:
    artifact = read_json(run_dir / 'artifact.json', {}) or {}
    summaries = _summary_map(docs_dir)
    doc_text = {str(d.get('doc_id')): summaries.get(str(d.get('source_file')), '') for d in artifact.get('documents', [])}
    mismatches, midword, empty = [], [], []
    for span in artifact.get('spans', []):
        text = doc_text.get(str(span.get('doc_id')), '')
        start, end = span.get('start'), span.get('end')
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(text)):
            mismatches.append(span.get('id'))
            continue
        if text[start:end] != span.get('text'):
            mismatches.append(span.get('id'))
        if not span.get('text'):
            empty.append(span.get('id'))
        if start > 0 and _word(text[start - 1]) and _word(text[start]):
            midword.append({'edge_span': span.get('id'), 'boundary': 'start'})
        if end < len(text) and _word(text[end - 1]) and _word(text[end]):
            midword.append({'edge_span': span.get('id'), 'boundary': 'end'})
    return {
        'run': run_dir.name,
        'spans': len(artifact.get('spans', [])),
        'text_mismatch': len(mismatches),
        'midword_boundaries': len(midword),
        'empty_text': len(empty),
        'mismatch_examples': mismatches[:10],
        'midword_examples': midword[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate artifact span offsets')
    parser.add_argument('--exp-root', required=True)
    parser.add_argument('--docs-root', default=str(ROOT / 'data' / 'Test-text' / 'input' / 'goldgen_gen5'))
    parser.add_argument('--out', default=None)
    args = parser.parse_args()
    root = Path(args.exp_root)
    docs_root = Path(args.docs_root)
    rows = []
    for corpus in ('water', 'win95pts'):
        for run_dir in sorted((root / corpus).glob('run_*')):
            if (run_dir / 'artifact.json').exists():
                row = {'corpus': corpus, **validate_run(run_dir, docs_root / corpus)}
                rows.append(row)
    payload = {
        'exp_root': str(root.resolve()),
        'runs': len(rows),
        'spans': sum(r['spans'] for r in rows),
        'text_mismatch': sum(r['text_mismatch'] for r in rows),
        'midword_boundaries': sum(r['midword_boundaries'] for r in rows),
        'empty_text': sum(r['empty_text'] for r in rows),
        'rows': rows,
    }
    out = Path(args.out) if args.out else root / 'span_offset_validation.json'
    write_json(out, payload)
    print(json.dumps({k: payload[k] for k in ('runs', 'spans', 'text_mismatch', 'midword_boundaries', 'empty_text')}, indent=2))
    return 0 if payload['text_mismatch'] == 0 and payload['empty_text'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
