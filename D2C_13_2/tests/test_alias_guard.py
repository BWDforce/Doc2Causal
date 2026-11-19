import networkx as nx

from d2c13_2.alias_guard import alias_conflict, sanitize_graph_aliases, semantic_alias_veto
from d2c13_2.lang7_flow import normalize


def test_temporal_phrase_is_not_content_alias():
    assert alias_conflict('Before Selecting a Job', 'Print Data Content')
    assert alias_conflict('12:15 Kjeldahl Nitrogen Concentration', 'time point 12:15')
    assert alias_conflict('dissolved BOD 12:00', 'level')


def test_time_specific_abbreviation_remains_allowed():
    assert not alias_conflict(
        'Nitrogenous Biochemical Oxygen Demand Concentration (12:45)',
        'NBOD_1245_conc',
    )


def test_desc_only_temporal_pair_is_vetoed_before_adjudication():
    graph = nx.DiGraph()
    shared = 'the print data content before selecting a job'
    graph.add_node('print data content', description=shared, type='variable')
    graph.add_node('before selecting a job', description=shared, type='event')
    pairs, vetoed, scored = normalize.block_pairs(graph, threshold=0.7)
    assert pairs == []
    assert scored == 0
    assert any(row['veto'] == 'semantic_alias_guard' for row in vetoed)


def test_semantic_veto_keeps_supported_abbreviation_candidate():
    channels = [('desc', 1.0)]
    assert semantic_alias_veto(
        'Nitrogenous Biochemical Oxygen Demand Concentration (12:45)',
        'NBOD_1245_conc',
        'variable', 'measurement', channels,
    ) is None


def test_compiler_repair_splits_alias_used_by_graph_edges():
    graph = nx.DiGraph()
    graph.add_node(
        'Before Selecting a Job',
        name='Before Selecting a Job',
        description='before selecting a job',
        type='event',
        merged_aliases=[
            'Before Selecting a Job', 'before selecting a job',
            'Print Data Content', 'print data content',
        ],
    )
    graph.add_node('Printer Timeout Value', name='Printer Timeout Value', type='condition')
    graph.add_edge(
        'Printer Timeout Value', 'Before Selecting a Job',
        cause='Printer Timeout Value', effect='Print Data Content',
        type='direct', description='timeout bounds print data content',
    )
    report = sanitize_graph_aliases(graph)
    assert graph.has_node('Print Data Content')
    assert graph.has_edge('Printer Timeout Value', 'Print Data Content')
    assert not graph.has_edge('Printer Timeout Value', 'Before Selecting a Job')
    assert report['split_count'] == 1
    assert report['rebound_edges'] == 1
    assert 'Print Data Content' not in graph.nodes['Before Selecting a Job']['merged_aliases']


def test_compiler_repair_removes_unused_conflicting_alias():
    graph = nx.DiGraph()
    graph.add_node(
        'dissolved BOD 12:00',
        name='dissolved BOD 12:00',
        type='measurement',
        merged_aliases=['dissolved BOD 12:00', 'level'],
    )
    report = sanitize_graph_aliases(graph)
    assert graph.nodes['dissolved BOD 12:00']['merged_aliases'] == ['dissolved BOD 12:00']
    assert report['split_count'] == 0
    assert report['removed_count'] == 1
