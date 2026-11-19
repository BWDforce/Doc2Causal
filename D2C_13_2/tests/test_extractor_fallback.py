# -*- coding: utf-8 -*-
import json

from d2c13_2.extractor import DeepSeekExtractor


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_empty_json_mode_falls_back_without_response_format(monkeypatch, tmp_path):
    calls = []
    responses = [
        FakeResponse({"choices": [{"message": {"content": ""}}]}),
        FakeResponse({"choices": [{"message": {"content": json.dumps({"entities": [], "links": [], "chains": []})}}]}),
    ]

    def fake_post(url, headers, json, timeout):
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr("d2c13_2.extractor.requests.post", fake_post)
    extractor = DeepSeekExtractor(
        api_key="test", base_url="https://example.test/v1", model="test-model",
        max_tokens=100, raw_dir=tmp_path,
    )
    payload = extractor.call("extract")
    assert payload == {"entities": [], "links": [], "chains": []}
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]
    assert list(tmp_path.glob("call_*.json"))

