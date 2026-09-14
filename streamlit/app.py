import io
import os
import sys
import traceback
import zipfile

import streamlit as st

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Must be the first Streamlit call, so it comes before the imports below that
# can fail (agent.graph raises at import time when GROQ_API_KEY is missing).
st.set_page_config(page_title="Coder Buddy", page_icon=":robot:", layout="wide")

try:
    from agent.graph import agent
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()

from agent.tools import GENERATED_PROJECT_ROOT, init_project_root, list_files, read_file

init_project_root()

st.title("Coder Buddy")

user_prompt = st.text_area("Enter your project prompt", placeholder="e.g. 'Build a colourful modern todo app in html css and js'")


if st.button("Generate Project"):
    if not user_prompt.strip():
        st.error("Please enter a project prompt")
    else:
        try:
            # stream() rather than invoke() so each agent reports as it finishes,
            # instead of the user staring at one spinner for several minutes.
            with st.status("Generating project...", expanded=True) as status:
                reported_steps = 0
                for chunk in agent.stream(
                    {"user_prompt": user_prompt},
                    {"recursion_limit": 100},
                    stream_mode="updates",
                ):
                    for node, update in chunk.items():
                        update = update or {}
                        if node == "planner":
                            plan = update.get("plan")
                            st.write(f"**Planned:** {plan.name}" if plan else "**Planned**")
                        elif node == "architect":
                            task_plan = update.get("task_plan")
                            count = len(task_plan.implementation_steps) if task_plan else 0
                            st.write(f"**Architected:** {count} implementation steps")
                        elif node == "coder":
                            coder_state = update.get("coder_state")
                            if coder_state:
                                steps = coder_state.task_plan.implementation_steps
                                # Report every step finished since the last update.
                                while reported_steps < coder_state.current_step_idx:
                                    st.write(f"Wrote `{steps[reported_steps].filepath}`  ({reported_steps + 1}/{len(steps)})")
                                    reported_steps += 1
                status.update(label="Project generated", state="complete")

            st.success("Project generated successfully")
            st.subheader("Generated Project Files")
            files = [f for f in list_files.run(".").splitlines() if f]
            if files:
                for f in files:
                    with st.expander(f):
                        st.code(read_file.run(f))
            else:
                st.info("No files found")

        except Exception as e:
            st.error(f"Error: {e}")
            st.code(traceback.format_exc())

# ---------------------
# Download Project Section
# ---------------------

st.subheader("Download your generated project")

# Built unconditionally. Nesting st.download_button inside `if st.button(...)`
# does not work: clicking the download button triggers a rerun in which the
# outer button is False, so the download button disappears before it fires.
generated_files = (
    [f for f in GENERATED_PROJECT_ROOT.rglob("*") if f.is_file()]
    if GENERATED_PROJECT_ROOT.exists()
    else []
)

if not generated_files:
    st.info("No generated project found. Please generate a project first.")
else:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file_path in generated_files:
            zipf.write(file_path, file_path.relative_to(GENERATED_PROJECT_ROOT))
    zip_buffer.seek(0)

    st.download_button(
        label="📦 Download Project ZIP",
        data=zip_buffer,
        file_name="generated_project.zip",
        mime="application/zip",
    )
