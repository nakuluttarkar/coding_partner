"""Tests for graph wiring and the coder loop.

The coder test guards the bug where every model failing still advanced
current_step_idx, silently skipping a file and reporting success on a project
that was never fully written.
"""
import pytest

from agent import graph as g
from agent import utils
from agent.states import CoderState, ImplementationTask, TaskPlan


def _task_plan(*filepaths):
    return TaskPlan(implementation_steps=[
        ImplementationTask(filepath=fp, task_description=f"build {fp}") for fp in filepaths
    ])


class _StubAgent:
    """Stands in for the compiled react agent returned by create_react_agent."""

    def __init__(self, fail):
        self.fail = fail

    def invoke(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("model unavailable")
        return {"messages": []}


class _StubReadFile:
    """StructuredTool is a pydantic model and rejects attribute patching, so the
    module-level reference is swapped instead."""

    def run(self, *args, **kwargs):
        return ""


@pytest.fixture
def no_disk_reads(monkeypatch):
    """coding_agent reads the target file for context; keep tests off the disk."""
    monkeypatch.setattr(g, "read_file", _StubReadFile())


def test_graph_compiles_and_exposes_expected_state_keys():
    assert set(g.AgentState.__annotations__) == {
        "user_prompt", "plan", "task_plan", "coder_state", "status", "problems"
    }
    assert g.agent is not None


def test_fallback_chain_starts_and_ends_with_production_models():
    # Preview models can be retired at short notice; the ends of the chain
    # must be production so it degrades rather than breaking outright.
    assert g.FALLBACK_MODEL_IDS[0].startswith("openai/gpt-oss")
    assert g.FALLBACK_MODEL_IDS[-1].startswith("openai/gpt-oss")
    assert len(g.FALLBACK_MODELS) == len(g.FALLBACK_MODEL_IDS)


def test_retired_model_is_not_referenced():
    assert not any("llama-4-scout" in m for m in g.FALLBACK_MODEL_IDS)


def test_coder_reports_done_when_all_steps_are_complete(no_disk_reads):
    state = {"coder_state": CoderState(task_plan=_task_plan("a.txt"), current_step_idx=1)}
    assert g.coding_agent(state)["status"] == "DONE"


def test_coder_advances_one_step_on_success(monkeypatch, no_disk_reads):
    monkeypatch.setattr(g, "create_react_agent", lambda model, tools: _StubAgent(fail=False))
    state = {"task_plan": _task_plan("a.txt", "b.txt")}
    result = g.coding_agent(state)
    assert result["coder_state"].current_step_idx == 1
    assert "status" not in result


def test_coder_raises_instead_of_skipping_when_every_model_fails(monkeypatch, no_disk_reads):
    monkeypatch.setattr(g, "create_react_agent", lambda model, tools: _StubAgent(fail=True))
    coder_state = CoderState(task_plan=_task_plan("a.txt", "b.txt"), current_step_idx=0)

    with pytest.raises(RuntimeError) as exc:
        g.coding_agent({"coder_state": coder_state})

    assert "a.txt" in str(exc.value), "error should name the file that failed"
    assert coder_state.current_step_idx == 0, "a failed step must not be skipped"


def test_coder_error_names_every_attempted_model(monkeypatch, no_disk_reads):
    monkeypatch.setattr(g, "create_react_agent", lambda model, tools: _StubAgent(fail=True))
    with pytest.raises(RuntimeError) as exc:
        g.coding_agent({"task_plan": _task_plan("a.txt")})
    for model_id in g.CODER_MODEL_IDS:
        assert model_id in str(exc.value)


def test_task_plan_hides_attached_plan_from_the_llm_schema():
    # `plan` is attached by architect_agent after generation; exposing it in the
    # function-calling schema would ask the model to fill in the whole plan again.
    assert "plan" not in TaskPlan.model_json_schema()["properties"]


def test_coder_chain_excludes_the_low_output_cap_model():
    """qwen/qwen3.6-27b has a 1,000 output-token/min free-tier cap; a stylesheet
    exceeds it on its own, so the coder must not fall back to it."""
    assert "qwen/qwen3.6-27b" not in g.CODER_MODEL_IDS
    assert "qwen/qwen3.6-27b" in g.FALLBACK_MODEL_IDS


def test_coder_retries_a_rate_limited_model_instead_of_giving_up(monkeypatch, no_disk_reads):
    """A 413 TPM error clears within the minute; the old loop gave up on it and
    failed a run that would have succeeded on retry."""
    monkeypatch.setattr(utils.time, "sleep", lambda s: None)

    class Flaky:
        def __init__(self):
            self.n = 0

        def invoke(self, *a, **k):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("Error code: 413 ... 'code': 'rate_limit_exceeded'")
            return {"messages": []}

    flaky = Flaky()
    monkeypatch.setattr(g, "create_react_agent", lambda model, tools: flaky)
    result = g.coding_agent({"task_plan": _task_plan("style.css")})
    assert flaky.n == 2, "should retry the rate-limited model in place"
    assert result["coder_state"].current_step_idx == 1


def test_verifier_reports_a_missing_stylesheet(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="style.css">', encoding="utf-8")
    monkeypatch.setattr(g, "GENERATED_PROJECT_ROOT", tmp_path)
    problems = g.verifier_agent({})["problems"]
    assert len(problems) == 1 and "style.css" in problems[0]


def test_verifier_is_quiet_on_a_complete_project(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="style.css">', encoding="utf-8")
    (tmp_path / "style.css").write_text("body{}", encoding="utf-8")
    monkeypatch.setattr(g, "GENERATED_PROJECT_ROOT", tmp_path)
    assert g.verifier_agent({})["problems"] == []


def test_graph_routes_through_the_verifier_before_finishing():
    nodes = set(g.agent.get_graph().nodes)
    assert "verifier" in nodes
