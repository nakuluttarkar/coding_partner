from typing import Optional, TypedDict

from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import create_react_agent
from .prompts import *
from .states import *
from agent.tools import (
    write_file, read_file, get_current_directory, list_files, GENERATED_PROJECT_ROOT,
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
#
# For projects whose files outgrow the 131K window, swap the middle entry for
# "minimaxai/minimax-m2.7" (196K context, also preview).
FALLBACK_MODEL_IDS = [
    "openai/gpt-oss-120b",  # production, 131K ctx -- flagship
    "qwen/qwen3.6-27b",     # preview,    131K ctx -- Groq's recommended scout replacement
    "openai/gpt-oss-20b",   # production, 131K ctx -- fastest, guaranteed floor
]

# The coder writes a whole file in one tool call, so it needs output headroom.
# qwen/qwen3.6-27b is excluded: its free-tier output cap is 1,000 tokens/min,
# which a stylesheet exceeds on its own (observed: "OTPM Limit 1000, Requested
# 1064"). It stays in the chain above, where responses are far smaller.
CODER_MODEL_IDS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]

# Injecting a large existing file doubles input tokens against an 8K/min budget.
MAX_EXISTING_CONTENT_CHARS = 2000


def _build(model_ids):
    return [ChatGroq(model=m, api_key=os.getenv("GROQ_API_KEY")) for m in model_ids]


FALLBACK_MODELS = _build(FALLBACK_MODEL_IDS)
CODER_MODELS = _build(CODER_MODEL_IDS)


class AgentState(TypedDict, total=False):
    """State passed between graph nodes. total=False since each node
    contributes only its own keys."""
    user_prompt: str
    plan: Plan
    task_plan: TaskPlan
    coder_state: Optional[CoderState]
    status: str
    problems: list


def planner_agent(state: AgentState) -> dict:
    console("\n ------- ENTERING PLANNER AGENT-------\n")
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

    system_prompt = coder_prompt()
    user_prompt = (
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


def verifier_agent(state: AgentState) -> dict:
    """Deterministic post-generation checks -- no model call, no tokens spent."""
    console("\n ------- ENTERING VERIFIER-------\n")
    problems = find_problems(GENERATED_PROJECT_ROOT)
    for problem in problems:
        console(f"[VERIFY] {problem}")
    if not problems:
        console("[VERIFY] All referenced assets exist.")
    return {"problems": problems}


graph = StateGraph(AgentState)
graph.add_node("planner", planner_agent)
graph.add_node("architect", architect_agent)
graph.add_node("coder", coding_agent)
graph.add_node("verifier", verifier_agent)
graph.add_edge("planner", "architect")
graph.add_edge("architect", "coder")

graph.add_conditional_edges("coder",
lambda s: "verifier" if s.get("status") == "DONE" else "coder", 
{"verifier": "verifier", "coder": "coder"})

graph.add_edge("verifier", END)

graph.set_entry_point("planner")

agent = graph.compile()

if __name__ == "__main__":
    result = agent.invoke({"user_prompt": "Build a colourful modern todo app in html css and js"},
                          {"recursion_limit": 100})
    console(f"Final State: {result}")
