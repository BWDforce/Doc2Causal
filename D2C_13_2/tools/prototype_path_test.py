import copy
import json
import statistics
from pathlib import Path
from d2c13_2.evaluation import align_node, artifact_views, edge_metrics
from d2c13_2.path_control import apply_path_aware_control
from d2c13_2.semantics import materialize_views
root = Path('nova_exp/exp11')
gold_root = Path('gold_gen_text/output_gen5')
def pairs(artifact, view, nodes, aliases):
    by_id = {str(edge.get('id')): edge for edge in artifact.get('edges', [])}
    labels = {str(entity.get('id')): entity.get('label', entity.get('id', '')) for entity in artifact.get('entities', [])}
    out = set()
    unaligned = 0
    self_loops = 0
    for row in artifact.get('views', {}).get(view, []):
        edge = by_id.get(str(row.get('edge_id')))
        if not edge:
            continue
        left = align_node(edge.get('source_name') or labels.get(str(edge.get('source')), edge.get('source')), nodes, aliases)
        right = align_node(edge.get('target_name') or labels.get(str(edge.get('target')), edge.get('target')), nodes, aliases)
        if not left or not right:
            unaligned += 1
        elif left == right:
            self_loops += 1
        else:
            out.add((left, right))
    return out, unaligned, self_loops
for corpus in ['water', 'win95pts']:
    manifest = json.loads((gold_root / corpus / 'seed1' / 'manifest.json').read_text(encoding='utf-8-sig'))
    nodes = list(manifest.get('gold_nodes', []))
    aliases = {}
    for doc in manifest.get('docs', []):
        for item in doc.get('aliases', []) or []:
            if item.get('surface') and item.get('canonical'):
                aliases[str(item['surface']).lower().strip()] = str(item['canonical'])
    gold = {tuple(edge) for edge in manifest.get('gold_edges', [])}
    rows = []
    for run_dir in sorted((root / corpus).glob('run_*')):
        if not (run_dir / 'artifact.json').exists():
            continue
        artifact = json.loads((run_dir / 'artifact.json').read_text(encoding='utf-8'))
        apply_path_aware_control(artifact)
        materialize_views(artifact)
        result = {}
        for view in ['soft_core', 'soft_core_lenient', 'soft_core_balanced']:
            p, u, s = pairs(artifact, view, nodes, aliases)
            result[view] = edge_metrics(p, gold, set(nodes), set(nodes), s, u)['f1']
        rows.append(result)
    print(corpus, {view: round(statistics.mean(row[view] for row in rows), 4) for view in ['soft_core', 'soft_core_lenient', 'soft_core_balanced']})

