# -*- coding: utf-8 -*-
import json
from pathlib import Path
from d2c13_2.content_cache import commit,extraction_key,lookup,restore
def write_doc(root: Path,text: str,config: dict) -> Path:
 root.mkdir(parents=True,exist_ok=True);(root/'doc.json').write_text(json.dumps({'title':{'eng':'test'},'summary':text}),encoding='utf-8')
 cfg=root.parent/'config.json';cfg.write_text(json.dumps(config),encoding='utf-8');return cfg
def base_config() -> dict:
 return {'extractor':{'model':'m1','base_url':'https://example/v1','max_tokens':1000},'entity_first':{'enabled':True,'max_items':20,'max_chunks':2,'temperature':0.0,'selection_policy':'p','relation_batch_policy':'q','attach_inventory_nodes':True,'relation_parallel_workers':4},'runtime':{'verifier_parallel_workers':4,'content_cache_enabled':True}}
def test_key_is_stable_and_content_sensitive(tmp_path: Path):
 root=tmp_path/'a';cfg=write_doc(root,'alpha',base_config());k1,_=extraction_key(root,cfg,'theme',0.3);k2,_=extraction_key(root,cfg,'theme',0.3);assert k1==k2
 (root/'doc.json').write_text(json.dumps({'title':{'eng':'test'},'summary':'beta'}),encoding='utf-8');k3,_=extraction_key(root,cfg,'theme',0.3);assert k3!=k1
def test_cache_namespace_keeps_stochastic_runs_distinct(tmp_path: Path):
 root=tmp_path/'ns';cfg=write_doc(root,'alpha',base_config());k1,_=extraction_key(root,cfg,'theme',0.3,'run_01');k2,_=extraction_key(root,cfg,'theme',0.3,'run_02');assert k1!=k2
def test_key_ignores_parallel_worker_settings(tmp_path: Path):
 root=tmp_path/'b';cfg=write_doc(root,'alpha',base_config());k1,_=extraction_key(root,cfg,'theme',0.3)
 cfgdata=base_config();cfgdata['entity_first']['relation_parallel_workers']=99;cfgdata['runtime']['verifier_parallel_workers']=99;cfg.write_text(json.dumps(cfgdata),encoding='utf-8');k2,_=extraction_key(root,cfg,'theme',0.3);assert k1==k2
def test_commit_lookup_and_restore(tmp_path: Path):
 source=tmp_path/'source';source.mkdir();(source/'evidence.json').write_text('{"ok":true}',encoding='utf-8');(source/'keep.txt').write_text('x',encoding='utf-8');(source/'content_cache_hit.json').write_text('ignored',encoding='utf-8')
 root=tmp_path/'cache';entry=commit(root,'abc',{'fp':1},source);assert lookup(root,'abc')==entry
 dest=tmp_path/'dest';marker=restore(entry,dest,'abc');assert marker['cache_hit'] is True
 assert (dest/'evidence.json').read_text(encoding='utf-8')=='{"ok":true}'
 assert (dest/'keep.txt').exists()
 assert not (dest/'content_cache_hit.json').read_text(encoding='utf-8').startswith('ignored')

def test_vote_mode_changes_cache_key(tmp_path: Path):
 root=tmp_path/'vote';cfg=write_doc(root,'alpha',base_config());k1,_=extraction_key(root,cfg,'theme',0.3)
 cfgdata=base_config();cfgdata['canonical_arbitration']={'vote_mode':'canonical_document'};cfg.write_text(json.dumps(cfgdata),encoding='utf-8');k2,_=extraction_key(root,cfg,'theme',0.3);assert k1!=k2