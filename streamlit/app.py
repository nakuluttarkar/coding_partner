from mimetypes import init
import streamlit as st
import subprocess
import os
import sys
import traceback
import io
import zipfile

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.graph import agent, planner_agent, architect_agent, coding_agent
from agent.tools import PROJECT_ROOT, init_project_root, list_files, read_file, write_file, get_current_directory, run_cmd

init_project_root()

st.set_page_config(page_title="Coder Buddy", page_icon=":robot:", layout="wide")
st.title("Coder Buddy")

user_prompt = st.text_area("Enter your project prompt", placeholder="e.g. 'Build a colourful modern todo app in html css and js'")

recursion_limit = st.number_input("Enter the recursion limit", value=100, min_value=1, max_value=1000)

if st.button("Generate Project"):
    if not user_prompt.strip():
        st.error("Please enter a project prompt")
    else:
    
        try:
            with st.spinner("Generating project..."):
                result = agent.invoke({"user_prompt": user_prompt}, {"recursion_limit": recursion_limit})
            st.success("Project generated successfully")
            st.write("Final State:", result)
            st.subheader("Generated Project Files")
            files = list_files.run(".").splitlines()
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

if st.button("Download Project"):
    generated_project_path = os.path.join(PROJECT_ROOT, "generated_project")
    
    if not os.path.exists(generated_project_path):
        st.error("No generated project found. Please generate a project first.")
    else:
        # Create in-memory ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(generated_project_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    zipf.write(file_path, os.path.relpath(file_path, generated_project_path))
        zip_buffer.seek(0)

        st.download_button(
            label="📦 Download Project ZIP",
            data=zip_buffer,
            file_name="generated_project.zip",
            mime="application/zip"
        )