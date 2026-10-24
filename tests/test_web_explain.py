"""API «Объясни новичку» (срез 88)."""

import pytest

from course_fixtures import enter_module, use_lessons_course
from llm_tutor.db import repos
from llm_tutor.llm.schemas import ExplainObservation, IdeaVerdict
from llm_tutor.student import survey
from llm_tutor.web import accounts
from web_fakes import ExplainLLM

BASE = "/api/courses/mlcourse/explain"


@pytest.fixture
def web_llm():
    return ExplainLLM(replies=["А зачем это?"])


def _user_db(web_app):
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    return web_app.state.userdbs.open(user.id, "mlcourse")


@pytest.fixture
async def enrolled(monkeypatch, student):
    use_lessons_course(monkeypatch)
    response = await student.post("/api/courses/mlcourse/enroll", json={})
    assert response.status_code == 200
    return student


@pytest.fixture
async def passed(enrolled, web_app):
    """Ученик записан, модуль 1 пройден."""
    enter_module(_user_db(web_app), 2)
    return enrolled


async def _start(client, topic: int = 1) -> dict:
    response = await client.post(f"{BASE}/start", json={"topic": topic})
    assert response.status_code == 200, response.text
    return response.json()["session"]


async def _turn(client, session_id: int, text: str = "Series — это столбец"):
    return await client.post(f"{BASE}/turn", json={"session_id": session_id, "text": text})


async def test_overview_lists_modules(passed) -> None:
    data = (await passed.get(BASE)).json()
    modules = {m["topic"]: m for m in data["modules"]}
    assert (modules[1]["available"], modules[1]["reason"]) == (True, "ok")
    assert (modules[2]["available"], modules[2]["reason"]) == (False, "not_passed")
    assert data["active"] is None
    assert data["history"] == []
    assert data["share"] is False


async def test_flow_start_turn_finish(passed, web_app) -> None:
    session = await _start(passed)
    assert session["mode"] == "novice"
    assert [m["role"] for m in session["messages"]] == ["novice"]
    response = await _turn(passed, session["id"])
    assert response.status_code == 200, response.text
    data = response.json()
    assert [m["role"] for m in data["messages"]] == ["student", "novice"]
    assert "А зачем это?" in data["messages"][1]["html"]
    assert data["session"]["progress"] == {"done": 0, "total": 3}
    finished = await passed.post(f"{BASE}/finish", json={"session_id": session["id"]})
    assert finished.status_code == 200, finished.text
    report = finished.json()["report"]
    assert report["required_total"] == 3
    view = (await passed.get(f"{BASE}/sessions/{session['id']}")).json()["session"]
    assert view["status"] == "finished"
    assert view["report"] == report
    assert len(view["messages"]) == 3
    history = (await passed.get(BASE)).json()["history"]
    assert [h["id"] for h in history] == [session["id"]]


async def test_active_in_overview(passed) -> None:
    session = await _start(passed)
    assert (await passed.get(BASE)).json()["active"]["id"] == session["id"]


async def test_not_enrolled_403(student, monkeypatch) -> None:
    use_lessons_course(monkeypatch)
    assert (await student.get(BASE)).status_code == 403
    assert (await student.post(f"{BASE}/start", json={"topic": 1})).status_code == 403


async def test_guest_401(web, monkeypatch) -> None:
    use_lessons_course(monkeypatch)
    assert (await web.get(BASE)).status_code == 401


async def test_not_passed_409(enrolled) -> None:
    response = await enrolled.post(f"{BASE}/start", json={"topic": 1})
    assert response.status_code == 409


async def test_other_active_409(passed, web_app) -> None:
    conn = _user_db(web_app)
    enter_module(conn, 3)  # модули 1 и 2 пройдены
    await _start(passed, 1)
    assert (await passed.post(f"{BASE}/start", json={"topic": 2})).status_code == 409


async def test_whitespace_text_422(passed, web_app) -> None:
    session = await _start(passed)
    response = await _turn(passed, session["id"], "   \n ")
    assert response.status_code == 422
    assert web_app.state.client.order == []


async def test_too_long_text_422(passed) -> None:
    session = await _start(passed)
    assert (await _turn(passed, session["id"], "а" * 8001)).status_code == 422


async def test_turn_after_finish_409(passed, web_app) -> None:
    session = await _start(passed)
    await _turn(passed, session["id"])
    await passed.post(f"{BASE}/finish", json={"session_id": session["id"]})
    before = _user_db(web_app).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert (await _turn(passed, session["id"])).status_code == 409
    after = _user_db(web_app).execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert before == after


async def test_unknown_session_404(passed) -> None:
    assert (await passed.get(f"{BASE}/sessions/999")).status_code == 404
    assert (await _turn(passed, 999)).status_code == 404
    assert (await passed.post(f"{BASE}/finish", json={"session_id": 999})).status_code == 404


async def test_llm_failure_502(passed, web_app) -> None:
    session = await _start(passed)
    web_app.state.client.fail = {"novice"}
    response = await _turn(passed, session["id"])
    assert response.status_code == 502
    assert len(repos.get_explain_turns(_user_db(web_app), session["id"])) == 1


async def test_mentor_switch(passed) -> None:
    session = await _start(passed)
    response = await passed.post(f"{BASE}/mentor", json={"session_id": session["id"], "on": True})
    assert response.status_code == 200
    assert response.json()["session"]["mode"] == "mentor"
    data = (await _turn(passed, session["id"], "Помоги")).json()
    assert data["messages"][1]["role"] == "mentor"


async def test_progress_hides_idea_names(passed, web_app) -> None:
    config = repos.get_topic(_user_db(web_app), 1).explain
    web_app.state.client.observations = [
        ExplainObservation(
            ideas=[IdeaVerdict(id="series_vs_frame", verdict="correct", quote="Series — это столбец")]
        )
    ]
    session = await _start(passed)
    response = await _turn(passed, session["id"])
    body = response.text
    assert response.json()["session"]["progress"] == {"done": 1, "total": 3}
    for idea in config.ideas:
        assert idea.id not in body
        assert idea.text not in body


async def test_settings_share(passed, web_app) -> None:
    response = await passed.post(f"{BASE}/settings", json={"share": True})
    assert response.json() == {"share": True}
    assert repos.get_fact(_user_db(web_app), "explain_share") == "1"
    assert (await passed.get(BASE)).json()["share"] is True
    await passed.post(f"{BASE}/settings", json={"share": False})
    assert repos.get_fact(_user_db(web_app), "explain_share") == "0"


async def test_bodyless_post_requires_json(passed) -> None:
    session = await _start(passed)
    response = await passed.post(f"{BASE}/finish", content=f'{{"session_id": {session["id"]}}}')
    assert response.status_code == 415


async def _finish_surveys(client, web_app) -> dict:
    """Проходит висящие анкеты модулей: без этого состояние занятия — анкета."""
    for _ in range(3):
        state = (await client.get("/api/courses/mlcourse/lesson/state")).json()["state"]
        if not state["survey"]:
            return state
        number = state["survey_topic"]["number"]
        config = repos.get_topic(_user_db(web_app), number).survey
        progress, answers = survey.Progress(config), []
        while progress.next_key() is not None:
            progress, answers = progress.answer(0), [*answers, 0]
        response = await client.post(
            "/api/courses/mlcourse/lesson/survey/finish", json={"answers": answers}
        )
        assert response.status_code == 200, response.text
    raise AssertionError("анкеты не кончаются")


async def test_lesson_state_module_explain_flag(passed, web_app) -> None:
    state = await _finish_surveys(passed, web_app)
    flags = {m["number"]: m["explain"] for m in state["modules"]}
    assert flags == {1: True, 2: False}
