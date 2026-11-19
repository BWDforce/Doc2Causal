from pathlib import Path

from d2c13_2.evidence_completeness import package_evidence, restore_pre_verification_evidence, snapshot_pre_verification_evidence
from d2c13_2.rdfio import artifact_from_rdf, write_turtle
from d2c13_2.semantics import apply_verdicts, materialize_views


def _artifact(edge_condition=False, span_text='A drives B.', context='A drives B in the reactor.', source='A', target='B'):
    conditions = [{'id': 'c1', 'raw': 'in the reactor', 'normalized': 'in the reactor', 'equivalence_key': 'k1'}] if edge_condition else []
    return {
        'artifact_id': 'urn:d2c:artifact:t', 'version': 'D2C_13_2',
        'entities': [{'id': 'a', 'label': source, 'aliases': []}, {'id': 'b', 'label': target, 'aliases': []}],
        'spans': [{'id': 's1', 'doc_id': 'doc1', 'start': 0, 'end': len(span_text), 'text': span_text, 'context': context}],
        'assertions': [{'id': 'as1', 'source': 'a', 'target': 'b', 'doc_id': 'doc1', 'span_id': 's1', 'channel': 'pre', 'mechanism': f'{source} drives {target}'}],
        'conditions': conditions,
        'edges': [{
            'id': 'e1', 'source': 'a', 'target': 'b', 'source_name': source, 'target_name': target,
            'kind': 'conditional' if edge_condition else 'direct_core',
            'support_assertion_ids': ['as1'], 'candidate_assertion_ids': [],
            'support_doc_ids': ['doc1'], 'span_ids': ['s1'], 'candidate_span_ids': [],
            'condition_id': 'c1' if edge_condition else None, 'derivation_edge_ids': [],
            'direction_status': 'supported', 'identity_status': 'ok', 'verification_status': 'accepted',
        }],
        'views': {}, 'diagnostics': {}, 'promotion_policy': {},
    }


def test_exact_span_packaging():
    artifact = _artifact()
    counts = package_evidence(artifact)
    edge = artifact['edges'][0]
    assert counts['exact'] == 1
    assert edge['evidence_completeness'] == 'exact'
    assert edge['support_scope'] == 'exact_span'
    assert edge['best_window_kind'] == 'exact_span'
    assert edge['evidence_exact_match'] is True
    assert edge['evidence_coverage']['source'] is True
    assert edge['evidence_coverage']['target'] is True
    assert edge['evidence_coverage']['predicate'] is True


def test_minimal_sentence_window_is_exact():
    artifact = _artifact(span_text='A drives', context='Background. A drives B in the reactor.')
    counts = package_evidence(artifact)
    edge = artifact['edges'][0]
    assert counts['exact'] == 1
    assert edge['evidence_completeness'] == 'exact'
    assert edge['support_scope'] == 'exact_sentence_window'
    assert edge['best_window_kind'] == 'exact_window'
    assert edge['best_window_source'] == 'sentence_window'
    assert edge['best_window_width'] == 1


def test_full_context_can_be_complete_without_being_exact():
    source = 'dissolved oxygen concentration in water'
    target = 'algae bloom'
    context = 'Dissolved oxygen concentration in the water increases algae bloom.'
    artifact = _artifact(span_text='Dissolved oxygen.', context=context, source=source, target=target)
    counts = package_evidence(artifact)
    edge = artifact['edges'][0]
    assert counts['context_assisted'] == 1
    assert edge['evidence_completeness'] == 'context_assisted'
    assert edge['support_scope'] == 'context_assisted'
    assert edge['best_window_kind'] == 'context'
    assert edge['context_window_complete'] is True
    assert edge['evidence_exact_match'] is False


def test_condition_must_be_grounded_for_exact_core():
    artifact = _artifact(edge_condition=True, span_text='A drives B in the reactor.', context='A drives B in the reactor.')
    package_evidence(artifact)
    edge = artifact['edges'][0]
    assert edge['condition_grounding'] == 'grounded'
    materialize_views(artifact)
    assert any(row['edge_id'] == 'e1' for row in artifact['views']['exact_support_core'])


def test_condition_not_grounded_is_partial():
    artifact = _artifact(edge_condition=True, span_text='A drives B.', context='A drives B.')
    package_evidence(artifact)
    edge = artifact['edges'][0]
    assert edge['condition_grounding'] == 'ungrounded'
    assert edge['evidence_completeness'] == 'partial'


def test_no_evidence_queue():
    artifact = _artifact(span_text='', context='')
    artifact['spans'] = []
    artifact['assertions'][0]['span_id'] = None
    artifact['assertions'][0]['mechanism'] = ''
    artifact['edges'][0]['span_ids'] = []
    package_evidence(artifact)
    materialize_views(artifact)
    assert artifact['edges'][0]['evidence_completeness'] == 'absent'
    assert any(row['edge_id'] == 'e1' for row in artifact['views']['no_evidence_queue'])


def test_candidate_demotion_does_not_erase_evidence_audit_view():
    artifact = _artifact()
    package_evidence(artifact)
    snapshot_pre_verification_evidence(artifact)
    edge = artifact['edges'][0]
    edge['candidate_assertion_ids'] = edge['support_assertion_ids']
    edge['candidate_span_ids'] = edge['span_ids']
    edge['candidate_doc_ids'] = edge['support_doc_ids']
    edge['support_assertion_ids'] = []
    edge['span_ids'] = []
    edge['support_doc_ids'] = []
    edge['kind'] = 'candidate'
    edge['edge_kind'] = 'candidate'
    package_evidence(artifact)
    assert edge['evidence_completeness'] == 'candidate_only'
    restore_pre_verification_evidence(artifact)
    assert edge['kind'] == 'candidate'
    assert edge['evidence_completeness'] == 'exact'
    assert edge['support_scope'] == 'exact_span'
    materialize_views(artifact)
    assert any(row['edge_id'] == 'e1' for row in artifact['views']['exact_support_core'])
    assert any(row['edge_id'] == 'e1' for row in artifact['views']['context_assisted_core'])


def test_rdf_preserves_evidence_completeness(tmp_path: Path):
    artifact = _artifact()
    package_evidence(artifact)
    snapshot_pre_verification_evidence(artifact)
    path = tmp_path / 'artifact.ttl'
    write_turtle(artifact, path)
    recovered = artifact_from_rdf(path)
    edge = recovered['edges'][0]
    assert edge['evidence_completeness'] == 'exact'
    assert edge['support_scope'] == 'exact_span'
    assert edge['evidence_coverage']['predicate'] is True
    assert edge['evidence_packaging_version'] == 'D2C_13_2-evidence-v3'
    assert edge['evidence_contract_complete'] is True
    assert edge['evidence_contract']['text_complete'] is True
    assert edge['pre_verification_evidence_completeness'] == 'exact'

def test_rdf_preserves_exact_window_and_context_scope(tmp_path: Path):
    artifact = _artifact(span_text='A drives', context='Background. A drives B in the reactor.')
    package_evidence(artifact)
    snapshot_pre_verification_evidence(artifact)
    artifact['edges'][0]['verifier_support_scope'] = 'context_assisted'
    artifact['edges'][0]['verifier_window_kind'] = 'context'
    path = tmp_path / 'window.ttl'
    write_turtle(artifact, path)
    edge = artifact_from_rdf(path)['edges'][0]
    assert edge['support_scope'] == 'exact_sentence_window'
    assert edge['best_window_kind'] == 'exact_window'
    assert edge['best_window_source'] == 'sentence_window'
    assert edge['verifier_support_scope'] == 'context_assisted'
    assert edge['pre_verification_best_window_kind'] == 'exact_window'

def test_exact_evidence_sets_complete_contract():
    artifact = _artifact()
    package_evidence(artifact)
    edge = artifact['edges'][0]
    assert edge['evidence_contract_complete'] is True
    assert edge['evidence_contract']['text_complete'] is True
    assert edge['evidence_contract']['components'] == {
        'source': True, 'target': True, 'predicate': True, 'condition': True,
    }


def test_partial_span_keeps_legacy_verifier_acceptance_by_default():
    artifact = _artifact(span_text='A drives.', context='A drives.')
    package_evidence(artifact)
    edge = artifact['edges'][0]
    assert edge['evidence_completeness'] == 'partial'
    assert edge['evidence_contract_complete'] is False
    apply_verdicts(artifact, [{
        'edge_id': 'e1', 'support': 'entailed', 'direction': 'forward',
        'direct_support': 'entailed', 'assertion_support': 'entailed',
        'path_support': 'not_applicable',
    }])
    assert edge['verification_status'] == 'accepted'
    assert edge['kind'] == 'direct_core'
    assert 'verified_core' in edge['view_tags']
    assert 'contract_verified_core' not in edge['view_tags']


def test_partial_span_is_demoted_in_strict_contract_mode():
    artifact = _artifact(span_text='A drives.', context='A drives.')
    artifact['promotion_policy'] = {
        'require_complete_evidence_contract_for_acceptance': True,
    }
    package_evidence(artifact)
    edge = artifact['edges'][0]
    assert edge['evidence_completeness'] == 'partial'
    assert edge['evidence_contract_complete'] is False
    apply_verdicts(artifact, [{
        'edge_id': 'e1', 'support': 'entailed', 'direction': 'forward',
        'direct_support': 'entailed', 'assertion_support': 'entailed',
        'path_support': 'not_applicable',
    }])
    assert edge['verification_status'] != 'accepted'
    assert edge['kind'] in {'candidate', 'unsupported_hypothesis'}


def test_complete_contract_edge_enters_both_verified_views():
    artifact = _artifact()
    package_evidence(artifact)
    edge = artifact['edges'][0]
    apply_verdicts(artifact, [{
        'edge_id': 'e1', 'support': 'entailed', 'direction': 'forward',
        'direct_support': 'entailed', 'assertion_support': 'entailed',
        'path_support': 'not_applicable',
    }])
    assert edge['evidence_contract_complete'] is True
    assert 'verified_core' in edge['view_tags']
    assert 'contract_verified_core' in edge['view_tags']


def test_context_contract_keeps_complete_window():
    source = 'dissolved oxygen concentration in water'
    target = 'algae bloom'
    context = 'Dissolved oxygen concentration in the water increases algae bloom.'
    artifact = _artifact(span_text='Dissolved oxygen.', context=context, source=source, target=target)
    package_evidence(artifact)
    edge = artifact['edges'][0]
    assert edge['evidence_completeness'] == 'context_assisted'
    assert edge['evidence_contract_complete'] is True
    assert edge['evidence_contract']['text_complete'] is True

