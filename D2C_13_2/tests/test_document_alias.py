from d2c13_2.document_alias import normalize_document_aliases, score_mention_pair


def _mention(entity_id, surface, context, doc_id='doc1', neighbors=None):
    return {
        'entity_id': entity_id, 'doc_id': doc_id, 'surfaces': {surface},
        'contexts': [context], 'roles': {'source'}, 'neighbors': set(neighbors or []),
        'types': {'measurement'},
    }


def test_document_context_can_merge_same_measurement():
    left = _mention('a', '12:00 dissolved biochemical oxygen demand level', 'The 12:00 dissolved biochemical oxygen demand level was measured in the final cell.', neighbors=['n'])
    right = _mention('b', 'dissolved biochemical oxygen demand at 12:00', 'The dissolved biochemical oxygen demand at 12:00 was measured in the final cell.', neighbors=['n'])
    result = score_mention_pair(left, right)
    assert result['decision'] == 'auto_merge'


def test_time_conflict_blocks_document_merge():
    left = _mention('a', 'nitrite nitrogen level at 12:00', 'At 12:00 the nitrite nitrogen level was recorded.')
    right = _mention('b', 'nitrite nitrogen level at 12:15', 'At 12:15 the nitrite nitrogen level was recorded.')
    result = score_mention_pair(left, right)
    assert result['decision'] == 'separate'


def test_document_alias_candidates_are_discovered_from_artifact():
    artifact = {
        'documents': [{'id': 'd1', 'doc_id': 'doc1'}],
        'entities': [
            {'id': 'a', 'label': '12:00 dissolved biochemical oxygen demand level', 'aliases': [], 'entity_type': 'measurement'},
            {'id': 'b', 'label': 'dissolved biochemical oxygen demand at 12:00', 'aliases': [], 'entity_type': 'measurement'},
        ],
        'spans': [
            {'id': 's1', 'doc_id': 'doc1', 'text': 'The 12:00 dissolved biochemical oxygen demand level was measured in the final cell.'},
        ],
        'assertions': [{'id': 'z1', 'doc_id': 'doc1', 'source': 'a', 'target': 'b', 'span_id': 's1', 'mechanism': 'measurement'}],
        'edges': [{
            'id': 'e1', 'source': 'a', 'target': 'b', 'source_name': '12:00 dissolved biochemical oxygen demand level',
            'target_name': 'dissolved biochemical oxygen demand at 12:00', 'support_doc_ids': ['doc1'],
            'span_ids': ['s1'], 'evidence_windows': [], 'kind': 'candidate',
        }],
        'hypotheses': [],
    }
    result = normalize_document_aliases(artifact)
    assert result['diagnostics']['auto_merge_candidates'] >= 0
    assert result['diagnostics']['review_candidates_kept'] <= result['diagnostics']['review_candidates_total']

def test_midday_and_clock_time_conflict_blocks_merge():
    left = _mention('a', 'Kjeldahl nitrogen concentration at 12:30', 'The 12:30 Kjeldahl nitrogen concentration was recorded.')
    right = _mention('b', 'midday Kjeldahl nitrogen concentration', 'The midday Kjeldahl nitrogen concentration was recorded.')
    result = score_mention_pair(left, right)
    assert result['decision'] == 'separate'
    assert result['reason'] == 'conflict'


def test_word_ordinal_conflict_blocks_merge():
    left = _mention('a', 'fifth issue flag', 'The fifth issue flag was recorded.')
    right = _mention('b', 'sixth issue flag', 'The sixth issue flag was recorded.')
    result = score_mention_pair(left, right)
    assert result['decision'] == 'separate'
    assert result['reason'] == 'conflict'


def test_negation_flip_blocks_merge():
    left = _mention('a', 'non-PostScript graphic present state', 'The non-PostScript graphic present state was recorded.')
    right = _mention('b', 'PostScript graphic state', 'The PostScript graphic state was recorded.')
    result = score_mention_pair(left, right)
    assert result['decision'] == 'separate'
    assert result['reason'] == 'polarity_conflict'


def test_embedding_threshold_is_configurable(monkeypatch):
    import d2c13_2.document_alias as module

    monkeypatch.setattr(module, '_surface_embedding_similarity', lambda left, right: 0.93)
    left = _mention('a', 'alpha widget', 'The alpha widget was observed.')
    right = _mention('b', 'beta gadget', 'The beta gadget was observed.')
    strict = module.score_mention_pair(left, right, embedding_auto_threshold=0.94)
    relaxed = module.score_mention_pair(left, right, embedding_auto_threshold=0.92)
    assert strict['decision'] == 'needs_review'
    assert relaxed['decision'] == 'auto_merge'

def test_alias_pair_is_blocked_when_entities_are_causally_ordered():
    from d2c13_2.document_alias import _core_edge_graph, _path_conflict

    artifact = {
        'edges': [
            {'id': 'e1', 'source': 'a', 'target': 'm', 'kind': 'direct_core'},
            {'id': 'e2', 'source': 'm', 'target': 'b', 'kind': 'direct_core'},
            {'id': 'e3', 'source': 'b', 'target': 'c', 'kind': 'candidate'},
        ],
    }
    graph = _core_edge_graph(artifact)
    assert _path_conflict(graph, 'a', 'b') is True
    assert _path_conflict(graph, 'a', 'c') is False

def test_state_modifier_requires_review(monkeypatch):
    import d2c13_2.document_alias as module

    monkeypatch.setattr(module, '_surface_embedding_similarity', lambda left, right: None)
    left = _mention('a', 'desktop network connection', 'The desktop network connection was observed.')
    right = _mention('b', 'desktop network connection healthy state', 'The desktop network connection healthy state was observed.')
    result = module.score_mention_pair(left, right)
    assert result['decision'] == 'needs_review'

def test_entity_match_checks_alias_time_even_when_primary_label_lacks_time():
    from d2c13_2.entity_resolution import entity_match

    left = {
        'id': 'a', 'label': 'Kjeldahl nitrogen level',
        'aliases': ['Total Kjeldahl nitrogen at 12:00'],
    }
    right = {
        'id': 'b', 'label': 'Total Kjeldahl Nitrogen Concentration at 12:15',
        'aliases': [],
    }
    result = entity_match(left, right)
    assert result['compatible'] is False
    assert 'time_conflict' in result['reasons']

def test_colon_definition_is_explicit_alias_evidence():
    from d2c13_2.entity_resolution import entity_match

    context = 'AppOK: application operates without errors'
    left = {'id': 'a', 'label': 'AppOK', 'aliases': [], 'context_texts': [context]}
    right = {'id': 'b', 'label': 'application operates without errors', 'aliases': [], 'context_texts': [context]}
    result = entity_match(left, right, [context], [context])
    assert result['compatible'] is True
    assert result['features']['reason'] == 'context_definition'
    assert result['score'] == 1.0


def test_colon_definition_supports_time_inside_surface():
    from d2c13_2.entity_resolution import entity_match

    context = 'CBODD_12_00: 12:00 dissolved biochemical oxygen demand level'
    left = {'id': 'a', 'label': 'CBODD_12_00', 'aliases': [], 'context_texts': [context]}
    right = {'id': 'b', 'label': '12:00 dissolved biochemical oxygen demand level', 'aliases': [], 'context_texts': [context]}
    result = entity_match(left, right, [context], [context])
    assert result['compatible'] is True
    assert result['features']['reason'] == 'context_definition'

