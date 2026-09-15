"""Tests for graph wiring, the coder loop, and the review loop.

The coder test guards the bug where every model failing still advanced
current_step_idx, silently skipping a file and reporting success on a project
that was never fully written.

The review-loop tests drive the compiled graph end to end with stubbed models,
to pin down the loop shape: coder -> reviewer -> coder -> reviewer -> coder ->
finish, never more than two reviews or two fix passes.
"""
import pytest

from agent import graph as g
from agent import tools
from agent import utils
from agent.states import (
    CoderState, File, ImplementationTask, Plan, ReviewIssue, ReviewResult, TaskPlan,
)


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


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throwaway generated_project/, wired into both the graph and the tools."""
    root = tmp_path / "generated_project"
    root.mkdir()
    monkeypatch.setattr(tools, "GENERATED_PROJECT_ROOT", root)
    monkeypatch.setattr(g, "GENERATED_PROJECT_ROOT", root)
    return root


def test_graph_compiles_and_exposes_expected_state_keys():
    assert set(g.AgentState.__annotations__) == {
        "user_prompt", "plan", "task_plan", "coder_state", "status", "problems",
        "review_round", "review_issues", "review_history",
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


def test_output_capped_model_is_not_used_anywhere():
    """qwen/qwen3.6-27b refuses any request expecting over 1,000 output tokens on
    the free tier, which both a stylesheet and a task plan exceed."""
    assert "qwen/qwen3.6-27b" not in g.CODER_MODEL_IDS
    assert "qwen/qwen3.6-27b" not in g.FALLBACK_MODEL_IDS


def test_the_reliable_architect_model_leads_every_chain():
    """gpt-oss-120b is the only model that handled the architect's nested
    function-calling schema reliably, so it must be tried first."""
    assert g.FALLBACK_MODEL_IDS[0] == "openai/gpt-oss-120b"
    assert g.CODER_MODEL_IDS[0] == "openai/gpt-oss-120b"


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


def test_graph_has_reviewer_and_verifier_nodes():
    nodes = set(g.agent.get_graph().nodes)
    assert {"reviewer", "verifier"} <= nodes


# --- shared class contract ---------------------------------------------------
# Making the architect repeat the contract in every task description blew the
# output token budget and failed the whole plan. It is emitted once and injected
# into each coder prompt here instead.

def test_shared_contract_is_injected_into_the_coder_prompt(monkeypatch, no_disk_reads):
    captured = {}

    class Capturing:
        def invoke(self, payload, *a, **k):
            captured["user"] = payload["messages"][1]["content"]
            return {"messages": []}

    monkeypatch.setattr(g, "create_react_agent", lambda model, tools: Capturing())
    tp = _task_plan("index.html")
    tp.shared_contract = ".hidden -> display:none\n.recipe-card > .title"
    g.coding_agent({"task_plan": tp})

    assert ".hidden -> display:none" in captured["user"]
    assert "Shared class/structure contract" in captured["user"]


def test_coder_prompt_omits_the_contract_block_when_empty(monkeypatch, no_disk_reads):
    captured = {}

    class Capturing:
        def invoke(self, payload, *a, **k):
            captured["user"] = payload["messages"][1]["content"]
            return {"messages": []}

    monkeypatch.setattr(g, "create_react_agent", lambda model, tools: Capturing())
    g.coding_agent({"task_plan": _task_plan("index.html")})
    assert "Shared class/structure contract" not in captured["user"]
    assert captured["user"].startswith("Task:")


def test_shared_contract_is_not_exposed_to_the_llm_as_a_hidden_field():
    """It must be a real schema field -- the architect has to fill it in."""
    props = TaskPlan.model_json_schema()["properties"]
    assert "shared_contract" in props
    assert "plan" not in props


# --- review loop: routing ----------------------------------------------------

def test_coder_keeps_going_until_its_pass_is_done():
    assert g.route_after_coder({}) == "coder"
    assert g.route_after_coder({"status": "FIXING"}) == "coder"


def test_a_finished_pass_is_reviewed_until_the_round_limit():
    assert g.route_after_coder({"status": "DONE"}) == "reviewer"
    assert g.route_after_coder({"status": "DONE", "review_round": 1}) == "reviewer"
    assert g.route_after_coder({"status": "DONE", "review_round": g.MAX_REVIEW_ROUNDS}) == "verifier"


def test_review_issues_go_back_to_the_coder_and_a_clean_review_finishes():
    issue = ReviewIssue(file="index.html", problem="p", fix="f")
    assert g.route_after_reviewer({"review_issues": [issue]}) == "coder"
    assert g.route_after_reviewer({"review_issues": []}) == "verifier"


def test_the_loop_limit_is_two_rounds():
    assert g.MAX_REVIEW_ROUNDS == 2


def test_reviewer_models_cap_output_to_fit_the_free_tier():
    assert all(m.max_tokens == g.REVIEW_MAX_OUTPUT_TOKENS for m in g.REVIEWER_MODELS)


# --- review loop: the reviewer node ------------------------------------------

def _script_reviews(monkeypatch, outcomes):
    """Stub the reviewer's model call. Each call pops the next outcome: a
    ReviewResult to return, or an exception to raise. Returns the prompts sent."""
    prompts = []

    def fake_safe_invoke(models, structured_output=None, method=None, prompt=None, retries=2):
        prompts.append(prompt)
        outcome = outcomes.pop(0) if outcomes else ReviewResult()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(g, "safe_invoke", fake_safe_invoke)
    return prompts


def test_reviewer_turns_issues_into_a_fix_pass(project, monkeypatch):
    (project / "index.html").write_text('<script src="app.js" defer></script>', encoding="utf-8")
    (project / "app.js").write_text("storage.getTasks();", encoding="utf-8")
    _script_reviews(monkeypatch, [ReviewResult(issues=[
        ReviewIssue(file="./index.html", problem="storage.js is never loaded", fix="add its script tag"),
    ])])

    out = g.reviewer_agent({"user_prompt": "kanban", "task_plan": _task_plan("index.html", "app.js")})

    assert out["review_round"] == 1
    assert out["status"] == "FIXING"
    assert out["coder_state"].current_step_idx == 0
    fix_steps = out["coder_state"].task_plan.implementation_steps
    assert [s.filepath for s in fix_steps] == ["index.html"]
    assert "add its script tag" in fix_steps[0].task_description


def test_reviewer_with_no_issues_finishes(project, monkeypatch):
    (project / "index.html").write_text("<p>x</p>", encoding="utf-8")
    _script_reviews(monkeypatch, [ReviewResult()])

    out = g.reviewer_agent({"task_plan": _task_plan("index.html")})

    assert out["review_issues"] == []
    assert out["status"] == "REVIEWED"
    assert "coder_state" not in out
    assert g.route_after_reviewer(out) == "verifier"


def test_reviewer_prompt_carries_request_contract_index_findings_and_code(project, monkeypatch):
    (project / "index.html").write_text('<div id="task-modal" class="modal hidden"></div>', encoding="utf-8")
    prompts = _script_reviews(monkeypatch, [ReviewResult()])
    tp = _task_plan("index.html")
    tp.shared_contract = ".hidden -> display:none"

    g.reviewer_agent({"user_prompt": "Build a kanban board", "task_plan": tp})

    prompt = prompts[0]
    assert "Build a kanban board" in prompt
    assert ".hidden -> display:none" in prompt
    assert "===== FILE: index.html =====" in prompt
    assert "#task-modal" in prompt                 # from the project index
    assert "no stylesheet defines" in prompt       # the verifier's finding


def test_reviewer_splits_a_large_project_across_requests(project, monkeypatch):
    """The free tier rejects a request over 8K tokens, so a big project has to be
    reviewed in several requests -- and issues from all of them must survive."""
    for i in range(4):
        (project / f"part{i}.js").write_text(f"window.part{i} = 1;\n" + "x;\n" * 1500, encoding="utf-8")
    prompts = _script_reviews(monkeypatch, [
        ReviewResult(issues=[ReviewIssue(file="part0.js", problem="first", fix="f")]),
        ReviewResult(),
        ReviewResult(),
        ReviewResult(issues=[ReviewIssue(file="part3.js", problem="last", fix="f")]),
    ])

    out = g.reviewer_agent({"task_plan": _task_plan("part0.js")})

    assert len(prompts) == 4
    # Each request carries exactly one file's code. (The instructions mention the
    # header format too, so count real headers rather than the marker text.)
    for prompt in prompts:
        assert sum(f"===== FILE: part{i}.js =====" in prompt for i in range(4)) == 1
    assert {issue.file for issue in out["review_issues"]} == {"part0.js", "part3.js"}


def test_a_failed_review_batch_does_not_fail_the_run(project, monkeypatch):
    """The project is already written when review runs, so a review that cannot
    reach a model must not throw the work away."""
    (project / "a.js").write_text("x;\n" * 2500, encoding="utf-8")
    (project / "b.js").write_text("y;\n" * 2500, encoding="utf-8")
    _script_reviews(monkeypatch, [
        RuntimeError("All fallback models failed"),
        ReviewResult(issues=[ReviewIssue(file="b.js", problem="p", fix="f")]),
    ])

    out = g.reviewer_agent({"task_plan": _task_plan("a.js", "b.js")})

    assert [issue.file for issue in out["review_issues"]] == ["b.js"]
    assert len(out["review_history"][0]["errors"]) == 1


def test_reviewer_drops_issues_pointing_outside_the_project(project, monkeypatch):
    (project / "index.html").write_text("<p>x</p>", encoding="utf-8")
    _script_reviews(monkeypatch, [ReviewResult(issues=[
        ReviewIssue(file="../../outside.js", problem="p", fix="f"),
    ])])

    out = g.reviewer_agent({"task_plan": _task_plan("index.html")})

    assert out["review_issues"] == []


# --- review loop: end to end through the compiled graph ----------------------

@pytest.fixture
def stubbed_run(project, monkeypatch):
    """Run the real compiled graph with every model call stubbed out.

    Returns (stats, review_script): push ReviewResults onto review_script to
    decide what each review returns; stats records reviews and coder prompts.
    """
    stats = {"reviews": 0, "coder_prompts": []}
    review_script = []

    def fake_safe_invoke(models, structured_output=None, method=None, prompt=None, retries=2):
        if structured_output is Plan:
            return Plan(name="t", description="d", features=["f"], technologies=["HTML"],
                        files=[File(path="index.html", purpose="page")])
        if structured_output is TaskPlan:
            return TaskPlan(shared_contract=".hidden -> display:none",
                            implementation_steps=[
                                ImplementationTask(filepath="index.html", task_description="build page")])
        if structured_output is ReviewResult:
            stats["reviews"] += 1
            return review_script.pop(0) if review_script else ReviewResult()
        raise AssertionError(f"unexpected structured output {structured_output}")

    class Coder:
        def invoke(self, payload, *a, **k):
            stats["coder_prompts"].append(payload["messages"][1]["content"])
            (project / "index.html").write_text("<p>page</p>", encoding="utf-8")
            return {"messages": []}

    monkeypatch.setattr(g, "safe_invoke", fake_safe_invoke)
    monkeypatch.setattr(g, "create_react_agent", lambda model, tools: Coder())
    return stats, review_script


def _node_sequence(user_prompt="build it"):
    nodes = [node for chunk in g.agent.stream({"user_prompt": user_prompt},
                                              {"recursion_limit": 60},
                                              stream_mode="updates")
             for node in chunk]
    # Collapse consecutive repeats: the coder runs once per file.
    return [n for i, n in enumerate(nodes) if i == 0 or n != nodes[i - 1]]


def _issue(n):
    return ReviewResult(issues=[ReviewIssue(file="index.html", problem=f"problem {n}", fix=f"fix number {n}")])


def test_loop_stops_after_two_rounds_even_if_issues_remain(stubbed_run):
    stats, script = stubbed_run
    script.extend(_issue(n) for n in range(5))  # a reviewer that is never satisfied

    sequence = _node_sequence()

    assert sequence == ["planner", "architect", "coder", "reviewer", "coder",
                        "reviewer", "coder", "verifier"]
    assert stats["reviews"] == 2
    assert len([p for p in stats["coder_prompts"] if "REVIEW FIX" in p]) == 2


def test_a_clean_first_review_finishes_without_a_fix_pass(stubbed_run):
    stats, script = stubbed_run
    script.append(ReviewResult())

    assert _node_sequence() == ["planner", "architect", "coder", "reviewer", "verifier"]
    assert not any("REVIEW FIX" in p for p in stats["coder_prompts"])


def test_fixes_are_re_reviewed_and_a_clean_second_review_finishes(stubbed_run):
    stats, script = stubbed_run
    script.extend([_issue(1), ReviewResult()])

    assert _node_sequence() == ["planner", "architect", "coder", "reviewer", "coder",
                                "reviewer", "verifier"]
    assert stats["reviews"] == 2
    assert len([p for p in stats["coder_prompts"] if "REVIEW FIX" in p]) == 1


def test_the_fix_pass_receives_the_feedback_and_the_contract(stubbed_run):
    stats, script = stubbed_run
    script.extend([_issue(7), ReviewResult()])

    _node_sequence()

    fix_prompt = next(p for p in stats["coder_prompts"] if "REVIEW FIX" in p)
    assert "problem 7" in fix_prompt and "fix number 7" in fix_prompt
    assert ".hidden -> display:none" in fix_prompt
