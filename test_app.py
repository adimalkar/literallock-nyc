"""Offline Streamlit checks: replay and resolver input never access services."""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent


@pytest.fixture
def offline():
    with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden in frontend tests")):
        yield


def page():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=10).run()
    assert not app.exception
    return app


@pytest.mark.parametrize("filename,has_claims", [("subway_exact_run.json", True),
                                                 ("codex_reviewed_absent_run.json", False)])
def test_recorded_runs_render_offline(offline, filename, has_claims):
    app = page()
    app.radio[0].set_value("Replay recorded run").run()
    selector = app.selectbox[0]
    target = ROOT / "data" / filename
    selector.set_value(target).run()
    assert not app.exception
    assert any("Recorded run replayed offline" in element.value for element in app.info)
    assert any("Answer &nbsp;" in element.value for element in app.markdown)
    if not has_claims:
        assert any("LOOKUP_EMPTY" in element.value for element in app.markdown)
    pills = [element.value for element in app.markdown if "class='tag" in element.value]
    if has_claims:
        assert pills and any("CAMIS" in pill or "41367337" in pill for pill in pills)
    else:
        assert not pills, "Absent scope must not fabricate claim evidence pills"


def test_resolver_preselects_but_user_can_override_offline(offline):
    app = page()
    app.text_input[0].set_value("What violations were recorded at Subway on Church Avenue?").run()
    assert not app.exception

    assert app.selectbox[0].value == "41456579"
    assert app.selectbox[1].value == "2026-10-05"
    assert any("Identity resolved from your words" in e.value for e in app.success)
    app.selectbox[0].set_value("41367337").run()
    assert app.selectbox[0].value == "41367337"
    app.text_input[0].set_value("What violations were recorded at Subway?").run()
    assert app.selectbox[0].value == "(none — conceptual question)"
    assert any("Name is ambiguous — identity NOT resolved" in e.value for e in app.warning)
    app.text_input[0].set_value("Convene in Manhattan").run()
    assert app.selectbox[0].value == "(none — conceptual question)"
    assert any("Name is ambiguous — identity NOT resolved" in e.value for e in app.warning)
    assert not app.exception


def test_chat_ambiguity_runs_separate_scopes_concurrently(offline, tmp_path):
    import threading
    from types import SimpleNamespace
    from contracts import RunResult
    barrier = threading.Barrier(2)
    calls = []
    # Borrow genuine recorded results as boundary fixtures. All saves are isolated
    # in pytest's temporary directory and are never presented as new live runs.
    fixtures = {"41367337": ROOT / "data/subway_exact_run.json",
                "41456579": ROOT / "data/live_subway_brooklyn_run.json"}
    def fake_run(query, *args, **kwargs):
        calls.append((query.camis, query.inspection_date_key))
        barrier.wait(timeout=3)
        return RunResult.model_validate_json(fixtures[query.camis].read_text())
    with patch("config.DATA_DIR", tmp_path), patch("config.get_es", return_value=SimpleNamespace(info=lambda: None)), \
         patch("mistralai.client.Mistral", return_value=object()), patch("demo.run", side_effect=fake_run):
        app = AppTest.from_file(str(ROOT / "chat_app.py"), default_timeout=10).run()
        assert not app.exception
        app.button[0].click().run()
        assert not app.exception
        assert sorted(calls) == [("41367337", "2026-10-05"), ("41456579", "2026-10-05")]
        assert any("answered separately, evidence never merged" in e.value for e in app.markdown)
        assert not any("which one?" in e.value for e in app.markdown)
        assert len(app.session_state["chat"][-1]["candidate_results"]) == 2
        assert len(list((tmp_path / "live_runs").glob("chat-*.json"))) == 2
        app.run()
        assert len(calls) == 2, "Rerender must not repeat the candidate runs"


def test_chat_landing_query_prefills_and_waits_for_user(offline):
    app = AppTest.from_file(str(ROOT / "chat_app.py"), default_timeout=10)
    q = "What did inspectors find at the Bronx Subway on June 15, 2025?"
    app.query_params["q"] = q
    app.run()
    assert not app.exception
    assert app.session_state["chat"] == []                       # nothing runs on arrival
    assert app.text_input(key="landing_edit").value == q          # question is pre-filled and editable
    app.run()
    assert app.session_state["chat"] == []                        # still waiting after a rerun
    edited = "What did inspectors find at the Bronx Subway on October 5?"
    app.text_input(key="landing_edit").set_value(edited)
    app.button(key="landing_ask").click().run()
    assert app.session_state["chat"][0]["question"] == edited      # the user's edited question is what runs
    assert not app.session_state["landing_prefill"]


def test_chat_landing_prefill_can_be_dismissed(offline):
    app = AppTest.from_file(str(ROOT / "chat_app.py"), default_timeout=10)
    app.query_params["q"] = "What violations were recorded at Subway?"
    app.run()
    app.button(key="landing_dismiss").click().run()
    assert app.session_state["chat"] == [] and not app.session_state["landing_prefill"]


@pytest.mark.parametrize("filename,has_claims", [("subway_exact_run.json", True),
                                                 ("codex_reviewed_absent_run.json", False)])
def test_chat_replay_offline(offline, filename, has_claims):
    app = AppTest.from_file(str(ROOT / "chat_app.py"), default_timeout=10).run()
    app.radio[0].set_value("Replay").run()
    app.selectbox[0].set_value(ROOT / "data" / filename).run()
    assert not app.exception
    assert any("Recorded run — no retrieval or inference now" in e.value for e in app.info)
    assert len(app.chat_message) == 2
    cards = [e.value for e in app.markdown if "class='citation-card'" in e.value]
    if has_claims:
        assert cards and any("41367337" in card for card in cards)
    else:
        assert not cards
