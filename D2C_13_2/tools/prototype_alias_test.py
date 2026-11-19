import copy
import copy
import json
import statistics
from pathlib import Path
from d2c13_2.entity_resolution import resolve_entity_aliases
from d2c13_2.evaluation import evaluate_unified_views
from d2c13_2.semantics import materialize_views
from d2c13_2.util import write_json
root = Path('nova_exp/exp11')
gold_root = Path('gold_gen_text/output_gen5')
out_root = Path('nova_exp/exp9_alias_test')
out_root.mkdir(parents=True, exist_ok=True)
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
        pre_path = run_dir / 'artifact.pre_verification.json'
        gmls = sorted((run_dir / 'lang7').glob('*.gml'))
        if not artifact_path.exists() or not pre_path.exists() or not gmls:
            continue
        original = json.loads(artifact_path.read_text(encoding='utf-8'))
        before = evaluate_unified_views(artifact_path, pre_path, nodes, aliases, gold, gmls[-1])
        resolved = copy.deepcopy(original)
        stats = resolve_entity_aliases(resolved)
        materialize_views(resolved)
        temp_path = out_root / corpus / run_dir.name / 'artifact.json'
        write_json(temp_path, resolved)
        after = evaluate_unified_views(temp_path, pre_path, nodes, aliases, gold, gmls[-1])
        rows.append((run_dir.name, stats, before, after))
        print(corpus, run_dir.name, 'merges', stats['auto_merges'], 'reviews', stats['review_candidates'], 'soft_before', before['soft_core']['f1'], 'soft_after', after['soft_core']['f1'], 'nodeR_before', before['soft_core']['node_recall'], 'nodeR_after', after['soft_core']['node_recall'])
    print(corpus, 'mean soft before/after', round(statistics.mean(r[2]['soft_core']['f1'] for r in rows), 4), round(statistics.mean(r[3]['soft_core']['f1'] for r in rows), 4), 'mean support before/after', round(statistics.mean(r[2]['support_aware_core']['f1'] for r in rows), 4), round(statistics.mean(r[3]['support_aware_core']['f1'] for r in rows), 4))

