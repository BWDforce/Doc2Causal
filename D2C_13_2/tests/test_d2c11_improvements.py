from d2c13_2.entity_resolution import resolve_entity_aliases
from d2c13_2.path_control import apply_path_aware_control
from d2c13_2.semantics import materialize_views
def test_exact_normalized_alias_merges_time_forms():
    artifact = {'entities': [{'id': 'a', 'label': 'CKNN_12_00', 'aliases': [], 'entity_type': 'measurement'}, {'id': 'b', 'label': 'CKNN 12:00', 'aliases': [], 'entity_type': 'measurement'}], 'edges': [{'id': 'e', 'source': 'a', 'target': 'b', 'source_name': 'CKNN_12_00', 'target_name': 'CKNN 12:00', 'kind': 'candidate'}], 'assertions': [], 'hypotheses': []}
    stats = resolve_entity_aliases(artifact)
    assert stats['auto_merges'] == 1
    assert len(artifact['entities']) == 1
    assert artifact['edges'][0]['kind'] == 'rejected'
def test_unrelated_nitrogen_variables_are_not_merged():
    artifact = {'entities': [{'id': 'a', 'label': 'Nitrogen Recovery', 'aliases': [], 'entity_type': 'measurement'}, {'id': 'b', 'label': 'Kjeldahl Nitrogen Concentration', 'aliases': [], 'entity_type': 'measurement'}], 'edges': [], 'assertions': [], 'hypotheses': []}
    stats = resolve_entity_aliases(artifact)
    assert stats['auto_merges'] == 0
    assert len(artifact['entities']) == 2
def test_path_guard_marks_candidate_shortcut():
    artifact = {'edges': [{'id': 'ab', 'source': 'a', 'target': 'b', 'kind': 'candidate', 'candidate_tier': 'high', 'evidence_grade': 'span_grounded', 'directness_class': 'direct_span'}, {'id': 'bc', 'source': 'b', 'target': 'c', 'kind': 'candidate', 'candidate_tier': 'high', 'evidence_grade': 'span_grounded', 'directness_class': 'direct_span'}, {'id': 'ac', 'source': 'a', 'target': 'c', 'kind': 'candidate', 'candidate_tier': 'high', 'evidence_grade': 'span_grounded', 'directness_class': 'none'}], 'diagnostics': {}}
    stats = apply_path_aware_control(artifact)
    assert stats['high_risk'] >= 1
    assert artifact['edges'][2]['shortcut_risk'] == 'high'
    assert artifact['edges'][2]['shortcut_path_edge_ids'] == ['ab', 'bc']
def test_balanced_view_keeps_lenient_candidate():
    artifact = {'entities': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}], 'edges': [{'id': 'e', 'source': 'a', 'target': 'b', 'source_name': 'A', 'target_name': 'B', 'kind': 'candidate', 'candidate_tier': 'high', 'evidence_grade': 'span_grounded', 'support_mode': 'direct_span', 'candidate_support_mode': 'direct_span', 'candidate_assertion_ids': ['a1'], 'candidate_span_ids': ['s1'], 'claim_scope': 'direct', 'direction_status': 'ambiguous', 'shortcut_risk': 'high', 'verification_status': 'needs_review'}], 'views': {}, 'diagnostics': {}, 'promotion_policy': {}}
    materialize_views(artifact)
    ids = {row['edge_id'] for row in artifact['views']['soft_core_balanced']}
    assert 'e' in ids
    assert 'e' not in {row['edge_id'] for row in artifact['views']['soft_core']}



def test_state_aware_without_errors_is_ok():
    artifact = {
        "entities": [
            {"id": "a", "label": "application operates without errors", "aliases": []},
            {"id": "b", "label": "AppOK", "aliases": []},
        ],
        "edges": [], "assertions": [], "hypotheses": [],
    }
    stats = resolve_entity_aliases(artifact)
    assert stats["auto_merges"] == 0
    assert stats["review_candidates"] == 1


def test_context_definition_alias_auto_merges():
    context = "PrtMem, also known as printer memory"
    artifact = {
        "entities": [
            {"id": "a", "label": "PrtMem", "aliases": [], "context_texts": [context]},
            {"id": "b", "label": "printer memory", "aliases": [], "context_texts": [context]},
        ],
        "edges": [], "assertions": [], "hypotheses": [],
    }
    stats = resolve_entity_aliases(artifact)
    assert stats["auto_merges"] == 1
    assert len(artifact["entities"]) == 1


def test_underscore_time_difference_is_not_merged():
    artifact = {
        "entities": [
            {"id": "a", "label": "ton_concentration_12_30", "aliases": []},
            {"id": "b", "label": "ton_concentration_12_00", "aliases": []},
        ],
        "edges": [], "assertions": [], "hypotheses": [],
    }
    stats = resolve_entity_aliases(artifact)
    assert stats["auto_merges"] == 0
    assert stats["review_candidates"] == 0
