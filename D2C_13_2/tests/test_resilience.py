# -*- coding: utf-8 -*-
from types import SimpleNamespace
from d2c13_2.lang7_flow import extract_boost


class _FakePipeline:
    def __init__(self, fail_after=0):
        self.calls = 0
        self.fail_after = fail_after

    def run_langextract(self, **kwargs):
        self.calls += 1
        if self.calls > self.fail_after:
            raise RuntimeError("simulated finish_reason=length")
        return {"ok": True}

    def grounded_extractions(self, document):
        return [{"text": "A causes B"}]


def _args(tmp_path):
    return SimpleNamespace(
        api_key="x", model="m", base_url="u", extraction_passes=2,
        evidence_union=True, checkpoint_dir=tmp_path,
        current_doc_key="doc1", evidence_union_docs={},
    )


def test_evidence_union_falls_back_after_second_pass_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_boost.time, "sleep", lambda _: None)
    args = _args(tmp_path)
    pipeline = _FakePipeline(fail_after=1)
    items = extract_boost.run_langextract_union(args, "A causes B", pipeline)
    assert len(items) == 1
    assert args.evidence_union_degraded is True
    assert args.evidence_union_completed_passes == 1
    assert (tmp_path / "doc1.pass1.json").exists()


def test_evidence_union_reuses_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_boost.time, "sleep", lambda _: None)
    first = _args(tmp_path)
    extract_boost.run_langextract_union(first, "A causes B", _FakePipeline(fail_after=2))
    assert (tmp_path / "doc1.pass1.json").exists()
    second = _args(tmp_path)
    pipeline = _FakePipeline(fail_after=0)
    items = extract_boost.run_langextract_union(second, "A causes B", pipeline)
    assert len(items) == 2
    assert second.evidence_union_docs["doc1"]["checkpoint_hits"] == 2
    assert pipeline.calls == 0


from pathlib import Path
import json
from types import SimpleNamespace
from d2c13_2.lang7_flow import extract_boost

def test_checkpoint_roundtrip_preserves_span_and_attributes(tmp_path):
    item = SimpleNamespace(
        extraction_class="causal_relation",
        extraction_text="A causes B",
        char_interval=SimpleNamespace(start_pos=0, end_pos=10),
        alignment_status=SimpleNamespace(value="match_exact"),
        extraction_index=1,
        group_index=0,
        description=None,
        attributes={"cause": "A", "effect": "B"},
    )
    path = tmp_path / "doc.pass1.json"
    extract_boost._write_checkpoint(path, 0, [item])
    restored = extract_boost._read_checkpoint(path)
    assert len(restored) == 1
    assert restored[0].extraction_class == "causal_relation"
    assert restored[0].char_interval.start_pos == 0
    assert restored[0].attributes["effect"] == "B"

def test_legacy_checkpoint_string_is_rehydrated(tmp_path):
    legacy = "Extraction(extraction_class='causal_relation', extraction_text='A causes B', char_interval=CharInterval(start_pos=0, end_pos=10), alignment_status=<AlignmentStatus.MATCH_EXACT: 'match_exact'>, extraction_index=1, group_index=0, description=None, attributes={'cause': 'A', 'effect': 'B'})"
    path = tmp_path / "legacy.pass1.json"
    path.write_text(json.dumps({"items": [legacy]}), encoding="utf-8")
    restored = extract_boost._read_checkpoint(path)
    assert len(restored) == 1
    assert restored[0].extraction_class == "causal_relation"
    assert restored[0].extraction_text == "A causes B"
    assert restored[0].char_interval.end_pos == 10
    assert restored[0].attributes == {"cause": "A", "effect": "B"}