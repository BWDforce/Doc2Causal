# -*- coding: utf-8 -*-
from d2c13_2.compiler import _normalize_legacy_span_offsets


def _evidence(span: str):
    return {"meta": {}, "entries": {"A || B": {"assertions": [{"doc_id": "doc1", "span": span}]}}}


def test_legacy_source_text_offset_is_converted_to_summary_coordinates():
    summary = "At quarter past noon, A drove B via turnover."
    title = "Example document"
    prefix = len(f"Title: {title}\n - \nContent:\n")
    start, end = 0, len("At quarter past noon, A drove B via turnover.")
    evidence = _evidence(f"{start + prefix}:{end + prefix}")
    stats = _normalize_legacy_span_offsets(evidence, {"doc1": summary}, {"doc1": prefix})
    assertion = evidence["entries"]["A || B"]["assertions"][0]
    assert assertion["span"] == f"{start}:{end}"
    assert evidence["meta"]["span_coordinate_system"] == "document_summary"
    assert stats == {"legacy_source_text_coordinates": True, "corrected": 1, "skipped": 0}


def test_already_normalized_summary_offsets_are_not_shifted_twice():
    summary = "At quarter past noon, A drove B via turnover."
    evidence = _evidence(f"0:{len(summary)}")
    evidence["meta"]["span_coordinate_system"] = "document_summary"
    _normalize_legacy_span_offsets(evidence, {"doc1": summary}, {"doc1": 100})
    assertion = evidence["entries"]["A || B"]["assertions"][0]
    assert assertion["span"] == f"0:{len(summary)}"
