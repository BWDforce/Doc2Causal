# -*- coding: utf-8 -*-
"""Replay the D2C_13_2 canonical audit on existing run directories without API calls."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from d2c13_2.canonical_audit import build_from_run_dir
def main():
 p=argparse.ArgumentParser();p.add_argument('--run-dir',default=None);p.add_argument('--exp-root',default=None);p.add_argument('--out-dir',default=None);a=p.parse_args()
 if not a.run_dir and not a.exp_root:raise SystemExit('Provide --run-dir or --exp-root')
 runs=[Path(a.run_dir)] if a.run_dir else [p.parent for p in sorted(Path(a.exp_root).glob('*/run_*/artifact.json'))]
 rows=[]
 for run in runs:
  out=Path(a.out_dir)/run.parent.name/run.name if a.out_dir else run/'audit'
  report=build_from_run_dir(run,out);rows.append({'run_dir':str(run.resolve()),'audit_dir':str(out.resolve()),'counts':report['counts'],'q3_ready':report['q3_ready'],'q4_ready':report['q4_ready'],'q7_ready':report['q7_ready'],'f1_neutral':report['f1_neutral']})
 print(json.dumps({'runs':len(rows),'results':rows},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
