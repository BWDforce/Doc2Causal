# -*- coding: utf-8 -*-
"""Inspect and seed the D2C content-addressed extraction cache."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from d2c13_2.content_cache import cache_root_from_config,key_info,seed_extraction
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='cmd',required=True)
 for name in ('key','seed'):
  q=sub.add_parser(name);q.add_argument('--input-dir',required=True);q.add_argument('--config',required=True);q.add_argument('--theme',required=True);q.add_argument('--temperature',type=float,default=0.3);q.add_argument('--cache-namespace',default=None)
  if name=='seed':q.add_argument('--source-dir',required=True);q.add_argument('--cache-root',default=None)
 q=sub.add_parser('list');q.add_argument('--config',required=True)
 a=p.parse_args()
 if a.cmd=='key':print(json.dumps(key_info(a.input_dir,a.config,a.theme,a.temperature,a.cache_namespace),ensure_ascii=False,indent=2));return 0
 if a.cmd=='seed':print(json.dumps(seed_extraction(a.input_dir,a.config,a.theme,a.temperature,a.source_dir,cache_root=a.cache_root,cache_namespace=a.cache_namespace),ensure_ascii=False,indent=2));return 0
 root=cache_root_from_config(a.config);entries=sorted((root/'lang7_extract').glob('*')) if (root/'lang7_extract').exists() else []
 rows=[]
 for e in entries:
  if not e.is_dir() or e.name.startswith('.tmp-'):continue
  m=e/'cache_manifest.json';rows.append({'key':e.name,'manifest':m.exists(),'files':sum(1 for x in e.rglob('*') if x.is_file()),'bytes':sum(x.stat().st_size for x in e.rglob('*') if x.is_file())})
 print(json.dumps({'cache_root':str(root.resolve()),'entries':rows},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
