"""Сценарии бота, которые веб обязан повторять (Срез 49).

Перед удалением Telegram каждый сценарий тестов бота сверен с тестами веба;
здесь — то, что было покрыто только через бота.
"""

import time

import pytest
from web_fakes import ScriptedTutor

from llm_tutor.core import lesson, turn, verify
from llm_tutor.course.graph import CourseGraph
from llm_tutor.db import repos
from llm_tutor.llm.prompts import FAILURE_REPLY
from llm_tutor.student import survey
from llm_tutor.web import accounts, views

COURSE = "mlcourse-topic01"
BASE = f"/api/courses/{COURSE}/lesson"


@pytest.fixture
def web_llm():
    return ScriptedTutor("Объясняю тему.")


@pytest.fixture
async def enrolled(student):
    assert (await student.post(f"/api/courses/{COURSE}/enroll", json={})).status_code == 200
    return student


def _answers(choice: int) -> list[int]:
    progress, given = survey.Progress(), []
    while (key := progress.next_key()) is not None:
        index = min(choice, len(survey.options_for(key)) - 1)
        progress, given = progress.answer(index), [*given, index]
    return given


def _db(web_app):
    user = accounts.find_user(web_app.state.accounts, "ann@example.com")
    return web_app.state.userdbs.open(user.id, COURSE)


async def _finish(client, choice: int = 1) -> dict:
    response = await client.post(f"{BASE}/survey/finish", json={"answers": _answers(choice)})
    assert response.status_code == 200, response.text
    return response.json()


def _broken(*args, **kwargs):
    raise RuntimeError("ядро упало")


async def _broken_async(*args, **kwargs):
    raise RuntimeError("ядро упало")


# --- сбой ядра — реплика, а не тишина или 500 ---


async def test_lesson_failure_after_survey_is_reported(enrolled, web_app, monkeypatch) -> None:
    monkeypatch.setattr(lesson, "begin_lesson", _broken_async)

    body = await _finish(enrolled)

    assert survey.is_completed(_db(web_app))  # анкета записана, урок продолжится ходом
    assert FAILURE_REPLY.split(".")[0] in body["messages"][-1]["html"]


@pytest.mark.parametrize(
    ("path", "body", "target", "name"),
    [
        ("switch", {"node_id": "groupby"}, lesson, "switch_node"),
        ("skip", {}, turn, "skip_pending"),
        ("task", {}, turn, "start_practice_reply"),
        ("verify", {}, verify, "start_verification"),
    ],
)
async def test_core_failure_on_action_is_a_reply(enrolled, monkeypatch, path, body, target, name) -> None:
    await _finish(enrolled)
    monkeypatch.setattr(target, name, _broken)

    response = await enrolled.post(f"{BASE}/{path}", json=body)

    assert response.status_code == 200, response.text
    last = response.json()["messages"][-1]
    assert last["role"] == "assistant"
    assert FAILURE_REPLY.split(".")[0] in last["html"]


# --- ответ на задание ---


async def test_answer_does_not_parse_intents(enrolled, web_app) -> None:
    """«Пропусти» в поле ответа — это ответ на задание, а не команда пропуска."""
    await _finish(enrolled)
    conn = _db(web_app)
    session_id = repos.get_open_session(conn)
    state = repos.get_session_state(conn, session_id)
    repos.update_session_state(conn, session_id, state.model_copy(update={"pending_item_id": 2}))
    before = repos.last_event_id(conn)

    body = (await enrolled.post(f"{BASE}/answer", json={"item_id": 2, "answer": "пропусти"})).json()

    assert repos.item_result_after(conn, 2, before) == 0.0  # записан неверный ответ
    assert body["verdict"]["item_id"] == 2


# --- анкета ---


def test_survey_summary_promises_check_only_with_confident_blocks() -> None:
    confident = {block.key: survey.CONFIDENT_INDEX for block in survey.BLOCKS}
    unknown = {block.key: 0 for block in survey.BLOCKS}

    assert views.survey_summary(confident)["claimed"] is True
    summary = views.survey_summary(unknown)
    assert summary["claimed"] is False
    assert [item["title"] for item in summary["items"]] == [block.title for block in survey.BLOCKS]
    assert {item["answer"] for item in summary["items"]} == {survey.SELF_LEVELS[0]}


async def test_survey_question_shows_progress_and_example(enrolled) -> None:
    question = (await enrolled.post(f"{BASE}/survey/next", json={"answers": [1]})).json()["question"]

    assert question["number"] == 1
    assert question["total"] == len(survey.BLOCKS)
    assert question["question"]
    assert question["example"]


async def test_confident_survey_starts_with_check(enrolled) -> None:
    body = await _finish(enrolled, choice=survey.CONFIDENT_INDEX)

    assert body["summary"]["claimed"] is True
    assert "начнём с короткой проверки" in body["messages"][0]["html"]
    assert body["state"]["mode"] == "verify"
    assert body["state"]["item"] is not None


async def test_turn_before_survey_does_not_open_session(enrolled, web_app) -> None:
    assert (await enrolled.post(f"{BASE}/chat", json={"text": "привет"})).status_code == 409
    assert repos.get_open_session(_db(web_app)) is None


async def test_all_mastered_student_gets_route_done(enrolled, web_app) -> None:
    conn = _db(web_app)
    now = time.time()  # давнее владение затухает — нужно свежее
    for node in CourseGraph.load(conn).node_ids:
        repos.upsert_mastery(conn, node, alpha=38.0, beta=2.0, last_seen=now, next_review=now + 1e9)

    body = await _finish(enrolled)

    assert turn.ROUTE_DONE_REPLY.split(".")[0] in body["messages"][-1]["html"]
    assert body["state"]["item"] is None
    (card,) = (await enrolled.get("/api/courses")).json()["courses"]
    assert card["status"] == "done"


# --- состояние и экранирование ---


async def test_lesson_survives_reopening_databases(enrolled, web_app) -> None:
    await _finish(enrolled)
    await enrolled.post(f"{BASE}/chat", json={"text": "что такое groupby?"})
    before = (await enrolled.get(f"{BASE}/state")).json()["state"]

    web_app.state.userdbs.close_all()
    after = (await enrolled.get(f"{BASE}/state")).json()["state"]

    assert after["messages"] == before["messages"]
    assert after["node"] == before["node"]
    assert after["item"] == before["item"]


async def test_topic_names_are_escaped(enrolled, web_app) -> None:
    await _finish(enrolled)
    conn = _db(web_app)
    conn.execute("UPDATE concepts SET name = '<b>Жирная</b>' WHERE id = 'groupby'")
    conn.commit()

    html = (await enrolled.get("/profile")).text

    assert "<b>Жирная</b>" not in html
    assert "&lt;b&gt;Жирная&lt;/b&gt;" in html
