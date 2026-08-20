"""Regression tests for the model fallback/retry handler.

Guards the bug where `retries` was never honoured: the backoff slept and then
advanced to the *next* model, so a rate-limited model was never actually retried.
"""
import pytest

from agent import utils


class FakeModel:
    """Stands in for ChatGroq. `script` is a list of outcomes per call:
    an Exception to raise, or None to succeed."""

    def __init__(self, name, script=()):
        self.model_name = name
        self.script = list(script)
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        outcome = self.script.pop(0) if self.script else None
        if isinstance(outcome, Exception):
            raise outcome
        return f"{self.model_name}-ok"


@pytest.fixture
def sleeps(monkeypatch):
    """Capture backoff durations instead of actually waiting."""
    recorded = []
    monkeypatch.setattr(utils.time, "sleep", recorded.append)
    return recorded


def test_returns_first_successful_model(sleeps):
    m1, m2 = FakeModel("m1"), FakeModel("m2")
    assert utils.safe_invoke([m1, m2], prompt="x") == "m1-ok"
    assert m2.calls == 0


def test_rate_limited_model_is_retried_in_place(sleeps):
    m1 = FakeModel("m1", [Exception("rate_limit exceeded")])
    m2 = FakeModel("m2")
    assert utils.safe_invoke([m1, m2], prompt="x") == "m1-ok"
    assert m1.calls == 2, "rate-limited model should be retried, not skipped"
    assert m2.calls == 0, "should not fall back when the retry succeeds"
    assert sleeps == [10]


def test_non_rate_limit_error_falls_through_without_sleeping(sleeps):
    m1 = FakeModel("m1", [Exception("bad request")])
    m2 = FakeModel("m2")
    assert utils.safe_invoke([m1, m2], prompt="x") == "m2-ok"
    assert m1.calls == 1
    assert sleeps == [], "non-retryable errors must not wait"


def test_retries_argument_is_honoured(sleeps):
    m1 = FakeModel("m1", [Exception("429")] * 9)
    with pytest.raises(RuntimeError):
        utils.safe_invoke([m1], prompt="x", retries=2)
    assert m1.calls == 3, "retries=2 means 3 total attempts"
    assert sleeps == [10, 20], "exponential backoff, and no sleep before raising"


def test_exhausting_all_models_raises_with_causes(sleeps):
    m1 = FakeModel("m1", [Exception("boom-one")])
    m2 = FakeModel("m2", [Exception("boom-two")])
    with pytest.raises(RuntimeError) as exc:
        utils.safe_invoke([m1, m2], prompt="x", retries=0)
    assert "boom-one" in str(exc.value) and "boom-two" in str(exc.value)


def test_structured_output_path_is_used_when_requested(sleeps):
    class Structured(FakeModel):
        def with_structured_output(self, schema, method=None):
            self.schema, self.method = schema, method
            return FakeModel("structured")

    m = Structured("m1")
    assert utils.safe_invoke([m], structured_output=dict, method="function_calling",
                             prompt="x") == "structured-ok"
    assert m.method == "function_calling"
