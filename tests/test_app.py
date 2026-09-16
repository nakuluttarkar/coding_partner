"""Tests for the Streamlit app, run headless with Streamlit's AppTest.

Guards a production bug: generated_project/ lives on disk and outlives every
browser session, and the app showed the preview, file list and download buttons
whenever that directory had files. A freshly opened app therefore offered the
previous project for download -- on a shared deployment, someone else's.

No model is called: the compiled graph is replaced by a stub that writes a small
project, and the project directory is a temporary one.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agent import graph as graph_module
from agent import tools
from agent.states import File, Plan

APP = str(Path(__file__).resolve().parents[1] / "streamlit" / "app.py")
ZIP_LABEL = "📦 Download Project ZIP"


class FakeAgent:
    """Writes a two-file project the way a run would, then reports success --
    or raises part-way through, if told to fail."""

    def __init__(self, root, fail=False):
        self.root = root
        self.fail = fail

    def stream(self, inputs, config=None, stream_mode=None):
        # A real run clears the directory before writing anything new.
        tools.clear_project_root()
        (self.root / "index.html").write_text(
            '<link rel="stylesheet" href="style.css"><p>my project</p>', encoding="utf-8")
        (self.root / "style.css").write_text("p { color: red; }", encoding="utf-8")
        yield {"planner": {"plan": Plan(name="Todo", description="d", features=["f"],
                                        technologies=["HTML"],
                                        files=[File(path="index.html", purpose="page")])}}
        if self.fail:
            raise RuntimeError("model unavailable")
        yield {"verifier": {"problems": []}}


@pytest.fixture
def root(tmp_path, monkeypatch):
    project = tmp_path / "generated_project"
    project.mkdir()
    monkeypatch.setattr(tools, "GENERATED_PROJECT_ROOT", project)
    return project


@pytest.fixture
def fake_agent(root, monkeypatch):
    agent = FakeAgent(root)
    monkeypatch.setattr(graph_module, "agent", agent)
    return agent


def _download_labels(at):
    return [element.proto.label for element in at.get("download_button")]


def _subheaders(at):
    return [element.value for element in at.subheader]


def _generate(at, prompt="Build a todo app"):
    at.text_area[0].input(prompt).run()
    at.button[0].click().run()
    return at


def _open_app():
    return AppTest.from_file(APP, default_timeout=60).run()


def test_a_freshly_opened_app_does_not_offer_a_previous_project(root, fake_agent):
    (root / "index.html").write_text("<p>last visitor's project</p>", encoding="utf-8")
    (root / "app.js").write_text("secret();", encoding="utf-8")

    at = _open_app()

    assert not at.exception
    assert _download_labels(at) == [], "no download for a project this session did not make"
    assert "Preview" not in _subheaders(at)
    assert "Generated Project Files" not in _subheaders(at)


def test_a_project_generated_in_this_session_is_offered(root, fake_agent):
    at = _generate(_open_app())

    assert not at.exception
    assert ZIP_LABEL in _download_labels(at)
    assert "Preview" in _subheaders(at)
    assert "Generated Project Files" in _subheaders(at)


def test_the_download_survives_the_rerun_that_clicking_it_triggers(root, fake_agent):
    at = _generate(_open_app())
    at.run()   # a download click reruns the script with the Generate button no longer pressed
    assert ZIP_LABEL in _download_labels(at)


def test_a_second_session_does_not_see_the_first_sessions_project(root, fake_agent):
    _generate(_open_app())

    other_visitor = _open_app()

    assert _download_labels(other_visitor) == []
    assert "Preview" not in _subheaders(other_visitor)


def test_a_project_replaced_by_another_run_is_no_longer_offered(root, fake_agent):
    at = _generate(_open_app())
    (root / "index.html").write_text("<p>a different run's much longer project</p>", encoding="utf-8")

    at.run()

    assert _download_labels(at) == []
    assert "Preview" not in _subheaders(at)
    assert any("replaced" in message.value for message in at.info)


def test_a_failed_generation_offers_nothing(root, monkeypatch):
    monkeypatch.setattr(graph_module, "agent", FakeAgent(root, fail=True))

    at = _generate(_open_app())

    assert any("model unavailable" in error.value for error in at.error)
    assert _download_labels(at) == [], "a half-written project must not be offered"
    assert "Preview" not in _subheaders(at)
