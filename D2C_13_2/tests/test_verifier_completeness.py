import pytest

from d2c13_2.verifier import _call_complete_batch


class _CompletingVerifier:
    def __init__(self):
        self.retries = 3
        self.retry_backoff = 0
        self.telemetry = {"missing_verdict_retries": 0}
        self.calls = []

    def _call(self, batch):
        ids = [row["edge_id"] for row in batch]
        self.calls.append(ids)
        if len(self.calls) == 1:
            return [
                {"edge_id": ids[0], "support": "entailed"},
                {"edge_id": "not-requested", "support": "unsupported"},
            ]
        return [{"edge_id": edge_id, "support": "partial"} for edge_id in ids]


class _IncompleteVerifier:
    def __init__(self):
        self.retries = 2
        self.retry_backoff = 0
        self.telemetry = {"missing_verdict_retries": 0}
        self.calls = 0

    def _call(self, batch):
        self.calls += 1
        return []


def test_missing_verdict_is_retried_and_unknown_id_is_ignored():
    verifier = _CompletingVerifier()
    batch = [{"edge_id": "e1"}, {"edge_id": "e2"}]
    verdicts = _call_complete_batch(verifier, batch, 1, 1, completion_retries=2)
    assert [row["edge_id"] for row in verdicts] == ["e1", "e2"]
    assert verdicts[0]["support"] == "entailed"
    assert verdicts[1]["support"] == "partial"
    assert verifier.telemetry["missing_verdict_retries"] == 1
    assert verifier.calls == [["e1", "e2"], ["e2"]]


def test_incomplete_verdict_fails_after_retry_budget():
    verifier = _IncompleteVerifier()
    with pytest.raises(RuntimeError, match="omitted"):
        _call_complete_batch(verifier, [{"edge_id": "e1"}], 1, 1, completion_retries=1)
    assert verifier.calls == 2