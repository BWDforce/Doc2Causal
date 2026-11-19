# -*- coding: utf-8 -*-
'''Corpus-independent path-aware shortcut diagnostics.'''
from __future__ import annotations
from collections import Counter
import networkx as nx
from .util import as_text
PATH_KINDS = {'direct_core', 'conditional', 'regime', 'transitive_derived', 'candidate'}
def _pathworthy(edge: dict) -> bool:
    kind = as_text(edge.get('kind'))
    if kind not in PATH_KINDS or edge.get('identity_status') == 'self_loop':
        return False
    if kind == 'candidate':
        return edge.get('candidate_tier') in {'high', 'medium'} and edge.get('evidence_grade') not in {None, '', 'none'}
    return edge.get('evidence_grade') not in {None, '', 'none'}
def _path_edge_ids(artifact: dict, path: list[str]) -> list[str]:
    index = {}
    for edge in artifact.get('edges', []):
        if edge.get('kind') in PATH_KINDS and edge.get('source') and edge.get('target'):
            index[(edge['source'], edge['target'])] = edge.get('id')
    return [index[(u, v)] for u, v in zip(path, path[1:]) if (u, v) in index]
def apply_path_aware_control(artifact: dict) -> dict:
    edges = [edge for edge in artifact.get('edges', []) if _pathworthy(edge)]
    graph = nx.DiGraph()
    for edge in edges:
        graph.add_edge(edge['source'], edge['target'], edge_id=edge.get('id'))
    counts = Counter()
    for edge in edges:
        source, target = edge.get('source'), edge.get('target')
        if not source or not target or source == target or not graph.has_edge(source, target):
            continue
        graph.remove_edge(source, target)
        path = None
        try:
            if nx.has_path(graph, source, target):
                path = nx.shortest_path(graph, source, target)
        finally:
            graph.add_edge(source, target, edge_id=edge.get('id'))
        if not path or len(path) < 3:
            edge['shortcut_risk'] = 'low'
            edge['shortcut_path_edge_ids'] = []
            continue
        directness = as_text(edge.get('directness_class'))
        risk = 'medium' if directness in {'direct_span', 'direct_assertion'} else 'high'
        edge['shortcut_risk'] = risk
        edge['shortcut_path_edge_ids'] = _path_edge_ids(artifact, path)
        edge['shortcut_path'] = path
        edge['path_summary'] = True
        edge['path_summary_explicit_direct'] = risk == 'medium'
        edge['path_aware_status'] = 'shortcut_medium' if risk == 'medium' else 'shortcut_high'
        counts[risk] += 1
    artifact.setdefault('diagnostics', {})['path_aware'] = {'high_risk': counts.get('high', 0), 'medium_risk': counts.get('medium', 0), 'pathworthy_edges': len(edges)}
    return {'high_risk': counts.get('high', 0), 'medium_risk': counts.get('medium', 0), 'pathworthy_edges': len(edges)}
if __name__ == '__main__':
    raise SystemExit('path_control is a library module')
