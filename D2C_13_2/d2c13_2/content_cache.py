# -*- coding: utf-8 -*-
"""Content-addressed cache for the expensive D2C Lang7 extraction stage."""
from __future__ import annotations
import functools,hashlib,json,os,shutil,uuid
from pathlib import Path
from typing import Any
from .constants import VERSION
PACKAGE_ROOT=Path(__file__).resolve().parents[1]
PROJECT_ROOT=PACKAGE_ROOT.parent
CACHE_SCHEMA_VERSION=2
STAGE='lang7_extract'
DEFAULT_CACHE_DIR=PACKAGE_ROOT/'.cache'/'content-addressed'
IGNORE_NAMES={'cache_manifest.json','content_cache_hit.json','__pycache__'}
def _sha256_file(path: Path) -> str:
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def _hash_tree(root: Path) -> str:
 if not root.exists():return ''
 h=hashlib.sha256();count=0
 for path in sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.json','.yaml','.yml','.toml'}):
  rel=path.relative_to(root).as_posix();h.update(rel.encode('utf-8'));h.update(b'\0');h.update(_sha256_file(path).encode('ascii'));h.update(b'\n');count+=1
 return f'{h.hexdigest()}:{count}'
@functools.lru_cache(maxsize=1)
def code_fingerprint() -> dict:
 roots={
  'lang7_flow':PACKAGE_ROOT/'d2c13_2'/'lang7_flow',
  'multi_extract':PROJECT_ROOT/'vendor'/'multi_extract',
  'langextract_core':PROJECT_ROOT/'vendor'/'langextract'/'langextract',
 }
 return {name:_hash_tree(root) for name,root in roots.items()}
def document_fingerprint(input_dir: str|Path) -> list[dict]:
 input_dir=Path(input_dir).resolve()
 files=sorted(p for p in input_dir.glob('*.json') if p.name!='provenance.json')
 return [{'name':p.name,'size':p.stat().st_size,'sha256':_sha256_file(p)} for p in files]
def config_payload(config_path: str|Path,theme: str,temperature: float,cache_namespace: str|None=None) -> dict:
 config=json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
 extractor=config.get('extractor') or {};entity=config.get('entity_first') or {}
 arbitration=config.get('canonical_arbitration') or {}
 vote_mode=arbitration.get('vote_mode','document')
 entity_semantics={k:entity.get(k) for k in ('enabled','max_items','max_chunks','temperature','selection_policy','relation_batch_policy','attach_inventory_nodes')}
 return {
  'cache_schema_version':CACHE_SCHEMA_VERSION,'stage':STAGE,'d2c_version':VERSION,
  'extractor':{k:extractor.get(k) for k in ('model','base_url','max_tokens')},
  'theme':theme,'temperature':float(temperature),'cache_namespace':cache_namespace or 'default',
  'fixed_extraction_args':{'max_steps':2,'extraction_passes':1,'vote_mode':vote_mode,'evidence_union':True},
  'entity_first':entity_semantics,'code':code_fingerprint(),
 }
def extraction_key(input_dir: str|Path,config_path: str|Path,theme: str,temperature: float,cache_namespace: str|None=None) -> tuple[str,dict]:
 payload=config_payload(config_path,theme,temperature,cache_namespace);payload['documents']=document_fingerprint(input_dir)
 encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
 return hashlib.sha256(encoded).hexdigest(),payload
def cache_root_from_config(config_path: str|Path|None=None) -> Path:
 raw=None
 if config_path:
  try:raw=(json.loads(Path(config_path).read_text(encoding='utf-8-sig')).get('runtime') or {}).get('content_cache_dir')
  except Exception:raw=None
 path=Path(raw) if raw else DEFAULT_CACHE_DIR
 return path if path.is_absolute() else PROJECT_ROOT/path
def cache_enabled_from_config(config_path: str|Path|None=None) -> bool:
 if not config_path:return True
 try:return bool((json.loads(Path(config_path).read_text(encoding='utf-8-sig')).get('runtime') or {}).get('content_cache_enabled',True))
 except Exception:return True
def cache_entry(cache_root: str|Path,key: str) -> Path:return Path(cache_root)/STAGE/key
def lookup(cache_root: str|Path,key: str) -> Path|None:
 entry=cache_entry(cache_root,key);manifest=entry/'cache_manifest.json'
 if not manifest.exists():return None
 try:
  payload=json.loads(manifest.read_text(encoding='utf-8'))
  return entry if payload.get('complete') and payload.get('key')==key else None
 except Exception:return None
def _copy_tree(source: Path,destination: Path) -> None:
 destination.mkdir(parents=True,exist_ok=True)
 for item in source.iterdir():
  if item.name in IGNORE_NAMES:continue
  target=destination/item.name
  if item.is_dir():shutil.copytree(item,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns(*IGNORE_NAMES))
  else:shutil.copy2(item,target)
def restore(cache_dir: str|Path,destination: str|Path,key: str) -> dict:
 cache_dir=Path(cache_dir);destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
 _copy_tree(cache_dir,destination)
 marker={'cache_hit':True,'cache_key':key,'cache_dir':str(cache_dir.resolve()),'stage':STAGE}
 (destination/'content_cache_hit.json').write_text(json.dumps(marker,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 return marker
def commit(cache_root: str|Path,key: str,fingerprint: dict,source_dir: str|Path) -> Path:
 root=Path(cache_root)/STAGE;root.mkdir(parents=True,exist_ok=True);final=root/key
 manifest_path=final/'cache_manifest.json'
 if manifest_path.exists():return final
 if final.exists():shutil.rmtree(final,ignore_errors=True)
 final.mkdir(parents=True,exist_ok=True)
 try:
  _copy_tree(Path(source_dir),final)
  manifest={'complete':True,'key':key,'stage':STAGE,'created_at':__import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds'),'fingerprint':fingerprint}
  manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
 except Exception:
  shutil.rmtree(final,ignore_errors=True)
  raise
 return final
def seed_extraction(input_dir: str|Path,config_path: str|Path,theme: str,temperature: float,source_dir: str|Path,cache_root: str|Path|None=None,cache_namespace: str|None=None) -> dict:
 key,fingerprint=extraction_key(input_dir,config_path,theme,temperature,cache_namespace);root=Path(cache_root) if cache_root else cache_root_from_config(config_path)
 existing=lookup(root,key);entry=existing or commit(root,key,fingerprint,source_dir)
 return {'key':key,'cache_dir':str(entry.resolve()),'created':existing is None,'manifest':str((entry/'cache_manifest.json').resolve())}
def key_info(input_dir: str|Path,config_path: str|Path,theme: str,temperature: float,cache_namespace: str|None=None) -> dict:
 key,fingerprint=extraction_key(input_dir,config_path,theme,temperature,cache_namespace);root=cache_root_from_config(config_path);entry=lookup(root,key)
 return {'key':key,'cache_root':str(root.resolve()),'hit':bool(entry),'entry':str(entry.resolve()) if entry else None,'fingerprint':fingerprint}
