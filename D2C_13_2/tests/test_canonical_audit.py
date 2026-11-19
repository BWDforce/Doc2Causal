# -*- coding: utf-8 -*-
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from d2c13_2.canonical_audit import build_canonical_audit
from d2c13_2.util import write_json
def make_fixture(tmp_path: Path):
 artifact={'entities':[{'id':'a','label':'A','aliases':['alpha']},{'id':'b','label':'B','aliases':[]},{'id':'c','label':'C','aliases':[]},{'id':'d','label':'D','aliases':[]}],'documents':[], 'spans':[],'assertions':[],'conditions':[], 'hypotheses':[], 'edges':[
  {'id':'e1','source':'a','target':'b','kind':'direct_core','support_doc_ids':['doc1','doc2']},
  {'id':'e2','source':'b','target':'c','kind':'direct_core','support_doc_ids':['doc1']},
  {'id':'e3','source':'c','target':'a','kind':'direct_core','support_doc_ids':['doc1']},
  {'id':'e4','source':'c','target':'d','kind':'candidate','support_doc_ids':[]},
  {'id':'e5','source':'d','target':'c','kind':'unsupported_hypothesis','support_doc_ids':[]},
 ], 'diagnostics':{},'views':{}}
 evidence={'entries':{
  'A || B':{'assertions':[{'doc_id':'doc1','source':'doc1_pre','channel':'pre','span':'0:1'},{'doc_id':'doc2','source':'doc2_pre','channel':'pre','span':'0:1'}],'sign':[['+','doc1_pre'],['-','doc2_pre']],'rung':[['2','doc1_pre']],'mechanisms':[],'mechanism':[['A->B','doc1_pre'],['A->B reverse?','doc2_pre']]},
  'B || A':{'assertions':[{'doc_id':'doc2','source':'doc2_llm','channel':'llm','span':''}],'sign':[['-','doc2_llm']],'rung':[],'mechanism':[]},
  'B || C':{'assertions':[{'doc_id':'doc1','source':'doc1_pre','channel':'pre','span':'2:3'}],'sign':[['+','doc1_pre']],'rung':[],'mechanism':[]},
  'C || A':{'assertions':[{'doc_id':'doc1','source':'doc1_pre','channel':'pre','span':'4:5'}],'sign':[['+','doc1_pre']],'rung':[],'mechanism':[]},
 }}
 ap=tmp_path/'artifact.json';ep=tmp_path/'evidence.json';write_json(ap,artifact);write_json(ep,evidence);return ap,ep
def test_audit_q3_q4_q7_and_f1_neutral(tmp_path: Path):
 ap,ep=make_fixture(tmp_path);before=hashlib.sha256(ap.read_bytes()).hexdigest();report=build_canonical_audit(ap,ep,tmp_path/'audit');after=hashlib.sha256(ap.read_bytes()).hexdigest();assert before==after
 assert report['f1_neutral'] is True
 assert report['counts']['multi_document_pairs']>=1
 kinds=report['counts']['conflict_kinds'];assert 'direction_conflict' in kinds and 'candidate_conflict' in kinds
 classes=report['counts']['cycle_classes'];assert 'evidence_cycle' in classes and 'candidate_cycle' in classes
 conflicts=json.loads((tmp_path/'audit'/'canonical_conflicts.json').read_text(encoding='utf-8'))['conflicts'];assert any(c['kind']=='candidate_conflict' and c['scope']=='candidate' for c in conflicts)
 votes=json.loads((tmp_path/'audit'/'canonical_votes.json').read_text(encoding='utf-8'));ab=[v for v in votes['votes'] if set(v['pair'])=={'a','b'}][0];assert ab['support_doc_ids'] and ab['opposition_doc_ids']
 cycles=json.loads((tmp_path/'audit'/'canonical_cycles.json').read_text(encoding='utf-8'));keys=[(c['classification'],tuple(sorted(e['id'] for e in c['edges']))) for c in cycles['cycles']];assert len(keys)==len(set(keys))
