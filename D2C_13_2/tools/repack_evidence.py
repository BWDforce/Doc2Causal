# -*- coding: utf-8 -*-
"""Repack cached D2C artifacts with D2C_13_2 evidence packaging."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'D2C_13_2'))
from d2c13_2.evidence_completeness import package_evidence
from d2c13_2.rdfio import write_jsonld, write_turtle
from d2c13_2.semantics import materialize_views
from d2c13_2.util import now_iso, read_json, write_json
from d2c13_2.validator import validate_artifact
from d2c13_2.viewer import write_viewer_from_file

VERSION = 'D2C_13_2'


def _artifacts(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob('artifact.json') if 'roundtrip' not in p.parts)


def main() -> int:
    parser = argparse.ArgumentParser(description='Repack cached artifacts with D2C_13_2 evidence packaging')
    parser.add_argument('--input-root', required=True)
    parser.add_argument('--output-root', required=True)
    args = parser.parse_args()
    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    files = _artifacts(input_root)
    if not files:
        raise SystemExit(f'no artifact.json found under {input_root}')
    summary = []
    for artifact_path in files:
        artifact = read_json(artifact_path, {}) or {}
        if not artifact:
            summary.append({'input': str(artifact_path), 'status': 'invalid_json'})
            continue
        relative = artifact_path.relative_to(input_root) if artifact_path != input_root else Path('artifact.json')
        out_dir = output_root / relative.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact['version'] = VERSION
        artifact.setdefault('provenance', {})['repacked_from'] = str(artifact_path)
        artifact['provenance']['repacked_at'] = now_iso()
        artifact['provenance']['producer'] = 'D2C_13_2-evidence-packager'
        counts = package_evidence(artifact)
        materialize_views(artifact)
        write_json(out_dir / 'artifact.json', artifact)
        write_jsonld(artifact, out_dir / 'artifact.jsonld')
        write_turtle(artifact, out_dir / 'artifact.ttl')
        validation = validate_artifact(artifact, run_shacl=True)
        write_json(out_dir / 'validation_report.json', validation)
        write_viewer_from_file(out_dir / 'artifact.json', out_dir / 'visualization.html', validation_path=out_dir / 'validation_report.json')
        summary.append({'input': str(artifact_path), 'output': str(out_dir / 'artifact.json'), 'evidence_completeness': counts, 'valid': validation.get('valid'), 'shacl_conforms': (validation.get('shacl') or {}).get('conforms')})
        print({'input': str(artifact_path), 'output': str(out_dir / 'artifact.json'), 'evidence_completeness': counts, 'valid': validation.get('valid')})
    write_json(output_root / 'repack_summary.json', {'version': VERSION, 'n': len(files), 'results': summary})
    return 0 if all(row.get('valid') for row in summary) else 1


if __name__ == '__main__':
    raise SystemExit(main())
