from typing import Optional, TypedDict

from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import create_react_agent
from .prompts import *
from .states import *
from agent.tools import (
    write_file, read_file, get_current_directory, list_files,
    GENERATED_PROJECT_ROOT, clear_project_root, safe_path_for_project,
)
from agent.review import (
    batch_files, build_digest, build_fix_plan, clean_issues, collect_review_files,
    format_files_block,
)
from agent.verify import find_problems
from dotenv import load_dotenv
from .utils import safe_invoke, invoke_with_fallback, console


import os
load_dotenv()

if not os.getenv("GROQ_API_KEY"):
    raise RuntimeError(
        "GROQ_API_KEY is not set. Copy .env.example to .env and add a key from "
        "https://console.groq.com/keys -- the Groq client is built at import "
        "time, so this module cannot load without one."
    )

# Groq fallback chain, best-first. Production models sit at the head AND tail so
# the chain still works if the preview model in the middle is retired -- which is
# exactly what happened to meta-llama/llama-4-scout-17b-16e-instruct, shut down
# on 2026-07-17. Check https://console.groq.com/docs/deprecations before editing.
FALLBACK_MODEL_IDS = [
    "openai/gpt-oss-120b",  # 131K ctx, 64K max out -- the only reliable architect
    "qwen/qwen3.8-27b",     # 131K ctx, 16K max out -- no output cap, unlike 3.6
    "openai/gpt-oss-20b",   # 131K ctx, 64K max out -- fastest, guaranteed floor
]

# qwen/qwen3.6-27b was removed from both chains: its free-tier output cap is
# 1,000 tokens/min, so it refused any request expecting more (observed "OTPM
# Limit 1000, Requested 1064" on a stylesheet and "Requested 2048" on a plan).
# qwen/qwen3.8-27b has no such cap -- measured by asking each for max_tokens=2048,
# which 3.6 rejects and 3.8 accepts -- so it replaces 3.6 and can also code.
CODER_MODEL_IDS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
]

# Injecting a large existing file doubles input tokens against an 8K/min budget.
MAX_EXISTING_CONTENT_CHARS = 2000

# Review loop: coder -> reviewer -> coder (fixes) -> reviewer -> coder -> done.
# At most MAX_REVIEW_ROUNDS reviews and the same number of fix passes; the last
# fix pass is not reviewed again, so a run always terminates.
MAX_REVIEW_ROUNDS = 2
MAX_ISSUES_PER_ROUND = 8

# Sized for the free tier's 8,000 tokens per minute. Groq counts a request's
# input plus its output allowance against that limit and rejects a request that
# exceeds it outright, so each review batch plus REVIEW_MAX_OUTPUT_TOKENS has to
# stay under it. Roughly 6K characters of code is ~2K tokens; with the prompt,
# project index and findings a batch lands near 4K tokens of input.
REVIEW_BATCH_CHARS = 6000
REVIEW_DIGEST_CHARS = 2000
REVIEW_MAX_OUTPUT_TOKENS = 3000


def _build(model_ids, **kwargs):
    return [ChatGroq(model=m, api_key=os.getenv("GROQ_API_KEY"), **kwargs) for m in model_ids]


FALLBACK_MODELS = _build(FALLBACK_MODEL_IDS)
CODER_MODELS = _build(CODER_MODEL_IDS)
REVIEWER_MODELS = _build(FALLBACK_MODEL_IDS, max_tokens=REVIEW_MAX_OUTPUT_TOKENS)


class AgentState(TypedDict, total=False):
    """State passed between graph nodes. total=False since each node
    contributes only its own keys."""
    user_prompt: str
    plan: Plan
    task_plan: TaskPlan
    coder_state: Optional[CoderState]
    status: str
    problems: list
    review_round: int
    review_issues: list
    review_history: list


def planner_agent(state: AgentState) -> dict:
    console("\n ------- ENTERING PLANNER AGENT-------\n")
    # Clear here rather than in a caller: the planner runs exactly once per run,
    # so every entry point (CLI and UI alike) starts from an empty directory.
    # Without this a run only overwrites colliding filenames and ships the
    # previous project's leftovers inside this project's download.
    clear_project_root()
    user_prompt = state["user_prompt"]
    resp = safe_invoke(
        FALLBACK_MODELS,
        structured_output=Plan,
        prompt=planner_prompt(user_prompt)
    )
    console(resp)
    return {"plan": resp}

def architect_agent(state: AgentState) -> dict:
    console("\n ------- ENTERING ARCHITECT AGENT-------\n")
    plan: Plan = state["plan"]
    resp = safe_invoke(
        FALLBACK_MODELS,
        structured_output=TaskPlan,
        method="function_calling",
        prompt=architect_prompt(plan)
    )
    if resp is None:
        raise ValueError("No response from Architect")
    console(resp)
    resp.plan = plan

    return {"task_plan": resp}

def coding_agent(state: AgentState) -> dict:
    console("\n ------- ENTERING CODING AGENT-------\n")
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        coder_state = CoderState(task_plan=state["task_plan"], current_step_idx=0)

    steps= coder_state.task_plan.implementation_steps
    if coder_state.current_step_idx >= len(steps):
        return {"coder_state": coder_state, "status": "DONE"}

    current_task = steps[coder_state.current_step_idx]
    existing_content = read_file.run(current_task.filepath)
    if len(existing_content) > MAX_EXISTING_CONTENT_CHARS:
        existing_content = existing_content[:MAX_EXISTING_CONTENT_CHARS] + "\n... (truncated)"

    # Injected here rather than repeated by the architect in every task: making
    # the model duplicate it across all descriptions blew the output token
    # budget and failed the whole plan. Emitted once, delivered everywhere.
    contract = getattr(coder_state.task_plan, "shared_contract", "") or ""
    contract_block = (
        f"Shared class/structure contract for the whole project -- use these exact "
        f"names:\n{contract}\n\n" if contract.strip() else ""
    )

    system_prompt = coder_prompt()
    user_prompt = (
        f"{contract_block}"
        f"Task: {current_task.task_description}\n"
        f"File: {current_task.filepath}\n"
        f"Existing Content: \n{existing_content}\n"
        "Use write_file(path, content) to save changes"
    )

    coder_tools = [read_file, write_file, list_files, get_current_directory]

    def run_coder(model):
        return create_react_agent(model, coder_tools).invoke(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            }
        )

    try:
        # Retries rate-limited models in place: Groq's per-minute caps clear on
        # their own, so giving up on the first 413 wastes a run that would have
        # succeeded a minute later.
        invoke_with_fallback(CODER_MODELS, run_coder, label="Coding with")
    except RuntimeError as exc:
        # Advancing here would silently skip the file and report success on a
        # project that was never fully written.
        raise RuntimeError(
            f"Step {coder_state.current_step_idx + 1}/{len(steps)} "
            f"({current_task.filepath}) failed. {exc}"
        ) from exc

    coder_state.current_step_idx += 1
    return {"coder_state": coder_state}


def _is_safe_path(path: str) -> bool:
    try:
        safe_path_for_project(path)
        return True
    except ValueError:
        return False


def reviewer_agent(state: AgentState) -> dict:
    """Review the written project and hand concrete fixes back to the coder.

    The coder writes one file per step and never sees the project as a whole,
    so cross-file defects -- a page not loading a script it depends on, a
    script looking up an id the HTML lacks -- are invisible to it. The reviewer
    reads every file (in batches, to fit the free tier's per-minute token
    limit) plus a project index, and returns issues as coder tasks.
    """
    review_round = state.get("review_round", 0) + 1
    console(f"\n ------- ENTERING REVIEWER (round {review_round}/{MAX_REVIEW_ROUNDS})-------\n")

    root = GENERATED_PROJECT_ROOT
    task_plan: TaskPlan = state["task_plan"]
    findings = find_problems(root)
    files = collect_review_files(root)
    digest = build_digest(root, REVIEW_DIGEST_CHARS)
    batches = batch_files(files, REVIEW_BATCH_CHARS)

    raw_issues, errors = [], []
    for n, batch in enumerate(batches, 1):
        names = ", ".join(rel for rel, _ in batch)
        console(f"[REVIEW] batch {n}/{len(batches)}: {names}")
        prompt = reviewer_prompt(
            state.get("user_prompt", ""),
            task_plan.shared_contract,
            digest,
            findings,
            format_files_block(batch),
            MAX_ISSUES_PER_ROUND,
        )
        try:
            result = safe_invoke(
                REVIEWER_MODELS,
                structured_output=ReviewResult,
                method="function_calling",
                prompt=prompt,
            )
        except RuntimeError as exc:
            # Unlike a failed coder step, a failed review loses nothing: the
            # project is already on disk. Record the gap and keep the issues the
            # other batches found, rather than failing the whole run.
            errors.append(f"batch {n} ({names}): {exc}")
            console(f"[REVIEW] batch {n} failed, continuing without it")
            continue
        raw_issues.extend(result.issues if result else [])

    issues = clean_issues(raw_issues, [rel for rel, _ in files], MAX_ISSUES_PER_ROUND, _is_safe_path)
    for issue in issues:
        console(f"[REVIEW] {issue.file}: {issue.problem}")
    if not issues:
        console("[REVIEW] No blocking issues." if not errors
                else "[REVIEW] No issues from the batches that completed.")

    history = list(state.get("review_history") or [])
    history.append({
        "round": review_round,
        "batches": len(batches),
        "issues": len(issues),
        "files": sorted({issue.file for issue in issues}),
        "errors": errors,
    })
    update = {"review_round": review_round, "review_issues": issues, "review_history": history}

    if issues:
        fix_plan = build_fix_plan(issues, task_plan, review_round, root)
        update["coder_state"] = CoderState(task_plan=fix_plan, current_step_idx=0)
        update["status"] = "FIXING"
    else:
        update["status"] = "REVIEWED"
    return update


def verifier_agent(state: AgentState) -> dict:
    """Deterministic post-generation checks -- no model call, no tokens spent."""
    console("\n ------- ENTERING VERIFIER-------\n")
    problems = find_problems(GENERATED_PROJECT_ROOT)
    for problem in problems:
        console(f"[VERIFY] {problem}")
    if not problems:
        console("[VERIFY] All referenced assets exist.")
    return {"problems": problems}


def route_after_coder(state: AgentState) -> str:
    if state.get("status") != "DONE":
        return "coder"
    # Each finished pass is reviewed until the round limit is spent. After the
    # final fix pass the review_round equals the limit, so it goes straight on.
    if state.get("review_round", 0) < MAX_REVIEW_ROUNDS:
        return "reviewer"
    return "verifier"


def route_after_reviewer(state: AgentState) -> str:
    return "coder" if state.get("review_issues") else "verifier"


graph = StateGraph(AgentState)
graph.add_node("planner", planner_agent)
graph.add_node("architect", architect_agent)
graph.add_node("coder", coding_agent)
graph.add_node("reviewer", reviewer_agent)
graph.add_node("verifier", verifier_agent)
graph.add_edge("planner", "architect")
graph.add_edge("architect", "coder")

graph.add_conditional_edges(
    "coder",
    route_after_coder,
    {"coder": "coder", "reviewer": "reviewer", "verifier": "verifier"},
)
graph.add_conditional_edges(
    "reviewer",
    route_after_reviewer,
    {"coder": "coder", "verifier": "verifier"},
)

graph.add_edge("verifier", END)

graph.set_entry_point("planner")

agent = graph.compile()

if __name__ == "__main__":
    result = agent.invoke({"user_prompt": "Build a colourful modern todo app in html css and js"},
                          {"recursion_limit": 100})
    console(f"Final State: {result}")
