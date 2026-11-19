import copy
import json
import statistics
from pathlib import Path
from d2c13_2.evaluation import align_node, artifact_views, edge_metrics
from d2c13_2.semantics import materialize_views
root = Path('nova_exp/exp11')
gold_root = Path('gold_gen_text/output_gen5')
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
        artifact_path = run_dir / 'artifact.json'
        if not artifact_path.exists():
            continue
        artifact = json.loads(artifact_path.read_text(encoding='utf-8'))
        materialize_views(artifact)
        data = artifact_views(artifact_path, nodes, aliases)
        by_id = {str(edge.get('id')): edge for edge in artifact.get('edges', [])}
        labels = {str(entity.get('id')): entity.get('label', entity.get('id', '')) for entity in artifact.get('entities', [])}
        balanced = set()
        unaligned = self_loops = 0
        for row in artifact.get('views', {}).get('soft_core_balanced', []):
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
                balanced.add((left, right))
        metrics = edge_metrics(balanced, gold, data['nodes'], set(nodes), self_loops, unaligned)
        rows.append((run_dir.name, metrics, data))
        print(corpus, run_dir.name, 'balanced', metrics['f1'], 'soft', edge_metrics(data['soft_core'], gold, data['nodes'], set(nodes), data['self_loops'], data['unaligned_edges'])['f1'], 'lenient', edge_metrics(data['soft_core_lenient'], gold, data['nodes'], set(nodes), data['self_loops'], data['unaligned_edges'])['f1'])
    print(corpus, 'mean balanced F1', round(statistics.mean(row[1]['f1'] for row in rows), 4))

