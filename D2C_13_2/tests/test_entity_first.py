from types import SimpleNamespace

import networkx as nx

from d2c13_2.lang7_flow import entity_inventory
from d2c13_2.lang7_flow import extract_boost


def _item(cls, text, attrs):
    return SimpleNamespace(extraction_class=cls, extraction_text=text, attributes=attrs)


def test_inventory_from_grounded_preserves_variable_and_relation_endpoints():
    items = [
        _item("causal_variable", "printer offline", {"canonical_name": "printer offline", "entity_type": "state"}),
        _item("causal_relation", "printer offline causes queue growth", {"cause": "printer offline", "effect": "queue growth"}),
    ]
    rows = entity_inventory.inventory_from_grounded(items)
    names = {row["canonical_name"] for row in rows}
    assert names == {"printer offline", "queue growth"}


def test_relation_candidates_prefer_source_local_pairs():
    source = "Printer offline causes queue growth. Queue growth increases wait time."
    inventory = [
        {"inventory_id": "i1", "canonical_name": "printer offline", "mention": "Printer offline", "aliases": []},
        {"inventory_id": "i2", "canonical_name": "queue growth", "mention": "queue growth", "aliases": []},
        {"inventory_id": "i3", "canonical_name": "wait time", "mention": "wait time", "aliases": []},
    ]
    pairs = entity_inventory.build_relation_pairs(source, inventory, max_pairs=10)
    assert any({row["source"], row["target"]} == {"printer offline", "queue growth"} for row in pairs)
    assert all(row["causal_cue"] for row in pairs)


def test_relation_completion_prompt_contains_inventory_and_pairs():
    prompt = entity_inventory.build_relation_completion_prompt(
        "BASE PROMPT",
        [{"canonical_name": "A", "aliases": ["alpha"], "entity_type": "state"}],
        [{"source": "A", "target": "B", "source_surface": "A", "target_surface": "B", "window_id": "s1", "evidence_text": "A causes B", "causal_cue": True}],
    )
    assert prompt.startswith("BASE PROMPT")
    assert "ENTITY INVENTORY" in prompt
    assert "A <-> B" in prompt


def test_attach_inventory_nodes_adds_isolated_entities():
    graph = nx.DiGraph()
    count = entity_inventory.attach_inventory_nodes(graph, [{
        "canonical_name": "printer offline",
        "entity_type": "state",
        "unit": "",
        "aliases": ["offline"],
        "evidence_text": "printer is offline",
        "inventory_id": "i1",
    }])
    assert count == 1
    assert "printer offline" in graph
    assert graph.nodes["printer offline"]["type"] == "state"


class _FakePipeline:
    PROMPT_DESCRIPTION = "BASE"

    def __init__(self):
        self.prompts = []

    def run_langextract(self, **kwargs):
        self.prompts.append(kwargs.get("prompt_description", self.PROMPT_DESCRIPTION))
        return object()

    def grounded_extractions(self, document):
        if len(self.prompts) == 1:
            return [_item("causal_variable", "A", {"canonical_name": "A"})]
        return [_item("causal_relation", "A causes B", {"cause": "A", "effect": "B"})]


def test_union_pass_two_uses_relation_matrix_prompt(monkeypatch, tmp_path):
    args = SimpleNamespace(
        extraction_passes=1, evidence_union=True, current_doc_key="doc1",
        api_key="x", model="m", base_url="u", max_output_tokens=100,
        entity_first=True, checkpoint_dir=tmp_path,
        evidence_union_docs={}, entity_first_docs={},
    )
    inventory = [{"inventory_id": "i1", "canonical_name": "A", "mention": "A", "aliases": []},
                 {"inventory_id": "i2", "canonical_name": "B", "mention": "B", "aliases": []}]
    monkeypatch.setattr(extract_boost.entity_first, "build_entity_inventory", lambda *a, **k: inventory)
    monkeypatch.setattr(extract_boost.entity_first, "build_relation_pairs", lambda *a, **k: [{"source": "A", "target": "B", "source_surface": "A", "target_surface": "B", "window_id": "s1", "evidence_text": "A causes B", "causal_cue": True}])
    pipeline = _FakePipeline()
    items = extract_boost.run_langextract_union(args, "A causes B.", pipeline)
    assert len(items) == 2
    assert pipeline.prompts[0] == "BASE"
    assert "ENTITY-FIRST RELATION-COMPLETION PASS" in pipeline.prompts[1]
    assert pipeline.PROMPT_DESCRIPTION == "BASE"
    assert args.entity_first_docs["doc1"]["inventory_count"] == 2

def test_lang7_launcher_passes_entity_first_config(monkeypatch, tmp_path):
    from d2c13_2 import lang7_pipeline

    captured = {}
    monkeypatch.setattr(lang7_pipeline, "role_config", lambda *a, **k: {"model": "m", "base_url": "u", "max_tokens": 1000})
    monkeypatch.setattr(lang7_pipeline, "load_config", lambda *a, **k: {"entity_first": {"enabled": True, "max_items": 12, "max_chunks": 2, "temperature": 0.0, "selection_policy": "evidence_window_compaction_v1"}})
    def fake_main(argv):
        captured["argv"] = argv
        return 0
    monkeypatch.setattr(lang7_pipeline.lang7_flow, "main", fake_main)
    summary = lang7_pipeline.run_lang7(tmp_path / "input", tmp_path / "output", "config.json", theme="test", temperature=0.3)
    assert summary["returncode"] == 0
    argv = captured["argv"]
    assert "--entity-first" in argv
    assert argv[argv.index("--entity-inventory-max-items") + 1] == "12"
    assert "--relation-matrix-max-pairs" not in argv

def test_build_entity_inventory_caches_llm_inventory(monkeypatch, tmp_path):
    args = SimpleNamespace(
        entity_first=True, current_doc_key="doc1", checkpoint_dir=tmp_path,
        api_key="x", base_url="u", model="m", entity_inventory_model=None,
        entity_inventory_temperature=0.0, entity_inventory_max_chunks=1,
        entity_inventory_max_items=10,
    )
    monkeypatch.setattr(entity_inventory, "chat_json", lambda *a, **k: {"entities": [{
        "mention": "printer offline", "canonical_name": "printer offline",
        "entity_type": "state", "aliases": ["offline printer"], "evidence_text": "printer offline",
    }]})
    rows = entity_inventory.build_entity_inventory(args, "Printer offline causes delay.")
    assert len(rows) == 1
    assert rows[0]["inventory_id"] == "inv_0001"
    cache = entity_inventory.inventory_cache_path(args)
    assert cache.exists()

def test_extraction_report_writer_does_not_chain_write_calls(tmp_path):
    from d2c13_2.lang7_flow import run_extract

    args = SimpleNamespace(output_dir=tmp_path, entity_first=True, entity_first_docs={"doc1": {"inventory_count": 3}})
    result = run_extract.write_extraction_reports(args, {"documents": {}})
    assert result["entity_first"].exists()
    assert result["evidence_union"].exists()
    assert "inventory_count" in result["entity_first"].read_text(encoding="utf-8")

def test_causal_cue_uses_token_boundaries_and_extended_verbs():
    assert not entity_inventory._has_causal_cue("different readings are available")
    assert entity_inventory._has_causal_cue("the earlier value carried forward to the later value")
    assert entity_inventory._has_causal_cue("the reading propagated into the next sample")


def test_relation_pairs_are_deduplicated_and_locality_ranked():
    source = "A causes B. B causes C. A causes B again."
    inventory = [
        {"inventory_id": "i1", "canonical_name": "A", "mention": "A", "aliases": []},
        {"inventory_id": "i2", "canonical_name": "B", "mention": "B", "aliases": []},
        {"inventory_id": "i3", "canonical_name": "C", "mention": "C", "aliases": []},
    ]
    pairs = entity_inventory.build_relation_pairs(source, inventory, max_pairs=1)
    assert len(pairs) == 1
    assert {pairs[0]["source"], pairs[0]["target"]} == {"A", "B"}
    assert pairs[0]["same_sentence"] is True
    all_pairs = entity_inventory.build_relation_pairs(source, inventory, max_pairs=20)
    keys = [tuple(sorted((row["source"], row["target"]))) for row in all_pairs]
    assert len(keys) == len(set(keys))

def test_relation_pairs_have_no_default_global_cap():
    names = [f"variable {index}" for index in range(8)]
    source = " ".join(f"{name} influences {names[(index + 1) % len(names)]}." for index, name in enumerate(names))
    inventory = [
        {"inventory_id": f"i{index}", "canonical_name": name, "mention": name, "aliases": []}
        for index, name in enumerate(names)
    ]
    pairs = entity_inventory.build_relation_pairs(source, inventory)
    assert len(pairs) > 1


def test_relation_prompt_deduplicates_shared_evidence():
    inventory = [
        {"canonical_name": "A", "aliases": [], "entity_type": "state"},
        {"canonical_name": "B", "aliases": [], "entity_type": "state"},
        {"canonical_name": "C", "aliases": [], "entity_type": "state"},
    ]
    pairs = [
        {"source": "A", "target": "B", "source_surface": "A", "target_surface": "B", "window_id": "s1", "evidence_text": "A causes B and C.", "causal_cue": True, "mention_gap": 9},
        {"source": "A", "target": "C", "source_surface": "A", "target_surface": "C", "window_id": "s1", "evidence_text": "A causes B and C.", "causal_cue": True, "mention_gap": 18},
    ]
    prompt = entity_inventory.build_relation_completion_prompt("BASE PROMPT", inventory, pairs)
    assert prompt.count("A causes B and C.") == 1
    assert "A <-> B" in prompt
    assert "A <-> C" in prompt

def test_relation_prompts_are_batched_by_paragraph_without_dropping_pairs():
    inventory = [
        {"canonical_name": "A", "aliases": [], "entity_type": "state"},
        {"canonical_name": "B", "aliases": [], "entity_type": "state"},
    ]
    pairs = [
        {"source": "A", "target": "B", "source_surface": "A", "target_surface": "B", "paragraph_id": "p1", "window_id": "p1s1", "evidence_text": "A causes B.", "causal_cue": True, "mention_gap": 9},
        {"source": "B", "target": "A", "source_surface": "B", "target_surface": "A", "paragraph_id": "p2", "window_id": "p2s1", "evidence_text": "B causes A.", "causal_cue": True, "mention_gap": 9},
    ]
    prompts = entity_inventory.build_relation_completion_prompts("BASE PROMPT", inventory, pairs)
    assert len(prompts) == 2
    assert "A causes B." in prompts[0]
    assert "B causes A." in prompts[1]

def test_union_runs_one_relation_prompt_per_paragraph(monkeypatch, tmp_path):
    class ParagraphPipeline:
        PROMPT_DESCRIPTION = "BASE"
        def __init__(self):
            self.prompts = []
        def run_langextract(self, **kwargs):
            self.prompts.append(self.PROMPT_DESCRIPTION)
            return object()
        def grounded_extractions(self, document):
            if len(self.prompts) == 1:
                return [_item("causal_variable", "A", {"canonical_name": "A"})]
            return [_item("causal_relation", "A causes B", {"cause": "A", "effect": "B"})]

    args = SimpleNamespace(
        extraction_passes=1, evidence_union=True, current_doc_key="doc1",
        api_key="x", model="m", base_url="u", max_output_tokens=100,
        entity_first=True, checkpoint_dir=tmp_path,
        evidence_union_docs={}, entity_first_docs={},
    )
    inventory = [
        {"inventory_id": "i1", "canonical_name": "A", "mention": "A", "aliases": []},
        {"inventory_id": "i2", "canonical_name": "B", "mention": "B", "aliases": []},
    ]
    pairs = [
        {"source": "A", "target": "B", "source_surface": "A", "target_surface": "B", "paragraph_id": "p1", "window_id": "p1s1", "evidence_text": "A causes B.", "causal_cue": True, "mention_gap": 9},
        {"source": "B", "target": "A", "source_surface": "B", "target_surface": "A", "paragraph_id": "p2", "window_id": "p2s1", "evidence_text": "B causes A.", "causal_cue": True, "mention_gap": 9},
    ]
    monkeypatch.setattr(extract_boost.entity_first, "build_entity_inventory", lambda *a, **k: inventory)
    monkeypatch.setattr(extract_boost.entity_first, "build_relation_pairs", lambda *a, **k: pairs)
    pipeline = ParagraphPipeline()
    items = extract_boost.run_langextract_union(args, "A causes B.\n\nB causes A.", pipeline)
    assert len(pipeline.prompts) == 3
    assert len(items) == 3
    assert args.entity_first_docs["doc1"]["relation_batch_count"] == 2



def test_relation_completion_batches_use_paragraph_local_sources_with_offsets():
    source = "Header.\n\nA causes B.\n\nB causes C."
    inventory = [
        {"inventory_id": "i1", "canonical_name": "A", "mention": "A", "aliases": [], "entity_type": "state"},
        {"inventory_id": "i2", "canonical_name": "B", "mention": "B", "aliases": [], "entity_type": "state"},
        {"inventory_id": "i3", "canonical_name": "C", "mention": "C", "aliases": [], "entity_type": "state"},
        {"inventory_id": "i4", "canonical_name": "Unrelated", "mention": "Unrelated", "aliases": [], "entity_type": "state"},
    ]
    pairs = [
        {"source": "A", "target": "B", "source_surface": "A", "target_surface": "B", "paragraph_id": "p2", "window_id": "p2s1", "evidence_text": "A causes B.", "causal_cue": True, "mention_gap": 9},
        {"source": "B", "target": "C", "source_surface": "B", "target_surface": "C", "paragraph_id": "p3", "window_id": "p3s1", "evidence_text": "B causes C.", "causal_cue": True, "mention_gap": 9},
    ]
    batches = entity_inventory.build_relation_completion_batches("BASE PROMPT", inventory, pairs, source)
    assert len(batches) == 2
    assert batches[0]["source_text"] == "A causes B."
    assert batches[0]["offset"] == source.index("A causes B.")
    assert batches[1]["source_text"] == "B causes C."
    assert batches[1]["offset"] == source.index("B causes C.")
    assert "A <-> B" in batches[0]["prompt"]
    assert "A causes B." not in batches[0]["prompt"]
    assert "Unrelated" not in batches[0]["prompt"]


def test_union_runs_relation_pass_on_paragraph_text_and_shifts_spans(monkeypatch, tmp_path):
    class OffsetPipeline:
        PROMPT_DESCRIPTION = "BASE"

        def __init__(self):
            self.calls = []

        def run_langextract(self, **kwargs):
            self.calls.append(kwargs)
            return object()

        def grounded_extractions(self, document):
            if len(self.calls) == 1:
                return [_item("causal_variable", "A", {"canonical_name": "A"})]
            interval = SimpleNamespace(start_pos=2, end_pos=13)
            return [SimpleNamespace(
                extraction_class="causal_relation",
                extraction_text="A causes B",
                attributes={"cause": "A", "effect": "B"},
                char_interval=interval,
            )]

    args = SimpleNamespace(
        extraction_passes=1, evidence_union=True, current_doc_key="doc1",
        api_key="x", model="m", base_url="u", max_output_tokens=100,
        entity_first=True, checkpoint_dir=tmp_path,
        evidence_union_docs={}, entity_first_docs={},
    )
    inventory = [
        {"inventory_id": "i1", "canonical_name": "A", "mention": "A", "aliases": []},
        {"inventory_id": "i2", "canonical_name": "B", "mention": "B", "aliases": []},
        {"inventory_id": "i3", "canonical_name": "C", "mention": "C", "aliases": []},
    ]
    pairs = [
        {"source": "A", "target": "B", "source_surface": "A", "target_surface": "B", "paragraph_id": "p2", "window_id": "p2s1", "evidence_text": "A causes B.", "causal_cue": True, "mention_gap": 9},
        {"source": "B", "target": "C", "source_surface": "B", "target_surface": "C", "paragraph_id": "p3", "window_id": "p3s1", "evidence_text": "B causes C.", "causal_cue": True, "mention_gap": 9},
    ]
    source = "Header.\n\nA causes B.\n\nB causes C."
    monkeypatch.setattr(extract_boost.entity_first, "build_entity_inventory", lambda *a, **k: inventory)
    monkeypatch.setattr(extract_boost.entity_first, "build_relation_pairs", lambda *a, **k: pairs)
    pipeline = OffsetPipeline()
    items = extract_boost.run_langextract_union(args, source, pipeline)
    assert len(pipeline.calls) == 3
    assert pipeline.calls[0]["max_char_buffer"] == 1800
    assert pipeline.calls[1]["source_text"] == "A causes B."
    assert pipeline.calls[2]["source_text"] == "B causes C."
    assert pipeline.calls[1]["max_char_buffer"] >= len("A causes B.")
    assert items[1].char_interval.start_pos == source.index("A causes B.") + 2
    assert items[2].char_interval.start_pos == source.index("B causes C.") + 2
    report = args.entity_first_docs["doc1"]
    assert report["relation_batch_count"] == 2
    assert [row["paragraph_id"] for row in report["relation_batches"]] == ["p2", "p3"]
