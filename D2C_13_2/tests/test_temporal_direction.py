# -*- coding: utf-8 -*-
from d2c13_2.semantics import temporal_direction_guard, materialize_views


def _artifact(source, target):
    return {
        "version": "D2C_13_2",
        "artifact_id": "urn:d2c:artifact:temporal-test",
        "entities": [
            {"id": "e:a", "label": source, "aliases": []},
            {"id": "e:b", "label": target, "aliases": []},
        ],
        "documents": [], "spans": [], "assertions": [], "conditions": [],
        "edges": [{
            "id": "x:ab", "source": "e:a", "target": "e:b",
            "source_name": source, "target_name": target,
            "kind": "direct_core", "evidence_grade": "span_grounded",
            "direction_status": "unverified", "attributes": {},
        }],
        "views": {}, "diagnostics": {},
    }


def test_temporal_guard_reverses_late_to_early_edge():
    artifact = _artifact("Nitrogen Concentration (12:30)", "Nitrogen Concentration (12:15)")
    result = temporal_direction_guard(artifact)
    edge = artifact["edges"][0]
    assert result["corrected"] == 1
    assert edge["source_name"] == "Nitrogen Concentration (12:15)"
    assert edge["target_name"] == "Nitrogen Concentration (12:30)"
    assert edge["direction_status"] == "corrected"
    assert edge["direction_audit"] == "temporal_order"
    materialize_views(artifact)


def test_temporal_guard_keeps_chronological_edge():
    artifact = _artifact("Nitrogen Concentration (12:00)", "Nitrogen Concentration (12:30)")
    result = temporal_direction_guard(artifact)
    assert result["corrected"] == 0
    assert artifact["edges"][0]["source_name"] == "Nitrogen Concentration (12:00)"
