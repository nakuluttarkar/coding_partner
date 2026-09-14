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
    assert sleeps == [20], "backoff should match Groq's 60s per-minute window"


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
    assert sleeps == [20, 40], "exponential backoff, and no sleep before raising"


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


# --- rate-limit classification and backoff -----------------------------------
# Real Groq free-tier failures: TPM exceeded arrives as 413, OTPM as 429, and a
# file too large to write arrives as a 400 with unparseable tool-call JSON.

TPM_413 = ("Error code: 413 - Request too large for model openai/gpt-oss-120b on tokens "
           "per minute (TPM): Limit 8000, Requested 8122 ... 'code': 'rate_limit_exceeded'")
OTPM_429 = ("Error code: 429 - Request too large for model qwen/qwen3.6-27b on output tokens "
            "per minute (OTPM): Limit 1000, Requested 1064 ... 'code': 'rate_limit_exceeded'")
TRUNCATED_400 = ("Error code: 400 - Failed to parse tool call arguments as JSON, "
                 "'code': 'tool_use_failed'")


@pytest.mark.parametrize("message", [TPM_413, OTPM_429, "429 Too Many Requests"])
def test_rate_limit_classification(message):
    assert utils.is_rate_limited(Exception(message))


def test_truncated_tool_call_is_not_treated_as_a_rate_limit():
    exc = Exception(TRUNCATED_400)
    assert utils.is_output_truncated(exc)
    assert not utils.is_rate_limited(exc)


def test_backoff_honours_the_wait_groq_asks_for():
    exc = Exception("Rate limit reached. Please try again in 7.482s")
    assert utils.backoff_seconds(exc, attempt=0) == pytest.approx(8.482)


def test_backoff_is_capped():
    assert utils.backoff_seconds(Exception("429"), attempt=10) == 60


def test_tpm_failure_is_retried_in_place(sleeps):
    m1 = FakeModel("m1", [Exception(TPM_413)])
    m2 = FakeModel("m2")
    assert utils.safe_invoke([m1, m2], prompt="x") == "m1-ok"
    assert m1.calls == 2, "a 413 TPM error clears on its own and must be retried"
    assert m2.calls == 0


def test_error_explains_truncation_when_a_tool_call_was_cut_off(sleeps):
    m1 = FakeModel("m1", [Exception(TRUNCATED_400)])
    with pytest.raises(RuntimeError, match="too large to write in a single call"):
        utils.safe_invoke([m1], prompt="x", retries=0)


def test_error_explains_free_tier_limits_when_rate_limited(sleeps):
    m1 = FakeModel("m1", [Exception(TPM_413)])
    with pytest.raises(RuntimeError, match="free-tier per-minute limits"):
        utils.safe_invoke([m1], prompt="x", retries=0)


def test_console_survives_a_non_utf8_stdout(monkeypatch, capsys):
    """Reproduces the Windows cp1252 crash: printing non-ASCII must not raise."""
    real_print = print

    def cp1252_print(msg):
        # Mimics a console that rejects anything outside cp1252.
        str(msg).encode("cp1252")
        real_print(msg)

    monkeypatch.setattr("builtins.print", cp1252_print)
    utils.console("done ← arrow")   # left-arrow: not encodable in cp1252
    monkeypatch.undo()

    out = capsys.readouterr().out
    assert "done" in out and "arrow" in out, "message should still reach the console"


def test_console_passes_ascii_through_unchanged(capsys):
    utils.console("[MODEL] plain ascii")
    assert capsys.readouterr().out.strip() == "[MODEL] plain ascii"


def test_backoff_parses_millisecond_waits():
    """Groq reports sub-second waits in ms ("Please try again in 975ms"). Missing
    that unit turned a 1-second wait into a 20-second backoff -- observed live."""
    exc = Exception("Rate limit reached ... Please try again in 975ms")
    assert utils.backoff_seconds(exc, attempt=0) == pytest.approx(1.975)


def test_backoff_still_parses_second_waits():
    exc = Exception("Rate limit reached ... Please try again in 7.482s")
    assert utils.backoff_seconds(exc, attempt=0) == pytest.approx(8.482)


def test_real_tpm_error_from_a_live_run_is_retryable():
    """Verbatim from the recipe-app run's README step."""
    live = ("Error code: 429 - {'error': {'message': 'Rate limit reached for model "
            "`openai/gpt-oss-120b` ... on tokens per minute (TPM): Limit 8000, Used 4541, "
            "Requested 3589. Please try again in 975ms.', 'code': 'rate_limit_exceeded'}}")
    assert utils.is_rate_limited(Exception(live))
    assert utils.backoff_seconds(Exception(live), 0) == pytest.approx(1.975)
