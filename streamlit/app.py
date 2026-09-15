import io
import os
import sys
import traceback
import zipfile

import streamlit as st
import streamlit.components.v1 as components

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Must be the first Streamlit call, so it comes before the imports below that
# can fail (agent.graph raises at import time when GROQ_API_KEY is missing).
st.set_page_config(page_title="Coder Buddy", page_icon=":robot:", layout="wide")

try:
    from agent.graph import agent
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()

from agent.bundle import find_entry_html, inline_html
from agent.tools import (
    GENERATED_PROJECT_ROOT, init_project_root, list_files, project_fingerprint, read_file,
)

init_project_root()

st.title("Coder Buddy")

user_prompt = st.text_area("Enter your project prompt", placeholder="e.g. 'Build a colourful modern todo app in html css and js'")


if st.button("Generate Project"):
    if not user_prompt.strip():
        st.error("Please enter a project prompt")
    else:
        # Forget any earlier project first, so a run that fails part-way never
        # leaves this session offering a half-written directory.
        st.session_state.pop("project_fingerprint", None)
        try:
            # stream() rather than invoke() so each agent reports as it finishes,
            # instead of the user staring at one spinner for several minutes.
            with st.status("Generating project...", expanded=True) as status:
                reported_steps = 0
                fixing = False
                fix_round = None
                failed_fixes = set()
                problems = []
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
                            for failure in update.get("failed_fixes") or []:
                                failed_fixes.add((failure.get("round"), failure.get("file")))
                            if coder_state:
                                steps = coder_state.task_plan.implementation_steps
                                verb = "Fixed" if fixing else "Wrote"
                                # Report every step finished since the last update.
                                while reported_steps < coder_state.current_step_idx:
                                    path = steps[reported_steps].filepath
                                    counter = f"({reported_steps + 1}/{len(steps)})"
                                    if fixing and (fix_round, path) in failed_fixes:
                                        st.write(f"Could not fix `{path}`; kept the previous version  {counter}")
                                    else:
                                        st.write(f"{verb} `{path}`  {counter}")
                                    reported_steps += 1
                        elif node == "reviewer":
                            issues = update.get("review_issues") or []
                            round_no = update.get("review_round")
                            history = update.get("review_history") or []
                            failed = history[-1]["errors"] if history else []
                            if issues:
                                st.write(f"**Review round {round_no}:** {len(issues)} issue(s) sent back to the coder")
                                for issue in issues:
                                    st.write(f"- `{issue.file}`: {issue.problem}")
                                # A fix pass is a fresh plan whose steps count from zero.
                                reported_steps = 0
                                fixing = True
                                fix_round = round_no
                            else:
                                st.write(f"**Review round {round_no}:** no blocking issues")
                            if failed:
                                st.write(f"Review incomplete: {len(failed)} batch(es) could not be reviewed")
                        elif node == "verifier":
                            problems = update.get("problems") or []
                            st.write(f"**Verified:** {len(problems)} problem(s) found"
                                     if problems else "**Verified:** all referenced assets exist")
                status.update(label="Project generated", state="complete")

            # Record exactly which files this session produced.
            st.session_state["project_fingerprint"] = project_fingerprint()

            if problems:
                st.warning("Verification found issues:\n\n"
                           + "\n".join(f"- {p}" for p in problems))
            else:
                st.success("Project generated successfully")

        except Exception as e:
            st.error(f"Error: {e}")
            st.code(traceback.format_exc())


# generated_project/ is shared on disk and outlives every browser session, so
# only show a project this session generated, and only while the files on disk
# are still the ones it generated. Without this check a freshly opened app
# offered the previous visitor's project -- preview, files and download.
session_fingerprint = st.session_state.get("project_fingerprint")
show_project = session_fingerprint is not None and session_fingerprint == project_fingerprint()


# ---------------------
# Preview
# ---------------------
# Rendered from the inlined single file, so the preview shows the project as a
# self-contained page -- the same thing the standalone download produces.

entry = find_entry_html(GENERATED_PROJECT_ROOT) if show_project else None
if entry is not None:
    st.subheader("Preview")
    try:
        components.html(inline_html(entry), height=600, scrolling=True)
    except Exception as e:  # a broken generated page must not take down the app
        st.warning(f"Could not render preview: {e}")


# ---------------------
# Generated files
# ---------------------

generated_files = (
    sorted(f for f in GENERATED_PROJECT_ROOT.rglob("*") if f.is_file())
    if show_project and GENERATED_PROJECT_ROOT.exists()
    else []
)

if generated_files:
    st.subheader("Generated Project Files")
    for f in [p for p in list_files.run(".").splitlines() if p]:
        with st.expander(f):
            st.code(read_file.run(f))


# ---------------------
# Download Project Section
# ---------------------

st.subheader("Download your generated project")

# Built unconditionally. Nesting a download button inside `if st.button(...)`
# does not work: clicking the download button triggers a rerun in which the
# outer button is False, so the download button disappears before it fires.
if not generated_files:
    if session_fingerprint is not None:
        st.info("The project you generated here has since been replaced by a newer run. "
                "Generate again to get a fresh copy.")
    else:
        st.info("No project generated in this session yet. Enter a prompt above and "
                "click Generate Project.")
else:
    col_zip, col_single = st.columns(2)

    with col_zip:
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
        st.caption(
            "Extract the ZIP fully before opening `index.html`. Opening it directly "
            "from inside the archive loads the page without its CSS or JavaScript, "
            "because the other files were never extracted."
        )

    with col_single:
        if entry is not None:
            try:
                st.download_button(
                    label="📄 Download standalone HTML",
                    data=inline_html(entry),
                    file_name="index.html",
                    mime="text/html",
                )
                other_pages = [p for p in generated_files
                               if p.suffix.lower() == ".html" and p != entry]
                if other_pages:
                    # Links to sibling pages cannot be inlined, so say so rather
                    # than hand over a file whose navigation quietly 404s.
                    st.caption(
                        f"Entry page only, with CSS and JavaScript inlined. This project "
                        f"has {len(other_pages)} other page(s) "
                        f"({', '.join(p.name for p in other_pages)}); links to them will "
                        f"not work in the standalone file. Use the ZIP for the full app."
                    )
                else:
                    st.caption(
                        "One self-contained file with the CSS and JavaScript inlined. "
                        "Works anywhere, with nothing to extract."
                    )
            except Exception as e:
                st.caption(f"Standalone build unavailable: {e}")
