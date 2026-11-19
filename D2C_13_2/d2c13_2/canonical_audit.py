# -*- coding: utf-8 -*-
"""F1-neutral canonical audit layer for Q3/Q4/Q7.

The audit is read-only with respect to artifact.json. It rebuilds canonical
evidence pairs from the Lang7 evidence ledger and artifact alias mapping, then
records support votes, conflict taxonomy and evidence/candidate cycles in
separate sidecars.
"""
from __future__ import annotations
import hashlib,json,re
from collections import Counter,defaultdict
from pathlib import Path
from typing import Any
import networkx as nx
from .util import read_json,write_json
EVIDENCE_KINDS={'direct_core','conditional','regime','transitive_derived'}
HYPOTHESIS_KINDS={'candidate','unsupported_hypothesis','hypothesis'}
def _norm(value: Any) -> str:
 return re.sub(r'\s+',' ',str(value or '').strip()).casefold()
def _sha256(path: Path) -> str:
 h=hashlib.sha256();h.update(path.read_bytes());return h.hexdigest()
def _entity_index(artifact: dict) -> tuple[dict[str,dict],dict[str,str],dict[str,str]]:
 entities={str(e.get('id')):e for e in artifact.get('entities',[]) if e.get('id')};aliases={};labels={}
 for e in entities.values():
  eid=str(e['id']);label=str(e.get('label') or eid);labels[eid]=label
  for value in [label,eid,*list(e.get('aliases') or [])]:
   if value:aliases[_norm(value)]=eid;aliases[str(value)]=eid
 return entities,aliases,labels
def _canonical_id(raw: Any,aliases: dict[str,str]) -> str:
 key=str(raw or '')
 return aliases.get(key) or aliases.get(_norm(key)) or key
def _evidence_entries(evidence: Any) -> dict[str,dict]:
 if isinstance(evidence,dict) and isinstance(evidence.get('entries'),dict):return evidence['entries']
 if isinstance(evidence,dict):return {str(k):v for k,v in evidence.items() if isinstance(v,dict)}
 return {}
def _source_doc(source: Any) -> str:
 return str(source or '').split('_',1)[0]
def _value_by_source(values: Any) -> dict[str,list[str]]:
 out=defaultdict(list)
 for item in values or []:
  if isinstance(item,(list,tuple)) and len(item)>=2:out[str(item[1])].append(str(item[0]))
 return dict(out)
def _edge_pair(edge: dict) -> tuple[str,str]:
 return tuple(sorted((str(edge.get('source')),str(edge.get('target')))))
def _cycle_scope(edges: list[dict]) -> str:
 kinds={str(e.get('kind')) for e in edges}
 has_evidence=bool(kinds & EVIDENCE_KINDS);has_hyp=bool(kinds & HYPOTHESIS_KINDS)
 any_regime=any(bool(e.get('regime_dependent')) or str(e.get('kind')) in {'regime','conditional'} for e in edges)
 if any_regime and (has_hyp or not has_evidence):return 'regime_candidate_cycle'
 if has_evidence and not has_hyp:return 'evidence_cycle'
 if has_hyp and not has_evidence:return 'candidate_cycle' if kinds!={'hypothesis'} else 'hypothesis_cycle'
 return 'mixed_cycle'
def _cycles(graph: nx.DiGraph,edge_by_id: dict[str,dict],max_cycles: int=200) -> list[list[dict]]:
 try:raw=list(nx.simple_cycles(graph,length_bound=12))
 except TypeError:raw=list(nx.simple_cycles(graph))
 out=[]
 for nodes in raw[:max_cycles]:
  edges=[]
  for i,u in enumerate(nodes):
   v=nodes[(i+1)%len(nodes)];data=graph.get_edge_data(u,v) or {};eid=str(data.get('edge_id') or '');edges.append(edge_by_id.get(eid,{'id':eid,'source':u,'target':v,**data}))
  out.append(edges)
 return out
def build_canonical_audit(artifact_path: str|Path,evidence_path: str|Path|None,out_dir: str|Path) -> dict:
 artifact_path=Path(artifact_path);out_dir=Path(out_dir);out_dir.mkdir(parents=True,exist_ok=True)
 artifact=read_json(artifact_path,{}) or {};evidence=read_json(evidence_path,{}) if evidence_path else {}
 entries=_evidence_entries(evidence);entities,aliases,labels=_entity_index(artifact)
 conditions={str(c.get('id')):c for c in artifact.get('conditions',[]) if c.get('id')}
 edges=[e for e in artifact.get('edges',[]) if e.get('id') and e.get('kind')!='rejected']
 artifact_by_pair=defaultdict(list)
 for e in edges:artifact_by_pair[_edge_pair(e)].append(e)
 pair_records={};raw_missing=0
 for raw_key,row in entries.items():
  if ' || ' not in raw_key:continue
  raw_source,raw_target=raw_key.split(' || ',1);cs=_canonical_id(raw_source,aliases);ct=_canonical_id(raw_target,aliases)
  if cs not in labels:raw_missing+=1
  if ct not in labels:raw_missing+=1
  pair=(cs,ct);sortp=tuple(sorted(pair));direction='forward' if pair==sortp else 'reverse'
  rec=pair_records.setdefault(sortp,{'pair':list(sortp),'source_id':sortp[0],'target_id':sortp[1],'source_label':labels.get(sortp[0],sortp[0]),'target_label':labels.get(sortp[1],sortp[1]),'evidence':[]})
  signs=_value_by_source(row.get('sign'));rungs=_value_by_source(row.get('rung'));mechs=_value_by_source(row.get('mechanism'))
  for assertion in row.get('assertions') or []:
   source=str(assertion.get('source') or '');doc=_source_doc(assertion.get('doc_id') or source)
   rec['evidence'].append({'raw_source':raw_source,'raw_target':raw_target,'canonical_source':cs,'canonical_target':ct,'direction':direction,'doc_id':doc,'channel':assertion.get('channel'),'span':assertion.get('span') or '', 'signs':signs.get(source,[]),'rungs':rungs.get(source,[]),'mechanisms':mechs.get(source,[])})
 votes=[];canonical_evidence=[]
 for pair,rec in sorted(pair_records.items()):
  forward_docs=sorted({x['doc_id'] for x in rec['evidence'] if x['direction']=='forward' and x['doc_id']});reverse_docs=sorted({x['doc_id'] for x in rec['evidence'] if x['direction']=='reverse' and x['doc_id']});both=sorted(set(forward_docs)&set(reverse_docs));uncertain=sorted(set(both))
  edge_rows=artifact_by_pair.get(pair,[])
  reference_orientation='forward'
  if edge_rows:
   best_edge=max(edge_rows,key=lambda e:(len(e.get('support_doc_ids') or []),len(e.get('support_assertion_ids') or []),str(e.get('id'))))
   reference_orientation='forward' if str(best_edge.get('source'))==pair[0] else 'reverse'
  support_docs,opp_docs=(forward_docs,reverse_docs) if reference_orientation=='forward' else (reverse_docs,forward_docs)
  signs_by_dir=defaultdict(set);mechs_by_dir=defaultdict(set)
  for x in rec['evidence']:
   signs_by_dir[x['direction']].update(s for s in x['signs'] if s);mechs_by_dir[x['direction']].update(m for m in x['mechanisms'] if m)
  rule='unanimous_forward' if support_docs and not opp_docs else 'unanimous_reverse' if opp_docs and not support_docs else 'majority_forward' if len(support_docs)>len(opp_docs) else 'majority_reverse' if len(opp_docs)>len(support_docs) else 'tie' if support_docs and opp_docs else 'no_evidence'
  condition_rows=[]
  for e in edge_rows:
   cid=str(e.get('condition_id') or '')
   if cid:condition_rows.append({'edge_id':e.get('id'),'condition_id':cid,'condition':conditions.get(cid,{}),'regime_dependent':bool(e.get('regime_dependent'))})
  vote={'pair':list(pair),'source_label':rec['source_label'],'target_label':rec['target_label'],'support_doc_ids':support_docs,'opposition_doc_ids':opp_docs,'forward_doc_ids':forward_docs,'reverse_doc_ids':reverse_docs,'uncertain_doc_ids':uncertain,'reference_orientation':reference_orientation,'support_votes':len(support_docs),'opposition_votes':len(opp_docs),'direction_rule':rule,'signs_by_direction':{k:sorted(v) for k,v in signs_by_dir.items()},'mechanisms_by_direction':{k:sorted(v) for k,v in mechs_by_dir.items()},'condition_differences':condition_rows,'edge_ids':[e.get('id') for e in edge_rows],'assertion_count':len(rec['evidence']),'multi_document':len(set(support_docs)|set(opp_docs))>=2}
  votes.append(vote);rec['docs']={'support':support_docs,'opposition':opp_docs,'uncertain':uncertain};rec['conditions']=condition_rows;canonical_evidence.append(rec)
 conflicts=[]
 def add_conflict(kind,pair,scope,details):
  conflicts.append({'kind':kind,'pair':list(pair),'scope':scope,'decision_value':'needs_review','details':details})
 for v in votes:
  pair=tuple(v['pair'])
  if v['support_doc_ids'] and v['opposition_doc_ids']:add_conflict('direction_conflict',pair,'evidence',{'support_doc_ids':v['support_doc_ids'],'opposition_doc_ids':v['opposition_doc_ids'],'direction_rule':v['direction_rule']})
  for direction,signs in v['signs_by_direction'].items():
   if len(signs)>1:add_conflict('sign_conflict',pair,'evidence',{'direction':direction,'signs':signs})
  for direction,mechs in v['mechanisms_by_direction'].items():
   if len(mechs)>1 and len(v['support_doc_ids']+v['opposition_doc_ids'])>=2:add_conflict('mechanism_conflict',pair,'evidence',{'direction':direction,'mechanism_count':len(mechs),'mechanisms':mechs[:8]})
  if len({x['condition_id'] for x in v['condition_differences']})>1:add_conflict('condition_conflict',pair,'evidence',{'condition_ids':sorted({x['condition_id'] for x in v['condition_differences']}),'conditions':v['condition_differences']})
 # reciprocal candidate pairs
 cand=defaultdict(lambda:defaultdict(list))
 for e in edges:
  if str(e.get('kind')) in HYPOTHESIS_KINDS:cand[_edge_pair(e)][(str(e.get('source')),str(e.get('target')))].append(e)
 for pair,directions in cand.items():
  if len(directions)>=2:
   add_conflict('candidate_conflict',pair,'candidate',{'edge_ids':[e.get('id') for rows in directions.values() for e in rows],'kinds':sorted({str(e.get('kind')) for rows in directions.values() for e in rows})})
 # entity identity conflicts
 identities=defaultdict(list)
 for e in artifact.get('entities',[]):
  key=str(e.get('identity_key') or '')
  if key:identities[key].append(e)
 for key,rows in identities.items():
  if len(rows)>1:conflicts.append({'kind':'entity_identity_conflict','pair':[r.get('id') for r in rows],'scope':'entity','decision_value':'needs_review','details':{'identity_key':key,'labels':[r.get('label') for r in rows]}})
 # cycles by scope
 edge_by_id={str(e.get('id')):e for e in edges};cycles=[]
 for scope,kinds in [('evidence',EVIDENCE_KINDS),('hypothesis',HYPOTHESIS_KINDS),('mixed',EVIDENCE_KINDS|HYPOTHESIS_KINDS)]:
  g=nx.DiGraph()
  for e in edges:
   if str(e.get('kind')) not in kinds:continue
   g.add_edge(str(e.get('source')),str(e.get('target')),edge_id=str(e.get('id')),kind=e.get('kind'),regime_dependent=bool(e.get('regime_dependent')),verification_status=e.get('verification_status'))
  for cycle_edges in _cycles(g,edge_by_id):cycles.append({'classification':_cycle_scope(cycle_edges),'graph_scope':scope,'nodes':[{'id':n,'label':labels.get(n,n)} for n in dict.fromkeys([x.get('source') for x in cycle_edges]+[x.get('target') for x in cycle_edges])],'edges':[{'id':e.get('id'),'source':e.get('source'),'target':e.get('target'),'kind':e.get('kind'),'verification_status':e.get('verification_status'),'support_doc_ids':e.get('support_doc_ids') or [],'regime_dependent':bool(e.get('regime_dependent'))} for e in cycle_edges],'has_evidence_edges':any(str(e.get('kind')) in EVIDENCE_KINDS for e in cycle_edges),'has_hypothesis_edges':any(str(e.get('kind')) in HYPOTHESIS_KINDS for e in cycle_edges),'suggested_action':'needs_review' if any(str(e.get('kind')) in HYPOTHESIS_KINDS for e in cycle_edges) else 'keep_for_audit'})
 unique_cycles={}
 for cycle in cycles:
  key=(cycle['classification'],tuple(sorted(str(e.get('id')) for e in cycle['edges'])))
  if key not in unique_cycles or cycle['graph_scope']!='mixed':unique_cycles[key]=cycle
 cycles=list(unique_cycles.values())
 conflict_kinds=Counter(c['kind'] for c in conflicts)
 q4_kinds={'direction_conflict','sign_conflict','mechanism_conflict','condition_conflict','candidate_conflict'}
 counts={'canonical_pairs':len(votes),'vote_records':len(votes),'multi_document_pairs':sum(v['multi_document'] for v in votes),'support_votes_total':sum(v['support_votes'] for v in votes),'opposition_votes_total':sum(v['opposition_votes'] for v in votes),'conflicts':len(conflicts),'conflict_kinds':dict(conflict_kinds),'cycles':len(cycles),'cycle_classes':dict(Counter(c['classification'] for c in cycles)),'raw_entity_labels_not_in_artifact':raw_missing}
 summary={'version':'D2C_13_2','stage':'canonical_audit','f1_neutral':True,'artifact':str(artifact_path.resolve()),'evidence':str(Path(evidence_path).resolve()) if evidence_path else None,'artifact_sha256_before':_sha256(artifact_path),'artifact_sha256_after':_sha256(artifact_path),'counts':counts,'q3_ready':counts['vote_records']>0,'q3_multi_document_ready':counts['multi_document_pairs']>0,'q4_ready':any(k in q4_kinds for k in conflict_kinds),'q7_ready':counts['cycles']>0}
 write_json(out_dir/'canonical_evidence.json',{'pairs':canonical_evidence,'counts':{'pairs':len(canonical_evidence)}})
 write_json(out_dir/'canonical_votes.json',{'votes':votes,'counts':{'pairs':len(votes),'multi_document_pairs':counts['multi_document_pairs']}})
 write_json(out_dir/'canonical_conflicts.json',{'conflicts':conflicts,'counts':counts['conflict_kinds']})
 write_json(out_dir/'canonical_cycles.json',{'cycles':cycles,'counts':counts['cycle_classes']})
 write_json(out_dir/'audit_report.json',summary)
 return summary
def build_from_run_dir(run_dir: str|Path,out_dir: str|Path|None=None) -> dict:
 run=Path(run_dir);artifact=run/'artifact.json';evidence=run/'lang7'/'evidence.json';out=Path(out_dir) if out_dir else run/'audit'
 if not artifact.exists():raise FileNotFoundError(artifact)
 return build_canonical_audit(artifact,evidence if evidence.exists() else None,out)
