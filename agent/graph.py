from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, FileUrl
from langgraph.prebuilt import create_react_agent
from .prompts import *
from .states import *
from agent.tools import write_file, read_file, get_current_directory, list_files
from dotenv import load_dotenv


import os
load_dotenv()

groq_model_gpt_20b = ChatGroq(model="openai/gpt-oss-20b", api_key=os.getenv("GROQ_API_KEY"))
groq_model_gpt_120b = ChatGroq(model="openai/gpt-oss-120b", api_key=os.getenv("GROQ_API_KEY"))
groq_model_llama_4_scout_17b = ChatGroq(model="meta-llama/llama-4-scout-17b-16e-instruct", api_key=os.getenv("GROQ_API_KEY"))

user_prompt = "Create a simple calculator web app using html, css, and javascript"


def planner_agent(state: dict) -> dict:
    print("\n ------- ENTERING PLANNER AGENT-------\n")
    user_prompt = state["user_prompt"]
    resp = groq_model_gpt_120b.with_structured_output(Plan).invoke(planner_prompt(user_prompt))
    print(resp)
    return {"plan": resp}

def architect_agent(state: dict) -> dict:
    print("\n ------- ENTERING ARCHITECT AGENT-------\n")
    plan: Plan = state["plan"]
    resp = groq_model_gpt_120b.with_structured_output(TaskPlan, method="function_calling").invoke(architect_prompt(plan))
    if resp is None:
        raise ValueError("No response from Architect")
    print(resp)
    resp.plan = plan

    return {"task_plan": resp}

def coding_agent(state: dict) -> dict :
    print("\n ------- ENTERING CODING AGENT-------\n")
    coder_state: CoderState = state.get("coder_state")
    if coder_state is None:
        coder_state = CoderState(task_plan=state["task_plan"], current_step_idx=0)
    
    steps= coder_state.task_plan.implementation_steps
    if coder_state.current_step_idx >= len(steps):
        return {"coder_state": coder_state, "status": "DONE"}
    
    current_task = steps[coder_state.current_step_idx]
    existing_content = read_file.run(current_task.filepath)

    system_prompt = coder_prompt()
    user_prompt = (
        f"Task: {current_task.task_description}\n"
        f"File: {current_task.filepath}\n"
        f"Existing Content: \n{existing_content}\n"
        "Use write_file(path, content) to save changes"
    )

    coder_tools = [read_file, write_file, list_files, get_current_directory]

    coder_agent = create_react_agent(groq_model_gpt_120b, coder_tools)

    coder_agent.invoke(
    {
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt}
        ]
    },
    {
        "tools": coder_tools 
    }
    )
                        
    coder_state.current_step_idx += 1
    return {'coder_state': coder_state}
    

    
graph = StateGraph(dict)
graph.add_node("planner", planner_agent)
graph.add_node("architect", architect_agent)
graph.add_node("coder", coding_agent)
graph.add_edge("planner", "architect")
graph.add_edge("architect", "coder")

graph.add_conditional_edges("coder",
lambda s: "END" if s.get("status") == "DONE" else "coder", 
{"END": END, "coder": "coder"})


graph.set_entry_point("planner")

agent = graph.compile()

if __name__ == "__main__":
    result = agent.invoke({"user_prompt": "Build a colourful modern todo app in html css and js"},
                          {"recursion_limit": 100})
    print("Final State:", result)
